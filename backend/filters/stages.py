"""The ten filtering stages. Every rejection carries an explainable reason.

Stages 1-9 can run in "cheap" mode (stream data only) before any paid enrichment; when the
context carries enriched data (holders from Helius, creator history, wallet profiles) the same
stages use it and raise their confidence. Stage 10 consumes ML predictions.
"""

from __future__ import annotations

from backend.config.settings import FilterSettings, get_settings
from backend.filters.base import EvaluationContext, StageResult
from backend.intelligence.maturity import thresholds_for
from backend.models import WhaleSignal


def _cfg() -> FilterSettings:
    return get_settings().filters


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


# ----------------------------------------------------------------------------- stage 1
def stage_data_validation(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    snap = ctx.snapshot
    state = ctx.state
    reasons: list[str] = []
    hard = False
    quality = snap.get("data_quality") or {}
    stream_age = quality.get("stream_age_seconds")
    if not state.mint or len(state.mint) < 32:
        reasons.append("invalid mint address")
        hard = True
    if state.pool_kind == "pump" and not state.bonding_curve_key and not state.flags.get("manual"):
        reasons.append("missing bonding curve key")
    if snap.get("price_sol") is None:
        reasons.append("no price observed")
        hard = True
    if state.total_trades < cfg.min_trades_for_evaluation:
        reasons.append(f"only {state.total_trades} trades (< {cfg.min_trades_for_evaluation})")
    age = snap.get("age_seconds") or 0
    if age < cfg.min_token_age_seconds:
        reasons.append(f"token too young ({age}s)")
    if age > cfg.max_token_age_minutes * 60:
        reasons.append(f"token older than {cfg.max_token_age_minutes} minutes")
    if stream_age is not None and stream_age > cfg.max_data_age_seconds:
        reasons.append(f"stream data stale ({stream_age}s)")
    if quality.get("dexscreener_stale") and state.pool_kind != "pump":
        reasons.append("DexScreener data stale")
    if quality.get("sol_usd_missing"):
        reasons.append("SOL/USD price unavailable (USD figures missing)")
    liq_sol, liq_usd = snap.get("liquidity_sol"), snap.get("liquidity_usd")
    if liq_sol is not None and liq_usd is not None and ctx.sol_usd and liq_sol > 0:
        implied = liq_usd / liq_sol
        if abs(implied - ctx.sol_usd) / ctx.sol_usd > 0.5:
            reasons.append("contradictory liquidity figures between sources")
    mcap = snap.get("market_cap_usd")
    if mcap is not None and liq_usd is not None and liq_usd > 0 and mcap / liq_usd > 500:
        reasons.append("market cap implausible relative to liquidity")
    completeness = 1.0 - 0.15 * len(reasons)
    return StageResult("s1", "data_validation", passed=not reasons, hard_fail=hard, score=_clamp(completeness), reasons=reasons, metrics={"age_seconds": age, "trades": state.total_trades, "stream_age_seconds": stream_age})


# ----------------------------------------------------------------------------- stage 2
def stage_developer(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    reasons: list[str] = []
    hard = False
    c = ctx.creator
    dev = ctx.state.dev_activity()
    sold_pct = float(dev.get("sold_pct") or 0.0)
    if sold_pct >= cfg.reject_if_dev_sold_pct_over:
        reasons.append(f"developer sold {sold_pct:.0%} of their tokens")
        hard = True
    elif sold_pct >= cfg.dev_sell_warning_pct:
        reasons.append(f"developer selling ({sold_pct:.0%} sold)")
    if c is None:
        return StageResult("s2", "developer", passed=not reasons, hard_fail=hard, score=0.5 if not reasons else 0.2, reasons=reasons, metrics={"dev_sold_pct": sold_pct}, data_available=False)
    if c.prior_rugs_db > cfg.max_creator_prior_rugs:
        reasons.append(f"creator linked to {c.prior_rugs_db} prior rug(s)")
        hard = True
    if c.prior_launches_db > cfg.max_creator_prior_launches:
        reasons.append(f"serial launcher: {c.prior_launches_db} prior launches seen")
    if c.dev_holding_pct is not None and c.dev_holding_pct > cfg.max_dev_holding_pct:
        reasons.append(f"developer holds {c.dev_holding_pct:.0%} ({c.dev_holding_source})")
    score = 1.0 - c.risk_score
    return StageResult("s2", "developer", passed=not reasons, hard_fail=hard, score=_clamp(score), reasons=reasons, metrics={"risk_score": c.risk_score, "prior_launches": c.prior_launches_db, "prior_rugs": c.prior_rugs_db, "dev_holding_pct": c.dev_holding_pct, "dev_sold_pct": sold_pct, "confidence": c.confidence, "sources": c.sources})


# ----------------------------------------------------------------------------- stage 3
def stage_holders(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    h = ctx.holders
    if h is None or not h.available:
        return StageResult("s3", "holders", passed=True, score=0.4, reasons=[], metrics={}, data_available=False)
    reasons: list[str] = []
    hard = False
    th = thresholds_for(ctx.state.age_at(ctx.now))
    judged_top10 = h.organic_top10_pct if h.organic_top10_pct is not None else h.top10_pct
    if h.largest_holder_pct is not None and h.largest_holder_pct > th.max_largest_holder_pct:
        reasons.append(f"largest holder {h.largest_holder_pct:.0%} > {th.max_largest_holder_pct:.0%} allowed at {th.age_minutes:.0f}m")
        # a single non-dev wallet with a dominant position after the launch minutes is a hard stop
        if h.largest_holder_pct > max(0.5, th.max_largest_holder_pct * 2.5) and h.confidence != "low" and th.phase != "launch":
            hard = True
    if judged_top10 is not None and judged_top10 > th.max_top10_pct:
        reasons.append(f"organic top-10 {judged_top10:.0%} > {th.max_top10_pct:.0%} allowed at {th.age_minutes:.0f}m")
    if h.top20_pct is not None and h.top20_pct > th.max_top20_pct:
        reasons.append(f"top-20 holders {h.top20_pct:.0%} > {th.max_top20_pct:.0%} allowed at {th.age_minutes:.0f}m")
    if h.holder_count is not None and h.holder_count < th.min_holders:
        reasons.append(f"{h.holder_count} holders < {th.min_holders} expected at {th.age_minutes:.0f}m ({h.holder_count_source})")
    if h.bundled_share_pct is not None and h.bundled_share_pct > cfg.bundle_hard_fail_share:
        reasons.append(f"launch bundle still holds {h.bundled_share_pct:.0%} of supply")
        hard = th.age_minutes >= 5  # give the bundle a few minutes to be absorbed; after that it is a stop
    elif h.bundled_wallets >= 5:
        reasons.append(f"{h.bundled_wallets} wallets bought in the same instant at launch (bundle)")
    return StageResult("s3", "holders", passed=not reasons, hard_fail=hard, score=h.score, reasons=reasons, metrics={"holder_count": h.holder_count, "source": h.holder_count_source, "largest_pct": h.largest_holder_pct, "top10_pct": h.top10_pct, "organic_top10_pct": h.organic_top10_pct, "top20_pct": h.top20_pct, "growth_5m": h.holder_growth_5m, "bundled": h.bundled_wallets, "bundled_share_pct": h.bundled_share_pct, "confidence": h.confidence, "thresholds": h.thresholds})


# ----------------------------------------------------------------------------- stage 4
def stage_relationships(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    cl = ctx.clusters
    if cl is None:
        return StageResult("s4", "wallet_relationships", passed=True, score=0.5, data_available=False)
    reasons: list[str] = []
    if cl.largest_cluster_pct > cfg.max_cluster_holding_pct:
        reasons.append(f"a wallet cluster holds {cl.largest_cluster_pct:.0%} of circulating supply")
    if cl.coordinated_wallets > cfg.max_coordinated_wallets:
        reasons.append(f"{cl.coordinated_wallets} coordinated wallets detected")
    if cl.creator_cluster:
        reasons.append(f"{len(cl.creator_cluster)} wallets linked to the creator")
    score = 1.0 - min(1.0, cl.largest_cluster_pct * 2 + 0.05 * cl.coordinated_wallets + (0.3 if cl.creator_cluster else 0))
    hard = cl.largest_cluster_pct > cfg.max_cluster_holding_pct * 2 and cl.confidence == "high"
    return StageResult("s4", "wallet_relationships", passed=not reasons, hard_fail=hard, score=_clamp(score), reasons=reasons, metrics=cl.to_dict())


# ----------------------------------------------------------------------------- stage 5
def stage_liquidity(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    snap = ctx.snapshot
    reasons: list[str] = []
    hard = False
    liq_usd = snap.get("liquidity_usd")
    liq_sol = snap.get("liquidity_sol")
    if liq_usd is None and liq_sol is None:
        return StageResult("s5", "liquidity_sellability", passed=False, score=0.2, reasons=["liquidity unknown"], data_available=False)
    if liq_usd is not None and liq_usd < cfg.min_liquidity_usd:
        reasons.append(f"liquidity ${liq_usd:,.0f} < ${cfg.min_liquidity_usd:,.0f}")
    elif liq_usd is None and liq_sol is not None and liq_sol < cfg.min_liquidity_sol:
        reasons.append(f"liquidity {liq_sol:.1f} SOL < {cfg.min_liquidity_sol} SOL")
    drop = ctx.state.liquidity_drop_pct(300, ctx.now)
    if drop is not None and drop >= cfg.max_liquidity_drop_pct_5m * 100:
        reasons.append(f"liquidity fell {drop:.0f}% in 5 minutes")
        hard = True
    ex = ctx.execution
    sell_impact = None
    round_trip = None
    if ex is not None:
        ref = ex.at_size(cfg.reference_trade_size_usd)
        if ref is not None and ref.available:
            sell_impact = ref.sell_impact_pct
            round_trip = ref.round_trip_cost_pct
            if sell_impact is not None and sell_impact > cfg.max_sell_impact_pct_at_reference_size * 100:
                reasons.append(f"selling ${cfg.reference_trade_size_usd:.0f} would move price {sell_impact:.1f}% (> {cfg.max_sell_impact_pct_at_reference_size:.0%})")
        elif cfg.require_sellability_evidence:
            reasons.append("no execution estimate available (reserves unknown)")
    elif cfg.require_sellability_evidence:
        reasons.append("sellability not evaluated")
    sells_seen = ctx.state.window(900, ctx.now).sells
    if cfg.require_sellability_evidence and sells_seen == 0 and ctx.state.total_trades >= 15:
        reasons.append("no successful sells observed yet (possible honeypot)")
    score = 1.0
    if liq_usd is not None:
        score = _clamp(liq_usd / (cfg.min_liquidity_usd * 4))
    if sell_impact is not None:
        score *= _clamp(1.0 - sell_impact / 25.0)
    if drop:
        score *= _clamp(1.0 - drop / 50.0)
    return StageResult("s5", "liquidity_sellability", passed=not reasons, hard_fail=hard, score=_clamp(score), reasons=reasons, metrics={"liquidity_usd": liq_usd, "liquidity_sol": liq_sol, "sell_impact_pct_ref": sell_impact, "round_trip_cost_pct_ref": round_trip, "liquidity_drop_5m_pct": drop, "sells_15m": sells_seen})


# ----------------------------------------------------------------------------- stage 6
def stage_buyer_quality(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    w5 = ctx.state.window(300, ctx.now)
    reasons: list[str] = []
    th = thresholds_for(ctx.state.age_at(ctx.now))
    if w5.unique_buyers < th.min_unique_buyers_5m:
        reasons.append(f"{w5.unique_buyers} unique buyers in 5m < {th.min_unique_buyers_5m} expected at {th.age_minutes:.0f}m")
    if w5.top_wallet_share > cfg.max_buyer_concentration_pct:
        reasons.append(f"one wallet is {w5.top_wallet_share:.0%} of 5m volume")
    smart = ctx.whales.smart_money_buyers if ctx.whales else 0
    independent = ctx.whales.independent_profitable_buyers if ctx.whales else 0
    new_ratio = (w5.new_buyers / w5.unique_buyers) if w5.unique_buyers else 0.0
    score = _clamp(0.4 * min(1.0, w5.unique_buyers / (max(1, th.min_unique_buyers_5m) * 3)) + 0.2 * new_ratio + 0.3 * min(1.0, smart / 3) + 0.1 * (1.0 - w5.top_wallet_share))
    return StageResult("s6", "buyer_quality", passed=not reasons, safety=False, score=score, reasons=reasons, metrics={"unique_buyers_5m": w5.unique_buyers, "new_buyers_5m": w5.new_buyers, "smart_money_buyers": smart, "independent_profitable_buyers": independent, "top_wallet_share": w5.top_wallet_share, "repeat_buyer_trades": w5.repeat_buyer_trades})


# ----------------------------------------------------------------------------- stage 7
def stage_trading_pressure(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    w5 = ctx.state.window(300, ctx.now)
    w1 = ctx.state.window(60, ctx.now)
    reasons: list[str] = []
    ratio = w5.buy_sell_volume_ratio
    if ratio is not None and ratio < cfg.min_buy_sell_volume_ratio:
        reasons.append(f"buy/sell volume ratio {ratio:.2f} < {cfg.min_buy_sell_volume_ratio}")
    if w5.net_sol < cfg.min_net_sol_flow_5m:
        reasons.append(f"net SOL flow {w5.net_sol:.2f} over 5m is negative")
    liq = ctx.liquidity_sol
    large_sells = 0
    if liq:
        cutoff = ctx.now.timestamp() - 300
        large_sells = sum(1 for t in ctx.state.trades if t.side == "sell" and t.ts.timestamp() >= cutoff and t.sol >= liq * cfg.large_sell_pct_of_liquidity)
        if large_sells > cfg.max_large_sells_5m:
            reasons.append(f"{large_sells} large sells (> {cfg.large_sell_pct_of_liquidity:.0%} of liquidity) in 5m")
    sizes = sorted(w5.trade_sizes)
    median_size = sizes[len(sizes) // 2] if sizes else 0.0
    score = _clamp(0.4 * min(1.0, (ratio or 0) / 3) + 0.3 * _clamp((w5.net_sol / liq) * 5 + 0.5 if liq else 0.5) + 0.2 * min(1.0, w1.buys / 10) + 0.1 * (1.0 if large_sells == 0 else 0.0))
    return StageResult("s7", "trading_pressure", passed=not reasons, safety=False, score=score, reasons=reasons, metrics={"buy_sell_ratio_5m": ratio if ratio != float("inf") else None, "net_sol_5m": w5.net_sol, "buys_1m": w1.buys, "sells_1m": w1.sells, "large_sells_5m": large_sells, "median_trade_sol": median_size})


# ----------------------------------------------------------------------------- stage 8
def stage_momentum(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    s = ctx.state
    reasons: list[str] = []
    m = {k: s.momentum_pct(sec, ctx.now) for k, sec in (("1m", 60), ("3m", 180), ("5m", 300), ("10m", 600), ("15m", 900))}
    m5 = m["5m"]
    if m5 is not None and m5 < cfg.min_momentum_5m_pct:
        reasons.append(f"5m momentum {m5:.1f}% below {cfg.min_momentum_5m_pct}%")
    if m5 is not None and m5 > cfg.max_momentum_5m_pct:
        reasons.append(f"5m momentum {m5:.0f}% is a vertical spike (chasing risk)")
    accel = s.volume_acceleration(ctx.now)
    if accel is not None and accel < cfg.min_volume_acceleration:
        reasons.append(f"volume decelerating (x{accel:.2f} vs prior minutes)")
    vol = s.price_volatility_pct(300, ctx.now)
    if vol is not None and vol > cfg.max_price_volatility_pct:
        reasons.append(f"price volatility {vol:.0f}% too high")
    positive = [v for v in m.values() if v is not None and v > 0]
    sustain = len(positive) / max(1, sum(1 for v in m.values() if v is not None))
    score = _clamp(0.35 * _clamp(((m5 or 0) + 10) / 60) + 0.25 * min(1.0, (accel or 0) / 2.5) + 0.25 * sustain + 0.15 * _clamp(1.0 - (vol or 0) / 80))
    return StageResult("s8", "momentum", passed=not reasons, safety=False, score=score, reasons=reasons, metrics={**{f"momentum_{k}_pct": v for k, v in m.items()}, "volume_acceleration": accel, "volatility_pct": vol, "sustainability": sustain})


# ----------------------------------------------------------------------------- stage 9
def stage_manipulation(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    mp = ctx.manipulation
    if mp is None:
        return StageResult("s9", "manipulation", passed=True, score=0.5, data_available=False)
    reasons: list[str] = []
    hard = mp.hard_fail
    if mp.hard_fail and mp.hard_fail_reason:
        reasons.append(mp.hard_fail_reason)
    if mp.wash_trade_score > cfg.max_wash_trade_score:
        reasons.append(f"wash-trading score {mp.wash_trade_score:.2f}")
    if mp.coordinated_buy_score > cfg.max_coordinated_buy_score:
        reasons.append(f"coordinated buying score {mp.coordinated_buy_score:.2f}")
    if mp.pump_dump_score >= 0.5:
        reasons.append("pump-and-dump pattern")
    if mp.top_wallet_trade_share > cfg.max_same_wallet_trade_share:
        reasons.append(f"single wallet {mp.top_wallet_trade_share:.0%} of volume")
    if ctx.whales and WhaleSignal.WHALE_EXIT_WARNING.value in ctx.whales.signals:
        reasons.append("whale exit warning: " + (ctx.whales.hard_warning or "large whale selling"))
        hard = True
    if ctx.whales and WhaleSignal.INSIDER_SELLING.value in ctx.whales.signals:
        reasons.append(f"insider/developer selling ({ctx.whales.insider_sell_sol + ctx.whales.dev_sell_sol:.2f} SOL)")
    return StageResult("s9", "manipulation", passed=not reasons, hard_fail=hard, score=_clamp(1.0 - mp.overall_score), reasons=reasons, metrics=mp.to_dict())


# ----------------------------------------------------------------------------- stage 10
def stage_ml(ctx: EvaluationContext) -> StageResult:
    cfg = _cfg()
    p = ctx.prediction
    if p is None:
        return StageResult("s10", "ml_ai", passed=True, score=0.5, data_available=False)
    reasons: list[str] = []
    hard = False
    if p.rug_risk is not None and p.rug_risk > cfg.max_rug_risk:
        reasons.append(f"rug-risk {p.rug_risk:.0%} > {cfg.max_rug_risk:.0%}" + (" (experimental model)" if p.is_experimental else ""))
        if not p.is_experimental and p.rug_risk > cfg.max_rug_risk + 0.25:
            hard = True
    if p.p10 is not None and p.p10 < cfg.min_breakout_p10:
        reasons.append(f"P(+10%) {p.p10:.0%} < {cfg.min_breakout_p10:.0%}" + (" (experimental)" if p.is_experimental else ""))
    score = _clamp(0.5 * (1.0 - (p.rug_risk or 0.5)) + 0.5 * (p.p10 or 0.0))
    return StageResult("s10", "ml_ai", passed=not reasons, hard_fail=hard, safety=True, score=score, reasons=reasons, metrics=p.to_dict())


CHEAP_STAGES = [stage_data_validation, stage_developer, stage_holders, stage_liquidity, stage_buyer_quality, stage_trading_pressure, stage_momentum, stage_manipulation]
ALL_STAGES = [stage_data_validation, stage_developer, stage_holders, stage_relationships, stage_liquidity, stage_buyer_quality, stage_trading_pressure, stage_momentum, stage_manipulation, stage_ml]
