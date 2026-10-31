"""Lifecycle-aware evaluation helpers.

A Pump.fun launch is structurally concentrated at minute two (creator + snipers hold most of the float,
a handful of holders, no history) and organic buyers arrive later. Judging a 2-minute-old token with the
thresholds of a 1-hour-old token rejects every launch. Here every threshold is a function of age, and a
token that is not qualified *yet* stays under observation instead of being discarded. Only hard safety
events (rug, dev dump, liquidity pull, confirmed manipulation) are final.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.config.settings import get_settings

PHASE_LAUNCH = "launch"  # < 30 min
PHASE_SURVIVAL = "survival"  # 30 min - 24 h
PHASE_ESTABLISHED = "established"  # 1 - 7 days
PHASE_DEAD = "dead"
PHASE_ARCHIVED = "archived"


def parse_curve(curve: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for part in curve.split(","):
        if ":" not in part:
            continue
        age, value = part.split(":", 1)
        points.append((float(age), float(value)))
    return sorted(points)


def interp_curve(curve: str | list[tuple[float, float]], age_minutes: float) -> float:
    """Piecewise-linear interpolation in log(age). Clamped at both ends."""
    points = parse_curve(curve) if isinstance(curve, str) else curve
    if not points:
        raise ValueError("empty curve")
    age = max(0.01, age_minutes)
    if age <= points[0][0]:
        return points[0][1]
    if age >= points[-1][0]:
        return points[-1][1]
    for (a0, v0), (a1, v1) in zip(points, points[1:], strict=False):
        if a0 <= age <= a1:
            t = (math.log(age) - math.log(a0)) / (math.log(a1) - math.log(a0)) if a1 > a0 else 0.0
            return v0 + t * (v1 - v0)
    return points[-1][1]


def phase_for_age(age_seconds: float) -> str:
    if age_seconds < 1800:
        return PHASE_LAUNCH
    if age_seconds < 86_400:
        return PHASE_SURVIVAL
    return PHASE_ESTABLISHED


@dataclass(frozen=True)
class AgeThresholds:
    age_minutes: float
    phase: str
    max_top10_pct: float
    max_top20_pct: float
    max_largest_holder_pct: float
    min_holders: int
    min_unique_buyers_5m: int

    def to_dict(self) -> dict[str, Any]:
        return {"age_minutes": round(self.age_minutes, 1), "phase": self.phase, "max_top10_pct": round(self.max_top10_pct, 3), "max_top20_pct": round(self.max_top20_pct, 3), "max_largest_holder_pct": round(self.max_largest_holder_pct, 3), "min_holders": self.min_holders, "min_unique_buyers_5m": self.min_unique_buyers_5m}


def thresholds_for(age_seconds: float) -> AgeThresholds:
    f = get_settings().filters
    m = max(0.0, age_seconds) / 60.0
    return AgeThresholds(
        age_minutes=m,
        phase=phase_for_age(age_seconds),
        max_top10_pct=interp_curve(f.top10_pct_curve, m),
        max_top20_pct=interp_curve(f.top20_pct_curve, m),
        max_largest_holder_pct=interp_curve(f.largest_holder_curve, m),
        min_holders=int(round(interp_curve(f.min_holders_curve, m))),
        min_unique_buyers_5m=int(round(interp_curve(f.min_unique_buyers_5m_curve, m))),
    )


# ----------------------------------------------------------------------------- growth metrics
@dataclass
class GrowthPoint:
    """One observation of a token's market state (from a growth check or a tracked snapshot)."""

    at: datetime
    price_usd: float | None
    market_cap_usd: float | None
    liquidity_usd: float | None
    holder_count: int | None = None
    volume_h1_usd: float | None = None
    volume_h24_usd: float | None = None
    buys_h1: int | None = None
    sells_h1: int | None = None
    buys_h24: int | None = None
    sells_h24: int | None = None
    top10_pct: float | None = None
    dev_holding_pct: float | None = None
    migrated: bool = False
    boosted: bool = False


