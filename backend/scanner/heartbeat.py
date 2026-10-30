"""Worker heartbeat, leader election and structured-log mirroring to Redis."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend import __version__
from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.ingestion.persistence import PersistenceWriter
from backend.models import WorkerHeartbeat
from backend.services import keys
from backend.services.redis_client import RedisLock, get_redis

log = get_logger(__name__)


class RedisLogHandler(logging.Handler):
    """Mirror WARNING+ (and INFO scanner events) into a capped Redis list for the dashboard."""

    def __init__(self, loop: asyncio.AbstractEventLoop, max_items: int = 500) -> None:
        super().__init__(level=logging.INFO)
        self.loop = loop
        self.max_items = max_items
        self._queue: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            try:
                data = json.loads(msg)
            except ValueError:
                data = {"event": msg}
            data.setdefault("level", record.levelname.lower())
            data.setdefault("timestamp", datetime.now(UTC).isoformat())
            data.setdefault("logger", record.name)
            self._queue.append(json.dumps(data, default=str))
            if len(self._queue) > 200:
                self._queue = self._queue[-200:]
        except Exception:  # noqa: BLE001
            pass

    async def drain(self) -> None:
        if not self._queue:
            return
        items, self._queue = self._queue, []
        try:
            r = get_redis()
            pipe = r.pipeline()
            pipe.rpush(keys.SCANNER_LOGS, *items)
            pipe.ltrim(keys.SCANNER_LOGS, -self.max_items, -1)
            await pipe.execute()
        except Exception:  # noqa: BLE001
            pass


class Heartbeat:
    def __init__(self, stats_fn: Callable[[], dict[str, Any]], role: str = "scanner") -> None:
        settings = get_settings().scanner
        self.role = role
        self.worker_id = f"{role}-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.hostname = socket.gethostname()
        self.started_at = datetime.now(UTC)
        self.interval = settings.heartbeat_interval_seconds
        self.lock = RedisLock(keys.SCANNER_LEADER_LOCK, self.worker_id, settings.leader_lock_ttl_seconds)
        self.is_leader = False
        self.status = "starting"
        self.stats_fn = stats_fn
        self._stop = asyncio.Event()
        self.log_handler: RedisLogHandler | None = None

    async def acquire_leadership(self, wait_seconds: float = 0) -> bool:
        """Try to become the single active scanner. On redeploys the old instance releases on SIGTERM."""
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            try:
                if await self.lock.acquire():
                    self.is_leader = True
                    log.info("heartbeat.leader_acquired", worker_id=self.worker_id)
                    return True
                holder = await self.lock.holder()
            except Exception as exc:  # noqa: BLE001
                log.warning("heartbeat.lock_error", error=str(exc)[:200])
                holder = None
            if asyncio.get_running_loop().time() >= deadline:
                log.warning("heartbeat.leader_held_by_other", holder=holder)
                return False
            await asyncio.sleep(2)

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.beat()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass

    async def beat(self) -> None:
        now = datetime.now(UTC)
        stats = {}
        try:
            stats = self.stats_fn()
        except Exception as exc:  # noqa: BLE001
            stats = {"stats_error": str(exc)[:100]}
        if self.is_leader:
            try:
                if not await self.lock.refresh():
                    # Lost the lock (e.g. Redis restarted): try to re-acquire, otherwise step down.
                    self.is_leader = await self.lock.acquire()
                    if not self.is_leader:
                        log.error("heartbeat.leadership_lost")
            except Exception as exc:  # noqa: BLE001
                log.warning("heartbeat.refresh_failed", error=str(exc)[:200])
        try:
            r = get_redis()
            await r.hset(
                keys.SCANNER_HEARTBEAT,
                mapping={
                    "worker_id": self.worker_id,
                    "ts": now.isoformat(),
                    "status": self.status,
                    "is_leader": "1" if self.is_leader else "0",
                    "started_at": self.started_at.isoformat(),
                    "stats": json.dumps(stats, default=str),
                },
            )
            await r.expire(keys.SCANNER_HEARTBEAT, max(60, self.interval * 6))
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat.redis_failed", error=str(exc)[:200])
        try:
            async with session_scope() as session:
                stmt = pg_insert(WorkerHeartbeat).values(
                    worker_id=self.worker_id,
                    role=self.role,
                    hostname=self.hostname,
                    version=__version__,
                    started_at=self.started_at,
                    last_heartbeat_at=now,
                    is_leader=self.is_leader,
                    status=self.status,
                    stats=json.loads(json.dumps(stats, default=str)),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[WorkerHeartbeat.worker_id],
                    set_={"last_heartbeat_at": now, "is_leader": self.is_leader, "status": self.status, "stats": stmt.excluded.stats},
                )
                await session.execute(stmt)
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat.db_failed", error=str(exc)[:200])
        try:
            await PersistenceWriter.persist_provider_usage()
        except Exception as exc:  # noqa: BLE001
            log.debug("heartbeat.provider_usage_failed", error=str(exc)[:200])
        if self.log_handler:
            await self.log_handler.drain()

    async def stop(self) -> None:
        self._stop.set()
        self.status = "stopping"
        await self.beat()
        if self.is_leader:
            await self.lock.release()
            self.is_leader = False
