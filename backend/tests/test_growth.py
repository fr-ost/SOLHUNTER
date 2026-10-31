"""Lifecycle-aware evaluation: age curves, growth metrics, growth tracker, discovery, migration auto-track, 24h labels."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response
from sqlalchemy import select

from backend.database.engine import session_scope
from backend.ingestion.dedupe import Deduplicator
from backend.ingestion.persistence import PersistenceWriter
from backend.intelligence.maturity import (
    GrowthPoint,
    compute_growth,
    interp_curve,
    phase_for_age,
    thresholds_for,
)
from backend.models import Token, TokenSnapshot, TrainingSample
from backend.scanner.growth import next_checkpoint
from backend.scanner.tracker import TokenTracker
from backend.tests.conftest import requires_services
from backend.tests.fixtures import pumpportal as fx


def test_curves_tighten_with_age() -> None:
    assert interp_curve("1:0.8,10:0.4", 1) == 0.8 and interp_curve("1:0.8,10:0.4", 10) == 0.4 and interp_curve("1:0.8,10:0.4", 100) == 0.4
    mid = interp_curve("1:0.8,10:0.4", 3.1623)  # geometric midpoint in log-age
    assert abs(mid - 0.6) < 0.01
    t2, t15, t24h = thresholds_for(120), thresholds_for(900), thresholds_for(86_400)
    assert t2.max_top10_pct > t15.max_top10_pct > t24h.max_top10_pct
    assert t2.min_holders < t15.min_holders < t24h.min_holders
    assert t2.phase == "launch" and thresholds_for(3600).phase == "survival" and t24h.phase == "established"
    assert phase_for_age(10) == "launch"
    with pytest.raises(ValueError):
        interp_curve("", 5)


def _pt(hours_ago: float, price: float, mcap: float, liq: float, holders: int | None = None, vol1=None, vol24=None, b1=None, s1=None, migrated=False, now=None) -> GrowthPoint:  # noqa: ANN001
    now = now or datetime.now(UTC)
    return GrowthPoint(at=now - timedelta(hours=hours_ago), price_usd=price, market_cap_usd=mcap, liquidity_usd=liq, holder_count=holders, volume_h1_usd=vol1, volume_h24_usd=vol24, buys_h1=b1, sells_h1=s1, migrated=migrated)


def test_growth_metrics_reward_healthy_growers_and_flag_collapses() -> None:
    now = datetime.now(UTC)
    launch = now - timedelta(hours=8)
    healthy = [
        _pt(7, 0.0010, 100_000, 20_000, 300, now=now),
        _pt(6, 0.0011, 110_000, 22_000, 360, now=now),
        _pt(5, 0.0010, 100_000, 23_000, 420, now=now),
        _pt(4, 0.0013, 130_000, 26_000, 500, now=now),
        _pt(3, 0.0012, 120_000, 27_000, 560, now=now),
        _pt(2, 0.0015, 150_000, 30_000, 650, now=now),
        _pt(1, 0.0014, 140_000, 31_000, 720, now=now),
        _pt(0, 0.0018, 180_000, 35_000, 820, vol1=20_000, vol24=250_000, b1=60, s1=30, migrated=True, now=now),
    ]
    m = compute_growth(healthy, launch, None, now)
    assert m.alive and not m.hard_fail and m.phase == "survival"
    assert m.holder_growth_pct_per_hour is not None and m.holder_growth_pct_per_hour > 8
    assert m.liquidity_trend_pct is not None and m.liquidity_trend_pct > 25
    assert m.drawdown_from_ath_pct == 0.0 and m.higher_lows is True
    assert m.mcap_change_6h_pct is not None and m.mcap_change_6h_pct > 20
    assert m.buy_ratio_h1 is not None and abs(m.buy_ratio_h1 - 2 / 3) < 1e-9
    assert m.score >= 0.75, (m.score, m.positives, m.reasons)
    assert "completed bonding curve (migrated)" in m.positives
    rug = [_pt(2, 0.001, 100_000, 20_000, now=now), _pt(0, 0.0001, 10_000, 1_500, vol24=20, now=now)]  # -92% liquidity = pull
    r = compute_growth(rug, launch, None, now)
    assert r.hard_fail and any("liquidity collapsed" in x for x in r.reasons) and r.score == 0.0
    dead = [_pt(0, 0.00001, 500, 100, vol24=0, now=now)]
    d = compute_growth(dead, launch, ath_price_usd=0.001, now=now)
    assert not d.alive and d.score == 0.0 and d.drawdown_from_ath_pct is not None and d.drawdown_from_ath_pct < -90
    assert compute_growth([], launch, None, now).alive is False


def test_checkpoints() -> None:
    cps = [0.5, 1, 3, 6, 24, 168]
    assert next_checkpoint(0.6, cps) == 1 and next_checkpoint(1.0, cps) == 3 and next_checkpoint(200, cps) is None


class FakePumpPortal:
    def __init__(self) -> None:
        self.tokens: set[str] = set()

    async def subscribe_token_trades(self, mints):  # noqa: ANN001
        self.tokens.update(mints)

    async def unsubscribe_token_trades(self, mints):  # noqa: ANN001
        self.tokens.difference_update(mints)

    async def subscribe_account_trades(self, accounts):  # noqa: ANN001
        pass

    async def unsubscribe_account_trades(self, accounts):  # noqa: ANN001
        pass

    @property
    def token_subscription_count(self) -> int:
        return len(self.tokens)


@requires_services
async def test_migration_of_unknown_token_starts_tracking() -> None:
    writer = PersistenceWriter()
    pp = FakePumpPortal()
    tracker = TokenTracker(writer, pp, Deduplicator())  # type: ignore[arg-type]
    await tracker.handle_raw(fx.migration_event(mint=fx.MINT, signature="mig-1"))
    assert fx.MINT in tracker.states and fx.MINT in pp.tokens
    st = tracker.states[fx.MINT]
    assert st.migrated_at is not None and st.pool_kind == "pump-amm" and st.discovery_source == "migration"


@requires_services
async def test_retire_schedules_growth_checks_only_for_tokens_with_activity(monkeypatch) -> None:
    from types import SimpleNamespace

    from backend.scanner.growth import GrowthTracker

    worker = SimpleNamespace(dex=None, tracker=None, sol_price=None)
    growth = GrowthTracker(worker)  # type: ignore[arg-type]
    writer = PersistenceWriter()
    tracker = TokenTracker(writer, FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    tracker.retire_listeners.append(growth.schedule_after_tracking)
    await tracker.handle_raw(fx.create_event())
    quiet = tracker.states[fx.MINT]
    quiet.launch_time = datetime.now(UTC) - timedelta(minutes=95)
    quiet.tracking_until = datetime.now(UTC) - timedelta(minutes=1)
    await tracker.expire()
    assert quiet.next_growth_check_at is None  # no trades -> not followed
    active_mint = "Act" + "a" * 41
    await tracker.handle_raw(fx.create_event(mint=active_mint, signature="c2"))
    for i in range(15):
        await tracker.handle_raw(fx.trade_event(f"t{i}", f"B{i}" + "b" * 41, "buy", mint=active_mint))
    st = tracker.states[active_mint]
    st.launch_time = datetime.now(UTC) - timedelta(minutes=95)
    st.tracking_until = datetime.now(UTC) - timedelta(minutes=1)
    await tracker.expire()
    assert st.next_growth_check_at is not None and st.next_growth_check_at > datetime.now(UTC)
    assert st.phase == "survival" and st.status == "archived"
    await writer.flush()
    async with session_scope() as s:
        row = await s.get(Token, active_mint)
        assert row.next_growth_check_at is not None and row.phase == "survival"


@requires_services
@respx.mock
async def test_growth_cycle_checks_due_tokens_and_retracks_growers(monkeypatch) -> None:
    from backend.config.settings import get_settings
    from backend.scanner.main import ScannerWorker

    s = get_settings()
    monkeypatch.setattr(s, "pumpportal_enabled", False)
    monkeypatch.setattr(s.growth, "retrack_min_score", 0.5)
    worker = ScannerWorker()
    worker.tracker = TokenTracker(worker.persistence, FakePumpPortal(), Deduplicator())  # type: ignore[arg-type]
    from backend.scanner.growth import GrowthTracker

    growth = GrowthTracker(worker)
    now = datetime.now(UTC)
    launch = now - timedelta(hours=5)
    grower, fader, gone = "Grow" + "g" * 40, "Fade" + "f" * 40, "Gone" + "n" * 40
    async with session_scope() as sess:
        for mint in (grower, fader, gone):
            sess.add(Token(mint=mint, symbol=mint[:4], status="archived", first_seen_at=launch, launch_time=launch, phase="survival", next_growth_check_at=now - timedelta(minutes=1), creator_wallet=fx.CREATOR))
        await sess.flush()
        # earlier observations so growth metrics have history
        for hours_ago, price, liq, holders in ((4, 0.0010, 12_000, 200), (2, 0.0012, 16_000, 320)):
            sess.add(TokenSnapshot(mint=grower, captured_at=now - timedelta(hours=hours_ago), snapshot_kind="growth", price_usd=price, price_sol=price / 150, market_cap_usd=price * 1e9, liquidity_usd=liq, holder_count=holders, data_quality={"holder_count_source": "helius"}))
            sess.add(TokenSnapshot(mint=fader, captured_at=now - timedelta(hours=hours_ago), snapshot_kind="growth", price_usd=price, price_sol=price / 150, market_cap_usd=price * 1e9, liquidity_usd=liq, holder_count=holders, data_quality={"holder_count_source": "helius"}))

    def pair(mint: str, price: float, liq: float, vol24: float, b1: int, s1: int, dex: str = "pumpswap") -> dict:
        p = fx.dexscreener_pair(mint=mint, liquidity_usd=liq, price_usd=price)
        p["dexId"] = dex
        p["volume"]["h24"] = vol24
        p["volume"]["h1"] = vol24 / 24
        p["txns"]["h1"] = {"buys": b1, "sells": s1}
        p["marketCap"] = price * 1e9
        p["fdv"] = price * 1e9
        return p

    respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{grower},{fader},{gone}").mock(
        return_value=Response(200, json=[pair(grower, 0.0020, 30_000, 400_000, 80, 30), pair(fader, 0.0006, 9_000, 3_000, 2, 15)])
    )
    respx.get("https://api.dexscreener.com/token-pairs/v1/solana/So11111111111111111111111111111111111111112").mock(return_value=Response(200, json=[]))
    try:
        checked = await growth.cycle()
        assert checked == 3 and growth.checked == 3
        async with session_scope() as sess:
            g = await sess.get(Token, grower)
            f = await sess.get(Token, fader)
            n = await sess.get(Token, gone)
            assert g.growth_checks == 1 and g.growth_score is not None and g.growth_score >= 0.5, g.growth_score
            assert g.ath_price_usd == 0.0020 and g.last_liquidity_usd == 30_000 and g.migrated_at is not None
            assert g.retracks == 1 and grower in worker.tracker.states  # strong grower goes back to real-time tracking
            assert worker.tracker.states[grower].growth is not None and worker.tracker.states[grower].flags["source"] == "growth"
            assert f.growth_score is not None and f.growth_score < 0.3 and f.retracks == 0 and fader not in worker.tracker.states
            assert f.next_growth_check_at is not None and f.status != "rejected"  # weak but alive -> keeps being checked
            assert n.next_growth_check_at is not None and n.growth_checks == 1 and n.phase == "survival"  # not indexed yet: retried later
            snaps = (await sess.execute(select(TokenSnapshot).where(TokenSnapshot.snapshot_kind == "growth", TokenSnapshot.mint == grower))).scalars().all()
            assert len(snaps) == 3 and snaps[-1].features and snaps[-1].features["growth_score"] == g.growth_score
            assert snaps[-1].data_quality["growth"]["positives"]
        assert growth.retracked == 1
    finally:
        await worker.dex.aclose()
        await worker.helius.aclose()


@requires_services
@respx.mock
async def test_discovery_adds_unknown_boosted_tokens(monkeypatch) -> None:
    from types import SimpleNamespace

    from backend.providers.dexscreener import DexScreenerClient
    from backend.scanner.growth import GrowthTracker

    new_mint = "Boost" + "b" * 39
    respx.get("https://api.dexscreener.com/token-profiles/latest/v1").mock(return_value=Response(200, json=[{"chainId": "solana", "tokenAddress": new_mint, "url": "https://dexscreener.com/solana/x", "description": "A token", "links": []}, {"chainId": "ethereum", "tokenAddress": "0xabc"}]))
    respx.get("https://api.dexscreener.com/token-boosts/latest/v1").mock(return_value=Response(200, json=[{"chainId": "solana", "tokenAddress": new_mint, "totalAmount": 100}]))
    respx.get("https://api.dexscreener.com/token-boosts/top/v1").mock(return_value=Response(200, json=[]))
    p = fx.dexscreener_pair(mint=new_mint, liquidity_usd=40_000, price_usd=0.01)
    p["pairCreatedAt"] = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
    respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{new_mint}").mock(return_value=Response(200, json=[p]))
    dex = DexScreenerClient()
    growth = GrowthTracker(SimpleNamespace(dex=dex, tracker=None, sol_price=None))  # type: ignore[arg-type]
    try:
        assert await growth.discover() == 1
        async with session_scope() as sess:
            row = await sess.get(Token, new_mint)
            assert row is not None and row.discovery_source == "dexscreener_boost" and row.phase == "established"
            assert row.next_growth_check_at is not None and row.metadata_json["dexscreener_boosted"] is True and row.symbol == "GEM"
        assert await growth.discover() == 0  # already known
    finally:
        await dex.aclose()


@requires_services
async def test_24h_labels() -> None:
    from backend.ml.labeler import label_24h_outcomes

    now = datetime.now(UTC)
    old = now - timedelta(hours=30)
    async with session_scope() as s:
        s.add(Token(mint=fx.MINT, symbol="GEM", status="archived", first_seen_at=old, launch_time=old))
        await s.flush()
        snap = TokenSnapshot(mint=fx.MINT, captured_at=old, snapshot_kind="m5", price_sol=1.0, features={"a": 1})
        s.add(snap)
        await s.flush()
        s.add(TrainingSample(mint=fx.MINT, snapshot_id=snap.id, snapshot_at=old, snapshot_kind="m5", features={"a": 1}, entry_price_sol=1.0, horizon_minutes=60, label_complete=True))
        s.add(TokenSnapshot(mint=fx.MINT, captured_at=old + timedelta(hours=5), snapshot_kind="growth", price_sol=2.4, liquidity_usd=20_000))
        s.add(TokenSnapshot(mint=fx.MINT, captured_at=old + timedelta(hours=20), snapshot_kind="growth", price_sol=1.6, liquidity_usd=15_000))
    async with session_scope() as s:
        assert await label_24h_outcomes(s, now=now) == 1
    async with session_scope() as s:
        sample = (await s.execute(select(TrainingSample))).scalar_one()
        assert sample.label_24h_complete and sample.hit_100_24h and sample.hit_50_24h and sample.alive_24h
        assert abs(sample.max_return_24h_pct - 140.0) < 1e-6 and abs(sample.close_return_24h_pct - 60.0) < 1e-6
    async with session_scope() as s:
        assert await label_24h_outcomes(s, now=now) == 0
