"""Run the agents:  python -m shopops [agent-name ...]   (default: all three)."""
import asyncio
import logging
import sys

from .agents import ALL
from .agents.base import run_agents


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    names = sys.argv[1:] or list(ALL)
    unknown = [n for n in names if n not in ALL]
    if unknown:
        raise SystemExit(f"unknown agent(s): {unknown}; choose from {list(ALL)}")
    asyncio.run(run_agents(*(ALL[n]() for n in names)))


if __name__ == "__main__":
    main()
