"""Keep the shop store alive across restarts on hosts with an ephemeral disk.

Free hosts (Render) wipe local files whenever the container restarts. When a
Postgres DATABASE_URL is present, the SQLite shop store is snapshotted into a
`shopops.snapshot` table every few seconds (only when it changed) and restored
on boot, so orders, stock, approvals and the audit trail survive.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import ssl
from pathlib import Path

log = logging.getLogger("shopops.persist")


def _ssl():
    mode = os.getenv("DB_SSL_MODE", "disable")
    if mode in ("disable", "allow", "prefer"):
        return False
    ctx = ssl.create_default_context()
    if mode in ("require", "verify-ca"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _connect():
    import asyncpg

    return await asyncpg.connect(os.environ["DATABASE_URL"], ssl=_ssl(), timeout=20)


async def restore(path: str) -> bool:
    """Write the saved snapshot to `path` if there is one and no local file."""
    if not os.getenv("DATABASE_URL"):
        return False
    try:
        c = await _connect()
        try:
            await c.execute("CREATE SCHEMA IF NOT EXISTS shopops")
            await c.execute("CREATE TABLE IF NOT EXISTS shopops.snapshot "
                            "(id int PRIMARY KEY, data bytea NOT NULL, saved timestamptz DEFAULT now())")
            row = None if Path(path).exists() else await c.fetchrow(
                "SELECT data FROM shopops.snapshot WHERE id=1")
        finally:
            await c.close()
    except Exception as exc:
        log.warning("snapshot restore failed: %r", exc)
        return False
    if not row:
        return False
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(row["data"]))
    log.info("restored shop store from Postgres snapshot (%d bytes)", len(row["data"]))
    return True


async def snapshot_loop(conn: sqlite3.Connection, every: float = 5.0) -> None:
    if not os.getenv("DATABASE_URL"):
        return
    last = ""
    while True:
        await asyncio.sleep(every)
        try:
            data = conn.serialize()
            digest = hashlib.sha1(data).hexdigest()
            if digest == last:
                continue
            c = await _connect()
            try:
                await c.execute("INSERT INTO shopops.snapshot (id, data) VALUES (1, $1) "
                                "ON CONFLICT (id) DO UPDATE SET data=$1, saved=now()", data)
            finally:
                await c.close()
            last = digest
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("snapshot failed: %r", exc)
