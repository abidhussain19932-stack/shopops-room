#!/usr/bin/env python3
"""End-to-end check of the Switch wiring, no Telegram needed.

Uses the agents registered by setup_switch.py: sales-agent creates an internal
room with the other two, inventory-agent and fulfillment-agent connect over the
WebSocket, sales-agent addresses each by name, and we wait for both replies.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SHOPOPS_DB"] = str(Path(tempfile.mkdtemp()) / "smoke.db")  # before importing shopops

from shopops.agents import FulfillmentAgent, InventoryAgent, SalesAgent  # noqa: E402


async def main() -> int:
    sales, inv, ful = SalesAgent(), InventoryAgent(), FulfillmentAgent()
    tasks = [asyncio.create_task(a.run()) for a in (inv, ful)]
    await asyncio.wait_for(asyncio.gather(inv.switch.connected.wait(),
                                          ful.switch.connected.wait()), 20)
    room = await sales.switch.op("create_room", {
        "name": f"shopops-smoke-{int(time.time())}",
        "description": "ShopOps smoke test",
        "agent_names": ["sales-agent", "inventory-agent", "fulfillment-agent"],
        "internal_only": True, "bridge_id": None,
        "instructions": "Smoke test room for ShopOps Room.",
    }, bind_connection=False)
    room_id = room.get("room_id") or room.get("id")
    print("room:", room_id)
    await asyncio.sleep(2)  # let the room claim reach the live connections
    await sales.switch.post(room_id, "@inventory-agent ping")
    await sales.switch.post(room_id, "@fulfillment-agent ping")

    want = {"inventory-agent", "fulfillment-agent"}
    deadline = time.time() + 30
    seen: set[str] = set()
    while time.time() < deadline and seen != want:
        await asyncio.sleep(1.5)
        hist = await sales.switch.history(room_id, 20)
        items = (hist.get("events") or hist.get("messages") or []) if isinstance(hist, dict) else hist
        for m in items or []:
            who = m.get("sender_name") or m.get("sender") or ""
            body = m.get("body") or m.get("content") or ""
            for name in want:
                if name in who and "online" in body:
                    if name not in seen:
                        print(f"reply from {name}: {body}")
                    seen.add(name)
    ok = seen == want

    # Day-2 workflow over the real server: sales posts a paid order, inventory
    # reserves stock and drafts a PO, fulfilment ships. (Owner approvals need a
    # human sender, so those are covered by tests/test_workflow.py.)
    from shopops.store import get_store
    store = get_store()
    oid = f"O{int(time.time()) % 100000}"
    await sales.switch.post(room_id, f"@inventory-agent ORDER {oid} | customer: Smoke Test | "
                            f"items: S201 x2 | total: 178.00 USD | paypal: X | capture: Y")
    flow = {"reserved": False, "po": False, "shipped": False}
    deadline = time.time() + 40
    while time.time() < deadline and not all(flow.values()):
        await asyncio.sleep(1.5)
        o = store.order(oid)
        flow["reserved"] = bool(o and o["status"] in ("reserved", "shipped"))
        flow["po"] = bool(store.pending("po"))
        flow["shipped"] = bool(o and o["status"] == "shipped")
    print("workflow:", flow)
    ok = ok and all(flow.values())
    for t in tasks:
        t.cancel()
    print("SMOKE TEST", "PASSED" if ok else f"FAILED (got {seen}, flow {flow})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
