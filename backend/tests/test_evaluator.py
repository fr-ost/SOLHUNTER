"""Evaluator integration: discovery -> trades -> snapshot -> evaluation -> candidate_scores (needs services)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.database.engine import session_scope
from backend.ingestion.dedupe import Deduplicator
from backend.models import (
    CandidateHistory,
    CandidateScore,
    ModelPrediction,
    TokenRiskEvent,
    TokenSnapshot,
    WalletTokenPosition,
)
from backend.scanner.evaluator import Evaluator
from backend.scanner.main import ScannerWorker
from backend.scanner.snapshots import build_snapshot_row, persist_snapshot
from backend.scanner.tracker import TokenTracker
from backend.tests.conftest import requires_services
from backend.tests.fixtures import pumpportal as fx

pytestmark = requires_services


class FakePumpPortal:
    def __init__(self) -> None:
        self.subscribed: set[str] = set()

    async def subscribe_token_trades(self, mints: list[str]) -> None:
        self.subscribed.update(mints)

    async def unsubscribe_token_trades(self, mints: list[str]) -> None:
        self.subscribed.difference_update(mints)

    @property
    def token_subscription_count(self) -> int:
        return len(self.subscribed)


async def _worker_with_evaluator(monkeypatch) -> tuple[ScannerWorker, Evaluator]:
    from backend.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "pumpportal_enabled", False)
    worker = ScannerWorker()
    worker.tracker = TokenTracker(worker.persistence, FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    evaluator = Evaluator(worker)
    worker.evaluator = evaluator
    return worker, evaluator


async def _simulate_token(worker: ScannerWorker, now: datetime, n_buyers: int = 30) -> None:
    tracker = worker.tracker
    assert tracker is not None
    await tracker.handle_raw(fx.create_event())
    state = tracker.states[fx.MINT]
    state.launch_time = now - timedelta(seconds=320)
    v_sol, v_tok = 31.8, 1_014_000_000.0
    for i in range(n_buyers):
        v_sol += 1.1
        v_tok -= 20_000_000
        raw = fx.trade_event(f"t{i}", f"Buyer{i:03d}" + "b" * 33, "buy", sol=1.1, tokens=20_000_000, v_sol=v_sol, v_tokens=v_tok)
        raw["timestamp"] = int((state.launch_time + timedelta(seconds=15 + i * 9)).timestamp())
        await tracker.handle_raw(raw)
    for i in range(2):
        raw = fx.trade_event(f"sell{i}", f"Buyer{i:03d}" + "b" * 33, "sell", sol=0.2, tokens=5_000_000, v_sol=v_sol - 0.2, v_tokens=v_tok + 5_000_000)
        raw["timestamp"] = int((now - timedelta(seconds=30)).timestamp())
        await tracker.handle_raw(raw)
    await worker.persistence.flush()


async def test_end_to_end_discovery_to_candidate_score(monkeypatch) -> None:
    worker, evaluator = await _worker_with_evaluator(monkeypatch)
    now = datetime.now(UTC)
    await _simulate_token(worker, now)
    state = worker.tracker.states[fx.MINT]  # type: ignore[union-attr]
    row = build_snapshot_row(state, "m5", now, sol_usd=150.0)
    snapshot_id = await persist_snapshot(row)
    hook_calls: list[str] = []

    async def hook(ctx, result, ranking, sid):  # noqa: ANN001
        hook_calls.append(ranking.status)

    evaluator.hooks.append(hook)
    result, ranking = await evaluator.evaluate(state, "m5", row, snapshot_id)
    assert not result.hard_fail, result.rejection_reasons
    assert hook_calls == [ranking.status]
    await worker.persistence.flush()
    await evaluator._flush_positions()  # noqa: SLF001
    async with session_scope() as s:
        scores = (await s.execute(select(CandidateScore).where(CandidateScore.mint == fx.MINT))).scalars().all()
        assert len(scores) == 1 and scores[0].is_latest and scores[0].snapshot_id == snapshot_id
        assert scores[0].components and scores[0].filter_results["stages"]
        assert scores[0].execution["estimates"][1]["size_usd"] == 250.0
        assert scores[0].whale_signal is not None and scores[0].ml_is_experimental is True
        preds = (await s.execute(select(ModelPrediction).where(ModelPrediction.mint == fx.MINT))).scalars().all()
        assert len(preds) == 1 and preds[0].model_version == "heuristic-v0"
        snap = await s.get(TokenSnapshot, snapshot_id)
        assert snap is not None and snap.features and "momentum_5m_pct" in snap.features
        positions = (await s.execute(select(WalletTokenPosition).where(WalletTokenPosition.mint == fx.MINT))).scalars().all()
        assert len(positions) == 30
        history = (await s.execute(select(CandidateHistory).where(CandidateHistory.mint == fx.MINT))).scalars().all()
        assert history and history[0].from_status == "tracking"
    # second evaluation marks the first as not latest
    row2 = build_snapshot_row(state, "periodic", now + timedelta(seconds=60), sol_usd=150.0)
    sid2 = await persist_snapshot(row2)
    await evaluator.evaluate(state, "periodic", row2, sid2)
    async with session_scope() as s:
        latest = (await s.execute(select(CandidateScore).where(CandidateScore.mint == fx.MINT, CandidateScore.is_latest.is_(True)))).scalars().all()
        assert len(latest) == 1 and latest[0].snapshot_id == sid2
    assert evaluator.counters["evaluations"] == 2


async def test_rug_creates_risk_events_and_blocks_enrichment(monkeypatch) -> None:
    worker, evaluator = await _worker_with_evaluator(monkeypatch)
    now = datetime.now(UTC)
    await _simulate_token(worker, now)
    state = worker.tracker.states[fx.MINT]  # type: ignore[union-attr]
    raw = fx.trade_event("rug", "Rugger" + "r" * 37, "sell", sol=12.0, tokens=400_000_000, v_sol=12.0, v_tokens=1_400_000_000.0)
    raw["timestamp"] = int((now - timedelta(seconds=2)).timestamp())
    await worker.tracker.handle_raw(raw)  # type: ignore[union-attr]
    await worker.persistence.flush()
    row = build_snapshot_row(state, "m5", now, sol_usd=150.0)
    sid = await persist_snapshot(row)
    result, ranking = await evaluator.evaluate(state, "m5", row, sid)
    assert result.hard_fail and ranking.status == "rejected"
    assert state.flags.get("hard_rejected") and state.flags.get("rugged")
    await worker.persistence.flush()
    async with session_scope() as s:
        events = (await s.execute(select(TokenRiskEvent).where(TokenRiskEvent.mint == fx.MINT))).scalars().all()
        types = {e.risk_type for e in events}
        assert "liquidity_withdrawal" in types and "rug_pull" in types
        assert any(e.severity == "critical" for e in events)
    assert evaluator.counters["hard_fails"] == 1 and evaluator.counters["enrichments"] == 0
