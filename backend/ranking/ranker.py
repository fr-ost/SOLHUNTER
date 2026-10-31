"""Configurable weighted candidate ranking with component transparency and confidence levels."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.config.settings import RankingSettings, get_settings
from backend.filters.base import EvaluationContext, PipelineResult
from backend.models import GptRecommendation, WhaleSignal

COMPONENTS = [
    "safety",
    "liquidity",
    "smart_money",
    "whale_accumulation",
    "buyer_quality",
    "volume_acceleration",
    "momentum",
    "ml_breakout",
    "expected_return",
    "risk_reward",
    "gpt",
    "growth",
]


@dataclass
class RankingResult:
    overall_score: float
    components: dict[str, float]
    weights: dict[str, float]
    penalties: dict[str, float]
    confidence: str
    status: str  # candidate / watchlist / rejected
    explanation: list[str] = field(default_factory=list)
    missing_components: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _c(x: float | None, default: float = 50.0) -> float:
    if x is None:
        return default
    return max(0.0, min(100.0, x))


def _gpt_component(recommendation: str | None, probability_hint: float | None) -> float | None:
    if recommendation is None:
        return None
    base = {
        GptRecommendation.STRONG_CANDIDATE.value: 85.0,
        GptRecommendation.WATCHLIST.value: 60.0,
        GptRecommendation.INSUFFICIENT_DATA.value: 45.0,
        GptRecommendation.HIGH_RISK.value: 20.0,
        GptRecommendation.REJECT.value: 5.0,
    }.get(recommendation, 50.0)
    if probability_hint is not None:
        base = 0.7 * base + 0.3 * probability_hint * 100.0
    return base


class Ranker:
    def __init__(self, settings: RankingSettings | None = None) -> None:
        self.cfg = settings or get_settings().ranking

    def rank(self, ctx: EvaluationContext, pipeline: PipelineResult, gpt_recommendation: str | None = None, gpt_probability: float | None = None) -> RankingResult:
        cfg = self.cfg
        p = ctx.prediction
        w = ctx.whales
        ex = ctx.execution
        scores = pipeline.stage_scores
        comps: dict[str, float | None] = {}
        explanation: list[str] = []

        # --- safety: creator, holders, relationships, manipulation, rug risk --------------------
        safety_parts = [scores.get("s2"), scores.get("s3"), scores.get("s4"), scores.get("s9")]
        if p is not None and p.rug_risk is not None:
            safety_parts.append(1.0 - p.rug_risk)
        safety_known = [s for s in safety_parts if s is not None]
        comps["safety"] = 100.0 * sum(safety_known) / len(safety_known) if safety_known else None

        # --- liquidity & sellability ----------------------------------------------------------------
        liq = scores.get("s5")
        comps["liquidity"] = 100.0 * liq if liq is not None else None

        # --- smart money ------------------------------------------------------------------------------
        if w is not None:
            sm = min(1.0, 0.35 * w.smart_money_buyers + 0.25 * w.independent_profitable_buyers)
            if WhaleSignal.SMART_MONEY_CONSENSUS.value in w.signals:
                sm = max(sm, 0.9)
            if w.smart_money_sellers:
                sm *= 0.5
            comps["smart_money"] = 100.0 * sm if (w.smart_money_buyers or w.smart_money_sellers or w.data_confidence != "low") else 15.0
            acc = w.accumulation_score - w.distribution_score
            comps["whale_accumulation"] = _c(50.0 + 50.0 * acc)
        else:
            comps["smart_money"] = None
            comps["whale_accumulation"] = None

        # --- buyer quality / volume / momentum from stage scores ------------------------------------
        comps["buyer_quality"] = 100.0 * scores["s6"] if "s6" in scores else None
        accel = ctx.state.volume_acceleration(ctx.now)
        comps["volume_acceleration"] = _c(min(100.0, 35.0 * (accel or 0.0))) if accel is not None else None
        comps["momentum"] = 100.0 * scores["s8"] if "s8" in scores else None

        # --- ML ---------------------------------------------------------------------------------------
        if p is not None and p.p10 is not None:
            breakout = 100.0 * (0.5 * p.p10 + 0.3 * (p.p15 or 0.0) + 0.2 * (p.p20 or 0.0)) / 1.0
            if p.is_experimental:
                breakout = 0.5 * breakout + 0.5 * 40.0  # shrink toward a neutral-low prior
                explanation.append("ML predictions are experimental (insufficient validated history)")
            comps["ml_breakout"] = _c(breakout)
            er = p.expected_return_pct
            comps["expected_return"] = _c(50.0 + 2.5 * er) if er is not None else None
            if er is not None and p.expected_downside_pct:
                rr = er / max(0.5, abs(p.expected_downside_pct))
                comps["risk_reward"] = _c(30.0 + 25.0 * rr)
            else:
                comps["risk_reward"] = None
        else:
            comps["ml_breakout"] = comps["expected_return"] = comps["risk_reward"] = None

        comps["gpt"] = _gpt_component(gpt_recommendation, gpt_probability)
        growth = ctx.state.growth
        comps["growth"] = _c(100.0 * float(growth.get("score") or 0.0)) if growth else None
        if p is not None and getattr(p, "p_2x_24h", None) is not None and comps.get("ml_breakout") is not None:
            comps["ml_breakout"] = _c(0.7 * comps["ml_breakout"] + 0.3 * 100.0 * float(p.p_2x_24h))

        # --- weighted average over available components ---------------------------------------------
        weights = {name: getattr(cfg, f"weight_{name}") for name in COMPONENTS}
        available = {k: v for k, v in comps.items() if v is not None}
        total_w = sum(weights[k] for k in available) or 1.0
        base = sum(weights[k] * v for k, v in available.items()) / total_w
        missing = [k for k in COMPONENTS if comps.get(k) is None]

        # --- penalties --------------------------------------------------------------------------------
        penalties: dict[str, float] = {}
        h = ctx.holders
        if h is not None and h.top10_pct is not None and h.top10_pct > get_settings().filters.max_top10_pct:
            penalties["high_concentration"] = 8.0 + 40.0 * (h.top10_pct - get_settings().filters.max_top10_pct)
        if w is not None:
            if WhaleSignal.INSIDER_SELLING.value in w.signals:
                penalties["insider_selling"] = 15.0
            if WhaleSignal.WHALE_DISTRIBUTION.value in w.signals:
                penalties["whale_distribution"] = 10.0
            if WhaleSignal.WHALE_EXIT_WARNING.value in w.signals:
                penalties["whale_exit_warning"] = 25.0
            if WhaleSignal.WHALE_ACCUMULATION.value in w.signals and WhaleSignal.WHALE_DISTRIBUTION.value in w.signals:
                penalties["conflicting_whale_signals"] = 6.0
        drop = ctx.state.liquidity_drop_pct(300, ctx.now)
        if drop and drop > 10:
            penalties["liquidity_instability"] = min(20.0, drop / 2)
        s1 = pipeline.result("s1")
        if s1 is not None and s1.reasons:
            penalties["unreliable_data"] = 5.0 * len(s1.reasons)
        mp = ctx.manipulation
        if mp is not None and mp.overall_score > 0.4:
            penalties["manipulation_indicators"] = 30.0 * mp.overall_score
        if ex is not None:
            ref = ex.at_size(get_settings().filters.reference_trade_size_usd)
            if ref is None or not ref.available:
                penalties["sellability_unknown"] = 8.0
            else:
                if ref.sell_impact_pct is not None and ref.sell_impact_pct > get_settings().execution.max_acceptable_slippage_pct:
                    penalties["excessive_slippage"] = min(25.0, ref.sell_impact_pct)
                if ref.round_trip_cost_pct is not None and ref.round_trip_cost_pct > 12:
                    penalties["poor_sellability"] = min(20.0, ref.round_trip_cost_pct - 8)
        if "s8" in scores and "s7" in scores and scores["s8"] > 0.7 and scores["s7"] < 0.3:
            penalties["conflicting_signals"] = 5.0
        overall = max(0.0, min(100.0, base - sum(penalties.values())))

        # --- confidence --------------------------------------------------------------------------------
        conf_points = 0
        if h is not None and h.confidence == "high":
            conf_points += 1
        if w is not None and w.data_confidence in {"high", "medium"}:
            conf_points += 1
        if p is not None and not p.is_experimental:
            conf_points += 1
        if ex is not None and ex.source in {"stream_virtual_reserves", "dexscreener_reserves"}:
            conf_points += 1
        if ctx.state.total_trades >= 40:
            conf_points += 1
        if s1 is not None and s1.reasons:
            conf_points -= 1
        confidence = "high" if conf_points >= 4 else ("medium" if conf_points >= 2 else "low")

        # --- status -----------------------------------------------------------------------------------
        age_minutes = ctx.state.age_at(ctx.now) / 60.0
        too_young = age_minutes < cfg.min_candidate_age_minutes and not ctx.state.flags.get("manual")
        if pipeline.hard_fail:
            status = "rejected"
        elif pipeline.rejected:
            status = "screening"  # not qualified yet; keeps being re-evaluated while it lives
        elif pipeline.candidate_eligible and overall >= cfg.candidate_min_score and not too_young:
            status = "candidate"
        elif overall >= cfg.watchlist_min_score:
            status = "watchlist"
            if too_young and pipeline.candidate_eligible and overall >= cfg.candidate_min_score:
                explanation.append(f"candidate-grade score but only {age_minutes:.0f}m old (min {cfg.min_candidate_age_minutes}m)")
        else:
            status = "screening"
        top = sorted(available.items(), key=lambda kv: kv[1] * weights[kv[0]], reverse=True)[:3]
        explanation.extend(f"{k}: {v:.0f}" for k, v in top)
        explanation.extend(f"penalty {k}: -{v:.0f}" for k, v in sorted(penalties.items(), key=lambda kv: -kv[1])[:3])
        return RankingResult(
            overall_score=round(overall, 2),
            components={k: round(v, 2) for k, v in available.items()},
            weights={k: weights[k] for k in available},
            penalties={k: round(v, 2) for k, v in penalties.items()},
            confidence=confidence,
            status=status,
            explanation=explanation,
            missing_components=missing,
        )
