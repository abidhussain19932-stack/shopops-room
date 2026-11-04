"""Groq (OpenAI-compatible) chat helper shared by the agents."""
from __future__ import annotations

import logging

from .config import settings

log = logging.getLogger("shopops.llm")

_client = None


def client():
    global _client
    if _client is None and settings.llm_api_key:
        from openai import AsyncOpenAI

        _client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    return _client


async def chat(system: str, user: str, *, max_tokens: int = 400) -> str | None:
    """One short completion, or None when no LLM key is set or the call fails.

    Callers always have a deterministic fallback, so a Groq rate limit never
    stops the shop workflow itself.
    """
    c = client()
    if c is None:
        return None
    try:
        r = await c.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return (r.choices[0].message.content or "").strip() or None
    except Exception as exc:
        log.warning("LLM call failed: %r", exc)
        return None
