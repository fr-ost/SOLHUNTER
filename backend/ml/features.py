"""Feature vector generation for the ML models (point-in-time, no future information).

Every feature is computed from data available at the snapshot time. Unavailable values are
NaN (XGBoost handles missing values natively); never substituted with zero.
"""

from __future__ import annotations

import math
from typing import Any

from backend.filters.base import EvaluationContext

NAN = float("nan")

FEATURE_NAMES: list[str] = [
    "age_seconds",
    "pool_is_pump",
    "bonding_progress",
    "log_market_cap_usd",
    "log_liquidity_usd",
    "liquidity_sol",
    "trades_total",
    "buys_1m",
    "sells_1m",
    "buys_5m",
    "sells_5m",
    "buy_sell_ratio_5m",
    "volume_sol_1m",
    "volume_sol_5m",
    "volume_acceleration",
    "net_sol_flow_5m",
    "net_flow_pct_liquidity_5m",
    "unique_buyers_5m",
    "unique_sellers_5m",
    "new_buyers_5m",
    "unique_buyers_total",
    "trades_per_buyer_5m",
    "log_holder_count",
    "largest_holder_pct",
    "top10_pct",
    "top20_pct",
    "dev_holding_pct",
    "dev_sold_pct",
    "dev_sells",
    "initial_buy_sol",
    "creator_prior_launches",
    "creator_prior_rugs",
    "creator_risk_score",
    "holder_growth_5m",
    "bundled_wallets",
    "momentum_1m_pct",
    "momentum_3m_pct",
    "momentum_5m_pct",
    "momentum_10m_pct",
    "momentum_15m_pct",
    "price_volatility_5m",
    "drawdown_from_peak_pct",
    "wash_trade_score",
    "coordinated_buy_score",
    "pump_dump_score",
    "manipulation_overall",
    "whale_net_flow_5m_pct_liq",
    "whales_buying_5m",
    "whales_selling_5m",
    "smart_money_buyers",
    "independent_profitable_buyers",
    "accumulation_score",
    "distribution_score",
    "insider_sell_sol",
    "largest_cluster_pct",
    "coordinated_wallets",
    "sell_impact_pct_ref",
    "round_trip_cost_pct_ref",
    "largest_sell_pct_liq_5m",
    "top_wallet_share_5m",
    "repeat_buyer_ratio_5m",
    "data_quality_score",
    # lifecycle / growth (NaN while a token is only minutes old and has no growth history)
    "age_hours",
    "migrated",
    "growth_score",
    "mcap_change_pct",
    "mcap_change_6h_pct",
    "liquidity_change_pct",
    "liquidity_trend_pct",
    "holder_growth_pct_per_hour",
    "drawdown_from_ath_growth_pct",
    "higher_lows",
    "volume_h1_over_mcap",
    "volume_h24_over_mcap",
    "buy_ratio_h1",
    "buy_ratio_h24",
    "top10_change_growth_pct",
    "boosted",
]


def _f(x: Any) -> float:
    try:
        if x is None:
            return NAN
        v = float(x)
        return v if math.isfinite(v) else NAN
    except (TypeError, ValueError):
        return NAN


def _log(x: Any) -> float:
    v = _f(x)
    return math.log1p(v) if not math.isnan(v) and v >= 0 else NAN


