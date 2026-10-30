"""Developer, holder, manipulation, wallet classification, relationships and whale engine tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.ingestion.events import TradeEvent
from backend.ingestion.normalizer import normalize_pumpportal
from backend.intelligence import holders as holders_mod
from backend.intelligence.manipulation import assess_manipulation
from backend.intelligence.relationships import Edge, analyze_clusters, shared_funder_edges, timing_edges
from backend.intelligence.wallets import (
    TradeInput,
    WalletProfileSummary,
    apply_trade_to_position,
    classify_wallet,
    compute_profile_stats,
)
from backend.models import WalletTokenPosition, WhaleSignal
from backend.providers.helius import HolderDistribution, TokenHolder
from backend.scanner.state import TokenState
from backend.tests.fixtures import pumpportal as fx
from backend.whale_engine.engine import WhaleEngine, whale_event_rows


def _trade(state: TokenState, sig: str, trader: str, side: str, sol: float, ts: datetime, tokens: float | None = None, v_sol: float = 40.0) -> None:
    tokens = tokens if tokens is not None else sol * 25_000_000
    ev = normalize_pumpportal(fx.trade_event(sig, trader, side, sol=sol, tokens=tokens, v_sol=v_sol, v_tokens=900_000_000.0))
    assert isinstance(ev, TradeEvent)
    state.apply_trade(ev.model_copy(update={"block_time": ts, "received_at": ts}))


def _fresh_state(now: datetime, age_seconds: int = 240) -> TokenState:
    launch = now - timedelta(seconds=age_seconds)
    state = TokenState(fx.MINT, launch)
    create = normalize_pumpportal(fx.create_event())
    state.apply_create(create.model_copy(update={"block_time": launch, "received_at": launch}))  # type: ignore[union-attr]
    return state


# ----------------------------------------------------------------------------- holders
def test_holder_analysis_from_distribution_excludes_infra_and_flags_concentration() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now)
    dist = HolderDistribution(
        mint=fx.MINT,
        total_supply=1_000_000_000.0,
        holder_count=120,
        holder_count_complete=True,
        top_holders=[
            TokenHolder("curve", fx.CURVE, 500_000_000.0, True, "pool/curve"),
            TokenHolder("w1", "WalletOne", 120_000_000.0),
            TokenHolder("dev", fx.CREATOR, 40_000_000.0, label="creator"),
            TokenHolder("w2", "WalletTwo", 30_000_000.0),
        ],
        circulating_excluding_infra=500_000_000.0,
        captured_at=now,
        source="helius",
    )
    analysis = holders_mod.analyze_holders(state, dist, now)
    assert analysis.holder_count == 120 and analysis.confidence == "high"
    assert abs(analysis.largest_holder_pct - 0.24) < 1e-9  # 120M / 500M circulating
    assert abs(analysis.dev_holding_pct - 0.08) < 1e-9
    assert abs(analysis.infra_excluded_pct - 0.5) < 1e-9
    assert any(f.startswith("largest_holder_") for f in analysis.flags)
    assert analysis.score < 1.0


def test_holder_analysis_falls_back_to_stream_estimate() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now)
    for i in range(6):
        _trade(state, f"b{i}", f"W{i}" + "x" * 40, "buy", 0.5, now - timedelta(seconds=100 - i))
    analysis = holders_mod.analyze_holders(state, None, now)
    assert analysis.available and analysis.holder_count_source == "stream_estimate" and analysis.confidence == "low"
    assert "holders_from_stream_estimate" in analysis.flags
    assert analysis.top10_pct is not None and analysis.top10_pct <= 1.0


def test_bundle_detection_counts_simultaneous_first_buys() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now, age_seconds=120)
    t0 = state.launch_time + timedelta(seconds=1)
    for i in range(5):
        _trade(state, f"bundle{i}", f"Bundle{i}" + "y" * 35, "buy", 0.3, t0 + timedelta(milliseconds=200 * i))
    _trade(state, "late", "Late" + "z" * 40, "buy", 0.3, t0 + timedelta(seconds=25))
    assert holders_mod.detect_bundles(state) == 5
    edges = timing_edges(state)
    assert len(edges) == 10  # C(5,2) pairs, late buyer not included
    clusters = analyze_clusters(state, [Edge(e.a, e.b, e.relationship_type, 0.6) for e in edges])
    assert clusters.coordinated_wallets == 5 and clusters.largest_cluster_pct > 0
    assert any(f.startswith("cluster_holds_") or f.startswith("coordinated_wallets") for f in clusters.flags) or clusters.largest_cluster_pct <= 0.25


def test_weak_evidence_alone_does_not_cluster_but_combined_does() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now)
    _trade(state, "a", "WalletA" + "a" * 36, "buy", 1.0, now - timedelta(seconds=10))
    _trade(state, "b", "WalletB" + "b" * 36, "buy", 1.0, now - timedelta(seconds=9))
    a, b = "WalletA" + "a" * 36, "WalletB" + "b" * 36
    weak = shared_funder_edges({a: "Funder1", b: "Funder1"}, fx.MINT)
    assert len(weak) == 1 and weak[0].confidence == 0.35
    assert analyze_clusters(state, weak).coordinated_wallets == 0  # shared funding alone is not proof
    combined = weak + [Edge(a, b, "timing_correlation", 0.35)]
    assert analyze_clusters(state, combined).coordinated_wallets == 2  # 1-(0.65*0.65)=0.58 >= 0.5


# ----------------------------------------------------------------------------- manipulation
def test_manipulation_detects_wash_trading_and_liquidity_withdrawal() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now, age_seconds=400)
    washer = "Washer" + "w" * 37
    for i in range(8):
        side = "buy" if i % 2 == 0 else "sell"
        _trade(state, f"wash{i}", washer, side, 1.0, now - timedelta(seconds=200 - i * 10), v_sol=40.0)
    for i in range(3):
        _trade(state, f"other{i}", f"Other{i}" + "o" * 36, "buy", 0.1, now - timedelta(seconds=150 - i))
    a = assess_manipulation(state, now)
    assert a.round_trip_wallets == 1 and a.wash_trade_score > 0.5
    assert "wash_trading_pattern" in a.flags and not a.hard_fail
    # liquidity withdrawal: v_sol drops 40 -> 20 within 5 minutes
    _trade(state, "dump", "Dumper" + "d" * 37, "sell", 5.0, now, v_sol=20.0)
    a2 = assess_manipulation(state, now)
    assert a2.hard_fail and "liquidity dropped" in (a2.hard_fail_reason or "")
    assert a2.overall_score == 1.0


def test_manipulation_coordinated_burst() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now, age_seconds=300)
    t0 = now - timedelta(seconds=120)
    for i in range(10):
        _trade(state, f"burst{i}", f"Burst{i}" + "q" * 36, "buy", 0.25, t0 + timedelta(milliseconds=150 * i))
    a = assess_manipulation(state, now)
    assert a.coordinated_buy_score > 0.5
    assert any(f.startswith("buy_burst_") for f in a.flags)


def test_manipulation_clean_token_scores_low() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now, age_seconds=300)
    for i in range(12):
        _trade(state, f"ok{i}", f"Buyer{i}" + "k" * 35, "buy", 0.1 + i * 0.07, now - timedelta(seconds=280 - i * 20), v_sol=40 + i)
    a = assess_manipulation(state, now)
    assert a.overall_score < 0.35 and not a.hard_fail


# ----------------------------------------------------------------------------- wallets
def _pos(wallet: str = "W", mint: str = fx.MINT) -> WalletTokenPosition:
    return WalletTokenPosition(wallet=wallet, mint=mint)


def test_position_average_cost_and_realized_pnl() -> None:
    pos = _pos()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    launch = t0 - timedelta(seconds=30)
    apply_trade_to_position(pos, TradeInput("W", fx.MINT, "buy", 1.0, 1000.0, t0, launch))
    apply_trade_to_position(pos, TradeInput("W", fx.MINT, "buy", 3.0, 1000.0, t0 + timedelta(seconds=10), launch))
    assert pos.tokens_held_estimate == 2000 and abs(pos.avg_cost_sol_per_token - 0.002) < 1e-12
    assert pos.is_early_buyer and pos.entry_age_seconds == 30
    apply_trade_to_position(pos, TradeInput("W", fx.MINT, "sell", 3.0, 1000.0, t0 + timedelta(seconds=60), launch, price_sol=0.003))
    assert abs(pos.realized_pnl_sol - 1.0) < 1e-9  # sold 1000 @ 0.003 with cost 0.002
    assert pos.status == "partial" and pos.cost_basis_confidence == "high"
    assert abs(pos.unrealized_pnl_sol - 1.0) < 1e-9  # 1000 left, marked at 0.003
    apply_trade_to_position(pos, TradeInput("W", fx.MINT, "sell", 2.0, 1000.0, t0 + timedelta(seconds=90), launch))
    assert pos.status == "closed" and pos.tokens_held_estimate == 0 and abs(pos.realized_pnl_sol - 1.0) < 1e-9


def test_position_excess_sale_is_not_treated_as_profit() -> None:
    pos = _pos()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    apply_trade_to_position(pos, TradeInput("W", fx.MINT, "buy", 1.0, 1000.0, t0))
    # sells 3000 tokens although only 1000 were bought here: 2000 came from an unseen transfer
    apply_trade_to_position(pos, TradeInput("W", fx.MINT, "sell", 6.0, 3000.0, t0 + timedelta(seconds=5)))
    assert pos.cost_basis_confidence == "low"
    assert abs(pos.realized_pnl_sol - 1.0) < 1e-9  # only the 1000 known tokens: 2.0 proceeds - 1.0 cost
    assert pos.tokens_transferred_in == 2000


def test_new_token_balance_corrects_estimate() -> None:
    pos = _pos()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    apply_trade_to_position(pos, TradeInput("W", fx.MINT, "buy", 1.0, 1000.0, t0, new_token_balance=5000.0))
    assert pos.tokens_held_estimate == 5000 and pos.tokens_transferred_in == 4000 and pos.cost_basis_confidence == "medium"


def _stats(n_wins: int, n_losses: int, early: bool = False, sniper: bool = False) -> dict:
    positions = []
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n_wins + n_losses):
        p = _pos("W", f"mint{i}")
        p.sol_spent = 1.0
        p.tokens_bought = p.tokens_sold = 1000.0
        p.buy_count = p.sell_count = 1
        p.status = "closed"
        p.first_buy_at = t0 + timedelta(minutes=i)
        p.last_trade_at = p.first_buy_at + timedelta(minutes=5)
        win = i < n_wins
        p.sol_received = 2.0 if win else 0.6
        p.realized_pnl_sol = 1.0 if win else -0.4
        p.is_early_buyer = early
        p.entry_age_seconds = 2 if sniper else 60
        if sniper:
            p.last_trade_at = p.first_buy_at + timedelta(seconds=60)
        positions.append(p)
    return compute_profile_stats(positions)


def test_profile_stats_and_classification() -> None:
    stats = _stats(8, 4)
    assert abs(stats["win_rate"] - 8 / 12) < 1e-9
    assert abs(stats["realized_pnl_sol"] - (8 - 1.6)) < 1e-9
    assert abs(stats["profit_factor"] - 8 / 1.6) < 1e-9
    assert stats["avg_return_pct"] is not None and stats["max_drawdown_sol"] >= 0
    category, confidence, reasons = classify_wallet(stats)
    assert category == "smart_money" and confidence >= 0.5 and reasons
    # a big but unproven wallet is NOT smart money
    big = {"positions_with_sales": 1, "win_rate": 1.0, "profit_factor": 99.0, "realized_pnl_sol": 500.0, "tokens_traded": 1, "trades_count": 2, "total_sol_bought": 500.0, "total_sol_sold": 1000.0}
    assert classify_wallet(big)[0] == "unknown"
    assert classify_wallet(_stats(2, 10, sniper=True))[0] == "sniper"
    assert classify_wallet(_stats(5, 2, early=True), {"is_developer": False})[0] in {"profitable_early_buyer", "smart_money"}
    assert classify_wallet(stats, {"is_developer": True})[0] == "developer"
    assert classify_wallet(stats, {"insider_associated": True, "insider_confidence": 0.7})[0] == "insider_associated"
    assert classify_wallet({}, {"is_infrastructure": True})[0] == "infrastructure"
    assert classify_wallet({"tokens_traded": 0}, {"net_flow_24h_sol": 50.0})[0] == "accumulating_whale"


# ----------------------------------------------------------------------------- whale engine
def _profiles(smart: list[str], insiders: list[str] | None = None) -> dict[str, WalletProfileSummary]:
    out = {w: WalletProfileSummary(address=w, category="smart_money", category_confidence=0.8, realized_pnl_sol=20.0, win_rate=0.6, profit_factor=2.0, closed_positions=12, tokens_traded=15) for w in smart}
    for w in insiders or []:
        out[w] = WalletProfileSummary(address=w, category="insider_associated", category_confidence=0.7, insider_associated=True)
    return out


def test_whale_engine_smart_money_consensus_and_accumulation() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now, age_seconds=400)
    smart = [f"Smart{i}" + "s" * 36 for i in range(3)]
    for i, w in enumerate(smart):
        _trade(state, f"sm{i}", w, "buy", 1.5, now - timedelta(seconds=120 + i * 10), v_sol=45.0)
    _trade(state, "whale", "Whale" + "h" * 37, "buy", 3.0, now - timedelta(seconds=60), v_sol=48.0)
    a = WhaleEngine().assess(state, _profiles(smart), None, liquidity_sol=18.0, sol_usd=150.0, now=now)
    assert WhaleSignal.SMART_MONEY_CONSENSUS.value in a.signals
    assert WhaleSignal.SMART_MONEY_ACCUMULATION.value in a.signals
    assert WhaleSignal.WHALE_ACCUMULATION.value in a.signals
    assert a.primary_signal == WhaleSignal.SMART_MONEY_CONSENSUS.value
    assert a.smart_money_buyers == 3 and a.independent_profitable_buyers == 3
    assert a.whale_net_flow_5m > 0 and a.accumulation_score > 0.3 and a.distribution_score == 0.0
    buys = [x for x in a.activities if x.direction == "buy"]
    assert buys and all(x.corroborated for x in buys)
    smart_act = next(x for x in buys if x.wallet == smart[0])
    assert smart_act.usd_amount is not None and smart_act.performance["win_rate"] == 0.6
    whale_act = next(x for x in buys if x.wallet.startswith("Whale"))
    assert whale_act.performance["known"] is False
    whale_rows, smart_rows = whale_event_rows(fx.MINT, a, 18.0)
    assert len(smart_rows) == 3 and len(whale_rows) == 4


def test_whale_engine_distribution_insider_and_exit_warning() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now, age_seconds=600)
    whale = "Whale" + "h" * 37
    insider = "Insider" + "i" * 35
    _trade(state, "wb", whale, "buy", 4.0, now - timedelta(seconds=500), v_sol=40.0)
    _trade(state, "ib", insider, "buy", 1.0, now - timedelta(seconds=480), v_sol=41.0)
    _trade(state, "ws", whale, "sell", 3.0, now - timedelta(seconds=100), v_sol=38.0)
    _trade(state, "is", insider, "sell", 0.9, now - timedelta(seconds=80), v_sol=37.0)
    _trade(state, "dev", fx.CREATOR, "sell", 1.2, now - timedelta(seconds=60), v_sol=36.0)
    a = WhaleEngine().assess(state, _profiles([], [insider]), None, liquidity_sol=20.0, sol_usd=150.0, now=now)
    assert WhaleSignal.WHALE_DISTRIBUTION.value in a.signals
    assert WhaleSignal.INSIDER_SELLING.value in a.signals
    assert WhaleSignal.WHALE_EXIT_WARNING.value in a.signals
    assert a.primary_signal == WhaleSignal.WHALE_EXIT_WARNING.value
    assert a.hard_warning and a.distribution_score >= 0.8
    assert a.dev_sell_sol == 1.2 and a.insider_sell_sol == 0.9
    rows, _ = whale_event_rows(fx.MINT, a, 20.0)
    types = {r["event_type"] for r in rows}
    assert "dev_sell" in types and "insider_sell" in types


def test_whale_engine_quiet_token() -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now, age_seconds=300)
    for i in range(5):
        _trade(state, f"s{i}", f"Small{i}" + "m" * 36, "buy", 0.05, now - timedelta(seconds=100 - i))
    a = WhaleEngine().assess(state, {}, None, liquidity_sol=30.0, sol_usd=None, now=now)
    assert a.signals == [WhaleSignal.NO_SIGNIFICANT_WHALE_ACTIVITY.value]
    assert a.activities == [] and a.data_confidence in {"low", "medium"}
    assert "smart-money detection inactive" in a.notes[0]


@pytest.mark.parametrize("sold_frac,expected", [(0.2, "holding"), (0.9, "distributing")])
def test_whale_holding_vs_distributing(sold_frac: float, expected: str) -> None:
    now = datetime.now(UTC)
    state = _fresh_state(now, age_seconds=600)
    whale = "Whale" + "h" * 37
    _trade(state, "wb", whale, "buy", 5.0, now - timedelta(seconds=500), tokens=100_000_000.0, v_sol=40.0)
    _trade(state, "ws", whale, "sell", 5.0 * sold_frac, now - timedelta(seconds=30), tokens=100_000_000.0 * sold_frac, v_sol=38.0)
    a = WhaleEngine().assess(state, {}, None, liquidity_sol=20.0, sol_usd=150.0, now=now)
    if expected == "holding":
        assert a.whales_holding == 1 and a.whales_distributing == 0
    else:
        assert a.whales_distributing == 1 and WhaleSignal.WHALE_DISTRIBUTION.value in a.signals
