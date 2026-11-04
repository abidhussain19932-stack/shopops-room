"""Base class for a ShopOps agent: Switch presence + Groq brain + fallback."""
from __future__ import annotations

import asyncio
import logging
import re

from .. import llm
from ..config import settings
from ..switch_client import Message, SwitchAgent

log = logging.getLogger("shopops.agent")


class ShopAgent:
    name: str = "agent"
    role: str = "a ShopOps agent"
    system_prompt: str = "You are a helpful back-office agent for a small online shop."

    def __init__(self, switch_url: str | None = None, agent_id: str | None = None,
                 api_key: str | None = None):
        aid, key = settings.agent_credentials(self.name)
        self.switch = SwitchAgent(switch_url or settings.switch_url, agent_id or aid,
                                  api_key or key, self.name, self._on_message)
        if not self.switch.agent_id or not self.switch.api_key:
            raise SystemExit(f"{self.name}: no id/key; run scripts/setup_switch.py first")

    # Subclasses override this. Return the reply text, or None for no reply.
    async def handle(self, msg: Message) -> str | None:
        text = strip_mentions(msg.body)
        if text.lower() in {"ping", "hello", "hi", "status"}:
            return f"✅ {self.name} online — {self.role}."
        answer = await llm.chat(self.system_prompt, f"{msg.sender_name}: {text}")
        return answer or f"✅ {self.name} here ({self.role}). I heard: “{text[:200]}”"

    async def _on_message(self, sw: SwitchAgent, msg: Message) -> None:
        log.info("[%s] <- %s: %s", self.name, msg.sender_name, msg.body[:120])
        reply = await self.handle(msg)
        if reply:
            await sw.post(msg.room_id, reply)

    async def run(self) -> None:
        await self.switch.run_forever()


_MENTION = re.compile(r"@?[a-z0-9][a-z0-9._-]*-agent\b[:,]?\s*", re.I)


def strip_mentions(body: str) -> str:
    return _MENTION.sub("", body).strip()


async def run_agents(*agents: ShopAgent) -> None:
    await asyncio.gather(*(a.run() for a in agents))
