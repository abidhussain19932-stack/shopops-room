"""Workflow tests with no network: in-memory store, fake PayPal, handler called directly."""
import asyncio
import json
from pathlib import Path

import pytest

from shopops.agents import FulfillmentAgent, InventoryAgent
from shopops.protocol import parse_decision, parse_order
from shopops.store import Store
from shopops.switch_client import Message

CATALOG = Path(__file__).resolve().parent.parent / "data" / "catalog.json"
ORDER = ("ORDER O1001 | customer: Priya | items: S201 x2 | total: 178.00 USD | "
         "paypal: 5OX1 | capture: CAP123")


class FakePayPal:
    configured = True

    def __init__(self):
        self.calls = []

    async def refund_capture(self, capture_id, amount, currency, note, request_id=None):
        self.calls.append((capture_id, amount, currency, request_id))
        return {"id": "REF1", "status": "COMPLETED"}


def msg(body, sender="Abid", addressed=True, room="r1"):
    return Message(room_id=room, body=body, sender="@x:localhost", sender_name=sender,
                   sender_kind="", addressed=addressed, message_id="m", thread_id=None,
                   timestamp=1)


@pytest.fixture
def world():
    store = Store(":memory:")
    store.seed_catalog(str(CATALOG))
    inv = InventoryAgent(agent_id="i", api_key="k", store=store)
    pp = FakePayPal()
    ful = FulfillmentAgent(agent_id="f", api_key="k", store=store, paypal=pp)
    return store, inv, ful, pp


def run(coro):
    out = asyncio.run(coro)
    return "\n".join(out) if isinstance(out, list) else out


def test_parsers():
    o = parse_order(ORDER)
    assert o["id"] == "O1001" and o["items"] == [{"sku": "S201", "qty": 2}] and o["total"] == 178.0
    assert o["capture_id"] == "CAP123"
    assert parse_decision("approve A12") == (True, 12)
    assert parse_decision("@inventory-agent deny #3") == (False, 3)
    assert parse_decision("approved by nobody") is None


def test_order_reserves_stock_and_drafts_po(world):
    store, inv, ful, _ = world
    before = store.product("S201")["stock"]  # 3
    reply = run(inv.handle(msg("@inventory-agent " + ORDER, sender="sales-agent")))
    assert "Reserved for O1001" in reply and "@fulfillment-agent please ship O1001" in reply
    assert store.product("S201")["stock"] == before - 2
    assert "APPROVAL A1" in reply and store.pending("po")[0]["payload"]["sku"] == "S201"


def test_po_needs_owner_and_agents_cannot_approve(world):
    store, inv, _, _ = world
    run(inv.handle(msg(ORDER, sender="sales-agent")))
    assert run(inv.handle(msg("approve A1", sender="sales-agent", addressed=True))) is None
    assert store.approval(1)["status"] == "pending"
    out = run(inv.handle(msg("approve A1", addressed=False)))
    assert "approved" in out and store.approval(1)["status"] == "ordered"
    again = run(inv.handle(msg("approve A1", addressed=False)))
    assert "already" in again
    before = store.product("S201")["stock"]
    assert "received" in run(inv.handle(msg("receive A1")))
    assert store.product("S201")["stock"] == before + 10


def test_deny_po(world):
    store, inv, *_ = world
    run(inv.handle(msg(ORDER, sender="sales-agent")))
    assert "denied" in run(inv.handle(msg("deny A1", addressed=False)))
    assert store.approval(1)["status"] == "denied"


def test_overselling_blocked(world):
    store, inv, *_ = world
    big = ORDER.replace("S201 x2", "S201 x9")
    out = run(inv.handle(msg(big, sender="sales-agent")))
    assert "Can't reserve" in out and store.product("S201")["stock"] == 3


def test_ship_then_refund_flow(world):
    store, inv, ful, pp = world
    run(inv.handle(msg(ORDER, sender="sales-agent")))
    assert "shipped" in run(ful.handle(msg("@fulfillment-agent please ship O1001")))
    out = run(ful.handle(msg("refund O1001 50 arrived damaged")))
    assert "APPROVAL A2" in out and not pp.calls  # nothing moves before approval
    # inventory-agent must ignore a refund approval
    assert run(inv.handle(msg("approve A2", addressed=False))) is None
    assert store.approval(2)["status"] == "pending"
    done = run(ful.handle(msg("approve A2", addressed=False)))
    assert "Refunded 50.00 USD" in done
    assert pp.calls == [("CAP123", 50.0, "USD", "shopops-A2")]
    assert store.order("O1001")["refunded"] == 50.0
    assert "only 128.00" in run(ful.handle(msg("refund O1001 500")))


def test_refund_denied_does_not_call_paypal(world):
    store, inv, ful, pp = world
    run(inv.handle(msg(ORDER, sender="sales-agent")))
    run(ful.handle(msg("refund O1001")))
    assert "denied" in run(ful.handle(msg("deny A2", addressed=False)))
    assert not pp.calls


def test_bare_approve_with_two_pending_asks_which(world):
    store, inv, ful, _ = world
    run(inv.handle(msg(ORDER, sender="sales-agent")))
    run(inv.handle(msg("po K102")))
    out = run(inv.handle(msg("approve", addressed=False)))
    assert "which one" in out


@pytest.mark.parametrize("text", ["show stock", "stock", "list inventory", "check stock levels"])
def test_stock_without_sku_lists_everything(world, text):
    _, inv, _, _ = world
    out = run(inv.handle(msg(text)))
    assert out.startswith("Stock:") and "S201" in out
