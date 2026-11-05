"""The three ShopOps agents."""
from .fulfillment import FulfillmentAgent
from .inventory import InventoryAgent
from .sales import SalesAgent

ALL = {a.name: a for a in (SalesAgent, InventoryAgent, FulfillmentAgent)}