def build_features(ctx: EvaluationContext) -> dict[str, float]:
    s = ctx.state
    snap = ctx.snapshot
    now = ctx.now
    w1, w5 = s.window(60, now), s.window(300, now)
    liq_sol = _f(snap.get("liquidity_sol"))
    c = ctx.creator
    h = ctx.holders
    mp = ctx.manipulation
    w = ctx.whales
    cl = ctx.clusters
    ex = ctx.execution
    ref = ex.at_size(250.0) if ex is not None else None
    peak = max(s._price_vals[-300:]) if s._price_vals else None  # noqa: SLF001
    drawdown = ((s.last_price / peak) - 1.0) * 100.0 if (peak and s.last_price) else NAN
    quality = snap.get("data_quality") or {}
    q_score = 1.0
    if quality.get("sol_usd_missing"):
        q_score -= 0.3
    if quality.get("holder_count_source") == "stream_estimate":
        q_score -= 0.2
    if (quality.get("stream_age_seconds") or 0) > 60:
        q_score -= 0.2
    dev = s.dev_activity()
    ratio = w5.buy_sell_volume_ratio
    feats = {
        "age_seconds": _f(snap.get("age_seconds")),
        "pool_is_pump": 1.0 if s.pool_kind == "pump" else 0.0,
        "bonding_progress": _f(snap.get("bonding_progress")),
        "log_market_cap_usd": _log(snap.get("market_cap_usd")),
        "log_liquidity_usd": _log(snap.get("liquidity_usd")),
        "liquidity_sol": liq_sol,
        "trades_total": float(s.total_trades),
        "buys_1m": float(w1.buys),
        "sells_1m": float(w1.sells),
        "buys_5m": float(w5.buys),
        "sells_5m": float(w5.sells),
        "buy_sell_ratio_5m": min(20.0, ratio) if ratio is not None and math.isfinite(ratio) else (20.0 if ratio == float("inf") else NAN),
        "volume_sol_1m": w1.volume_sol,
        "volume_sol_5m": w5.volume_sol,
        "volume_acceleration": _f(s.volume_acceleration(now)),
        "net_sol_flow_5m": w5.net_sol,
        "net_flow_pct_liquidity_5m": (w5.net_sol / liq_sol) if liq_sol and liq_sol > 0 else NAN,
        "unique_buyers_5m": float(w5.unique_buyers),
        "unique_sellers_5m": float(w5.unique_sellers),
        "new_buyers_5m": float(w5.new_buyers),
        "unique_buyers_total": float(len(s.buyers_total)),
        "trades_per_buyer_5m": (w5.buys / w5.unique_buyers) if w5.unique_buyers else NAN,
        "log_holder_count": _log(h.holder_count if h else snap.get("holder_count")),
        "largest_holder_pct": _f(h.largest_holder_pct if h else snap.get("largest_holder_pct")),
        "top10_pct": _f(h.top10_pct if h else snap.get("top10_pct")),
        "top20_pct": _f(h.top20_pct if h else snap.get("top20_pct")),
        "dev_holding_pct": _f(c.dev_holding_pct if c else (h.dev_holding_pct if h else None)),
        "dev_sold_pct": _f(dev.get("sold_pct")),
        "dev_sells": _f(dev.get("sells")),
        "initial_buy_sol": _f(s.initial_buy_sol),
        "creator_prior_launches": _f(c.prior_launches_db if c else None),
        "creator_prior_rugs": _f(c.prior_rugs_db if c else None),
        "creator_risk_score": _f(c.risk_score if c else None),
        "holder_growth_5m": _f(h.holder_growth_5m if h else None),
        "bundled_wallets": _f(h.bundled_wallets if h else None),
        "momentum_1m_pct": _f(s.momentum_pct(60, now)),
        "momentum_3m_pct": _f(s.momentum_pct(180, now)),
        "momentum_5m_pct": _f(s.momentum_pct(300, now)),
        "momentum_10m_pct": _f(s.momentum_pct(600, now)),
        "momentum_15m_pct": _f(s.momentum_pct(900, now)),
        "price_volatility_5m": _f(s.price_volatility_pct(300, now)),
        "drawdown_from_peak_pct": drawdown,
        "wash_trade_score": _f(mp.wash_trade_score if mp else None),
        "coordinated_buy_score": _f(mp.coordinated_buy_score if mp else None),
        "pump_dump_score": _f(mp.pump_dump_score if mp else None),
        "manipulation_overall": _f(mp.overall_score if mp else None),
        "whale_net_flow_5m_pct_liq": (w.whale_net_flow_5m / liq_sol) if (w and liq_sol and liq_sol > 0) else NAN,
        "whales_buying_5m": _f(w.whales_buying_5m if w else None),
        "whales_selling_5m": _f(w.whales_selling_5m if w else None),
        "smart_money_buyers": _f(w.smart_money_buyers if w else None),
        "independent_profitable_buyers": _f(w.independent_profitable_buyers if w else None),
        "accumulation_score": _f(w.accumulation_score if w else None),
        "distribution_score": _f(w.distribution_score if w else None),
        "insider_sell_sol": _f((w.insider_sell_sol + w.dev_sell_sol) if w else None),
        "largest_cluster_pct": _f(cl.largest_cluster_pct if cl else None),
        "coordinated_wallets": _f(cl.coordinated_wallets if cl else None),
        "sell_impact_pct_ref": _f(ref.sell_impact_pct if (ref and ref.available) else None),
        "round_trip_cost_pct_ref": _f(ref.round_trip_cost_pct if (ref and ref.available) else None),
        "largest_sell_pct_liq_5m": (w5.largest_sell_sol / liq_sol) if liq_sol and liq_sol > 0 else NAN,
        "top_wallet_share_5m": w5.top_wallet_share,
        "repeat_buyer_ratio_5m": (w5.repeat_buyer_trades / w5.buys) if w5.buys else NAN,
        "data_quality_score": q_score,
    }
    feats.update(_growth_feature_values(s.growth, age_hours=_f(snap.get("age_seconds")) / 3600.0 if snap.get("age_seconds") is not None else NAN, migrated=bool(s.migrated_at)))
    return {name: feats[name] for name in FEATURE_NAMES}


