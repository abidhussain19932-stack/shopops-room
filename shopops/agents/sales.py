"""sales-agent: posts paid orders and customer escalations into the room."""
from __future__ import annotations

import re

from ..protocol import _ITEM
from ..switch_client import Message
from .base import ShopAgent

HELP = """- sell <customer>: K102 x2, S201 x1   record a paid ShopPal order and hand it to inventory
- escalate <customer>: <problem>      flag a customer issue for the owner (refunds go to fulfilment)
- orders                              list recent orders"""


class SalesAgent(ShopAgent):
    name = "sales-agent"
    role = "posts paid PayPal orders and customer escalations from ShopPal"
    help_text = HELP
    system_prompt = ("You are sales-agent in a small shop's Telegram ops room run on Switch. "
                     "You relay paid orders and customer escalations from the ShopPal storefront "
                     "bot. You never approve refunds or purchases yourself: the owner does. "
                     "Hand stock questions to inventory-agent and shipping or refunds to "
                     "fulfillment-agent by writing their name. Reply in 1-3 short lines.")

    def context(self) -> str:
        return "\n".join(f"{p['sku']} {p['name']} ${p['price']:.2f}" for p in self.store.products())

    def _next_order_id(self) -> str:
        nums = [int(m.group(1)) for o in self.store.orders()
                if (m := re.match(r"O(\d+)$", o["id"]))]
        return f"O{max(nums + [1000]) + 1}"

    async def command(self, msg: Message, text: str) -> str | list[str] | None:
        t = text.strip()
        if m := re.match(r"(?:sell|new order)\s+([^:]+):\s*(.+)$", t, re.I):
            return self.sell(m[1].strip(), m[2])
        if m := re.match(r"escalate\s+([^:]+):\s*(.+)$", t, re.I):
            self.store.log(self.name, f"escalation {m[1].strip()}: {m[2].strip()}")
            return (f"🚨 Escalation from {m[1].strip()}: {m[2].strip()}\n"
                    "Owner, your call. If a refund is due, say `@fulfillment-agent refund <order>`.")
        if t.lower() in {"orders", "recent orders"}:
            rows = self.store.orders()[-8:]
            return ("Orders:\n" + "\n".join(
                f"{o['id']} [{o['status']}] {o['customer']} {o['total']:.2f} {o['currency']}"
                for o in rows)) if rows else "No orders yet."
        return None

    def sell(self, customer: str, items_text: str) -> str | list[str]:
        items = [{"sku": s.upper(), "qty": int(q)} for s, q in _ITEM.findall(items_text)]
        if not items:
            return "I need items like `K102 x2, S201 x1`."
        total = 0.0
        for i in items:
            p = self.store.product(i["sku"])
            if not p:
                return f"Unknown SKU {i['sku']}."
            total += p["price"] * i["qty"]
        oid = self._next_order_id()
        line = (f"ORDER {oid} | customer: {customer} | items: "
                + ", ".join(f"{i['sku']} x{i['qty']}" for i in items)
                + f" | total: {total:.2f} USD")
        self.store.log(self.name, f"posted {oid}")
        return [f"💰 Paid ShopPal order from {customer}: {total:.2f} USD.", f"@inventory-agent {line}"]
