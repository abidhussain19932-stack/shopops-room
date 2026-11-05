"""Base class for a ShopOps agent: Switch presence, owner approvals, Groq brain.

Message flow for every agent:
  1. owner says `approve A3` / `deny A3`  -> the agent that owns approval A3 acts
  2. addressed message                    -> deterministic command if it matches,
                                             else Groq turns free text into a command
                                             or answers from the shared store
  3. anything else                        -> ignored
Only the owner (a human, never another agent) can decide an approval, and the
store flips an approval pending -> approved exactly once.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .. import llm
from ..config import AGENTS, settings
from ..protocol import parse_decision
from ..store import Store, get_store
from ..switch_client import Message, SwitchAgent

log = logging.getLogger("shopops.agent")


class ShopAgent:
    name: str = "agent"
    role: str = "a ShopOps agent"
    system_prompt: str = "You are a helpful back-office agent for a small online shop."
    approval_kinds: tuple[str, ...] = ()
    help_text: str = ""

    def __init__(self, switch_url: str | None = None, agent_id: str | None = None,
                 api_key: str | None = None, store: Store | None = None):
        aid, key = settings.agent_credentials(self.name)
        self.switch = SwitchAgent(switch_url or settings.switch_url, agent_id or aid,
                                  api_key or key, self.name, self._on_message,
                                  only_addressed=False)
        if not self.switch.agent_id or not self.switch.api_key:
            raise SystemExit(f"{self.name}: no id/key; run scripts/setup_switch.py first")
        self.store = store or get_store()

    # ----------------------------------------------------------- overridables
    async def command(self, msg: Message, text: str) -> str | None:
        """Deterministic commands. Return reply text, or None when not recognised."""
        return None

    async def execute_approval(self, approval: dict) -> str:
        """Do the thing an approved request asked for. Return the room message."""
        return f"Approval A{approval['id']} approved."

    def deny_text(self, approval: dict) -> str:
        return f"Understood, A{approval['id']} denied. Nothing was done."

    def context(self) -> str:
        """Facts from the shared store for the LLM to ground its answers."""
        return ""

    # ------------------------------------------------------------------ core
    def is_owner(self, msg: Message) -> bool:
        return (msg.sender_name not in AGENTS and msg.sender_kind != "platform"
                and msg.sender_kind != "agent")

    async def handle(self, msg: Message) -> str | list[str] | None:
        text = strip_mentions(msg.body)
        decision = parse_decision(text)
        if decision and self.is_owner(msg):
            return await self._owner_decision(msg, *decision)
        if not msg.addressed:
            return None
        if text.lower() in {"ping", "hello", "hi", "status"}:
            return f"✅ {self.name} online: {self.role}."
        reply = await self.command(msg, text)
        if reply is not None:
            return reply
        if not self.is_owner(msg):
            return None  # never start LLM chatter between agents
        return await self._free_text(msg, text)

    async def _owner_decision(self, msg: Message, approve: bool, aid: int | None) -> str | None:
        mine = [a for a in self.store.pending() if a["kind"] in self.approval_kinds]
        if aid is None:
            if len(mine) != 1:
                return (f"{len(mine)} approvals are waiting, which one? Say `approve A<number>`."
                        if mine else None)
            aid = mine[0]["id"]
        a = self.store.approval(aid)
        if not a or a["kind"] not in self.approval_kinds:
            return None  # another agent's approval (or unknown): stay quiet
        done = self.store.decide(aid, approve, msg.sender_name or "owner")
        if not done:
            return f"A{aid} is already {a['status']}, nothing to do."
        self.store.log(self.name, f"A{aid} {done['status']} by {msg.sender_name}")
        if not approve:
            self.store.finish_approval(aid, "denied", "owner denied")
            return self.deny_text(done)
        return await self.execute_approval(done)

    async def _free_text(self, msg: Message, text: str) -> str:
        if self.help_text:
            cmd = await llm.chat(
                f"{self.system_prompt}\nTranslate the user's request into exactly ONE of these "
                f"commands, or reply NONE if it is not one of them. Output only the command.\n"
                f"{self.help_text}\nShop facts:\n{self.context()}", text, max_tokens=60)
            if cmd and cmd.strip().upper() != "NONE" and "\n" not in cmd.strip():
                cmd = cmd.strip().strip("`")
                log.info("[%s] LLM mapped %r -> %r", self.name, text[:60], cmd)
                reply = await self.command(msg, cmd)
                if reply is not None:
                    return reply
        answer = await llm.chat(
            f"{self.system_prompt}\nAnswer only from these shop facts; if you can't, say what "
            f"command to use.\n{self.help_text}\nShop facts:\n{self.context()}",
            f"{msg.sender_name}: {text}")
        return answer or f"I didn't catch that. I can do:\n{self.help_text or self.role}"

    async def _on_message(self, sw: SwitchAgent, msg: Message) -> None:
        log.info("[%s] <- %s: %s", self.name, msg.sender_name, msg.body[:120])
        reply = await self.handle(msg)
        # A list is several chat messages in order: an @-mention only reaches
        # another agent when it opens its own message.
        for part in ([reply] if isinstance(reply, str) else reply or []):
            if part:
                await sw.post(msg.room_id, part)

    async def post(self, room_id: str, text: str) -> None:
        await self.switch.post(room_id, text)

    async def run(self) -> None:
        await self.switch.run_forever()


_MENTION = re.compile(r"@?[a-z0-9][a-z0-9._-]*-agent\b[:,]?\s*", re.I)


def strip_mentions(body: str) -> str:
    return _MENTION.sub("", body).strip()


async def run_agents(*agents: ShopAgent) -> None:
    await asyncio.gather(*(a.run() for a in agents))
