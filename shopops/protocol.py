"""The little text protocol the agents and the owner speak in the room.

Plain chat lines, so the Telegram group stays readable and every step is visible:

  ORDER O1001 | customer: Priya | items: K102 x2, S201 x1 | total: 167.00 USD | paypal: 5O1.. | capture: 3AB..
  approve A3 / deny A3            (owner only)
  refund O1001 [amount] [reason]
  ship O1001
"""
from __future__ import annotations

import re

_ORDER = re.compile(r"^\s*ORDER\s+(?P<id>[A-Za-z0-9_-]+)\s*(?P<rest>.*)$", re.I | re.S)
_ITEM = re.compile(r"([A-Za-z]\d{2,4})\s*(?:x|×|\*)\s*(\d+)", re.I)
_DECISION = re.compile(r"^\s*(?:@\S+\s+)?(approve|deny|reject)\s*(?:#|a)?\s*(\d+)?\b", re.I)


def parse_order(text: str) -> dict | None:
    m = _ORDER.match(text)
    if not m:
        return None
    fields: dict[str, str] = {}
    for part in m["rest"].split("|"):
        if ":" in part:
            k, v = part.split(":", 1)
            fields[k.strip().lower()] = v.strip()
    items = [{"sku": s.upper(), "qty": int(q)} for s, q in _ITEM.findall(fields.get("items", ""))]
    if not items:
        return None
    total_s = fields.get("total", "0")
    nm = re.search(r"[\d.]+", total_s)
    cur = re.search(r"[A-Za-z]{3}", total_s)
    return {
        "id": m["id"].upper(),
        "customer": fields.get("customer", "customer"),
        "items": items,
        "total": float(nm.group()) if nm else 0.0,
        "currency": (cur.group().upper() if cur else "USD"),
        "paypal_order_id": fields.get("paypal", ""),
        "capture_id": fields.get("capture", ""),
    }


def parse_decision(text: str) -> tuple[bool, int | None] | None:
    """'approve A3' -> (True, 3); 'deny' -> (False, None). None if not a decision."""
    m = _DECISION.match(text)
    if not m:
        return None
    return m[1].lower() == "approve", (int(m[2]) if m[2] else None)


def fmt_items(items: list[dict], names: dict[str, str] | None = None) -> str:
    names = names or {}
    return ", ".join(f"{i['qty']} x {names.get(i['sku'], i['sku'])}" for i in items)
