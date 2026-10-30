"""Liquidity & execution analyzer: can a manual trade of $100/$250/$500 realistically be executed?

Uses the bonding-curve virtual reserves (Pump.fun) or pool reserves (DexScreener liquidity base/quote)
with constant-product maths. Everything here is an ESTIMATE from reserve data; when reserves are
unknown the figures are reported as unavailable rather than guessed. Nothing is executed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from backend.config.settings import get_settings
from backend.scanner import pump_math
from backend.scanner.state import TokenState

WSOL = "So11111111111111111111111111111111111111112"


@dataclass
class SizeEstimate:
    size_usd: float
    size_sol: float | None
    available: bool
    reason: str | None = None
    spot_price_sol: float | None = None
    executable_buy_price_sol: float | None = None
    tokens_received: float | None = None
    buy_impact_pct: float | None = None
    sell_impact_pct: float | None = None
    immediate_exit_proceeds_sol: float | None = None
    immediate_exit_proceeds_usd: float | None = None
    round_trip_cost_pct: float | None = None  # loss if bought and immediately sold (fees + impact)
    fees_usd: float | None = None
    network_fees_usd: float | None = None
    targets: dict[str, dict[str, float | None]] = field(default_factory=dict)  # +10/+15/+20 scenarios

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ExecutionReport:
    venue: str
    source: str
    reserves_sol: float | None
    reserves_tokens: float | None
    sol_usd: float | None
    fee_bps: int
    estimates: list[SizeEstimate]
    max_size_usd_within_slippage: float | None
    notes: list[str] = field(default_factory=list)

    def at_size(self, size_usd: float) -> SizeEstimate | None:
        return next((e for e in self.estimates if abs(e.size_usd - size_usd) < 1e-6), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "source": self.source,
            "reserves_sol": self.reserves_sol,
            "reserves_tokens": self.reserves_tokens,
            "sol_usd": self.sol_usd,
            "fee_bps": self.fee_bps,
            "estimates": [e.to_dict() for e in self.estimates],
            "max_size_usd_within_slippage": self.max_size_usd_within_slippage,
            "notes": self.notes,
            "disclaimer": "Estimates from reserve data using constant-product maths; not quotes and not executed.",
        }


def _reserves(state: TokenState) -> tuple[float | None, float | None, str, str, int]:
    exec_cfg = get_settings().execution
    if state.pool_kind == "pump" and state.last_v_sol and state.last_v_tokens:
        return state.last_v_sol, state.last_v_tokens, "pump.fun bonding curve", "stream_virtual_reserves", exec_cfg.pump_fee_bps
    pair = state.pair
    if pair is not None and pair.liquidity_base and pair.liquidity_quote and pair.quote_mint == WSOL:
        return pair.liquidity_quote, pair.liquidity_base, pair.dex_id, "dexscreener_reserves", exec_cfg.amm_fee_bps
    if pair is not None and pair.liquidity_usd and pair.price_usd and pair.price_usd > 0:
        # Only USD totals known: assume a balanced pool (half quote, half base) -> flagged as lower confidence
        return None, None, pair.dex_id, "dexscreener_usd_only", exec_cfg.amm_fee_bps
    return None, None, state.pool_kind, "unavailable", exec_cfg.amm_fee_bps


def analyze_execution(state: TokenState, sol_usd: float | None, sizes_usd: list[float] | None = None) -> ExecutionReport:
    exec_cfg = get_settings().execution
    sizes = sizes_usd or exec_cfg.trade_sizes
    r_sol, r_tok, venue, source, fee_bps = _reserves(state)
    notes: list[str] = []
    if source == "dexscreener_usd_only" and state.pair is not None and sol_usd:
        liq_usd = state.pair.liquidity_usd or 0.0
        r_sol = liq_usd / 2.0 / sol_usd
        r_tok = (liq_usd / 2.0) / state.pair.price_usd if state.pair.price_usd else None
        notes.append("pool reserves estimated from USD liquidity assuming a balanced pool")
    network_fee_sol = exec_cfg.network_fee_sol + exec_cfg.priority_fee_sol
    estimates: list[SizeEstimate] = []
    if not sol_usd:
        notes.append("SOL/USD unavailable: sizes cannot be converted")
    if r_sol is None or r_tok is None or r_sol <= 0 or r_tok <= 0 or not sol_usd:
        for size in sizes:
            estimates.append(SizeEstimate(size, None, False, reason="reserves or SOL price unavailable"))
        return ExecutionReport(venue, source, r_sol, r_tok, sol_usd, fee_bps, estimates, None, notes)
    for size in sizes:
        size_sol = size / sol_usd
        buy = pump_math.buy_quote(r_sol, r_tok, size_sol, fee_bps)
        if not buy.available or buy.amount_out <= 0:
            estimates.append(SizeEstimate(size, size_sol, False, reason="buy quote unavailable"))
            continue
        # reserves after the buy
        r_sol_after = r_sol + size_sol * (1 - fee_bps / 10_000)
        r_tok_after = r_tok - buy.amount_out
        sell = pump_math.sell_quote(r_sol_after, r_tok_after, buy.amount_out, fee_bps)
        exit_sol = sell.amount_out - 2 * network_fee_sol
        round_trip_cost = (1.0 - exit_sol / size_sol) * 100.0
        targets: dict[str, dict[str, float | None]] = {}
        for pct in get_settings().risk.targets:
            k = math.sqrt(1 + pct / 100.0)
            t_sol, t_tok = r_sol_after * k, r_tok_after / k  # reserves consistent with price up pct%
            s2 = pump_math.sell_quote(t_sol, t_tok, buy.amount_out, fee_bps)
            proceeds_sol = s2.amount_out - 2 * network_fee_sol
            targets[f"+{int(pct)}%"] = {
                "exit_proceeds_usd": round(proceeds_sol * sol_usd, 2),
                "net_return_pct": round((proceeds_sol / size_sol - 1.0) * 100.0, 2),
                "sell_impact_pct": round(s2.price_impact_pct, 2),
            }
        estimates.append(
            SizeEstimate(
                size_usd=size,
                size_sol=round(size_sol, 6),
                available=True,
                spot_price_sol=buy.spot_price,
                executable_buy_price_sol=buy.effective_price,
                tokens_received=buy.amount_out,
                buy_impact_pct=round(buy.price_impact_pct, 3),
                sell_impact_pct=round(sell.price_impact_pct, 3),
                immediate_exit_proceeds_sol=round(exit_sol, 6),
                immediate_exit_proceeds_usd=round(exit_sol * sol_usd, 2),
                round_trip_cost_pct=round(round_trip_cost, 3),
                fees_usd=round((buy.fee_paid + sell.fee_paid) * sol_usd, 4),
                network_fees_usd=round(2 * network_fee_sol * sol_usd, 4),
                targets=targets,
            )
        )
    # largest size whose sell impact stays within the configured slippage ceiling (binary search)
    limit = exec_cfg.max_acceptable_slippage_pct
    lo, hi = 0.0, max(sizes) * 20
    for _ in range(30):
        mid = (lo + hi) / 2
        q = pump_math.buy_quote(r_sol, r_tok, mid / sol_usd, fee_bps)
        s = pump_math.sell_quote(r_sol + (mid / sol_usd) * (1 - fee_bps / 10_000), r_tok - q.amount_out, q.amount_out, fee_bps) if q.available else None
        if s is not None and s.price_impact_pct <= limit:
            lo = mid
        else:
            hi = mid
    return ExecutionReport(venue, source, r_sol, r_tok, sol_usd, fee_bps, estimates, round(lo, 2), notes)
