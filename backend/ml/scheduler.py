"""Background ML tasks for the scanner: outcome labelling, periodic retraining, predictor reload."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.ml.labeler import label_pending_snapshots
from backend.ml.registry import load_predictor, train_and_store
from backend.services import keys
from backend.services.redis_client import RedisLock

log = get_logger(__name__)


class MLScheduler:
    def __init__(self, evaluator: Any) -> None:
        self.evaluator = evaluator
        self._stop = asyncio.Event()
        self.last_label_at: datetime | None = None
        self.last_train_at: datetime | None = None
        self.last_train_summary: dict[str, Any] | None = None
        self.labelled_total = 0

    def stats(self) -> dict[str, Any]:
        return {"last_label_at": self.last_label_at.isoformat() if self.last_label_at else None, "last_train_at": self.last_train_at.isoformat() if self.last_train_at else None, "labelled_total": self.labelled_total, "last_train": {k: v for k, v in (self.last_train_summary or {}).items() if k != "models"}}

    async def stop(self) -> None:
        self._stop.set()

    async def load_predictor(self) -> None:
        async with session_scope() as session:
            self.evaluator.predictor = await load_predictor(session)

    async def run(self) -> None:
        cfg = get_settings().ml
        await self._safe(self.load_predictor)
        if cfg.train_on_start:
            await self._safe(self.train_once)
        while not self._stop.is_set():
            await self._safe(self.label_once)
            if cfg.auto_train_enabled and (self.last_train_at is None or datetime.now(UTC) - self.last_train_at > timedelta(hours=cfg.retrain_interval_hours)):
                await self._safe(self.train_once)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except TimeoutError:
                pass

    async def _safe(self, fn: Any) -> None:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("ml_scheduler.error", step=getattr(fn, "__name__", "step"))

    async def label_once(self) -> None:
        async with session_scope() as session:
            n = await label_pending_snapshots(session, limit=300)
        self.labelled_total += n
        self.last_label_at = datetime.now(UTC)

    async def train_once(self) -> None:
        lock = RedisLock(f"{keys.PREFIX}:ml:train", "scanner", ttl_seconds=3600)
        if not await lock.acquire():
            return
        try:
            async with session_scope() as session:
                summary = await train_and_store(session, activate_if_better=True)
            self.last_train_summary = summary
            self.last_train_at = datetime.now(UTC)
            if summary.get("trained"):
                log.info("ml_scheduler.trained", version=summary.get("version"), samples=summary.get("samples"))
                await self.load_predictor()
            else:
                log.info("ml_scheduler.skipped", reason=summary.get("reason"))
        finally:
            await lock.release()
