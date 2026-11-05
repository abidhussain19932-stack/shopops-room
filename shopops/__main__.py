"""Run the agents:  python -m shopops [agent-name ...]   (default: all three)."""
import asyncio
import logging
import sys

from .agents import ALL
from .agents.base import run_agents
from .config import settings


async def amain(names: list[str]) -> None:
    from . import persist
    from .store import get_store

    # Restore the shop store from the Postgres snapshot (no-op without DATABASE_URL).
    await persist.restore(settings.store_path)
    store = get_store()
    snap = asyncio.create_task(persist.snapshot_loop(store.conn))
    try:
        await run_agents(*(ALL[n]() for n in names))
    finally:
        snap.cancel()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    names = sys.argv[1:] or list(ALL)
    unknown = [n for n in names if n not in ALL]
    if unknown:
        raise SystemExit(f"unknown agent(s): {unknown}; choose from {list(ALL)}")
    asyncio.run(amain(names))


if __name__ == "__main__":
    main()
