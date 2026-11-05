#!/usr/bin/env python3
"""Single-container entrypoint for free hosts (Render): switch-core + setup + agents.

Needs only DATABASE_URL (a Postgres, e.g. Render's free one) and MASTER_SECRET;
every other Switch secret is derived from MASTER_SECRET so a restart keeps the
same keys (the Telegram bridge token in the DB is encrypted with them).
Supervises three processes and exits if any dies, so the host restarts it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def derive(master: str, label: str, n: int = 32) -> str:
    return hmac.new(master.encode(), label.encode(), hashlib.sha256).hexdigest()[:n]


def build_env() -> dict[str, str]:
    master = os.environ.get("MASTER_SECRET", "")
    url = os.environ.get("DATABASE_URL", "")
    if len(master) < 16 or not url:
        sys.exit("MASTER_SECRET (16+ chars) and DATABASE_URL are required")
    u = urllib.parse.urlparse(url)
    env = dict(os.environ)
    port = os.environ.get("PORT", "10000")
    env.update({
        "DB_HOST": u.hostname or "", "DB_PORT": str(u.port or 5432),
        "DB_NAME": (u.path or "/switch").lstrip("/"),
        "DB_OWNER_USER": urllib.parse.unquote(u.username or ""),
        "DB_OWNER_PASSWORD": urllib.parse.unquote(u.password or ""),
        "DB_USER": "switch_app", "DB_PASSWORD": derive(master, "db-app"),
        "DB_SSL_MODE": os.environ.get("DB_SSL_MODE", "require"),
        "AGENT_REGISTRATION_TOKEN": derive(master, "agent-reg"),
        "SECRET_KEYS": "k1:" + derive(master, "secret-keys", 64),
        "GATEWAY_ADMIN_EMAIL": os.environ.get("GATEWAY_ADMIN_EMAIL", "owner@shopops.local"),
        "GATEWAY_ADMIN_PASSWORD": derive(master, "admin-pw", 24),
        "GATEWAY_COOKIE_SECURE": "true",
        "SERVER_PORT": port, "SWITCH_URL": f"http://127.0.0.1:{port}",
        "ID_SERVER_NAME": "localhost", "MATRIX_SERVER_NAME": "localhost",
        "TELEMETRY_ENABLED": "false",
        "FRONTEND_BASE_URL": os.environ.get("RENDER_EXTERNAL_URL", f"http://127.0.0.1:{port}"),
        "AGENTS_ENV_FILE": "/tmp/agents.env",
        "SHOPOPS_DB": os.environ.get("SHOPOPS_DB", "/tmp/shopops.db"),
        "NO_PROXY": "127.0.0.1,localhost", "PYTHONUNBUFFERED": "1",
    })
    return env


def _ssl(mode: str):
    if mode in ("disable", "allow", "prefer"):
        return False
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    return ctx


async def ensure_db_role(env: dict[str, str]) -> None:
    import asyncpg

    for attempt in range(30):
        try:
            c = await asyncpg.connect(host=env["DB_HOST"], port=int(env["DB_PORT"]),
                                      user=env["DB_OWNER_USER"], password=env["DB_OWNER_PASSWORD"],
                                      database=env["DB_NAME"], ssl=_ssl(env["DB_SSL_MODE"]),
                                      timeout=20)
            break
        except Exception as exc:
            print(f"waiting for Postgres ({exc!r})", flush=True)
            await asyncio.sleep(3)
    else:
        sys.exit("Postgres unreachable")
    try:
        exists = await c.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", env["DB_USER"])
        pw = env["DB_PASSWORD"].replace("'", "''")
        if not exists:
            await c.execute(f"CREATE ROLE {env['DB_USER']} LOGIN PASSWORD '{pw}' "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
            print("created runtime DB role", flush=True)
        else:
            await c.execute(f"ALTER ROLE {env['DB_USER']} PASSWORD '{pw}'")
    finally:
        await c.close()


def wait_health(url: str, timeout: int = 900) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if urllib.request.urlopen(url + "/health", timeout=5).status == 200:
                return True
        except Exception:
            time.sleep(2)
    return False


def main() -> None:
    env = build_env()
    asyncio.run(ensure_db_role(env))
    py = sys.executable
    procs: dict[str, subprocess.Popen] = {}
    procs["switch"] = subprocess.Popen([py, "-m", "switch_core.main"], env=env, cwd=os.environ.get("SWITCH_CWD", "/app"))
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for p in procs.values():
            if p.poll() is None:
                p.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    if not wait_health(env["SWITCH_URL"]):
        stop()
        sys.exit("switch-core never became healthy")
    print("switch-core healthy; registering bridge + agents", flush=True)
    rc = subprocess.call([py, os.path.join(ROOT, "scripts", "setup_switch.py")], env=env, cwd=ROOT)
    if rc:
        stop()
        sys.exit(f"setup_switch.py failed ({rc})")
    procs["agents"] = subprocess.Popen([py, "-m", "shopops"], env=env, cwd=ROOT)
    # Best-effort helper: not in `procs`, so its failure never takes the service down.
    subprocess.Popen([py, os.path.join(ROOT, "scripts", "auto_link_owner.py")], env=env, cwd=ROOT)
    while not stopping:
        for name, p in procs.items():
            if p.poll() is not None:
                print(f"{name} exited with {p.returncode}; shutting down", flush=True)
                stop()
                sys.exit(1)
        time.sleep(2)


if __name__ == "__main__":
    main()