def _growth_feature_values(growth: dict[str, Any] | None, age_hours: float, migrated: bool) -> dict[str, float]:
    g = growth or {}
    return {
        "age_hours": age_hours,
        "migrated": 1.0 if (migrated or g.get("migrated")) else 0.0,
        "growth_score": _f(g.get("score")),
        "mcap_change_pct": _f(g.get("mcap_change_pct")),
        "mcap_change_6h_pct": _f(g.get("mcap_change_6h_pct")),
        "liquidity_change_pct": _f(g.get("liquidity_change_pct")),
        "liquidity_trend_pct": _f(g.get("liquidity_trend_pct")),
        "holder_growth_pct_per_hour": _f(g.get("holder_growth_pct_per_hour")),
        "drawdown_from_ath_growth_pct": _f(g.get("drawdown_from_ath_pct")),
        "higher_lows": (1.0 if g.get("higher_lows") else 0.0) if g.get("higher_lows") is not None else NAN,
        "volume_h1_over_mcap": _f(g.get("volume_h1_over_mcap")),
        "volume_h24_over_mcap": _f(g.get("volume_h24_over_mcap")),
        "buy_ratio_h1": _f(g.get("buy_ratio_h1")),
        "buy_ratio_h24": _f(g.get("buy_ratio_h24")),
        "top10_change_growth_pct": _f(g.get("top10_change_pct")),
        "boosted": 1.0 if g.get("boosted") else 0.0,
    }


def growth_features(metrics: Any, point: Any, age_hours: float) -> dict[str, float | None]:
    """Feature vector for a growth-check snapshot (no real-time stream data available)."""
    feats = {name: NAN for name in FEATURE_NAMES}
    feats.update(
        {
            "log_market_cap_usd": _log(point.market_cap_usd),
            "log_liquidity_usd": _log(point.liquidity_usd),
            "log_holder_count": _log(point.holder_count),
            "top10_pct": _f(point.top10_pct),
            "dev_holding_pct": _f(point.dev_holding_pct),
            "pool_is_pump": 0.0 if point.migrated else 1.0,
            "bonding_progress": 1.0 if point.migrated else NAN,
            "data_quality_score": 0.6,
        }
    )
    feats.update(_growth_feature_values(metrics.to_dict(), age_hours, point.migrated))
    return features_json_safe(feats)


def features_to_vector(features: dict[str, float], names: list[str] | None = None) -> list[float]:
    names = names or FEATURE_NAMES
    return [float(features.get(n, NAN)) if features.get(n) is not None else NAN for n in names]


def features_json_safe(features: dict[str, float]) -> dict[str, float | None]:
    return {k: (None if (v is None or (isinstance(v, float) and math.isnan(v))) else v) for k, v in features.items()}
