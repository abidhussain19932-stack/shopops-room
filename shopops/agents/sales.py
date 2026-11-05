"""sales-agent: posts paid orders and customer escalations into the room (ShopPal bridge lands Nov 6)."""
from __future__ import annotations

from .base import ShopAgent


class SalesAgent(ShopAgent):
    name = "sales-agent"
    role = "posts paid PayPal orders and customer escalations from ShopPal"
    system_prompt = ("You are sales-agent in a small shop's Telegram ops room run on Switch. "
                     "You relay paid orders and customer escalations from the ShopPal storefront "
                     "bot. You never approve refunds or purchases yourself: the owner does. "
                     "Hand stock questions to inventory-agent and shipping or refunds to "
                     "fulfillment-agent by writing their name. Reply in 1-3 short lines.")