@dataclass
class GrowthMetrics:
    age_hours: float
    phase: str
    points: int
    mcap_change_pct: float | None = None  # vs previous observation
    mcap_change_6h_pct: float | None = None
    liquidity_change_pct: float | None = None
    liquidity_trend_pct: float | None = None  # vs first observation
    holder_growth_per_hour: float | None = None  # holders gained per hour (recent)
    holder_growth_pct_per_hour: float | None = None
    drawdown_from_ath_pct: float | None = None
    higher_lows: bool | None = None
    volume_h1_over_mcap: float | None = None
    volume_h24_over_mcap: float | None = None
    buy_ratio_h1: float | None = None
    buy_ratio_h24: float | None = None
    top10_change_pct: float | None = None  # negative = distributing to organic holders (good)
    liquidity_usd: float | None = None
    market_cap_usd: float | None = None
    migrated: bool = False
    boosted: bool = False
    alive: bool = True
    score: float = 0.0
    hard_fail: bool = False
    reasons: list[str] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 0:
        return None
    return (new / old - 1.0) * 100.0


def compute_growth(points: list[GrowthPoint], launch_time: datetime, ath_price_usd: float | None = None, now: datetime | None = None) -> GrowthMetrics:
    """Growth assessment from chronological observations (last one = current)."""
    g = get_settings().growth
    now = now or datetime.now(UTC)
    age_h = max(0.0, (now - launch_time).total_seconds() / 3600.0)
    m = GrowthMetrics(age_hours=round(age_h, 3), phase=phase_for_age(age_h * 3600), points=len(points))
    if not points:
        m.alive = False
        m.reasons.append("no market data")
        return m
    cur = points[-1]
    prev = points[-2] if len(points) >= 2 else None
    first = points[0]
    m.liquidity_usd, m.market_cap_usd, m.migrated, m.boosted = cur.liquidity_usd, cur.market_cap_usd, cur.migrated, cur.boosted
    # ---- liveness ------------------------------------------------------------------------------
    liq = cur.liquidity_usd or 0.0
    if liq < g.dead_liquidity_usd and (cur.volume_h24_usd or 0.0) < 50.0:
        m.alive = False
        m.reasons.append(f"illiquid (${liq:,.0f}) and no volume")
    # ---- changes ----------------------------------------------------------------------------------
    if prev is not None:
        m.mcap_change_pct = _pct(cur.market_cap_usd, prev.market_cap_usd)
        m.liquidity_change_pct = _pct(cur.liquidity_usd, prev.liquidity_usd)
        dt_h = max(0.05, (cur.at - prev.at).total_seconds() / 3600.0)
        if cur.holder_count is not None and prev.holder_count is not None:
            m.holder_growth_per_hour = (cur.holder_count - prev.holder_count) / dt_h
            if prev.holder_count > 0:
                m.holder_growth_pct_per_hour = m.holder_growth_per_hour / prev.holder_count * 100.0
        if cur.top10_pct is not None and prev.top10_pct is not None:
            m.top10_change_pct = (cur.top10_pct - prev.top10_pct) * 100.0
    # medium-horizon change: vs the most recent observation that is at least 3h old (6h when available)
    older = [p for p in points if (cur.at - p.at).total_seconds() >= 3 * 3600]
    six = [p for p in older if (cur.at - p.at).total_seconds() >= 6 * 3600]
    ref = (six or older)
    if ref:
        m.mcap_change_6h_pct = _pct(cur.market_cap_usd, ref[-1].market_cap_usd)
    m.liquidity_trend_pct = _pct(cur.liquidity_usd, first.liquidity_usd) if first is not cur else None
    prices = [p.price_usd for p in points if p.price_usd]
    ath = max([ath_price_usd or 0.0, *prices]) if (prices or ath_price_usd) else None
    if ath and cur.price_usd:
        m.drawdown_from_ath_pct = (cur.price_usd / ath - 1.0) * 100.0
    # higher lows: compare the minimum of the last third vs the minimum of the middle third
    if len(prices) >= 6:
        third = len(prices) // 3
        m.higher_lows = min(prices[-third:]) > min(prices[third : 2 * third]) and min(prices[third : 2 * third]) > min(prices[:third]) * 0.9
    if cur.market_cap_usd:
        m.volume_h1_over_mcap = (cur.volume_h1_usd or 0.0) / cur.market_cap_usd
        m.volume_h24_over_mcap = (cur.volume_h24_usd or 0.0) / cur.market_cap_usd
    if cur.buys_h1 is not None and cur.sells_h1 is not None and (cur.buys_h1 + cur.sells_h1) > 0:
        m.buy_ratio_h1 = cur.buys_h1 / (cur.buys_h1 + cur.sells_h1)
    if cur.buys_h24 is not None and cur.sells_h24 is not None and (cur.buys_h24 + cur.sells_h24) > 0:
        m.buy_ratio_h24 = cur.buys_h24 / (cur.buys_h24 + cur.sells_h24)
    # ---- hard failures --------------------------------------------------------------------------------
    if m.liquidity_change_pct is not None and m.liquidity_change_pct <= -70.0 and (prev.liquidity_usd or 0) > 2_000:  # type: ignore[union-attr]
        m.hard_fail = True
        m.reasons.append(f"liquidity collapsed {m.liquidity_change_pct:.0f}% since last check")
    if m.drawdown_from_ath_pct is not None and m.drawdown_from_ath_pct <= -92.0 and age_h > 1:
        m.hard_fail = True
        m.reasons.append(f"price {abs(m.drawdown_from_ath_pct):.0f}% below its high: dead chart")
    # ---- score ------------------------------------------------------------------------------------------
    score = 0.0
    # 1) liquidity depth and direction (0..0.25)
    if liq >= 50_000:
        score += 0.15
    elif liq >= 15_000:
        score += 0.11
    elif liq >= 8_000:
        score += 0.07
    if m.liquidity_trend_pct is not None and m.liquidity_trend_pct > 25:
        score += 0.10
        m.positives.append(f"liquidity +{m.liquidity_trend_pct:.0f}% since first seen")
    elif m.liquidity_change_pct is not None and m.liquidity_change_pct < -25:
        score -= 0.10
        m.reasons.append(f"liquidity -{abs(m.liquidity_change_pct):.0f}% since last check")
    # 2) holders growing and distributing (0..0.25)
    if m.holder_growth_pct_per_hour is not None:
        if m.holder_growth_pct_per_hour >= 25:
            score += 0.18
            m.positives.append(f"holders +{m.holder_growth_pct_per_hour:.0f}%/h")
        elif m.holder_growth_pct_per_hour >= 8:
            score += 0.10
            m.positives.append(f"holders +{m.holder_growth_pct_per_hour:.0f}%/h")
        elif m.holder_growth_pct_per_hour < -5:
            score -= 0.08
            m.reasons.append("holders shrinking")
    if m.top10_change_pct is not None and m.top10_change_pct <= -2.0:
        score += 0.07
        m.positives.append("top-10 share falling (distribution to organic holders)")
    # 3) price structure (0..0.25)
    if m.drawdown_from_ath_pct is not None:
        if m.drawdown_from_ath_pct > -25:
            score += 0.12
        elif m.drawdown_from_ath_pct > -50:
            score += 0.05
        else:
            score -= 0.10
            m.reasons.append(f"{abs(m.drawdown_from_ath_pct):.0f}% below its high")
    if m.higher_lows:
        score += 0.10
        m.positives.append("higher lows")
    if m.mcap_change_6h_pct is not None and m.mcap_change_6h_pct > 20:
        score += 0.05
        m.positives.append(f"mcap +{m.mcap_change_6h_pct:.0f}% over 6h")
    # 4) activity (0..0.2)
    if m.volume_h24_over_mcap is not None:
        if 0.15 <= m.volume_h24_over_mcap <= 8.0:
            score += 0.10
        elif 0.05 <= m.volume_h24_over_mcap < 0.15:
            score += 0.04
        elif m.volume_h24_over_mcap > 15.0:
            score -= 0.05
            m.reasons.append("turnover far above market cap (churn / wash)")
        elif m.volume_h24_over_mcap < 0.05 and age_h > 2:
            score -= 0.08
            m.reasons.append("almost no trading")
    if m.buy_ratio_h1 is not None and m.buy_ratio_h1 >= 0.55 and (cur.buys_h1 or 0) >= 10:
        score += 0.06
        m.positives.append(f"{m.buy_ratio_h1:.0%} of last-hour trades are buys")
    elif m.buy_ratio_h1 is not None and m.buy_ratio_h1 <= 0.40 and (cur.sells_h1 or 0) >= 10:
        score -= 0.06
        m.reasons.append("sell pressure last hour")
    # 5) survivorship (0..0.15)
    if cur.migrated:
        score += 0.10
        m.positives.append("completed bonding curve (migrated)")
    if age_h >= 24 and liq >= 10_000:
        score += 0.05
        m.positives.append("alive after 24h with real liquidity")
    if cur.dev_holding_pct is not None and cur.dev_holding_pct > 0.15:
        score -= 0.10
        m.reasons.append(f"developer still holds {cur.dev_holding_pct:.0%}")
    if cur.boosted:
        m.positives.append("has a DexScreener profile/boost (team is marketing)")
    if not m.alive:
        score = 0.0
    m.score = round(max(0.0, min(1.0, score)), 4)
    return m
