"""Tracker lifecycle, batch persistence idempotency, snapshots and worker start/stop (needs services)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from backend.database.engine import session_scope
from backend.ingestion.dedupe import Deduplicator
from backend.ingestion.persistence import PersistenceWriter
from backend.models import (
    RawEvent,
    Token,
    TokenLaunch,
    TokenSnapshot,
    TokenTransaction,
    WalletTransaction,
    WorkerHeartbeat,
)
from backend.scanner.snapshots import build_snapshot_row, due_snapshot_kind, persist_snapshot
from backend.scanner.tracker import TokenTracker
from backend.tests.conftest import requires_services
from backend.tests.fixtures import pumpportal as fx

pytestmark = requires_services


class FakePumpPortal:
    def __init__(self) -> None:
        self.subscribed: set[str] = set()
        self.unsubscribed: list[str] = []

    async def subscribe_token_trades(self, mints: list[str]) -> None:
        self.subscribed.update(mints)

    async def unsubscribe_token_trades(self, mints: list[str]) -> None:
        self.unsubscribed.extend(mints)
        self.subscribed.difference_update(mints)

    @property
    def token_subscription_count(self) -> int:
        return len(self.subscribed)


async def test_tracker_discovers_tracks_and_persists_idempotently() -> None:
    writer = PersistenceWriter()
    pp = FakePumpPortal()
    tracker = TokenTracker(writer, pp, Deduplicator())  # type: ignore[arg-type]
    await tracker.handle_raw(fx.create_event())
    await tracker.handle_raw(fx.create_event())  # duplicate create ignored
    assert len(tracker.states) == 1 and fx.MINT in pp.subscribed
    for i in range(5):
        await tracker.handle_raw(fx.trade_event(f"sig-{i}", fx.BUYER_A if i % 2 else fx.BUYER_B, "buy"))
    await tracker.handle_raw(fx.trade_event("sig-0", fx.BUYER_B, "buy"))  # duplicate trade
    await tracker.handle_raw(fx.trade_event("sig-unknown", fx.BUYER_A, "buy", mint="UnknownMint111111111111111111111111111111"))
    assert tracker.trades_processed == 5 and tracker.unknown_mint_trades == 1
    await tracker.handle_raw(fx.migration_event())
    assert tracker.states[fx.MINT].pool_kind == "pump-amm"
    await writer.flush()
    await writer.flush()  # second flush is a no-op
    async with session_scope() as s:
        assert await s.scalar(select(func.count()).select_from(Token)) == 1
        assert await s.scalar(select(func.count()).select_from(TokenLaunch)) == 1
        assert await s.scalar(select(func.count()).select_from(TokenTransaction)) == 6  # 5 buys + migration
        assert await s.scalar(select(func.count()).select_from(WalletTransaction)) == 0  # trades are not duplicated per wallet
        assert await s.scalar(select(func.count()).select_from(RawEvent)) == 2
        token = await s.get(Token, fx.MINT)
        assert token is not None and token.status == "tracking" and token.pool_kind == "pump-amm"
    # replaying the same trades after a restart must not duplicate rows
    writer2 = PersistenceWriter()
    tracker2 = TokenTracker(writer2, FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    await tracker2.handle_raw(fx.create_event())
    for i in range(5):
        await tracker2.handle_raw(fx.trade_event(f"sig-{i}", fx.BUYER_A if i % 2 else fx.BUYER_B, "buy"))
    await writer2.flush()
    async with session_scope() as s:
        assert await s.scalar(select(func.count()).select_from(TokenTransaction)) == 6


async def test_tracker_capacity_eviction_and_expiry(monkeypatch) -> None:
    from backend.config.settings import get_settings

    monkeypatch.setattr(get_settings().scanner, "max_tracked_tokens", 2)
    writer = PersistenceWriter()
    pp = FakePumpPortal()
    tracker = TokenTracker(writer, pp, Deduplicator())  # type: ignore[arg-type]
    for i in range(3):
        await tracker.handle_raw(fx.create_event(mint=f"Mint{i}" + "1" * 39, signature=f"c{i}"))
    assert len(tracker.states) == 2 and tracker.untracked_due_to_capacity == 1
    # age a token past 10 minutes so it becomes evictable
    old = tracker.states["Mint0" + "1" * 39]
    old.launch_time = datetime.now(UTC) - timedelta(minutes=12)
    await tracker.handle_raw(fx.create_event(mint="Mint9" + "1" * 39, signature="c9"))
    assert "Mint9" + "1" * 39 in tracker.states and "Mint0" + "1" * 39 not in tracker.states
    assert "Mint0" + "1" * 39 in pp.unsubscribed
    # expiry: a token with no trades after 5 minutes is marked dead
    dead = tracker.states["Mint1" + "1" * 39]
    dead.launch_time = datetime.now(UTC) - timedelta(minutes=6)
    dead.discovered_at = dead.launch_time
    dead.last_trade_at = dead.launch_time  # the dev's initial buy happened at launch
    retired = await tracker.expire()
    assert retired >= 1 and "Mint1" + "1" * 39 not in tracker.states
    await writer.flush()
    async with session_scope() as s:
        token = await s.get(Token, "Mint1" + "1" * 39)
        assert token is not None and token.status == "dead"


async def test_checkpoint_restore_retracks_recent_mints() -> None:
    writer = PersistenceWriter()
    tracker = TokenTracker(writer, FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    await tracker.handle_raw(fx.create_event())
    await tracker.persist_checkpoint()
    tracker2 = TokenTracker(PersistenceWriter(), FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    restored = await tracker2.restore_checkpoint()
    assert restored == 1 and fx.MINT in tracker2.states and tracker2.states[fx.MINT].flags.get("restored")


async def test_snapshot_schedule_and_persistence() -> None:
    writer = PersistenceWriter()
    tracker = TokenTracker(writer, FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    await tracker.handle_raw(fx.create_event())
    state = tracker.states[fx.MINT]
    now = datetime.now(UTC)
    assert due_snapshot_kind(state, now) is None  # too young
    state.launch_time = now - timedelta(seconds=61)
    assert due_snapshot_kind(state, now) == "m1"
    for i in range(4):
        await tracker.handle_raw(fx.trade_event(f"s{i}", f"B{i}" + "1" * 41, "buy", sol=0.3))
    await writer.flush()  # token row must exist before the snapshot references it
    row = build_snapshot_row(state, "m1", now, sol_usd=150.0)
    assert row["price_sol"] and row["price_usd"] == row["price_sol"] * 150.0
    assert row["liquidity_sol"] is not None and row["liquidity_sol"] > 0
    assert row["unique_buyers_5m"] == 5 and row["holder_count"] == 5  # 4 buyers + creator initial buy
    assert row["data_quality"]["holder_count_source"] == "stream_estimate"
    snapshot_id = await persist_snapshot(row)
    assert snapshot_id > 0
    state.snapshots_done.add("m1")
    state.launch_time = now - timedelta(seconds=181)
    assert due_snapshot_kind(state, now) == "m3"
    state.launch_time = now - timedelta(seconds=1000)  # far past m5/m10/m15 -> skipped, periodic scheduled
    assert due_snapshot_kind(state, now) is None
    assert {"m5", "m10", "m15"} <= state.snapshots_done
    async with session_scope() as s:
        assert await s.scalar(select(func.count()).select_from(TokenSnapshot)) == 1


async def test_sol_usd_missing_is_flagged_not_faked() -> None:
    writer = PersistenceWriter()
    tracker = TokenTracker(writer, FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    await tracker.handle_raw(fx.create_event())
    row = build_snapshot_row(tracker.states[fx.MINT], "m1", datetime.now(UTC), sol_usd=None)
    assert row["price_usd"] is None and row["liquidity_usd"] is None
    assert row["data_quality"]["sol_usd_missing"] is True


async def test_worker_starts_heartbeats_and_stops_cleanly(monkeypatch) -> None:
    from backend.config.settings import get_settings
    from backend.scanner.main import ScannerWorker

    s = get_settings()
    monkeypatch.setattr(s, "pumpportal_enabled", False)
    monkeypatch.setattr(s.scanner, "http_health_enabled", True)
    monkeypatch.setattr(s.scanner, "http_health_port", 18081)
    monkeypatch.setattr(s.scanner, "heartbeat_interval_seconds", 1)
    worker = ScannerWorker()
    run_task = asyncio.create_task(worker.run_forever())
    for _ in range(100):
        if worker.heartbeat.status == "running":
            break
        await asyncio.sleep(0.1)
    assert worker.heartbeat.status == "running" and worker.heartbeat.is_leader
    # health endpoint answers
    reader, writer = await asyncio.open_connection("127.0.0.1", 18081)
    writer.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    assert b"200 OK" in response and b'"is_leader": true' in response
    # a second worker cannot become leader while the first holds the lock
    second = ScannerWorker()
    assert not await second.heartbeat.acquire_leadership(wait_seconds=0)
    worker.request_stop()
    await asyncio.wait_for(run_task, 20)
    async with session_scope() as sess:
        hb = (await sess.execute(select(WorkerHeartbeat))).scalars().all()
        assert any(h.status == "stopping" for h in hb)
    # lock released on shutdown
    assert await second.heartbeat.acquire_leadership(wait_seconds=0)
    await second.heartbeat.lock.release()


async def test_redis_track_request_is_consumed() -> None:
    import json

    from backend.services import keys
    from backend.services.redis_client import get_redis

    await get_redis().rpush(keys.TRACK_REQUESTS, json.dumps({"mint": fx.MINT, "reason": "api"}))
    tracker = TokenTracker(PersistenceWriter(), FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    assert await tracker.process_track_requests() == 1
    assert tracker.states[fx.MINT].flags.get("manual") is True
