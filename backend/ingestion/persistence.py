"""Batched, idempotent persistence of ingestion events and derived rows.

All inserts use ``INSERT ... ON CONFLICT DO NOTHING`` so replays after a restart
or duplicate deliveries never create duplicate rows. Failed batches are retried
and finally parked in a Redis dead-letter list for inspection.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.ingestion.events import MigrationEvent, NewTokenEvent, TradeEvent
from backend.models import (
    HolderSnapshot,
    ProviderStatus,
    ProviderUsageDaily,
    RawEvent,
    ScannerCheckpoint,
    SmartMoneyEvent,
    Token,
    TokenLaunch,
    TokenPool,
    TokenRiskEvent,
    TokenTransaction,
    WalletTransaction,
    WhaleEvent,
)
from backend.providers.base import ProviderTracker
from backend.services import keys
from backend.services.redis_client import get_redis

if TYPE_CHECKING:
    from backend.scanner.state import TokenState

log = get_logger(__name__)

DEAD_LETTER_KEY = f"{keys.PREFIX}:persistence:dead"


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _homogenize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Multi-row INSERT needs identical key sets; fill missing columns with None."""
    columns: set[str] = set()
    for row in rows:
        columns.update(row.keys())
    return [{c: row.get(c) for c in columns} for row in rows]


