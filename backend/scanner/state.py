"""In-memory per-token state built from the real-time trade stream.

Everything the filters, whale engine and feature generator need in the first
minutes of a token's life is derived here without database round-trips.
"""

from __future__ import annotations

import bisect
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.ingestion.events import MigrationEvent, NewTokenEvent, TradeEvent
from backend.scanner import pump_math

MAX_TRADES = 3000  # per token; bounded to keep worker memory predictable (~250 tokens tracked)
MAX_PRICE_POINTS = 3000


@dataclass(slots=True)
class TradeRecord:
    ts: datetime
    trader: str
    side: str
    sol: float
    tokens: float
    price: float | None
    signature: str


@dataclass
class WalletTokenAgg:
    first_ts: datetime
    last_ts: datetime
    buys: int = 0
    sells: int = 0
    bought_sol: float = 0.0
    sold_sol: float = 0.0
    bought_tokens: float = 0.0
    sold_tokens: float = 0.0
    balance_estimate: float = 0.0

    @property
    def net_sol(self) -> float:
        return self.bought_sol - self.sold_sol


@dataclass
class WindowStats:
    seconds: int
    buys: int = 0
    sells: int = 0
    buy_sol: float = 0.0
    sell_sol: float = 0.0
    unique_buyers: int = 0
    unique_sellers: int = 0
    new_buyers: int = 0
    largest_buy_sol: float = 0.0
    largest_sell_sol: float = 0.0
    trade_sizes: list[float] = field(default_factory=list)
    top_wallet_share: float = 0.0
    repeat_buyer_trades: int = 0

    @property
    def trades(self) -> int:
        return self.buys + self.sells

    @property
    def volume_sol(self) -> float:
        return self.buy_sol + self.sell_sol

    @property
    def net_sol(self) -> float:
        return self.buy_sol - self.sell_sol

    @property
    def buy_sell_volume_ratio(self) -> float | None:
        if self.sell_sol <= 0:
            return None if self.buy_sol <= 0 else float("inf")
        return self.buy_sol / self.sell_sol


