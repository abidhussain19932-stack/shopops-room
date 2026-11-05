"""inventory-agent: reserves stock for paid orders, flags low stock, drafts supplier POs."""
from __future__ import annotations

import re

from ..protocol import parse_order
from ..switch_client import Message
from .base import ShopAgent

HELP = """- stock [SKU]            show stock levels
- low                    list items at or below their reorder level
- order O1001            show a recorded order
- reserve O1001          take a paid order's items out of stock
- po [SKU [qty]]         draft supplier purchase order(s) for the owner to approve
- receive A3             goods for approved PO A3 arrived: add them to stock"""


class InventoryAgent(ShopAgent):
    name = "inventory-agent"
    role = "reserves stock, flags low stock, drafts supplier POs for owner approval"
    approval_kinds = ("po",)
    help_text = HELP
    system_prompt = ("You are inventory-agent in a small shop's Telegram ops room run on Switch. "
                     "You reserve stock for paid orders, warn when an item is low and draft "
                     "supplier purchase orders that only the owner can approve. Reply in 1-3 "
                     "short lines.")

    def context(self) -> str:
        rows = [f"{p['sku']} {p['name']}: {p['stock']} in stock (reorder at {p['reorder_level']})"
                for p in self.store.products()]
        pend = [f"A{a['id']} {a['summary']}" for a in self.store.pending("po")]
        return "\n".join(rows + (["Pending approvals:"] + pend if pend else []))

    # ------------------------------------------------------------- commands
    async def command(self, msg: Message, text: str) -> str | None:
        t = text.strip()
        order = parse_order(t)
        if order:
            return await self.on_new_order(msg, order)
        low = t.lower()
        if m := re.match(r"(?:(?:please\s+)?(?:show|list|check|get)\s+(?:me\s+)?(?:the\s+|all\s+)?)?(?:inventory|stock(?:\s+levels?)?)(?:\s+(?:for\s+)?(?!levels?\b)(\w+))?\s*[.!?]*$", low):
            return self.show_stock(m[1])
        if re.match(r"low(?:\s*stock)?$", low):
            return self.show_low()
        if m := re.match(r"order\s+(\S+)$", low):
            return self.show_order(m[1])
        if m := re.match(r"(?:please\s+)?reserve\s+(\S+)$", low):
            return await self.reserve(msg.room_id, m[1])
        if m := re.match(r"(?:draft\s+)?po(?:\s+(\w+)(?:\s+(\d+))?)?$", low):
            return self.draft_po(msg, m[1], int(m[2]) if m[2] else None)
        if m := re.match(r"receive\s+a?(\d+)$", low):
            return self.receive(int(m[1]))
        return None

    def show_stock(self, sku: str | None) -> str:
        if sku:
            p = self.store.product(sku)
            return (f"{p['name']} ({p['sku']}): {p['stock']} in stock, reorder at {p['reorder_level']}"
                    if p else f"No product with SKU {sku.upper()}.")
        return "Stock:\n" + "\n".join(
            f"{'⚠️' if p['stock'] <= p['reorder_level'] else '•'} {p['sku']} {p['name']}: {p['stock']}"
            for p in self.store.products())

    def show_low(self) -> str:
        low = self.store.low_stock()
        if not low:
            return "Nothing is low right now. ✅"
        return "Low stock:\n" + "\n".join(
            f"⚠️ {p['sku']} {p['name']}: {p['stock']} left (reorder at {p['reorder_level']})"
            for p in low) + "\nSay `po` and I'll draft supplier orders for you to approve."

    def show_order(self, oid: str) -> str:
        o = self.store.order(oid)
        if not o:
            return f"I have no order {oid.upper()}."
        items = ", ".join(f"{i['qty']} x {i['sku']}" for i in o["items"])
        return f"{o['id']} [{o['status']}] {o['customer']}: {items}, total {o['total']:.2f} {o['currency']}"

    async def on_new_order(self, msg: Message, order: dict) -> str:
        self.store.upsert_order(order["id"], order["customer"], order["items"], order["total"],
                                order["currency"], order["paypal_order_id"], order["capture_id"])
        self.store.log(self.name, f"recorded {order['id']}")
        return await self.reserve(msg.room_id, order["id"])

    async def reserve(self, room_id: str, order_id: str) -> str | list[str]:
        o = self.store.order(order_id)
        if not o:
            return f"I have no order {order_id.upper()}. Sales has to post it first."
        if o["status"] != "paid":
            return f"{o['id']} is already {o['status']}."
        lines, problems = self.store.reserve_order(o["id"])
        if problems:
            self.store.set_order(o["id"], status="stock_issue")
            return (f"⛔ Can't reserve {o['id']}:\n" + "\n".join(f"• {p}" for p in problems)
                    + "\nSay `po` to restock, then `reserve " + o["id"] + "` again.")
        self.store.log(self.name, f"reserved {o['id']}")
        out = [f"📦 Reserved for {o['id']}:"] + [f"• {l}" for l in lines]
        low = [self.store.product(i["sku"]) for i in o["items"]]
        low = [p for p in low if p and p["stock"] <= p["reorder_level"]]
        if low:
            out.append("⚠️ Low after this order: " + ", ".join(
                f"{p['name']} ({p['stock']} left)" for p in low))
            drafted = [self._make_po(room_id, p, None) for p in low]
            out += [d for d in drafted if d]
        return ["\n".join(out), f"@fulfillment-agent please ship {o['id']}"]

    # ----------------------------------------------------- purchase orders
    def _open_po_for(self, sku: str) -> dict | None:
        for a in self.store.pending("po"):
            if a["payload"]["sku"] == sku:
                return a
        return None

    def _make_po(self, room_id: str, p: dict, qty: int | None) -> str | None:
        qty = qty or p["reorder_qty"]
        if self._open_po_for(p["sku"]):
            return None
        cost = round(p["price"] * 0.5 * qty, 2)  # wholesale ~50% of retail
        summary = f"PO: {qty} x {p['name']} ({p['sku']}) from {p['supplier']}, est. ${cost:.2f}"
        a = self.store.request_approval(
            "po", summary, {"sku": p["sku"], "qty": qty, "supplier": p["supplier"], "cost": cost},
            room_id, self.name)
        return (f"📝 APPROVAL A{a['id']}: {summary}\n"
                f"Owner: type `approve A{a['id']}` to send it or `deny A{a['id']}` to drop it.")

    def draft_po(self, msg: Message, sku: str | None, qty: int | None) -> str:
        if sku:
            p = self.store.product(sku)
            if not p:
                return f"No product with SKU {sku.upper()}."
            targets = [p]
        else:
            targets = self.store.low_stock()
            if not targets:
                return "Nothing is low, so no PO needed."
        out = []
        for p in targets:
            d = self._make_po(msg.room_id, p, qty)
            out.append(d or f"A PO for {p['sku']} is already waiting for approval.")
        return "\n".join(out)

    async def execute_approval(self, a: dict) -> str:
        pl = a["payload"]
        self.store.finish_approval(a["id"], "ordered", f"PO sent to {pl['supplier']}")
        self.store.log(self.name, f"PO A{a['id']} sent")
        return (f"✅ A{a['id']} approved. PO for {pl['qty']} x {pl['sku']} sent to {pl['supplier']} "
                f"(est. ${pl['cost']:.2f}). Say `receive A{a['id']}` when it arrives.")

    def deny_text(self, a: dict) -> str:
        return f"🚫 A{a['id']} denied. No order placed with {a['payload']['supplier']}."

    def receive(self, aid: int) -> str:
        a = self.store.approval(aid)
        if not a or a["kind"] != "po":
            return f"A{aid} is not a purchase order."
        if a["status"] != "ordered":
            return f"A{aid} is {a['status']}, so nothing to receive."
        pl = a["payload"]
        p = self.store.adjust_stock(pl["sku"], pl["qty"])
        self.store.finish_approval(aid, "received", "stock added")
        self.store.log(self.name, f"received A{aid}")
        return f"📥 PO A{aid} received: {p['name']} is now at {p['stock']}."
