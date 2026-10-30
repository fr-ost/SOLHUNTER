"""Builds the compact, structured context sent to the GPT analyst (and stored with the analysis)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backend import __version__
from backend.filters.base import EvaluationContext, PipelineResult
from backend.ranking.ranker import RankingResult


def _r(x: Any, nd: int = 4) -> Any:
    if isinstance(x, float):
        return round(x, nd)
    return x


def build_analyst_context(ctx: EvaluationContext, result: PipelineResult, ranking: RankingResult) -> dict[str, Any]:
    s = ctx.state
    snap = ctx.snapshot
    w5, w1 = s.window(300, ctx.now), s.window(60, ctx.now)
    p = ctx.prediction
    ex = ctx.execution
    whales = ctx.whales
    holders = ctx.holders
    creator = ctx.creator
    quality = snap.get("data_quality") or {}
    context: dict[str, Any] = {
        "scanner_version": __version__,
        "as_of": ctx.now.isoformat(),
        "token": {"mint": s.mint, "name": s.name, "symbol": s.symbol, "age_minutes": round((snap.get("age_seconds") or 0) / 60, 1), "venue": s.pool_kind, "migrated": bool(s.migrated_at), "creator": s.creator},
        "market": {
            "price_usd": _r(snap.get("price_usd"), 10),
            "price_sol": _r(snap.get("price_sol"), 12),
            "market_cap_usd": _r(snap.get("market_cap_usd"), 0),
            "fdv_usd": _r(snap.get("fdv_usd"), 0),
            "liquidity_usd": _r(snap.get("liquidity_usd"), 0),
            "liquidity_sol": _r(snap.get("liquidity_sol"), 2),
            "bonding_progress": _r(snap.get("bonding_progress"), 3),
            "sol_usd": snap.get("sol_usd"),
        },
        "trading_activity": {
            "trades_total": s.total_trades,
            "last_1m": {"buys": w1.buys, "sells": w1.sells, "volume_sol": _r(w1.volume_sol, 3), "net_sol": _r(w1.net_sol, 3)},
            "last_5m": {"buys": w5.buys, "sells": w5.sells, "buy_volume_sol": _r(w5.buy_sol, 3), "sell_volume_sol": _r(w5.sell_sol, 3), "net_sol": _r(w5.net_sol, 3), "unique_buyers": w5.unique_buyers, "unique_sellers": w5.unique_sellers, "new_buyers": w5.new_buyers, "largest_buy_sol": _r(w5.largest_buy_sol, 3), "largest_sell_sol": _r(w5.largest_sell_sol, 3), "top_wallet_volume_share": _r(w5.top_wallet_share, 3)},
            "unique_buyers_total": len(s.buyers_total),
            "volume_acceleration": _r(s.volume_acceleration(ctx.now), 3),
        },
        "momentum_pct": {k: _r(s.momentum_pct(sec, ctx.now), 2) for k, sec in (("1m", 60), ("3m", 180), ("5m", 300), ("10m", 600), ("15m", 900))},
        "holders": holders.to_dict() if holders else {"available": False},
        "developer": creator.to_dict() if creator else {"known": False},
        "whale_intelligence": (
            {
                "primary_signal": whales.primary_signal,
                "signals": whales.signals,
                "whale_net_flow_sol": {"1m": _r(whales.whale_net_flow_1m, 3), "5m": _r(whales.whale_net_flow_5m, 3), "15m": _r(whales.whale_net_flow_15m, 3), "1h": _r(whales.whale_net_flow_1h, 3)},
                "whales_buying_5m": whales.whales_buying_5m,
                "whales_selling_5m": whales.whales_selling_5m,
                "whales_holding": whales.whales_holding,
                "whales_distributing": whales.whales_distributing,
                "smart_money_buyers": whales.smart_money_buyers,
                "smart_money_sellers": whales.smart_money_sellers,
                "independent_profitable_buyers": whales.independent_profitable_buyers,
                "insider_sell_sol": _r(whales.insider_sell_sol, 3),
                "dev_sell_sol": _r(whales.dev_sell_sol, 3),
                "accumulation_trend": whales.accumulation_trend,
                "accumulation_score": whales.accumulation_score,
                "distribution_score": whales.distribution_score,
                "data_confidence": whales.data_confidence,
                "notes": whales.notes,
                "recent_activity": [a.to_dict() for a in whales.activities[:12]],
            }
            if whales
            else {"available": False}
        ),
        "wallet_clusters": ctx.clusters.to_dict() if ctx.clusters else {"available": False},
        "manipulation": ctx.manipulation.to_dict() if ctx.manipulation else {"available": False},
        "ml_predictions": (
            {**p.to_dict(), "warning": "EXPERIMENTAL - not validated" if p.is_experimental else "validated on historical data (see uncertainty)"}
            if p
            else {"available": False}
        ),
        "sellability": ex.to_dict() if ex else {"available": False},
        "filter_results": {"rejected": result.rejected, "hard_fail": result.hard_fail, "rejection_reasons": result.rejection_reasons, "quality_failures": result.quality_failures, "stage_scores": result.to_dict()["stage_scores"]},
        "ranking": {"overall_score": ranking.overall_score, "components": ranking.components, "penalties": ranking.penalties, "confidence": ranking.confidence, "status": ranking.status},
        "data_freshness": {"stream_age_seconds": quality.get("stream_age_seconds"), "holder_count_source": quality.get("holder_count_source"), "sol_usd_missing": bool(quality.get("sol_usd_missing")), "dexscreener_stale": bool(quality.get("dexscreener_stale")), "sources": snap.get("data_sources")},
        "risk_indicators": [e for e in s.flags.get("risk_events", [])][-10:],
    }
    return json.loads(json.dumps(context, default=str))


def context_hash(context: dict[str, Any]) -> str:
    """Hash of the decision-relevant parts (ignores timestamps) for caching/deduplication."""
    relevant = {k: v for k, v in context.items() if k not in {"as_of", "data_freshness"}}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, default=str).encode()).hexdigest()[:32]
