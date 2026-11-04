#!/usr/bin/env python3
"""Link the shop owner's Telegram account to the Switch owner (gateway admin).

Owner-only agents (the default addressing policy) recognise the owner only after
this link, which is what makes "approve" from the owner count and from anyone
else not. Telegram has no searchable user directory: post once in the ShopOps
group first so the bridge has seen you, then run:

    python scripts/link_owner.py <your_telegram_username>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from setup_switch import gateway_login  # noqa: E402
from shopops.config import settings  # noqa: E402


def main() -> None:
    username = (sys.argv[1] if len(sys.argv) > 1 else settings.owner_telegram_username).lstrip("@")
    if not username:
        sys.exit("usage: link_owner.py <telegram_username>  (or set OWNER_TELEGRAM_USERNAME)")
    gw = gateway_login(settings.switch_url)
    bridges = [b for b in gw.get("/gateway/collaborations").json() if b.get("bridge_type") == "telegram"]
    if not bridges:
        sys.exit("no Telegram bridge yet: run setup_switch.py with TELEGRAM_BOT_TOKEN set")
    bid = bridges[0]["bridge_id"]
    mine = gw.get("/gateway/auth/me/identities").json()
    if any(i.get("bridge_id") == bid for i in mine):
        print("owner already linked on this bridge:", [i["external_username"] for i in mine])
        return
    r = gw.get(f"/gateway/collaborations/{bid}/directory", params={"query": username})
    r.raise_for_status()
    users = r.json().get("users", [])
    match = next((u for u in users if (u.get("username") or "").lstrip("@").lower() == username.lower()),
                 users[0] if len(users) == 1 else None)
    if not match:
        sys.exit(f"@{username} not seen by the bot yet: post a message in the ShopOps group, then retry. "
                 f"Seen so far: {[u.get('username') for u in users]}")
    r = gw.post(f"/gateway/collaborations/{bid}/identities",
                json={"external_user_id": match["external_user_id"], "username": username})
    if not r.is_success:
        sys.exit(f"link failed: {r.status_code} {r.text}")
    print(f"linked Telegram @{username} to {settings.admin_email}")


if __name__ == "__main__":
    main()