class PersistenceWriter:
    def __init__(self, flush_interval: float | None = None, batch_size: int | None = None) -> None:
        s = get_settings().scanner
        self.flush_interval = flush_interval or s.flush_interval_seconds
        self.batch_size = batch_size or s.flush_batch_size
        self._buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._token_upserts: dict[str, dict[str, Any]] = {}
        self._status_updates: dict[str, dict[str, Any]] = {}
        self._stats: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._flush_now = asyncio.Event()
        self.flushes = 0
        self.rows_written = 0
        self.failures = 0
        self.last_flush_at: datetime | None = None

    # ------------------------------------------------------------------ enqueue API
    def add_token(self, state: TokenState) -> None:
        self._token_upserts[state.mint] = {
            "mint": state.mint,
            "name": state.name,
            "symbol": state.symbol,
            "uri": state.uri,
            "creator_wallet": state.creator,
            "launch_time": state.launch_time,
            "bonding_curve_key": state.bonding_curve_key,
            "pool_kind": state.pool_kind,
            "status": state.status,
            "status_reason": state.status_reason,
            "migrated_at": state.migrated_at,
            "first_seen_at": state.discovered_at,
            "last_activity_at": state.last_trade_at or state.last_event_at,
            "tracking_until": state.tracking_until,
            "total_supply": get_settings().scanner.pump_total_supply,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }

    def update_token_status(self, mint: str, status: str, reason: str | None = None, **extra: Any) -> None:
        self._status_updates[mint] = {"status": status, "status_reason": (reason or "")[:500] or None, "updated_at": datetime.now(UTC), **extra}

    def add_launch(self, ev: NewTokenEvent) -> None:
        self._buffers["launches"].append(
            {
                "mint": ev.mint,
                "signature": ev.signature,
                "creator_wallet": ev.creator,
                "launch_time": ev.block_time,
                "initial_buy_tokens": ev.initial_buy_tokens,
                "initial_buy_sol": ev.initial_buy_sol,
                "v_sol_in_curve": ev.v_sol_in_curve,
                "v_tokens_in_curve": ev.v_tokens_in_curve,
                "market_cap_sol": ev.market_cap_sol,
                "source": ev.source,
                "raw": _json_safe(ev.raw),
            }
        )

    def add_trade(self, ev: TradeEvent, store_raw: bool = False) -> None:
        self._buffers["trades"].append(
            {
                "mint": ev.mint,
                "signature": ev.signature,
                "wallet": ev.trader,
                "tx_type": ev.side,
                "sol_amount": ev.sol_amount,
                "token_amount": ev.token_amount,
                "price_sol": ev.price_sol,
                "block_time": ev.block_time,
                "pool_kind": ev.pool,
                "v_sol_in_curve": ev.v_sol_in_curve,
                "v_tokens_in_curve": ev.v_tokens_in_curve,
                "market_cap_sol": ev.market_cap_sol,
                "new_token_balance": ev.new_token_balance,
                "source": ev.source,
                "raw": _json_safe(ev.raw) if store_raw else None,
            }
        )
        # Note: wallet-level history is served from token_transactions (indexed by wallet); wallet_transactions
        # is reserved for transfer data from enhanced transaction parsing, so trades are not duplicated.

    def add_migration(self, ev: MigrationEvent) -> None:
        self._buffers["trades"].append(
            {
                "mint": ev.mint,
                "signature": ev.signature,
                "wallet": ev.pool_address or "migration",
                "tx_type": "migrate",
                "block_time": ev.block_time,
                "pool_kind": ev.pool,
                "source": ev.source,
                "raw": _json_safe(ev.raw),
            }
        )

    def add_raw(self, source: str, event_type: str, dedupe_key: str, mint: str | None, payload: dict[str, Any]) -> None:
        self._buffers["raw"].append(
            {"source": source, "event_type": event_type, "dedupe_key": dedupe_key[:256], "mint": mint, "payload": _json_safe(payload), "received_at": datetime.now(UTC)}
        )

    def add_holder_snapshot(self, row: dict[str, Any]) -> None:
        self._buffers["holders"].append(row)

    def add_risk_event(self, row: dict[str, Any]) -> None:
        self._buffers["risk_events"].append(row)

    def add_whale_event(self, row: dict[str, Any]) -> None:
        self._buffers["whale_events"].append(row)

    def add_smart_money_event(self, row: dict[str, Any]) -> None:
        self._buffers["smart_money_events"].append(row)

    def add_pool(self, row: dict[str, Any]) -> None:
        self._buffers["pools"].append(row)

    def incr_stat(self, name: str, n: int = 1) -> None:
        self._stats[name] += n

    @property
    def pending(self) -> int:
        return sum(len(b) for b in self._buffers.values()) + len(self._token_upserts) + len(self._status_updates)

    def request_flush(self) -> None:
        self._flush_now.set()

    # ------------------------------------------------------------------ loop
    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._flush_now.wait(), timeout=self.flush_interval)
            except TimeoutError:
                pass
            self._flush_now.clear()
            if self.pending >= self.batch_size or self.pending > 0:
                await self.flush()
            elif self._stats:
                await self._flush_stats()
        await self.flush()

    async def stop(self) -> None:
        self._stop.set()
        self._flush_now.set()

    async def flush(self) -> None:
        async with self._lock:
            buffers = {k: v for k, v in self._buffers.items() if v}
            token_upserts = self._token_upserts
            status_updates = self._status_updates
            self._buffers = defaultdict(list)
            self._token_upserts = {}
            self._status_updates = {}
        if not buffers and not token_upserts and not status_updates:
            await self._flush_stats()
            return
        for attempt in range(3):
            try:
                written = await self._write(buffers, token_upserts, status_updates)
                self.rows_written += written
                self.flushes += 1
                self.last_flush_at = datetime.now(UTC)
                await self._flush_stats()
                return
            except Exception as exc:  # noqa: BLE001
                self.failures += 1
                log.warning("persistence.flush_failed", attempt=attempt, error=f"{type(exc).__name__}: {exc}"[:300])
                await asyncio.sleep(1.5 * (attempt + 1))
        await self._dead_letter(buffers, token_upserts, status_updates)

    async def _dead_letter(self, buffers: dict[str, list[dict[str, Any]]], tokens: dict[str, Any], statuses: dict[str, Any]) -> None:
        try:
            payload = json.dumps({"buffers": buffers, "tokens": tokens, "statuses": statuses, "at": datetime.now(UTC).isoformat()}, default=str)
            r = get_redis()
            await r.rpush(DEAD_LETTER_KEY, payload)
            await r.ltrim(DEAD_LETTER_KEY, -50, -1)
            log.error("persistence.dead_lettered", rows=sum(len(v) for v in buffers.values()))
        except Exception as exc:  # noqa: BLE001
            log.error("persistence.dead_letter_failed", error=str(exc)[:200])

    async def _write(self, buffers: dict[str, list[dict[str, Any]]], token_upserts: dict[str, Any], status_updates: dict[str, Any]) -> int:
        written = 0
        async with session_scope() as session:
            if token_upserts:
                stmt = pg_insert(Token).values(list(token_upserts.values()))
                stmt = stmt.on_conflict_do_update(
                    index_elements=[Token.mint],
                    set_={
                        "name": stmt.excluded.name,
                        "symbol": stmt.excluded.symbol,
                        "uri": stmt.excluded.uri,
                        "creator_wallet": stmt.excluded.creator_wallet,
                        "bonding_curve_key": stmt.excluded.bonding_curve_key,
                        "pool_kind": stmt.excluded.pool_kind,
                        "status": stmt.excluded.status,
                        "status_reason": stmt.excluded.status_reason,
                        "migrated_at": stmt.excluded.migrated_at,
                        "last_activity_at": stmt.excluded.last_activity_at,
                        "tracking_until": stmt.excluded.tracking_until,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                await session.execute(stmt)
                written += len(token_upserts)
            for mint, values in status_updates.items():
                if mint in token_upserts:
                    continue
                from sqlalchemy import update

                await session.execute(update(Token).where(Token.mint == mint).values(**values))
                written += 1
            mapping = {
                "launches": (TokenLaunch, ["signature"]),
                "trades": (TokenTransaction, ["signature", "mint", "wallet", "tx_type"]),
                "wallet_tx": (WalletTransaction, ["signature", "wallet", "mint", "direction"]),
                "raw": (RawEvent, ["dedupe_key"]),
                "holders": (HolderSnapshot, None),
                "risk_events": (TokenRiskEvent, ["dedupe_key"]),
                "whale_events": (WhaleEvent, ["mint", "wallet", "signature", "event_type"]),
                "smart_money_events": (SmartMoneyEvent, ["mint", "wallet", "signature", "direction"]),
                "pools": (TokenPool, ["pool_address"]),
            }
            for name, rows in buffers.items():
                model, conflict = mapping[name]
                for i in range(0, len(rows), 500):
                    chunk = _homogenize(rows[i : i + 500])
                    stmt = pg_insert(model).values(chunk)
                    if conflict:
                        stmt = stmt.on_conflict_do_nothing(index_elements=conflict)
                    await session.execute(stmt)
                    written += len(chunk)
        return written

    async def _flush_stats(self) -> None:
        if not self._stats:
            return
        stats, self._stats = self._stats, defaultdict(int)
        try:
            r = get_redis()
            pipe = r.pipeline()
            key = keys.daily_stats_key()
            for name, n in stats.items():
                pipe.hincrby(key, name, n)
            pipe.expire(key, 3 * 86400)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            log.debug("persistence.stats_failed", error=str(exc)[:100])
            for name, n in stats.items():
                self._stats[name] += n

    # ------------------------------------------------------------------ checkpoints & provider usage
    @staticmethod
    async def save_checkpoint(key: str, value: dict[str, Any]) -> None:
        async with session_scope() as session:
            stmt = pg_insert(ScannerCheckpoint).values(key=key, value=_json_safe(value), updated_at=datetime.now(UTC))
            stmt = stmt.on_conflict_do_update(index_elements=[ScannerCheckpoint.key], set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at})
            await session.execute(stmt)

    @staticmethod
    async def load_checkpoint(key: str) -> dict[str, Any] | None:
        async with session_scope() as session:
            row = await session.get(ScannerCheckpoint, key)
            return dict(row.value) if row else None

    @staticmethod
    async def persist_provider_usage() -> None:
        """Flush provider trackers to provider_status + provider_usage_daily."""
        now = datetime.now(UTC)
        trackers = ProviderTracker.all()
        if not trackers:
            return
        async with session_scope() as session:
            for name, tracker in trackers.items():
                delta = tracker.take_delta()
                snap = tracker.snapshot()
                stmt = pg_insert(ProviderStatus).values(
                    provider=name,
                    status=tracker.status,
                    last_success_at=tracker.counters.last_success_at,
                    last_error_at=tracker.counters.last_error_at,
                    last_error=tracker.counters.last_error,
                    reconnect_count=tracker.counters.reconnects,
                    details=_json_safe(snap["details"]),
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ProviderStatus.provider],
                    set_={
                        "status": stmt.excluded.status,
                        "last_success_at": stmt.excluded.last_success_at,
                        "last_error_at": stmt.excluded.last_error_at,
                        "last_error": stmt.excluded.last_error,
                        "reconnect_count": stmt.excluded.reconnect_count,
                        "details": stmt.excluded.details,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                await session.execute(stmt)
                if delta.requests or delta.credits:
                    ustmt = pg_insert(ProviderUsageDaily).values(
                        provider=name,
                        day=now.date(),
                        requests=delta.requests,
                        errors=delta.errors,
                        rate_limited=delta.rate_limited,
                        credits_used=delta.credits,
                        estimated_cost_usd=0.0,
                        updated_at=now,
                    )
                    ustmt = ustmt.on_conflict_do_update(
                        index_elements=[ProviderUsageDaily.provider, ProviderUsageDaily.day],
                        set_={
                            "requests": ProviderUsageDaily.requests + ustmt.excluded.requests,
                            "errors": ProviderUsageDaily.errors + ustmt.excluded.errors,
                            "rate_limited": ProviderUsageDaily.rate_limited + ustmt.excluded.rate_limited,
                            "credits_used": ProviderUsageDaily.credits_used + ustmt.excluded.credits_used,
                            "updated_at": ustmt.excluded.updated_at,
                        },
                    )
                    await session.execute(ustmt)
                await tracker.publish()
