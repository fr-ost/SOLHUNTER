"""Snapshot scheduling and persistence (market state at 1/3/5/10/15 minutes and periodically)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.models import TokenSnapshot
from backend.providers.base import ProviderError
from backend.providers.dexscreener import DexScreenerClient, PairData
from backend.providers.sol_price import SolPriceService
from backend.scanner import pump_math
from backend.scanner.state import TokenState

log = get_logger(__name__)


def due_snapshot_kind(state: TokenState, now: datetime) -> str | None:
    """Which snapshot (if any) is due now for this token."""
    schedule = get_settings().scanner.snapshot_schedule
    age = state.age_at(now)
    for minutes in schedule:
        kind = f"m{minutes}"
        if kind in state.snapshots_done:
            continue
        if age >= minutes * 60:
            # Skip if we are far beyond the slot (e.g. restored after restart) to avoid mislabelled samples.
            if age > minutes * 60 + 45:
                state.snapshots_done.add(kind)
                continue
            return kind
    if state.next_periodic_at is None:
        state.next_periodic_at = now + timedelta(seconds=periodic_interval_seconds(state, now))
        return None
    if now >= state.next_periodic_at and age >= 60:
        # Idle, unsurfaced tokens are not re-snapshotted: nothing changed and the row would be waste.
        last_snapshot_at = state.flags.get("last_snapshot_at")
        idle = state.last_trade_at is not None and last_snapshot_at is not None and state.last_trade_at <= last_snapshot_at
        if idle and state.status not in {"candidate", "watchlist"}:
            state.next_periodic_at = now + timedelta(seconds=periodic_interval_seconds(state, now))
            return None
        return "periodic"
    return None


def periodic_interval_seconds(state: TokenState, now: datetime) -> int:
    """Every ``periodic_snapshot_interval_seconds`` for the first 20 minutes, then every 5 minutes."""
    base = get_settings().scanner.periodic_snapshot_interval_seconds
    if state.age_at(now) < 1200 or state.status in {"candidate", "watchlist"}:
        return base
    return max(base, 300)


async def refresh_pair(state: TokenState, dex: DexScreenerClient, now: datetime, force: bool = False) -> PairData | None:
    """Refresh DexScreener data for migrated tokens (bonding-curve tokens are priced from the stream)."""
    interval = get_settings().scanner.dexscreener_refresh_interval_seconds
    needs = force or state.pool_kind != "pump" or state.flags.get("manual") or state.flags.get("restored")
    if not needs:
        return state.pair
    if state.pair_fetched_at and (now - state.pair_fetched_at).total_seconds() < interval:
        return state.pair
    try:
        pairs = await dex.token_pairs(state.mint)
    except ProviderError as exc:
        log.debug("snapshots.dexscreener_failed", mint=state.mint, error=str(exc)[:120])
        return state.pair
    state.pair_fetched_at = now
    primary = DexScreenerClient.primary_pair(pairs)
    if primary:
        if state.flags.get("manual") and not state.flags.get("launch_resolved"):
            # Manually tracked tokens start with launch_time = now; use the pool creation time instead.
            created = min((p.pair_created_at for p in pairs if p.pair_created_at), default=None)
            if created is not None and created < state.launch_time:
                state.launch_time = created
            base = (primary.raw or {}).get("baseToken") or {}
            state.symbol = state.symbol or base.get("symbol")
            state.name = state.name or base.get("name")
            state.flags["launch_resolved"] = True
        state.pair = primary
        state.pool_address = primary.pair_address
        if primary.pool_kind != "pump" and state.pool_kind == "pump":
            state.pool_kind = primary.pool_kind
            state.flags["migrated"] = True
    return state.pair


def build_snapshot_row(state: TokenState, kind: str, now: datetime, sol_usd: float | None) -> dict[str, Any]:
    w1, w5, w15 = state.window(60, now), state.window(300, now), state.window(900, now)
    pair = state.pair
    price_sol = state.last_price
    price_usd = (price_sol * sol_usd) if (price_sol and sol_usd) else None
    liquidity_sol: float | None = None
    liquidity_usd: float | None = None
    market_cap_usd: float | None = None
    fdv_usd: float | None = None
    sources: dict[str, Any] = {"stream": "pumpportal"}
    quality: dict[str, Any] = {}
    if state.pool_kind == "pump" and state.last_v_sol is not None:
        # Bonding curve: SOL in the curve is the exit liquidity (virtual reserve includes the 30 SOL offset).
        liquidity_sol = pump_math.real_sol_in_curve(state.last_v_sol)
        liquidity_usd = liquidity_sol * sol_usd if (liquidity_sol is not None and sol_usd) else None
        sources["liquidity"] = "bonding_curve_reserves"
    if pair is not None:
        age_s = (now - pair.fetched_at).total_seconds()
        sources["dexscreener"] = {"pair": pair.pair_address, "dex": pair.dex_id, "age_seconds": int(age_s)}
        quality["dexscreener_stale"] = age_s > 120
        if pair.liquidity_usd is not None:
            liquidity_usd = pair.liquidity_usd
            liquidity_sol = pair.liquidity_quote if pair.quote_mint == "So11111111111111111111111111111111111111112" else (pair.liquidity_usd / sol_usd if sol_usd else None)
            sources["liquidity"] = "dexscreener"
        if pair.price_usd and (price_usd is None or state.pool_kind != "pump"):
            price_usd = pair.price_usd
            if sol_usd:
                price_sol = pair.price_usd / sol_usd
        market_cap_usd = pair.market_cap_usd
        fdv_usd = pair.fdv_usd
    market_cap_sol = state.last_market_cap_sol
    if market_cap_usd is None and market_cap_sol is not None and sol_usd:
        market_cap_usd = market_cap_sol * sol_usd
    if sol_usd is None:
        quality["sol_usd_missing"] = True
    stale = (now - state.last_event_at).total_seconds()
    quality["stream_age_seconds"] = int(stale)
    quality["trades_dropped"] = state.dropped_trades
    holders = state.holders
    holder_count = holders.holder_count if holders else None
    if holder_count is None:
        holder_count = state.stream_holder_estimate()
        quality["holder_count_source"] = "stream_estimate"
    else:
        quality["holder_count_source"] = "helius" if holders.holder_count_complete else "helius_partial"
    dev = state.dev_activity()
    return {
        "mint": state.mint,
        "captured_at": now,
        "snapshot_kind": kind,
        "age_seconds": int(state.age_at(now)),
        "pool_kind": state.pool_kind,
        "price_sol": price_sol,
        "price_usd": price_usd,
        "sol_usd": sol_usd,
        "market_cap_sol": market_cap_sol,
        "market_cap_usd": market_cap_usd,
        "fdv_usd": fdv_usd,
        "liquidity_sol": liquidity_sol,
        "liquidity_usd": liquidity_usd,
        "bonding_progress": pump_math.bonding_progress(state.last_v_tokens) if state.pool_kind == "pump" else 1.0,
        "v_sol_in_curve": state.last_v_sol,
        "v_tokens_in_curve": state.last_v_tokens,
        "trades_total": state.total_trades,
        "buys_1m": w1.buys,
        "sells_1m": w1.sells,
        "buys_5m": w5.buys,
        "sells_5m": w5.sells,
        "buys_15m": w15.buys,
        "sells_15m": w15.sells,
        "buy_volume_sol_5m": w5.buy_sol,
        "sell_volume_sol_5m": w5.sell_sol,
        "volume_sol_1m": w1.volume_sol,
        "volume_sol_5m": w5.volume_sol,
        "volume_sol_15m": w15.volume_sol,
        "net_sol_flow_5m": w5.net_sol,
        "unique_buyers_5m": w5.unique_buyers,
        "unique_sellers_5m": w5.unique_sellers,
        "unique_buyers_total": len(state.buyers_total),
        "holder_count": holder_count,
        "largest_holder_pct": holders.largest_pct if holders else None,
        "top10_pct": holders.concentration(10) if holders else None,
        "top20_pct": holders.concentration(20) if holders else None,
        "dev_holding_pct": (holders.wallet_pct(state.creator) if holders else None),
        "momentum_1m_pct": state.momentum_pct(60, now),
        "momentum_3m_pct": state.momentum_pct(180, now),
        "momentum_5m_pct": state.momentum_pct(300, now),
        "momentum_10m_pct": state.momentum_pct(600, now),
        "momentum_15m_pct": state.momentum_pct(900, now),
        "smart_money_buyers": state.flags.get("smart_money_buyers"),
        "whale_net_flow_sol_5m": state.flags.get("whale_net_flow_sol_5m"),
        "data_sources": sources,
        "data_quality": {**quality, "dev_sold_pct": dev.get("sold_pct")},
        "features": None,
    }


async def persist_snapshot(row: dict[str, Any]) -> int:
    async with session_scope() as session:
        snap = TokenSnapshot(**row)
        session.add(snap)
        await session.flush()
        return snap.id


class SnapshotScheduler:
    """Finds due snapshots and persists them; evaluation hooks are attached by the worker."""

    def __init__(self, dex: DexScreenerClient, sol_price: SolPriceService, persistence: Any | None = None) -> None:
        self.dex = dex
        self.sol_price = sol_price
        self.persistence = persistence
        self.snapshots_taken = 0
        self.on_snapshot = None  # async callable(state, kind, row, snapshot_id) set by the worker

    async def tick(self, states: list[TokenState]) -> int:
        now = datetime.now(UTC)
        taken = 0
        sol_usd = await self.sol_price.get()
        flushed = False
        for state in states:
            kind = due_snapshot_kind(state, now)
            if kind is None:
                continue
            try:
                if not flushed and self.persistence is not None and self.persistence.pending:
                    # Token rows must exist before snapshots reference them (FK).
                    await self.persistence.flush()
                    flushed = True
                await refresh_pair(state, self.dex, now)
                row = build_snapshot_row(state, kind, now, sol_usd)
                snapshot_id = await persist_snapshot(row)
                state.snapshots_done.add(kind)
                state.flags["last_snapshot_at"] = now
                if kind == "periodic":
                    state.next_periodic_at = now + timedelta(seconds=periodic_interval_seconds(state, now))
                taken += 1
                self.snapshots_taken += 1
                if self.on_snapshot is not None:
                    await self.on_snapshot(state, kind, row, snapshot_id)
            except Exception:  # noqa: BLE001
                log.exception("snapshots.failed", mint=state.mint, kind=kind)
        return taken
