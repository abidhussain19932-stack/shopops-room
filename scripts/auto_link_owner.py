#!/usr/bin/env python3
"""Background helper for hosted installs: link the owner's Telegram account automatically.

Telegram has no searchable directory, so Switch only knows people who have
spoken in the group. Every 30 s this loop checks whether the gateway admin
(the shop owner) is linked on the Telegram bridge; if not, it collects the
non-bot accounts the bridge has seen (probing one character at a time, because
the "known accounts" search is a substring match) and links the account named
by OWNER_TELEGRAM_USERNAME, or the only human seen so far when that is unset.
It never guesses between several humans.
"""
from __future__ import annotations

import string
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from setup_switch import gateway_login  # noqa: E402
from shopops.config import settings  # noqa: E402


def seen_humans(gw: httpx.Client, bridge_id: str) -> dict[str, dict]:
    people: dict[str, dict] = {}
    for ch in string.ascii_lowercase + string.digits + "_":
        r = gw.get(f"/gateway/collaborations/{bridge_id}/directory", params={"query": ch})
        if not r.is_success:
            continue
        for u in r.json().get("users", []):
            name = (u.get("username") or "").lower()
            if name.endswith("bot"):
                continue
            people[u["external_user_id"]] = u
    return people


def try_link() -> bool:
    gw = gateway_login(settings.switch_url)
    bridges = [b for b in gw.get("/gateway/collaborations").json() if b.get("bridge_type") == "telegram"]
    if not bridges:
        return False
    bid = bridges[0]["bridge_id"]
    if any(i.get("bridge_id") == bid for i in gw.get("/gateway/auth/me/identities").json()):
        return True
    people = seen_humans(gw, bid)
    want = settings.owner_telegram_username.lstrip("@").lower()
    pick = None
    if want:
        pick = next((u for u in people.values() if (u.get("username") or "").lower() == want), None)
    elif len(people) == 1:
        pick = next(iter(people.values()))
    if not pick:
        print(f"auto-link: waiting ({len(people)} human(s) seen: {[u.get('username') for u in people.values()]})", flush=True)
        return False
    r = gw.post(f"/gateway/collaborations/{bid}/identities",
                json={"external_user_id": pick["external_user_id"], "username": pick.get("username") or ""})
    print(f"auto-link: linked @{pick.get('username')} -> {r.status_code} {r.text[:200]}", flush=True)
    return r.is_success


def main() -> None:
    while True:
        try:
            if try_link():
                time.sleep(600)
                continue
        except (Exception, SystemExit) as exc:  # keep trying; the hosted container must not die for this
            print(f"auto-link error: {exc!r}", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
