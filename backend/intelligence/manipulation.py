"""Stage 9 - manipulation detection from the trade stream (wash trading, coordination, pump & dump...)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.config.settings import get_settings
from backend.scanner.state import TokenState


@dataclass
class ManipulationAssessment:
    wash_trade_score: float = 0.0
    coordinated_buy_score: float = 0.0
    artificial_volume_score: float = 0.0
    pump_dump_score: float = 0.0
    liquidity_withdrawal_pct: float | None = None
    top_wallet_trade_share: float = 0.0
    round_trip_wallets: int = 0
    flags: list[str] = field(default_factory=list)
    hard_fail: bool = False
    hard_fail_reason: str | None = None
    overall_score: float = 0.0  # 0 = clean .. 1 = manipulated
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def assess_manipulation(state: TokenState, now: datetime | None = None) -> ManipulationAssessment:
    settings = get_settings().filters
    now = now or datetime.now(UTC)
    a = ManipulationAssessment()
    w5 = state.window(300, now)
    w1 = state.window(60, now)
    trades5 = [t for t in state.trades if t.ts >= now - timedelta(seconds=300)]
    if not trades5:
        return a

    # --- wash trading: wallets that both buy and sell repeatedly within the window ---------------
    per_wallet: dict[str, dict[str, Any]] = {}
    for t in trades5:
        d = per_wallet.setdefault(t.trader, {"buys": 0, "sells": 0, "sol": 0.0, "sizes": []})
        d["buys" if t.side == "buy" else "sells"] += 1
        d["sol"] += t.sol
        d["sizes"].append(t.sol)
    round_trip = [w for w, d in per_wallet.items() if d["buys"] >= 2 and d["sells"] >= 2]
    a.round_trip_wallets = len(round_trip)
    total_sol = sum(d["sol"] for d in per_wallet.values()) or 1e-9
    round_trip_share = sum(per_wallet[w]["sol"] for w in round_trip) / total_sol
    a.top_wallet_trade_share = w5.top_wallet_share
    trades_per_wallet = len(trades5) / max(1, len(per_wallet))
    a.wash_trade_score = _clamp(0.6 * round_trip_share + 0.25 * min(1.0, (trades_per_wallet - 1.5) / 4.0) + 0.3 * max(0.0, a.top_wallet_trade_share - 0.25))
    if a.round_trip_wallets >= 2 and round_trip_share > 0.3:
        a.flags.append("wash_trading_pattern")
    if a.top_wallet_trade_share > settings.max_same_wallet_trade_share:
        a.flags.append(f"single_wallet_{int(a.top_wallet_trade_share * 100)}pct_of_volume")

    # --- coordinated buying: bursts of similar-sized buys from fresh wallets ---------------------
    buys = [t for t in trades5 if t.side == "buy"]
    burst_score = 0.0
    similar_size_score = 0.0
    if len(buys) >= 6:
        buys_sorted = sorted(buys, key=lambda t: t.ts)
        max_burst = 1
        i = 0
        for j in range(len(buys_sorted)):
            while (buys_sorted[j].ts - buys_sorted[i].ts).total_seconds() > 3.0:
                i += 1
            max_burst = max(max_burst, j - i + 1)
        burst_score = _clamp((max_burst - 3) / 8.0)
        sizes = [t.sol for t in buys if t.sol > 0]
        if len(sizes) >= 6:
            cv = statistics.pstdev(sizes) / (statistics.fmean(sizes) or 1e-9)
            similar_size_score = _clamp(1.0 - cv) if cv < 0.6 else 0.0
        if max_burst >= 5:
            a.flags.append(f"buy_burst_{max_burst}_in_3s")
    fresh_share = (w5.new_buyers / w5.unique_buyers) if w5.unique_buyers else 0.0
    a.coordinated_buy_score = _clamp(0.5 * burst_score + 0.3 * similar_size_score + 0.2 * (fresh_share if burst_score > 0.3 else 0.0))
    a.evidence["buy_burst_score"] = round(burst_score, 3)
    a.evidence["similar_size_score"] = round(similar_size_score, 3)

    # --- artificial volume: high volume per unique wallet with many repeat trades ---------------
    repeat_ratio = w5.repeat_buyer_trades / max(1, w5.buys)
    a.artificial_volume_score = _clamp(0.5 * repeat_ratio + 0.5 * a.wash_trade_score)
    if repeat_ratio > 0.5 and w5.buys >= 10:
        a.flags.append("repeat_buyer_volume")

    # --- pump and dump: spike then collapse with early wallets selling ---------------------------
    mom2 = state.momentum_pct(120, now)
    peak = max(state._price_vals[-200:]) if state._price_vals else None  # noqa: SLF001
    drawdown_from_peak = ((state.last_price / peak) - 1.0) * 100.0 if (peak and state.last_price) else 0.0
    if peak and state.last_price and drawdown_from_peak < -40 and w1.sells > w1.buys:
        a.pump_dump_score = _clamp(0.5 + min(0.5, -drawdown_from_peak / 100.0))
        a.flags.append(f"collapse_{int(-drawdown_from_peak)}pct_from_peak")
    elif mom2 is not None and mom2 > 150 and w1.sell_sol > w1.buy_sol:
        a.pump_dump_score = 0.5
        a.flags.append("spike_with_selling")
    a.evidence["drawdown_from_peak_pct"] = round(drawdown_from_peak, 2)

    # --- liquidity withdrawal -----------------------------------------------------------------------
    drop = state.liquidity_drop_pct(300, now)
    a.liquidity_withdrawal_pct = drop
    if drop is not None and drop >= settings.max_liquidity_drop_pct_5m * 100:
        a.flags.append(f"liquidity_drop_{int(drop)}pct_5m")
        a.hard_fail = True
        a.hard_fail_reason = f"liquidity dropped {int(drop)}% in 5 minutes"

    # --- sudden large sells relative to liquidity ---------------------------------------------------
    liquidity_sol = state.last_v_sol
    if liquidity_sol and w5.largest_sell_sol > liquidity_sol * settings.large_sell_pct_of_liquidity:
        a.flags.append("large_sell_vs_liquidity")

    a.overall_score = _clamp(max(a.wash_trade_score, a.coordinated_buy_score, a.pump_dump_score) * 0.7 + 0.3 * a.artificial_volume_score)
    if a.hard_fail:
        a.overall_score = 1.0
    return a
