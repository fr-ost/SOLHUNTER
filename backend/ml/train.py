"""CLI: train and evaluate models from labelled training samples.

  python -m backend.ml.train                # train, store versions, activate if better than current
  python -m backend.ml.train --activate     # force activation of the new versions
  python -m backend.ml.train --label-only   # just run the outcome labeller
"""

from __future__ import annotations

import argparse
import asyncio
import json

from backend.config.logging import configure_logging, get_logger
from backend.config.settings import get_settings
from backend.database.engine import dispose_engine, session_scope
from backend.ml.labeler import label_pending_snapshots
from backend.ml.registry import train_and_store
from backend.services.redis_client import close_redis

log = get_logger(__name__)


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, "console", service="ml-train")
    labelled = 0
    async with session_scope() as session:
        while True:
            n = await label_pending_snapshots(session, limit=500)
            labelled += n
            if n < 500:
                break
    print(f"labelled {labelled} new snapshots")
    if args.label_only:
        return
    async with session_scope() as session:
        summary = await train_and_store(session, activate_if_better=not args.no_activate, force_activate=args.activate)
    print(json.dumps(summary, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="SolHunter model training")
    parser.add_argument("--activate", action="store_true", help="activate the new versions regardless of metrics")
    parser.add_argument("--no-activate", action="store_true", help="store versions but never activate")
    parser.add_argument("--label-only", action="store_true", help="only run the outcome labeller")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    finally:
        asyncio.run(_cleanup())


async def _cleanup() -> None:
    await dispose_engine()
    await close_redis()


if __name__ == "__main__":
    main()
