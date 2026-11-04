#!/usr/bin/env python3
"""One-shot, idempotent setup of the ShopOps Room on a Switch server.

  1. waits for switch-core (/health)
  2. logs in to the gateway as the seeded admin (headless, no Console/UI)
  3. registers the Telegram bridge for the ops bot (if TELEGRAM_BOT_TOKEN is set)
  4. registers the three always-on agents (POST /agents) and writes their ids
     and API keys to agents.env (git-ignored)
  5. sets each agent's addressing policy (owner only by default; open with
     SHOPOPS_OPEN_ADDRESSING=true so judges in the demo group can talk to them)

Run again any time: existing bridge is reused; agents are re-keyed only with --rekey
or when agents.env has no key for them.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shopops.config import AGENTS, agent_env_prefix, settings  # noqa: E402

AGENTS_ENV = Path(os.getenv("AGENTS_ENV_FILE", ROOT / "agents.env"))


def wait_for_switch(url: str, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=5, trust_env=False).status_code == 200:
                print(f"switch-core is up at {url}")
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    sys.exit(f"switch-core did not answer at {url}/health within {timeout}s")


def gateway_login(url: str) -> httpx.Client:
    c = httpx.Client(base_url=url, timeout=30, trust_env=False)
    r = c.post("/gateway/auth/login",
               json={"email": settings.admin_email, "password": settings.admin_password})
    if not r.is_success:
        sys.exit(f"gateway login failed for {settings.admin_email}: {r.status_code} {r.text}")
    token = r.cookies.get("switch_auth")
    if not token:
        sys.exit("gateway accepted the login but set no switch_auth cookie")
    # Carried as a header: the cookie may be Secure while we talk plain HTTP.
    c.headers["Cookie"] = f"switch_auth={token}"
    return c


def ensure_telegram_bridge(gw: httpx.Client) -> str | None:
    r = gw.get("/gateway/collaborations")
    r.raise_for_status()
    for b in r.json():
        if b.get("bridge_type") == "telegram":
            print(f"Telegram bridge already registered: {b['bridge_id']}")
            return b["bridge_id"]
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN not set: skipping the Telegram bridge (internal rooms only)")
        return None
    r = gw.post("/gateway/collaborations", json={
        "bridge_type": "telegram",
        "display_name": "ShopOps Telegram",
        "connection_config": {"bot_token": settings.telegram_bot_token,
                              "bot_username": settings.telegram_bot_username.lstrip("@")},
        # Telegram bots cannot create chats: the group is made by a person and
        # Switch turns it into a room when the bot is added.
        "channel_creation_enabled": False,
        "set_as_default": True,
    })
    if not r.is_success:
        sys.exit(f"Telegram bridge registration failed: {r.status_code} {r.text}")
    bridge_id = r.json()["bridge_id"]
    print(f"Registered Telegram bridge {bridge_id} for @{settings.telegram_bot_username}")
    return bridge_id


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = ["# Written by scripts/setup_switch.py: agent ids and API keys. Do not commit."]
    lines += [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def register_agents(url: str, rekey: bool) -> dict[str, str]:
    env = read_env(AGENTS_ENV)
    for name, desc in AGENTS.items():
        p = agent_env_prefix(name)
        if not rekey and env.get(f"{p}_ID") and env.get(f"{p}_KEY"):
            print(f"{name}: already registered ({env[f'{p}_ID']})")
            continue
        r = httpx.post(f"{url}/agents", timeout=30, trust_env=False,
                       headers={"Authorization": f"Bearer {settings.registration_token}"},
                       json={"name": name, "description": desc, "connector_type": "external",
                             "integration_profile": {
                                 "connection_model": "always_on", "message_exchange": True,
                                 "pre_invocation_mediation": [],
                                 "post_invocation_mediation": [], "event_reporting": []},
                             "overwrite": True})
        if not r.is_success:
            sys.exit(f"registering {name} failed: {r.status_code} {r.text}")
        data = r.json()
        env[f"{p}_ID"], env[f"{p}_KEY"] = data["id"], data["api_key"]
        print(f"{name}: registered {data['id']}")
    write_env(AGENTS_ENV, env)
    print(f"agent keys saved to {AGENTS_ENV}")
    return env


def set_addressing(gw: httpx.Client, env: dict[str, str]) -> None:
    open_all = os.getenv("SHOPOPS_OPEN_ADDRESSING", "false").lower() == "true"
    for name in AGENTS:
        aid = env[f"{agent_env_prefix(name)}_ID"]
        # null = anyone in a room with the agent may address it. The default
        # (owner + owner's agents) is what keeps refunds owner-only.
        if open_all:
            r = gw.put(f"/gateway/agents/{aid}/addressing-policy", json={"policy": None})
            print(f"{name}: addressing open to everyone -> {r.status_code}")
        else:
            print(f"{name}: addressing = owner + owner's agents (server default)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rekey", action="store_true", help="rotate all agent API keys")
    args = ap.parse_args()
    for var in ("registration_token", "admin_email", "admin_password"):
        if not getattr(settings, var):
            sys.exit(f"missing {var.upper()} in the environment (.env)")
    url = settings.switch_url
    wait_for_switch(url)
    gw = gateway_login(url)
    bridge = ensure_telegram_bridge(gw)
    env = register_agents(url, args.rekey)
    set_addressing(gw, env)
    print("\nDone.")
    if bridge:
        print(f"Next: add @{settings.telegram_bot_username} to your Telegram group, then in "
              "the group type:\n  /invite_agent @sales-agent\n  /invite_agent @inventory-agent\n"
              "  /invite_agent @fulfillment-agent\nand run scripts/link_owner.py to link your "
              "Telegram account to the Switch owner.")


if __name__ == "__main__":
    main()
