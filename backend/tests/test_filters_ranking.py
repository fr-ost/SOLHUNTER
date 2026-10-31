"""Filter stages, pipeline decisions, execution analyzer, ranking, features and heuristic predictor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.execution.analyzer import analyze_execution
from backend.filters.base import EvaluationContext
from backend.filters.pipeline import cheap_pipeline, full_pipeline
from backend.filters.stages import stage_data_validation, stage_liquidity, stage_manipulation, stage_ml
from backend.ingestion.events import TradeEvent
from backend.ingestion.normalizer import normalize_pumpportal
from backend.intelligence.developer import CreatorAnalysis
from backend.intelligence.holders import analyze_holders
from backend.intelligence.manipulation import assess_manipulation
from backend.ml.features import FEATURE_NAMES, build_features, features_json_safe, features_to_vector
from backend.ml.predictor import HeuristicPredictor, Prediction
from backend.ranking.ranker import Ranker
from backend.scanner.snapshots import build_snapshot_row
from backend.scanner.state import TokenState
from backend.tests.fixtures import pumpportal as fx
from backend.whale_engine.engine import WhaleEngine


def _trade(state: TokenState, sig: str, trader: str, side: str, sol: float, ts: datetime, v_sol: float, v_tokens: float = 900_000_000.0) -> None:
    ev = normalize_pumpportal(fx.trade_event(sig, trader, side, sol=sol, tokens=sol * 20_000_000, v_sol=v_sol, v_tokens=v_tokens))
    assert isinstance(ev, TradeEvent)
    state.apply_trade(ev.model_copy(update={"block_time": ts, "received_at": ts}))


def healthy_state(now: datetime, n_buyers: int = 60, age: int = 320) -> TokenState:
    launch = now - timedelta(seconds=age)
    state = TokenState(fx.MINT, launch)
    create = normalize_pumpportal(fx.create_event())
    state.apply_create(create.model_copy(update={"block_time": launch, "received_at": launch}))  # type: ignore[union-attr]
    v_sol, v_tok = 31.8, 1_014_000_000.0
    for i in range(n_buyers):
        size = 0.4 + 0.2 * (i % 3)  # 0.4 / 0.6 / 0.8 SOL - varied sizes, no dominant holder
        v_sol += size
        v_tok -= size * 20_000_000
        _trade(state, f"b{i}", f"Buyer{i:03d}" + "b" * 33, "buy", size, launch + timedelta(seconds=15 + i * (age - 30) / n_buyers), v_sol, v_tok)
    # a couple of modest sells prove sellability
    for i in range(2):
        v_sol -= 0.2
        v_tok += 5_000_000
        _trade(state, f"s{i}", f"Buyer{i:03d}" + "b" * 33, "sell", 0.2, now - timedelta(seconds=40 - i * 10), v_sol, v_tok)
    return state


def make_ctx(state: TokenState, now: datetime, sol_usd: float | None = 150.0, kind: str = "m5") -> EvaluationContext:
    row = build_snapshot_row(state, kind, now, sol_usd)
    ctx = EvaluationContext(state=state, snapshot=row, now=now, sol_usd=sol_usd, snapshot_id=None)
    ctx.manipulation = assess_manipulation(state, now)
    ctx.execution = analyze_execution(state, sol_usd)
    ctx.holders = analyze_holders(state, None, now)
    ctx.creator = CreatorAnalysis(creator=state.creator, known=True, prior_launches_db=0, risk_score=0.2, confidence="medium", sources=["db", "stream"])
    ctx.whales = WhaleEngine().assess(state, {}, None, row["liquidity_sol"], sol_usd, now)
    ctx.features = build_features(ctx)
    ctx.prediction = HeuristicPredictor().predict(ctx.features)
    return ctx


def test_healthy_token_passes_pipeline_and_ranks() -> None:
    now = datetime.now(UTC)
    state = healthy_state(now)
    ctx = make_ctx(state, now)
    result = full_pipeline().run(ctx)
    assert not result.hard_fail, result.rejection_reasons
    assert not result.rejected, result.rejection_reasons
    ranking = Ranker().rank(ctx, result)
    assert 0 <= ranking.overall_score <= 100
    assert set(ranking.components) <= set(FEATURE_NAMES) | {"safety", "liquidity", "smart_money", "whale_accumulation", "buyer_quality", "volume_acceleration", "momentum", "ml_breakout", "expected_return", "risk_reward", "gpt"}
    assert "gpt" in ranking.missing_components  # no GPT analysis yet -> excluded from weighting
    assert ranking.status in {"candidate", "watchlist", "screening", "rejected"}
    assert ranking.explanation


def test_hard_safety_failure_overrides_positive_signals() -> None:
    now = datetime.now(UTC)
    state = healthy_state(now)
    # developer dumps 90% of their initial buy -> hard fail in the developer stage
    dev_tokens = state.wallets[fx.CREATOR].bought_tokens
    ev = normalize_pumpportal(fx.trade_event("devdump", fx.CREATOR, "sell", sol=2.5, tokens=dev_tokens * 0.9, v_sol=44.0, v_tokens=700_000_000.0))
    assert isinstance(ev, TradeEvent)
    state.apply_trade(ev.model_copy(update={"block_time": now - timedelta(seconds=5), "received_at": now}))
    ctx = make_ctx(state, now)
    # pretend whales and ML love it
    ctx.whales.signals = ["SMART_MONEY_CONSENSUS", "WHALE_ACCUMULATION"]
    ctx.whales.accumulation_score = 1.0
    ctx.prediction = Prediction(0.05, 0.9, 0.8, 0.7, 25.0, -3.0, 300.0, 0.05, 0.9, False, "fake-v1")
    result = full_pipeline().run(ctx)
    assert result.hard_fail and result.rejected
    assert any("developer sold" in r for r in result.rejection_reasons)
    ranking = Ranker().rank(ctx, result)
    assert ranking.status == "rejected"


def test_liquidity_withdrawal_is_hard_fail_in_cheap_pass() -> None:
    now = datetime.now(UTC)
    state = healthy_state(now)
    _trade(state, "rug", "Rugger" + "r" * 37, "sell", 10.0, now - timedelta(seconds=2), v_sol=15.0, v_tokens=1_300_000_000.0)
    ctx = make_ctx(state, now)
    result = cheap_pipeline().run(ctx)
    assert result.hard_fail
    assert any("liquidity" in r for r in result.rejection_reasons)
    s9 = stage_manipulation(ctx)
    assert s9.hard_fail


def test_quality_failures_allow_watchlist_but_not_candidate() -> None:
    now = datetime.now(UTC)
    state = healthy_state(now, n_buyers=6)  # too few buyers for stage 6
    ctx = make_ctx(state, now)
    result = full_pipeline().run(ctx)
    assert not result.rejected or result.rejection_reasons
    if not result.rejected:
        assert not result.candidate_eligible
        assert any("unique buyers" in r for r in result.quality_failures)
        ranking = Ranker().rank(ctx, result)
        assert ranking.status != "candidate"


def test_data_validation_reports_contradictions_and_missing_sol_price() -> None:
    now = datetime.now(UTC)
    state = healthy_state(now)
    ctx = make_ctx(state, now, sol_usd=None)
    s1 = stage_data_validation(ctx)
    assert any("SOL/USD" in r for r in s1.reasons)
    ctx2 = make_ctx(state, now)
    ctx2.snapshot["liquidity_usd"] = ctx2.snapshot["liquidity_sol"] * 1000  # contradictory vs sol_usd 150
    s1b = stage_data_validation(ctx2)
    assert any("contradictory" in r for r in s1b.reasons)
    young = healthy_state(now, age=20)
    s1c = stage_data_validation(make_ctx(young, now))
    assert any("too young" in r for r in s1c.reasons)


def test_liquidity_stage_requires_sellability_evidence() -> None:
    now = datetime.now(UTC)
    state = healthy_state(now)
    ctx = make_ctx(state, now)
    s5 = stage_liquidity(ctx)
    assert s5.passed, s5.reasons
    assert s5.metrics["sell_impact_pct_ref"] is not None
    ctx.execution = None
    s5b = stage_liquidity(ctx)
    assert not s5b.passed and any("sellability" in r for r in s5b.reasons)


def test_execution_analyzer_bonding_curve_estimates() -> None:
    now = datetime.now(UTC)
    state = healthy_state(now)
    report = analyze_execution(state, sol_usd=150.0)
    assert report.source == "stream_virtual_reserves" and report.venue == "pump.fun bonding curve"
    sizes = [e.size_usd for e in report.estimates]
    assert sizes == [100.0, 250.0, 500.0]
    e100, e500 = report.estimates[0], report.estimates[2]
    assert e100.available and e500.available
    assert e100.executable_buy_price_sol > e100.spot_price_sol
    assert e500.buy_impact_pct > e100.buy_impact_pct
    assert e500.sell_impact_pct > e100.sell_impact_pct
    assert 0 < e100.round_trip_cost_pct < 15
    assert e100.immediate_exit_proceeds_usd < 100.0
    assert e100.targets["+10%"]["net_return_pct"] > 0 and e100.targets["+20%"]["net_return_pct"] > e100.targets["+10%"]["net_return_pct"]
    assert e100.targets["+10%"]["net_return_pct"] < 10.0  # fees and impact eat into the move
    assert report.max_size_usd_within_slippage is not None and report.max_size_usd_within_slippage > 0


def test_execution_analyzer_marks_unavailable_without_reserves() -> None:
    now = datetime.now(UTC)
    state = TokenState(fx.MINT, now - timedelta(minutes=5))
    report = analyze_execution(state, sol_usd=150.0)
    assert report.source == "unavailable"
    assert all(not e.available and e.reason for e in report.estimates)
    state2 = healthy_state(now)
    report2 = analyze_execution(state2, sol_usd=None)
    assert all(not e.available for e in report2.estimates) and "SOL/USD unavailable" in report2.notes[0]


def test_features_are_complete_and_json_safe() -> None:
    now = datetime.now(UTC)
    ctx = make_ctx(healthy_state(now), now)
    feats = ctx.features
    assert list(feats) == FEATURE_NAMES
    vec = features_to_vector(feats)
    assert len(vec) == len(FEATURE_NAMES)
    safe = features_json_safe(feats)
    assert safe["creator_prior_rugs"] == 0.0 and safe["age_seconds"] > 0
    assert safe["largest_cluster_pct"] is None  # no cluster analysis -> NaN -> None, never 0
    import json

    json.dumps(safe)


def test_heuristic_predictor_is_flagged_experimental_and_bounded() -> None:
    now = datetime.now(UTC)
    ctx = make_ctx(healthy_state(now), now)
    p = HeuristicPredictor().predict(ctx.features)
    assert p.is_experimental and p.model_version == "heuristic-v0"
    assert 0 < p.rug_risk < 1 and 0 < p.p10 < 1 and p.p15 < p.p10 and p.p20 < p.p15
    assert p.expected_downside_pct < 0 and 0 < p.p_stop_loss < 1
    risky = dict(ctx.features)
    risky["creator_risk_score"] = 0.95
    risky["largest_holder_pct"] = 0.4
    risky["dev_sold_pct"] = 0.6
    p2 = HeuristicPredictor().predict(risky)
    assert p2.rug_risk > p.rug_risk and p2.p10 < p.p10
    empty = HeuristicPredictor().predict({})
    assert 0 < empty.rug_risk < 1


def test_ml_stage_experimental_predictions_never_hard_fail() -> None:
    now = datetime.now(UTC)
    ctx = make_ctx(healthy_state(now), now)
    ctx.prediction = Prediction(0.95, 0.05, 0.02, 0.01, -10.0, -20.0, 900.0, 0.8, 0.2, True, "heuristic-v0")
    s10 = stage_ml(ctx)
    assert not s10.passed and not s10.hard_fail and "experimental" in s10.reasons[0]
    ctx.prediction = Prediction(0.95, 0.05, 0.02, 0.01, -10.0, -20.0, 900.0, 0.8, 0.9, False, "xgb-v3")
    assert stage_ml(ctx).hard_fail


def test_ranker_penalties_and_confidence() -> None:
    now = datetime.now(UTC)
    ctx = make_ctx(healthy_state(now), now)
    result = full_pipeline().run(ctx)
    base = Ranker().rank(ctx, result)
    ctx.whales.signals = ["INSIDER_SELLING", "WHALE_DISTRIBUTION"]
    penalised = Ranker().rank(ctx, result)
    assert penalised.penalties["insider_selling"] == 15.0 and penalised.penalties["whale_distribution"] == 10.0
    assert penalised.overall_score < base.overall_score
    assert base.confidence in {"low", "medium", "high"}
    gpt = Ranker().rank(ctx, result, gpt_recommendation="STRONG_CANDIDATE", gpt_probability=0.6)
    assert "gpt" in gpt.components and gpt.components["gpt"] > 70
    rejected = Ranker().rank(ctx, result, gpt_recommendation="REJECT")
    assert rejected.components["gpt"] < 10
