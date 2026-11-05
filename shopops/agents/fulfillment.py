"""fulfillment-agent: ships orders, issues PayPal refunds only after the owner approves."""
from __future__ import annotations

import hashlib
import re

from ..paypal import PayPal, PayPalError
from ..switch_client import Message
from .base import ShopAgent

HELP = """- ship O1001                     mark a reserved order shipped and get a tracking number
- orders                         list orders and their status
- refund O1001 [amount] [reason] ask the owner to approve a PayPal refund (full if no amount)"""


class FulfillmentAgent(ShopAgent):
    name = "fulfillment-agent"
    role = "ships orders and issues PayPal refunds only after the owner approves"
    approval_kinds = ("refund",)
    help_text = HELP
    system_prompt = ("You are fulfillment-agent in a small shop's Telegram ops room run on "
                     "Switch. You pack and ship orders and issue PayPal Sandbox refunds, but "
                     "only after the owner types approve. Reply in 1-3 short lines.")

    def __init__(self, *a, paypal: PayPal | None = None, **kw):
        super().__init__(*a, **kw)
        self.paypal = paypal or PayPal()

    def context(self) -> str:
        return "\n".join(f"{o['id']} [{o['status']}] {o['customer']} {o['total']:.2f} {o['currency']}"
                         for o in self.store.orders()) or "No orders yet."

    async def command(self, msg: Message, text: str) -> str | None:
        low = text.strip().lower()
        if m := re.match(r"(?:please\s+)?ship\s+(\S+)$", low):
            return self.ship(m[1])
        if re.match(r"orders$", low):
            os_ = self.store.orders()
            return ("Orders:\n" + "\n".join(
                f"• {o['id']} [{o['status']}] {o['customer']} {o['total']:.2f} {o['currency']}"
                for o in os_)) if os_ else "No orders yet."
        if m := re.match(r"refund\s+(\S+)(?:\s+\$?(\d+(?:\.\d{1,2})?))?\s*(.*)$", text.strip(), re.I):
            return self.request_refund(msg, m[1], float(m[2]) if m[2] else None, m[3].strip())
        return None

    def ship(self, oid: str) -> str:
        o = self.store.order(oid)
        if not o:
            return f"I have no order {oid.upper()}."
        if o["status"] == "paid":
            return f"{o['id']} is paid but stock isn't reserved yet. @inventory-agent reserve {o['id']}"
        if o["status"] != "reserved":
            return f"{o['id']} is {o['status']}, can't ship."
        tracking = "SO" + hashlib.sha1(o["id"].encode()).hexdigest()[:8].upper()
        self.store.set_order(o["id"], status="shipped", tracking=tracking)
        self.store.log(self.name, f"shipped {o['id']}")
        return f"🚚 {o['id']} shipped to {o['customer']}. Tracking {tracking}."

    def request_refund(self, msg: Message, oid: str, amount: float | None, reason: str) -> str:
        o = self.store.order(oid)
        if not o:
            return f"I have no order {oid.upper()}."
        left = round(o["total"] - (o["refunded"] or 0), 2)
        if left <= 0:
            return f"{o['id']} is already fully refunded."
        amount = amount if amount is not None else left
        if amount > left + 1e-9:
            return f"Can't refund {amount:.2f}: only {left:.2f} {o['currency']} is left on {o['id']}."
        for a in self.store.pending("refund"):
            if a["payload"]["order_id"] == o["id"]:
                return f"A refund for {o['id']} is already waiting: A{a['id']}."
        reason = reason or "Refund requested"
        summary = f"REFUND {amount:.2f} {o['currency']} to {o['customer']} for {o['id']} ({reason})"
        a = self.store.request_approval(
            "refund", summary,
            {"order_id": o["id"], "amount": amount, "currency": o["currency"],
             "capture_id": o["capture_id"], "reason": reason}, msg.room_id, self.name)
        return (f"💸 APPROVAL A{a['id']}: {summary}\n"
                f"Owner: type `approve A{a['id']}` to refund via PayPal or `deny A{a['id']}`.")

    async def execute_approval(self, a: dict) -> str:
        pl = a["payload"]
        o = self.store.order(pl["order_id"])
        if not pl.get("capture_id"):
            self.store.finish_approval(a["id"], "failed", "order has no PayPal capture id")
            return f"⚠️ A{a['id']} approved but {pl['order_id']} has no PayPal capture id, so nothing was refunded."
        if not self.paypal.configured:
            self.store.finish_approval(a["id"], "failed", "PayPal not configured")
            return f"⚠️ A{a['id']} approved but PayPal credentials aren't set on this server, nothing was refunded."
        try:
            res = await self.paypal.refund_capture(
                pl["capture_id"], pl["amount"], pl["currency"], pl["reason"],
                request_id=f"shopops-A{a['id']}")
        except PayPalError as exc:
            self.store.finish_approval(a["id"], "failed", str(exc))
            return f"⚠️ PayPal refused A{a['id']}: {exc}"
        refunded = round((o["refunded"] or 0) + pl["amount"], 2)
        full = refunded + 1e-9 >= o["total"]
        self.store.set_order(o["id"], refunded=refunded, status="refunded" if full else o["status"])
        self.store.finish_approval(a["id"], "refunded", f"PayPal refund {res.get('id')} {res.get('status')}")
        self.store.log(self.name, f"refunded {o['id']} {pl['amount']}")
        return (f"✅ A{a['id']} approved. Refunded {pl['amount']:.2f} {pl['currency']} on {o['id']} "
                f"via PayPal (refund {res.get('id')}, {res.get('status')}).")

    def deny_text(self, a: dict) -> str:
        return f"🚫 A{a['id']} denied. {a['payload']['order_id']} was not refunded."
