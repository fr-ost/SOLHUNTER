"""Pump.fun bonding-curve and constant-product AMM maths (estimates, not execution quotes)."""

from __future__ import annotations

from dataclasses import dataclass

from backend.config.settings import get_settings


def price_sol(v_sol: float | None, v_tokens: float | None) -> float | None:
    if not v_sol or not v_tokens or v_tokens <= 0:
        return None
    return v_sol / v_tokens


def market_cap_sol(price: float | None, supply: float | None = None) -> float | None:
    if price is None:
        return None
    supply = supply or get_settings().scanner.pump_total_supply
    return price * supply


def bonding_progress(v_tokens: float | None) -> float | None:
    """0..1 fraction of the curve's sellable tokens already sold (None if unknown)."""
    if v_tokens is None:
        return None
    s = get_settings().scanner
    sold = s.pump_initial_virtual_tokens - v_tokens
    return max(0.0, min(1.0, sold / s.pump_curve_tokens_to_sell))


def real_sol_in_curve(v_sol: float | None) -> float | None:
    if v_sol is None:
        return None
    return max(0.0, v_sol - get_settings().scanner.pump_initial_virtual_sol)


@dataclass
class Quote:
    amount_in: float
    amount_out: float
    spot_price: float  # SOL per token before the trade
    effective_price: float  # SOL per token actually paid/received
    price_impact_pct: float  # positive = worse than spot
    fee_paid: float
    available: bool = True
    note: str | None = None


def buy_quote(reserve_sol: float, reserve_tokens: float, sol_in: float, fee_bps: int) -> Quote:
    """Buy tokens with ``sol_in`` SOL on a constant-product curve/pool (fee charged on input)."""
    if reserve_sol <= 0 or reserve_tokens <= 0 or sol_in <= 0:
        return Quote(sol_in, 0.0, 0.0, 0.0, 0.0, 0.0, available=False, note="no reserves")
    fee = sol_in * fee_bps / 10_000
    net_in = sol_in - fee
    k = reserve_sol * reserve_tokens
    tokens_out = reserve_tokens - k / (reserve_sol + net_in)
    spot = reserve_sol / reserve_tokens
    eff = sol_in / tokens_out if tokens_out > 0 else float("inf")
    impact = (eff / spot - 1.0) * 100.0 if spot > 0 else 0.0
    return Quote(sol_in, tokens_out, spot, eff, impact, fee)


def sell_quote(reserve_sol: float, reserve_tokens: float, tokens_in: float, fee_bps: int) -> Quote:
    """Sell ``tokens_in`` tokens for SOL (fee charged on output)."""
    if reserve_sol <= 0 or reserve_tokens <= 0 or tokens_in <= 0:
        return Quote(tokens_in, 0.0, 0.0, 0.0, 0.0, 0.0, available=False, note="no reserves")
    k = reserve_sol * reserve_tokens
    gross_out = reserve_sol - k / (reserve_tokens + tokens_in)
    fee = gross_out * fee_bps / 10_000
    net_out = gross_out - fee
    spot = reserve_sol / reserve_tokens
    eff = net_out / tokens_in if tokens_in > 0 else 0.0
    impact = (1.0 - eff / spot) * 100.0 if spot > 0 else 0.0
    return Quote(tokens_in, net_out, spot, eff, impact, fee)
