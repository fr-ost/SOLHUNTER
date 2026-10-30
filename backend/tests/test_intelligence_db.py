"""Database-backed intelligence tests: positions flush, profiler, relationships, developer history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.database.engine import session_scope
from backend.ingestion.normalizer import normalize_pumpportal
from backend.intelligence.developer import analyze_creator
from backend.intelligence.relationships import Edge, load_edges, persist_edges
from backend.intelligence.wallets import PositionUpdater, TradeInput, WalletProfiler, mark_token_rugged
from backend.models import Token, TokenRiskEvent, WalletProfile, WalletRelationship, WalletTokenPosition
from backend.scanner.state import TokenState
from backend.tests.conftest import requires_services
from backend.tests.fixtures import pumpportal as fx

pytestmark = requires_services


async def _seed_token(mint: str, creator: str, status: str = "tracking", launch: datetime | None = None) -> None:
    async with session_scope() as s:
        s.add(Token(mint=mint, symbol=mint[:4], creator_wallet=creator, status=status, first_seen_at=launch or datetime.now(UTC), launch_time=launch or datetime.now(UTC)))


async def test_position_updater_and_profiler_end_to_end() -> None:
    wallet = "Trader" + "t" * 37
    t0 = datetime.now(UTC) - timedelta(hours=2)
    mints = [f"Mint{i}" + "m" * 39 for i in range(10)]
    for m in mints:
        await _seed_token(m, fx.CREATOR, launch=t0)
    updater = PositionUpdater()
    for i, m in enumerate(mints):
        updater.add(TradeInput(wallet, m, "buy", 1.0, 1000.0, t0 + timedelta(minutes=i), t0 + timedelta(minutes=i) - timedelta(seconds=20)))
        win = i < 7
        updater.add(TradeInput(wallet, m, "sell", 2.0 if win else 0.5, 1000.0, t0 + timedelta(minutes=i, seconds=30), None))
    async with session_scope() as s:
        assert await updater.flush(s) == 20
    async with session_scope() as s:
        rows = (await s.execute(select(WalletTokenPosition).where(WalletTokenPosition.wallet == wallet))).scalars().all()
        assert len(rows) == 10 and all(r.status == "closed" for r in rows)
        assert all(r.is_early_buyer for r in rows)
    profiler = WalletProfiler()
    async with session_scope() as s:
        profiles = await profiler.recompute(s, [wallet], force=True)
    p = profiles[wallet]
    assert p.closed_positions == 10 and abs(p.win_rate - 0.7) < 1e-9
    assert p.category == "smart_money" and p.category_confidence >= 0.5
    async with session_scope() as s:
        row = await s.get(WalletProfile, wallet)
        assert row is not None and row.category == "smart_money" and row.classification_reasons
        loaded = await profiler.load(s, [wallet, "Unknown" + "u" * 36])
    assert loaded[wallet].is_smart_money and loaded["Unknown" + "u" * 36].category == "unknown"
    # rug exposure: mark one token rugged while a position is still open
    extra = "Rugged" + "r" * 37
    await _seed_token(extra, fx.CREATOR, launch=t0)
    updater.add(TradeInput(wallet, extra, "buy", 1.0, 1000.0, t0 + timedelta(hours=1), t0))
    async with session_scope() as s:
        await updater.flush(s)
        assert await mark_token_rugged(s, extra) == 1
    async with session_scope() as s:
        profiles = await profiler.recompute(s, [wallet], force=True)
    assert profiles[wallet].rug_exposure_count == 1


async def test_creator_is_classified_as_developer() -> None:
    await _seed_token(fx.MINT, fx.CREATOR)
    profiler = WalletProfiler()
    async with session_scope() as s:
        profiles = await profiler.recompute(s, [fx.CREATOR], force=True)
    assert profiles[fx.CREATOR].category == "developer" and profiles[fx.CREATOR].is_developer


async def test_relationship_persistence_accumulates_confidence() -> None:
    a, b = "WalletA" + "a" * 36, "WalletB" + "b" * 36
    async with session_scope() as s:
        await persist_edges(s, [Edge(a, b, "timing_correlation", 0.35, {"mint": "m1"})])
    async with session_scope() as s:
        await persist_edges(s, [Edge(b, a, "timing_correlation", 0.35, {"mint": "m2"})])  # reversed order -> same pair
        await persist_edges(s, [Edge(a, b, "shared_funder", 0.35, {"funder": "F"})])
    async with session_scope() as s:
        rows = (await s.execute(select(WalletRelationship))).scalars().all()
        by_type = {r.relationship_type: r for r in rows}
        assert abs(by_type["timing_correlation"].confidence - 0.5) < 1e-9 and by_type["timing_correlation"].observations == 2
        assert by_type["timing_correlation"].wallet_a == a and by_type["timing_correlation"].wallet_b == b
        edges = await load_edges(s, [a])
        assert len(edges) == 2
    # a wallet related to a developer becomes insider-associated
    await _seed_token(fx.MINT, a)
    profiler = WalletProfiler()
    async with session_scope() as s:
        profiles = await profiler.recompute(s, [b], force=True)
    assert profiles[b].category == "insider_associated" and profiles[b].insider_associated


async def test_developer_analysis_uses_db_history() -> None:
    old_launch = datetime.now(UTC) - timedelta(days=3)
    for i in range(3):
        await _seed_token(f"Old{i}" + "o" * 40, fx.CREATOR, status="dead", launch=old_launch)
    async with session_scope() as s:
        s.add(TokenRiskEvent(mint="Old0" + "o" * 40, risk_type="liquidity_withdrawal", severity="critical", title="rug", occurred_at=old_launch, dedupe_key="rug-old0"))
    now = datetime.now(UTC)
    state = TokenState(fx.MINT, now - timedelta(minutes=3))
    create = normalize_pumpportal(fx.create_event())
    state.apply_create(create)  # type: ignore[arg-type]
    async with session_scope() as s:
        analysis = await analyze_creator(state, s, helius=None, deep=False)
    assert analysis.prior_launches_db == 3 and analysis.prior_rugs_db == 1 and analysis.prior_dead_db == 3
    assert any(f.startswith("creator_prior_rugs") for f in analysis.risk_flags)
    assert analysis.risk_score >= 0.8 and "db" in analysis.sources and "helius" not in analysis.sources
    assert analysis.dev_holding_source == "stream_estimate" and analysis.dev_holding_pct is not None


async def test_developer_analysis_unknown_creator_is_not_clean() -> None:
    state = TokenState(fx.MINT, datetime.now(UTC))
    async with session_scope() as s:
        analysis = await analyze_creator(state, s)
    assert not analysis.known and "creator_unknown" in analysis.risk_flags and analysis.risk_score >= 0.5
