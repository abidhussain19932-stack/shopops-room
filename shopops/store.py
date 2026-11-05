"""Shared SQLite store for the ShopOps agents: stock, orders, approvals.

One file, opened by all three agents in the same process (or container), so
what the sales agent records the inventory agent can reserve and the
fulfilment agent can ship or refund.  Every state change that costs money
(supplier PO, refund) goes through the `approvals` table: an agent *requests*
an approval, only the owner's chat message *decides* it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  sku TEXT PRIMARY KEY, name TEXT, price REAL, stock INTEGER,
  reorder_level INTEGER, reorder_qty INTEGER, supplier TEXT);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, customer TEXT, items TEXT, total REAL, currency TEXT,
  paypal_order_id TEXT, capture_id TEXT, status TEXT, tracking TEXT,
  refunded REAL DEFAULT 0, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, summary TEXT, payload TEXT,
  status TEXT, room_id TEXT, requested_by TEXT, decided_by TEXT, result TEXT,
  created REAL, decided REAL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, text TEXT);
"""

DEFAULT_SUPPLIER = "Jaipur Textile Co."


class StockError(Exception):
    pass


class Store:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    # -------------------------------------------------------------- catalog
    def seed_catalog(self, catalog_path: str) -> int:
        """Load products from the ShopPal catalog.json once (idempotent)."""
        if self._x("SELECT COUNT(*) FROM products").fetchone()[0]:
            return 0
        items = json.loads(Path(catalog_path).read_text())
        for p in items:
            stock = int(p.get("stock", 0))
            # Re-order when stock falls to 3 or fewer; order 10 at a time.
            self._x("INSERT OR IGNORE INTO products VALUES (?,?,?,?,?,?,?)",
                    (p["sku"], p["name"], float(p["price"]), stock, 3, 10, DEFAULT_SUPPLIER))
        return len(items)

    def product(self, sku: str) -> dict | None:
        r = self._x("SELECT * FROM products WHERE sku=? COLLATE NOCASE", (sku,)).fetchone()
        return dict(r) if r else None

    def products(self) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM products ORDER BY sku").fetchall()]

    def low_stock(self) -> list[dict]:
        return [dict(r) for r in self._x(
            "SELECT * FROM products WHERE stock <= reorder_level ORDER BY stock, sku").fetchall()]

    def adjust_stock(self, sku: str, delta: int) -> dict:
        with self.lock:
            p = self.product(sku)
            if not p:
                raise StockError(f"unknown SKU {sku}")
            if p["stock"] + delta < 0:
                raise StockError(f"only {p['stock']} x {p['sku']} in stock, cannot take {-delta}")
            self._x("UPDATE products SET stock = stock + ? WHERE sku=?", (delta, p["sku"]))
            return self.product(p["sku"])

    # --------------------------------------------------------------- orders
    def upsert_order(self, order_id: str, customer: str, items: list[dict], total: float,
                     currency: str = "USD", paypal_order_id: str = "", capture_id: str = "",
                     status: str = "paid") -> dict:
        now = time.time()
        self._x("""INSERT INTO orders (id, customer, items, total, currency, paypal_order_id,
                   capture_id, status, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET customer=excluded.customer, items=excluded.items,
                   total=excluded.total, paypal_order_id=excluded.paypal_order_id,
                   capture_id=excluded.capture_id, updated=excluded.updated""",
                (order_id, customer, json.dumps(items), total, currency, paypal_order_id,
                 capture_id, status, now, now))
        return self.order(order_id)

    def order(self, order_id: str) -> dict | None:
        r = self._x("SELECT * FROM orders WHERE id=? COLLATE NOCASE", (order_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["items"] = json.loads(d["items"])
        return d

    def orders(self, status: str | None = None) -> list[dict]:
        q, a = "SELECT id FROM orders", ()
        if status:
            q, a = q + " WHERE status=?", (status,)
        return [self.order(r["id"]) for r in self._x(q + " ORDER BY created", a).fetchall()]

    def set_order(self, order_id: str, **fields) -> None:
        keys = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE orders SET {keys}, updated=? WHERE id=? COLLATE NOCASE",
                (*fields.values(), time.time(), order_id))

    def reserve_order(self, order_id: str) -> tuple[list[str], list[str]]:
        """Take the order's items out of stock. Returns (reserved lines, problems).

        All-or-nothing: if any line can't be covered nothing is taken.
        """
        o = self.order(order_id)
        if not o:
            raise StockError(f"unknown order {order_id}")
        if o["status"] != "paid":
            raise StockError(f"{o['id']} is {o['status']}, not waiting for stock")
        with self.lock:
            problems = []
            for it in o["items"]:
                p = self.product(it["sku"])
                if not p:
                    problems.append(f"unknown SKU {it['sku']}")
                elif p["stock"] < it["qty"]:
                    problems.append(f"{p['name']} ({p['sku']}): need {it['qty']}, have {p['stock']}")
            if problems:
                return [], problems
            lines = []
            for it in o["items"]:
                p = self.adjust_stock(it["sku"], -int(it["qty"]))
                lines.append(f"{it['qty']} x {p['name']} ({p['sku']}), {p['stock']} left")
            self.set_order(o["id"], status="reserved")
            return lines, []

    # ------------------------------------------------------------ approvals
    def request_approval(self, kind: str, summary: str, payload: dict, room_id: str,
                         requested_by: str) -> dict:
        cur = self._x("""INSERT INTO approvals (kind, summary, payload, status, room_id,
                         requested_by, created) VALUES (?,?,?,?,?,?,?)""",
                      (kind, summary, json.dumps(payload), "pending", room_id, requested_by,
                       time.time()))
        return self.approval(cur.lastrowid)

    def approval(self, approval_id: int) -> dict | None:
        r = self._x("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        return d

    def pending(self, kind: str | None = None) -> list[dict]:
        q, a = "SELECT id FROM approvals WHERE status='pending'", ()
        if kind:
            q, a = q + " AND kind=?", (kind,)
        return [self.approval(r["id"]) for r in self._x(q + " ORDER BY id", a).fetchall()]

    def decide(self, approval_id: int, approved: bool, by: str) -> dict | None:
        """Atomically move pending -> approved/denied. None if not pending (no double spend)."""
        cur = self._x("""UPDATE approvals SET status=?, decided_by=?, decided=?
                         WHERE id=? AND status='pending'""",
                      ("approved" if approved else "denied", by, time.time(), approval_id))
        return self.approval(approval_id) if cur.rowcount else None

    def finish_approval(self, approval_id: int, status: str, result: str) -> None:
        self._x("UPDATE approvals SET status=?, result=? WHERE id=?", (status, result, approval_id))

    # --------------------------------------------------------------- events
    def log(self, actor: str, text: str) -> None:
        self._x("INSERT INTO events (ts, actor, text) VALUES (?,?,?)", (time.time(), actor, text))

    def recent_events(self, n: int = 20) -> list[dict]:
        return [dict(r) for r in self._x(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()][::-1]


_store: Store | None = None


def get_store() -> Store:
    """Process-wide store (all agents in one process share it)."""
    global _store
    if _store is None:
        from .config import settings
        _store = Store(settings.store_path)
        if Path(settings.catalog_path).exists():
            _store.seed_catalog(settings.catalog_path)
    return _store