class TokenState:
    def __init__(self, mint: str, discovered_at: datetime) -> None:
        self.mint = mint
        self.discovered_at = discovered_at
        self.launch_time: datetime = discovered_at
        self.creator: str | None = None
        self.name: str | None = None
        self.symbol: str | None = None
        self.uri: str | None = None
        self.bonding_curve_key: str | None = None
        self.pool_kind: str = "pump"
        self.migrated_at: datetime | None = None
        self.pool_address: str | None = None
        self.initial_buy_sol: float | None = None
        self.initial_buy_tokens: float | None = None

        self.trades: deque[TradeRecord] = deque(maxlen=MAX_TRADES)
        self._price_ts: list[float] = []
        self._price_vals: list[float] = []
        self._liq_ts: list[float] = []
        self._liq_vals: list[float] = []
        self.wallets: dict[str, WalletTokenAgg] = {}
        self.buyers_total: set[str] = set()
        self.sellers_total: set[str] = set()
        self.total_trades = 0
        self.total_buy_sol = 0.0
        self.total_sell_sol = 0.0
        self.last_price: float | None = None
        self.last_v_sol: float | None = None
        self.last_v_tokens: float | None = None
        self.last_market_cap_sol: float | None = None
        self.last_trade_at: datetime | None = None
        self.last_event_at: datetime = discovered_at
        self.dropped_trades = 0

        # enrichment from other providers (set by the snapshot builder)
        self.pair: Any | None = None  # dexscreener PairData
        self.pair_fetched_at: datetime | None = None
        self.holders: Any | None = None  # HolderDistribution
        self.holders_fetched_at: datetime | None = None
        self.holder_history: list[tuple[datetime, int]] = []
        self.creator_analysis: dict[str, Any] | None = None
        self.sellability: dict[str, Any] | None = None
        self.flags: dict[str, Any] = {}
        self.snapshots_done: set[str] = set()
        self.next_periodic_at: datetime | None = None
        self.last_evaluation: dict[str, Any] | None = None
        self.status: str = "discovered"
        self.status_reason: str | None = None
        self.gpt_analyses = 0
        self.alerted = False
        self.subscribed = False
        self.tracking_until: datetime | None = None
        # lifecycle
        self.phase: str = "launch"
        self.discovery_source: str = "pumpportal"
        self.growth: dict[str, Any] | None = None  # latest growth metrics (set by the growth tracker)
        self.growth_score: float | None = None
        self.ath_price_usd: float | None = None
        self.ath_market_cap_usd: float | None = None
        self.peak_liquidity_usd: float | None = None
        self.next_growth_check_at: datetime | None = None

    # ------------------------------------------------------------------ ingestion
    def apply_create(self, ev: NewTokenEvent) -> None:
        self.launch_time = ev.block_time
        self.creator = ev.creator
        self.name = ev.name
        self.symbol = ev.symbol
        self.uri = ev.uri
        self.bonding_curve_key = ev.bonding_curve_key
        self.pool_kind = ev.pool or "pump"
        self.initial_buy_sol = ev.initial_buy_sol
        self.initial_buy_tokens = ev.initial_buy_tokens
        self._update_curve(ev.v_sol_in_curve, ev.v_tokens_in_curve, ev.market_cap_sol, ev.block_time)
        if ev.creator and ev.initial_buy_sol and ev.initial_buy_tokens:
            # Treat the dev's initial buy as the first trade for position tracking.
            self.apply_trade(
                TradeEvent(
                    source=ev.source,
                    signature=ev.signature,
                    mint=ev.mint,
                    received_at=ev.received_at,
                    block_time=ev.block_time,
                    trader=ev.creator,
                    side="buy",
                    sol_amount=ev.initial_buy_sol,
                    token_amount=ev.initial_buy_tokens,
                    v_sol_in_curve=ev.v_sol_in_curve,
                    v_tokens_in_curve=ev.v_tokens_in_curve,
                    market_cap_sol=ev.market_cap_sol,
                    pool=ev.pool,
                ),
                count_total=False,
            )
        self.last_event_at = ev.received_at

    def apply_migration(self, ev: MigrationEvent) -> None:
        self.migrated_at = ev.block_time
        self.pool_kind = ev.pool or "pump-amm"
        self.pool_address = ev.pool_address or self.pool_address
        self.last_event_at = ev.received_at
        self.flags["migrated"] = True

    def _update_curve(self, v_sol: float | None, v_tokens: float | None, mcap_sol: float | None, ts: datetime) -> None:
        if v_sol is not None:
            self.last_v_sol = v_sol
            self._push(self._liq_ts, self._liq_vals, ts.timestamp(), v_sol)
        if v_tokens is not None:
            self.last_v_tokens = v_tokens
        price = pump_math.price_sol(v_sol, v_tokens)
        if price:
            self.last_price = price
            self._push(self._price_ts, self._price_vals, ts.timestamp(), price)
        if mcap_sol is not None:
            self.last_market_cap_sol = mcap_sol
        elif price is not None:
            self.last_market_cap_sol = pump_math.market_cap_sol(price)

    @staticmethod
    def _push(ts_list: list[float], vals: list[float], ts: float, value: float) -> None:
        if ts_list and ts < ts_list[-1]:
            idx = bisect.bisect_right(ts_list, ts)
            ts_list.insert(idx, ts)
            vals.insert(idx, value)
        else:
            ts_list.append(ts)
            vals.append(value)
        if len(ts_list) > MAX_PRICE_POINTS:
            del ts_list[0]
            del vals[0]

    def apply_trade(self, ev: TradeEvent, count_total: bool = True) -> None:
        sol = ev.sol_amount or 0.0
        tokens = ev.token_amount or 0.0
        price = ev.price_sol
        rec = TradeRecord(ev.block_time, ev.trader, ev.side, sol, tokens, price, ev.signature)
        if len(self.trades) == self.trades.maxlen:
            self.dropped_trades += 1
        self.trades.append(rec)
        if ev.pool and ev.pool != self.pool_kind and ev.pool in {"pump-amm", "raydium"}:
            self.pool_kind = ev.pool
            self.flags["migrated"] = True
            self.migrated_at = self.migrated_at or ev.block_time
        self._update_curve(ev.v_sol_in_curve, ev.v_tokens_in_curve, ev.market_cap_sol, ev.block_time)
        if price and ev.v_sol_in_curve is None:
            self.last_price = price
            self._push(self._price_ts, self._price_vals, ev.block_time.timestamp(), price)
        agg = self.wallets.get(ev.trader)
        if agg is None:
            agg = WalletTokenAgg(first_ts=ev.block_time, last_ts=ev.block_time)
            self.wallets[ev.trader] = agg
        agg.last_ts = max(agg.last_ts, ev.block_time)
        if ev.side == "buy":
            agg.buys += 1
            agg.bought_sol += sol
            agg.bought_tokens += tokens
            agg.balance_estimate += tokens
            self.buyers_total.add(ev.trader)
            self.total_buy_sol += sol
        else:
            agg.sells += 1
            agg.sold_sol += sol
            agg.sold_tokens += tokens
            agg.balance_estimate = max(0.0, agg.balance_estimate - tokens)
            self.sellers_total.add(ev.trader)
            self.total_sell_sol += sol
        if ev.new_token_balance is not None:
            agg.balance_estimate = max(0.0, ev.new_token_balance)
        if count_total:
            self.total_trades += 1
        self.last_trade_at = ev.block_time
        self.last_event_at = ev.received_at

    # ------------------------------------------------------------------ queries
    @property
    def age_seconds(self) -> float:
        return (datetime.now(UTC) - self.launch_time).total_seconds()

    def age_at(self, now: datetime) -> float:
        return (now - self.launch_time).total_seconds()

    def price_at(self, ts: datetime) -> float | None:
        """Last known price at or before ``ts`` (None if no observation yet)."""
        if not self._price_ts:
            return None
        idx = bisect.bisect_right(self._price_ts, ts.timestamp()) - 1
        return self._price_vals[idx] if idx >= 0 else None

    def liquidity_sol_at(self, ts: datetime) -> float | None:
        if not self._liq_ts:
            return None
        idx = bisect.bisect_right(self._liq_ts, ts.timestamp()) - 1
        return self._liq_vals[idx] if idx >= 0 else None

    def momentum_pct(self, seconds: int, now: datetime | None = None) -> float | None:
        now = now or datetime.now(UTC)
        if self.last_price is None:
            return None
        then = now - timedelta(seconds=seconds)
        if then < self.launch_time:
            # Not enough history: compare to the first observed price if the token is at least ~half the window old.
            if self.age_at(now) < seconds / 2 or not self._price_vals:
                return None
            base = self._price_vals[0]
        else:
            base = self.price_at(then)
        if not base:
            return None
        return (self.last_price / base - 1.0) * 100.0

    def window(self, seconds: int, now: datetime | None = None) -> WindowStats:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=seconds)
        st = WindowStats(seconds=seconds)
        buyers: set[str] = set()
        sellers: set[str] = set()
        per_wallet: dict[str, float] = {}
        total = 0.0
        buyer_counts: dict[str, int] = {}
        for rec in reversed(self.trades):
            if rec.ts < cutoff:
                break
            per_wallet[rec.trader] = per_wallet.get(rec.trader, 0.0) + rec.sol
            total += rec.sol
            st.trade_sizes.append(rec.sol)
            if rec.side == "buy":
                st.buys += 1
                st.buy_sol += rec.sol
                buyers.add(rec.trader)
                buyer_counts[rec.trader] = buyer_counts.get(rec.trader, 0) + 1
                st.largest_buy_sol = max(st.largest_buy_sol, rec.sol)
                agg = self.wallets.get(rec.trader)
                if agg and agg.first_ts >= cutoff:
                    pass
            else:
                st.sells += 1
                st.sell_sol += rec.sol
                sellers.add(rec.trader)
                st.largest_sell_sol = max(st.largest_sell_sol, rec.sol)
        st.unique_buyers = len(buyers)
        st.unique_sellers = len(sellers)
        st.new_buyers = sum(1 for b in buyers if (self.wallets.get(b) and self.wallets[b].first_ts >= cutoff))
        st.repeat_buyer_trades = sum(c - 1 for c in buyer_counts.values() if c > 1)
        st.top_wallet_share = (max(per_wallet.values()) / total) if total > 0 and per_wallet else 0.0
        return st

    def volume_acceleration(self, now: datetime | None = None) -> float | None:
        """Volume in the last minute relative to the average per-minute volume of the prior 4 minutes."""
        now = now or datetime.now(UTC)
        last = self.window(60, now).volume_sol
        prior = self.window(300, now).volume_sol - last
        minutes_available = min(4.0, max(0.0, (self.age_at(now) - 60) / 60.0))
        if minutes_available < 1.0:
            return None
        avg_prior = prior / minutes_available
        if avg_prior <= 0:
            return None if last <= 0 else 3.0
        return last / avg_prior

    def price_volatility_pct(self, seconds: int = 300, now: datetime | None = None) -> float | None:
        now = now or datetime.now(UTC)
        cutoff = (now - timedelta(seconds=seconds)).timestamp()
        idx = bisect.bisect_left(self._price_ts, cutoff)
        vals = self._price_vals[idx:]
        if len(vals) < 5:
            return None
        mean = statistics.fmean(vals)
        if mean <= 0:
            return None
        return statistics.pstdev(vals) / mean * 100.0

    @property
    def market_cap_sol_safe(self) -> float | None:
        return self.last_market_cap_sol

    def stream_holder_estimate(self) -> int:
        return sum(1 for w in self.wallets.values() if w.balance_estimate > 0)

    def dev_activity(self) -> dict[str, Any]:
        if not self.creator:
            return {"known": False}
        agg = self.wallets.get(self.creator)
        if agg is None:
            return {"known": True, "buys": 0, "sells": 0, "bought_sol": 0.0, "sold_sol": 0.0, "sold_pct": 0.0, "balance_estimate": 0.0}
        sold_pct = (agg.sold_tokens / agg.bought_tokens) if agg.bought_tokens > 0 else 0.0
        return {
            "known": True,
            "buys": agg.buys,
            "sells": agg.sells,
            "bought_sol": agg.bought_sol,
            "sold_sol": agg.sold_sol,
            "bought_tokens": agg.bought_tokens,
            "sold_tokens": agg.sold_tokens,
            "sold_pct": min(1.0, sold_pct),
            "balance_estimate": agg.balance_estimate,
        }

    def top_wallets(self, n: int = 10) -> list[tuple[str, WalletTokenAgg]]:
        return sorted(self.wallets.items(), key=lambda kv: kv[1].net_sol, reverse=True)[:n]

    def liquidity_drop_pct(self, seconds: int = 300, now: datetime | None = None) -> float | None:
        now = now or datetime.now(UTC)
        then = self.liquidity_sol_at(now - timedelta(seconds=seconds))
        if not then or self.last_v_sol is None:
            return None
        return max(0.0, (then - self.last_v_sol) / then * 100.0)

    def summary(self) -> dict[str, Any]:
        return {
            "mint": self.mint,
            "symbol": self.symbol,
            "status": self.status,
            "pool_kind": self.pool_kind,
            "age_seconds": int(self.age_seconds),
            "trades": self.total_trades,
            "buyers": len(self.buyers_total),
            "price_sol": self.last_price,
            "market_cap_sol": self.last_market_cap_sol,
            "v_sol": self.last_v_sol,
        }
