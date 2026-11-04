"""A small client for the Switch agent protocol (WebSocket transport).

Switch ships no SDK: an agent is any process that
  * authenticates with the API key it got at registration (Bearer),
  * holds one live connection at  GET /agents/{id}/connection/ws,
    answering every server `ping` frame with {"type": "pong", "cursor": n},
  * and acts over plain HTTP (POST /agents/{id}/message, /ops/{name}).

This module implements exactly that, with reconnects that resume from the
last delivered event, so the three ShopOps agents only write their handlers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
import websockets

log = logging.getLogger("shopops.switch")


@dataclass
class Message:
    """A chat message delivered to the agent (a Switch `message` event)."""

    room_id: str
    body: str
    sender: str
    sender_name: str
    sender_kind: str
    addressed: bool
    message_id: str
    thread_id: str | None
    timestamp: int | None
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_event(cls, data: dict) -> "Message":
        p = data.get("payload") or {}
        return cls(
            room_id=data.get("room_id") or "",
            body=p.get("body") or "",
            sender=p.get("sender") or "",
            sender_name=p.get("sender_name") or "",
            sender_kind=p.get("sender_kind") or "",
            addressed=bool(p.get("addressed")),
            message_id=p.get("message_id") or "",
            thread_id=p.get("thread_id"),
            timestamp=p.get("timestamp"),
            raw=data,
        )


Handler = Callable[["SwitchAgent", Message], Awaitable[None]]


async def register_agent(base_url: str, registration_token: str, name: str,
                         description: str, overwrite: bool = True) -> dict:
    """Register (or re-key) an always-on external agent. Returns {id, api_key}."""
    body = {
        "name": name,
        "description": description,
        "connector_type": "external",
        "integration_profile": {
            "connection_model": "always_on",
            "message_exchange": True,
            "pre_invocation_mediation": [],
            "post_invocation_mediation": [],
            "event_reporting": [],
        },
        "overwrite": overwrite,
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=20, trust_env=False) as http:
        r = await http.post("/agents", json=body,
                            headers={"Authorization": f"Bearer {registration_token}"})
        r.raise_for_status()
        return r.json()


class SwitchAgent:
    """One agent's live presence on a Switch server."""

    def __init__(self, base_url: str, agent_id: str, api_key: str, name: str,
                 handler: Handler | None = None, *, only_addressed: bool = True,
                 connection_id: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.api_key = api_key
        self.name = name
        self.handler = handler
        self.only_addressed = only_addressed
        # Reused on every reconnect: reopening the same id resumes, a new id
        # would be a second connection.
        self.connection_id = connection_id or str(uuid.uuid4())
        self.cursor: int = 0
        self.generation: int | None = None
        self.rooms: list[str] = []
        self.connected = asyncio.Event()
        self._stop = False
        self._tasks: set[asyncio.Task] = set()
        self.http = httpx.AsyncClient(
            base_url=self.base_url, timeout=30, trust_env=False,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    # ------------------------------------------------------------------ HTTP
    async def post(self, room_id: str, content: str) -> dict:
        """Post a message into a room the agent is a member of."""
        r = await self.http.post(f"/agents/{self.agent_id}/message",
                                 json={"room_id": room_id, "content": content})
        r.raise_for_status()
        return r.json() if r.content else {}

    async def op(self, name: str, args: dict | None = None, *,
                 bind_connection: bool = True) -> Any:
        """Call one of the agent operations listed at GET /agents/{id}/ops."""
        headers = {"X-Switch-Connection-Id": self.connection_id} if bind_connection else {}
        r = await self.http.post(f"/agents/{self.agent_id}/ops/{name}",
                                 json=args or {}, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"op {name} failed: {r.status_code} {r.text[:300]}")
        return r.json().get("result")

    async def list_ops(self) -> list:
        r = await self.http.get(f"/agents/{self.agent_id}/ops")
        r.raise_for_status()
        return r.json()

    async def history(self, room_id: str, limit: int = 20) -> Any:
        r = await self.http.get(f"/agents/{self.agent_id}/rooms/{room_id}/history",
                                params={"limit": limit})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------- WebSocket
    def _ws_url(self) -> str:
        ws_base = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        q = (f"connection_id={self.connection_id}&scope=all"
             f"&filter={'addressed' if self.only_addressed else 'all'}"
             f"&client=shopops-room&client_version=0.1")
        if self.cursor:
            q += f"&start_from={self.cursor}"
            if self.generation is not None:
                q += f"&expected_generation={self.generation}"
        else:
            q += "&start_from=head"
        return f"{ws_base}/agents/{self.agent_id}/connection/ws?{q}"

    async def run_forever(self) -> None:
        """Stay connected until stop(); back off and resume on any drop."""
        delay = 1.0
        while not self._stop:
            try:
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except _Fatal as exc:
                log.error("[%s] connection refused for good: %s", self.name, exc)
                raise
            except Exception as exc:  # network blips, server restarts
                log.warning("[%s] connection dropped: %r", self.name, exc)
            self.connected.clear()
            if self._stop:
                break
            await asyncio.sleep(delay + random.random())
            delay = min(delay * 2, 30.0)

    async def _session(self) -> None:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with websockets.connect(self._ws_url(), additional_headers=headers,
                                      ping_interval=None, max_size=8 * 2**20,
                                      proxy=None) as ws:
            async for raw in ws:
                frame = json.loads(raw)
                kind = frame.get("event") or frame.get("type")
                if kind == "ping":
                    # Answer at once, never behind a slow handler: 6 s of
                    # silence and the server lapses the connection.
                    await ws.send(json.dumps({"type": "pong", "cursor": self.cursor}))
                    continue
                if isinstance(frame.get("id"), int):
                    self.cursor = max(self.cursor, frame["id"])
                data = frame.get("data") or {}
                if kind == "connection_state":
                    self.generation = data.get("generation", self.generation)
                    self.rooms = [r if isinstance(r, str) else r.get("room_id", "")
                                  for r in data.get("rooms") or []]
                    self.connected.set()
                    log.info("[%s] connected (rooms=%d, protocol=%s)", self.name,
                             len(self.rooms), data.get("protocol"))
                elif kind == "refused":
                    status = data.get("status")
                    if status in (401, 403):
                        raise _Fatal(f"{status} {data.get('detail')}")
                    raise RuntimeError(f"refused {status} {data.get('detail')}")
                elif kind == "evicted":
                    if data.get("code") == "taken_over":
                        self._stop = True
                        raise _Fatal("taken over by another connection")
                    raise RuntimeError(f"evicted: {data}")
                elif kind == "message":
                    self._dispatch(data)
                elif kind == "room_join":
                    rid = data.get("room_id")
                    if rid and rid not in self.rooms:
                        self.rooms.append(rid)

    def _dispatch(self, data: dict) -> None:
        msg = Message.from_event(data)
        if msg.sender_name == self.name:
            return  # our own echo
        if self.only_addressed and not msg.addressed:
            return
        if self.handler is None:
            return
        task = asyncio.create_task(self._safe_handle(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe_handle(self, msg: Message) -> None:
        try:
            await self.handler(self, msg)
        except Exception:
            log.exception("[%s] handler failed on %r", self.name, msg.body[:80])

    async def stop(self) -> None:
        self._stop = True
        for t in list(self._tasks):
            t.cancel()
        await self.http.aclose()


class _Fatal(Exception):
    pass
