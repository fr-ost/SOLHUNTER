"""GemFinder scanner worker: a persistent process (never a cron job).

Responsibilities: real-time discovery (PumpPortal), tracking, snapshots, evaluation
(filters/whale engine/ML/GPT - attached as the evaluator), alerts, heartbeat,
leader election (single active scanner across redeploys) and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import resource
import signal
from datetime import UTC, datetime
from typing import Any

from backend import __version__
from backend.config.logging import configure_logging, get_logger
from backend.config.settings import get_settings
from backend.database.engine import dispose_engine, get_engine, session_scope
from backend.database.migrate import run_migrations_with_lock, wait_for_schema
from backend.ingestion.dedupe import Deduplicator
from backend.ingestion.persistence import PersistenceWriter
from backend.providers.dexscreener import DexScreenerClient
from backend.providers.helius import HeliusClient
from backend.providers.pumpportal import PumpPortalClient
from backend.providers.sol_price import SolPriceService
from backend.scanner.health import HealthServer
from backend.scanner.heartbeat import Heartbeat, RedisLogHandler
from backend.scanner.snapshots import SnapshotScheduler
from backend.scanner.tracker import TokenTracker
from backend.services import retention
from backend.services.redis_client import check_redis, close_redis
from backend.services.settings_service import apply_overrides_to_settings

log = get_logger(__name__)


class ScannerWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.persistence = PersistenceWriter()
        self.dex = DexScreenerClient()
        self.helius = HeliusClient()
        self.sol_price = SolPriceService(self.dex)
        self.pumpportal: PumpPortalClient | None = None
        self.tracker: TokenTracker | None = None
        self.snapshots = SnapshotScheduler(self.dex, self.sol_price, self.persistence)
        self.heartbeat = Heartbeat(self.stats)
        self.health: HealthServer | None = None
        self.evaluator: Any | None = None  # attached in build_evaluator()
        self._tasks: list[asyncio.Task[Any]] = []
        self._stop = asyncio.Event()
        self.started_at = datetime.now(UTC)
        self.loop_errors = 0

    # ------------------------------------------------------------------ status
    def stats(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": __version__,
            "uptime_seconds": int((datetime.now(UTC) - self.started_at).total_seconds()),
            "memory_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
            "snapshots_taken": self.snapshots.snapshots_taken,
            "persistence": {"pending": self.persistence.pending, "rows_written": self.persistence.rows_written, "failures": self.persistence.failures, "last_flush_at": self.persistence.last_flush_at.isoformat() if self.persistence.last_flush_at else None},
            "loop_errors": self.loop_errors,
        }
        if self.tracker:
            data["tracker"] = self.tracker.stats()
        if self.pumpportal:
            data["pumpportal"] = {"connected": self.pumpportal.connected.is_set(), "messages": self.pumpportal.messages_received, "subscriptions": self.pumpportal.token_subscription_count}
        if self.evaluator is not None:
            data["evaluator"] = self.evaluator.stats()
        return data

    def health_status(self) -> dict[str, Any]:
        healthy = not self._stop.is_set() and (self.heartbeat.is_leader or self.heartbeat.status == "standby")
        return {"healthy": healthy, "status": self.heartbeat.status, "is_leader": self.heartbeat.is_leader, "worker_id": self.heartbeat.worker_id, "stats": self.stats()}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        s = self.settings
        configure_logging(s.log_level, s.log_format, service="scanner")
        for problem in s.validate_for_production():
            log.warning("startup.config_warning", problem=problem)
        log.info("scanner.starting", version=__version__, env=s.app_env)

        self.heartbeat.log_handler = RedisLogHandler(asyncio.get_running_loop())
        logging.getLogger().addHandler(self.heartbeat.log_handler)

        if s.scanner.http_health_enabled:
            self.health = HealthServer(s.scanner.http_health_port, self.health_status)
            await self.health.start()

        engine = get_engine()
        if s.run_migrations_on_start:
            await run_migrations_with_lock(engine, s.sqlalchemy_database_url)
        elif not await wait_for_schema(engine, s.migration_wait_timeout_seconds):
            raise RuntimeError("database schema is not at head revision; start the API with RUN_MIGRATIONS_ON_START=true first")
        ok, err = await check_redis()
        if not ok:
            raise RuntimeError(f"redis unavailable: {err}")

        async with session_scope() as session:
            await apply_overrides_to_settings(session, s)

        # Single active scanner: wait briefly for a previous deployment to release the lock.
        self.heartbeat.status = "standby"
        self._tasks.append(asyncio.create_task(self.heartbeat.run(), name="heartbeat"))
        acquired = await self.heartbeat.acquire_leadership(wait_seconds=s.scanner.leader_lock_ttl_seconds * 3)
        while not acquired and not self._stop.is_set():
            log.info("scanner.standby", reason="another scanner instance holds the leader lock")
            acquired = await self.heartbeat.acquire_leadership(wait_seconds=30)
        if self._stop.is_set():
            return
        self.heartbeat.status = "running"

        if s.pumpportal_enabled:
            self.pumpportal = PumpPortalClient(self._on_pumpportal_message)
        self.tracker = TokenTracker(self.persistence, self.pumpportal, Deduplicator())
        await self.tracker.restore_checkpoint()
        self.build_evaluator()

        self._tasks.append(asyncio.create_task(self.persistence.run(), name="persistence"))
        if self.pumpportal:
            self._tasks.append(asyncio.create_task(self.pumpportal.run(), name="pumpportal"))
        self._tasks.append(asyncio.create_task(self._snapshot_loop(), name="snapshots"))
        self._tasks.append(asyncio.create_task(self._maintenance_loop(), name="maintenance"))
        if self.evaluator is not None:
            self._tasks.extend(self.evaluator.background_tasks())
        log.info("scanner.started", worker_id=self.heartbeat.worker_id)

    def build_evaluator(self) -> None:
        """Attach the evaluation pipeline (filters -> whale engine -> ML -> ranking -> GPT -> alerts)."""
        try:
            from backend.scanner.evaluator import Evaluator

            assert self.tracker is not None
            self.evaluator = Evaluator(self)
            self.snapshots.on_snapshot = self.evaluator.on_snapshot
        except ImportError:  # evaluator not available in this build
            self.evaluator = None

    async def _on_pumpportal_message(self, raw: dict[str, Any]) -> None:
        if self.tracker is not None:
            await self.tracker.handle_raw(raw)

    async def _snapshot_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.tracker:
                    await self.snapshots.tick(list(self.tracker.states.values()))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.loop_errors += 1
                log.exception("scanner.snapshot_loop_error")
            await self._sleep(5)

    async def _maintenance_loop(self) -> None:
        s = self.settings.scanner
        last_retention = datetime.now(UTC)
        last_settings = datetime.now(UTC)
        last_checkpoint = datetime.now(UTC)
        while not self._stop.is_set():
            try:
                now = datetime.now(UTC)
                if self.tracker:
                    await self.tracker.process_track_requests()
                    await self.tracker.expire()
                    if (now - last_checkpoint).total_seconds() > 30:
                        await self.tracker.persist_checkpoint()
                        last_checkpoint = now
                if (now - last_settings).total_seconds() > 60:
                    async with session_scope() as session:
                        await apply_overrides_to_settings(session, self.settings)
                    last_settings = now
                if (now - last_retention).total_seconds() > s.retention_cleanup_interval_minutes * 60:
                    await retention.run_retention()
                    last_retention = now
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.loop_errors += 1
                log.exception("scanner.maintenance_error")
            await self._sleep(10)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    def request_stop(self) -> None:
        if not self._stop.is_set():
            log.info("scanner.stop_requested")
            self._stop.set()

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except NotImplementedError:  # pragma: no cover - Windows
                pass
        try:
            await self.start()
            await self._stop.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        log.info("scanner.shutting_down")
        self._stop.set()
        if self.pumpportal:
            await self.pumpportal.stop()
        if self.evaluator is not None:
            try:
                await self.evaluator.shutdown()
            except Exception:  # noqa: BLE001
                log.exception("scanner.evaluator_shutdown_error")
        for task in self._tasks:
            if task.get_name() not in {"heartbeat", "persistence"}:
                task.cancel()
        await asyncio.gather(*[t for t in self._tasks if t.get_name() not in {"heartbeat", "persistence"}], return_exceptions=True)
        if self.tracker:
            try:
                await self.tracker.persist_checkpoint()
            except Exception:  # noqa: BLE001
                log.warning("scanner.checkpoint_failed")
        await self.persistence.stop()
        for task in self._tasks:
            if task.get_name() == "persistence":
                await asyncio.gather(task, return_exceptions=True)
        await self.heartbeat.stop()
        for task in self._tasks:
            if task.get_name() == "heartbeat":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if self.health:
            await self.health.stop()
        await self.dex.aclose()
        await self.helius.aclose()
        await dispose_engine()
        await close_redis()
        log.info("scanner.stopped")


def main() -> None:
    worker = ScannerWorker()
    asyncio.run(worker.run_forever())


if __name__ == "__main__":
    main()
