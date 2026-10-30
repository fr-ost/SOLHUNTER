"""Whale intelligence engine: accumulation / distribution detection and whale signals.

Thresholds are configurable (``WHALE_*``). A whale trade is one that is at least
``min_whale_trade_sol`` SOL AND at least ``min_whale_pct_of_liquidity`` of the pool's SOL.
Smart-money classification comes from the wallet profiler (track record), never from size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.config.settings import get_settings
from backend.intelligence.relationships import ClusterAnalysis
from backend.intelligence.wallets import WalletProfileSummary
from backend.models import WalletCategory, WhaleSignal
from backend.scanner.state import TokenState


@dataclass
class WhaleActivity:
    wallet: str
    short: str
    category: str
    confidence: float
    direction: str
    sol_amount: float
    usd_amount: float | None
    pct_of_liquidity: float | None
    timestamp: datetime
    signature: str
    performance: dict[str, Any]
    corroborated: bool
    cluster_id: str | None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class WhaleAssessment:
    primary_signal: str = WhaleSignal.NO_SIGNIFICANT_WHALE_ACTIVITY.value
    signals: list[str] = field(default_factory=list)
    whale_net_flow_1m: float = 0.0
    whale_net_flow_5m: float = 0.0
    whale_net_flow_15m: float = 0.0
    whale_net_flow_1h: float = 0.0
    whale_buy_sol_5m: float = 0.0
    whale_sell_sol_5m: float = 0.0
    whales_buying_5m: int = 0
    whales_selling_5m: int = 0
    whales_holding: int = 0
    whales_distributing: int = 0
    whale_buys_pct_of_liquidity_5m: float | None = None
    smart_money_buyers: int = 0
    smart_money_sellers: int = 0
    independent_profitable_buyers: int = 0
    smart_money_net_sol: float = 0.0
    insider_sell_sol: float = 0.0
    dev_sell_sol: float = 0.0
    accumulation_trend: str = "flat"  # increasing / decreasing / flat
    accumulation_score: float = 0.0  # 0..1 positive evidence
    distribution_score: float = 0.0  # 0..1 negative evidence
    hard_warning: str | None = None
    activities: list[WhaleActivity] = field(default_factory=list)
    data_confidence: str = "low"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "activities"}
        d["activities"] = [a.to_dict() for a in self.activities[:40]]
        return d


def _short(addr: str) -> str:
    return f"{addr[:4]}…{addr[-4:]}" if len(addr) > 10 else addr


def _performance(p: WalletProfileSummary | None) -> dict[str, Any]:
    if p is None or p.tokens_traded == 0:
        return {"realized_pnl_sol": None, "win_rate": None, "profit_factor": None, "closed_positions": 0, "tokens_traded": 0, "rug_exposure": 0, "known": False}
    return {
        "realized_pnl_sol": round(p.realized_pnl_sol, 3),
        "win_rate": round(p.win_rate, 3) if p.win_rate is not None else None,
        "profit_factor": round(p.profit_factor, 2) if p.profit_factor is not None else None,
        "closed_positions": p.closed_positions,
        "tokens_traded": p.tokens_traded,
        "rug_exposure": p.rug_exposure_count,
        "known": True,
    }


class WhaleEngine:
    def assess(
        self,
        state: TokenState,
        profiles: dict[str, WalletProfileSummary],
        clusters: ClusterAnalysis | None,
        liquidity_sol: float | None,
        sol_usd: float | None,
        now: datetime | None = None,
    ) -> WhaleAssessment:
        s = get_settings().whale
        now = now or datetime.now(UTC)
        a = WhaleAssessment()
        liq = liquidity_sol if liquidity_sol and liquidity_sol > 0 else None
        whale_threshold = max(s.min_whale_trade_sol, (liq * s.min_whale_pct_of_liquidity) if liq else s.min_whale_trade_sol)
        wallet_to_cluster = clusters.wallet_to_cluster if clusters else {}
        creator_linked = set(clusters.creator_cluster) if clusters else set()

        def net_flow(seconds: int, wallets: set[str] | None = None) -> float:
            cutoff = now - timedelta(seconds=seconds)
            total = 0.0
            for t in reversed(state.trades):
                if t.ts < cutoff:
                    break
                if wallets is not None and t.trader not in wallets:
                    continue
                total += t.sol if t.side == "buy" else -t.sol
            return total

        # --- identify whales: wallets whose net position or single trade crosses the threshold -------
        whales: set[str] = set()
        for w, agg in state.wallets.items():
            if w == state.creator:
                continue
            if agg.bought_sol >= max(whale_threshold, s.min_whale_position_sol) or agg.sold_sol >= max(whale_threshold, s.min_whale_position_sol):
                whales.add(w)
        cutoff5 = now - timedelta(seconds=300)
        seen_sigs: set[str] = state.flags.setdefault("whale_event_sigs", set())
        buying5: set[str] = set()
        selling5: set[str] = set()
        smart_buyers: set[str] = set()
        smart_sellers: set[str] = set()
        for t in reversed(state.trades):
            if t.ts < cutoff5:
                break
            prof = profiles.get(t.trader)
            is_dev = t.trader == state.creator
            if is_dev and t.side == "buy":
                continue  # the creator's own buys (incl. the launch buy) are developer context, not whale flow
            is_whale_trade = t.sol >= whale_threshold
            is_smart = bool(prof and prof.is_smart_money)
            is_insider = bool(prof and (prof.insider_associated or prof.category == WalletCategory.INSIDER_ASSOCIATED.value)) or t.trader in creator_linked
            if t.side == "buy":
                a.whale_buy_sol_5m += t.sol if is_whale_trade else 0.0
                if is_whale_trade:
                    buying5.add(t.trader)
                if is_smart:
                    smart_buyers.add(t.trader)
                    a.smart_money_net_sol += t.sol
            else:
                a.whale_sell_sol_5m += t.sol if is_whale_trade else 0.0
                if is_whale_trade:
                    selling5.add(t.trader)
                if is_smart:
                    smart_sellers.add(t.trader)
                    a.smart_money_net_sol -= t.sol
                if is_insider and t.sol >= s.insider_sell_min_sol:
                    a.insider_sell_sol += t.sol
                if is_dev:
                    a.dev_sell_sol += t.sol
            if (is_whale_trade or is_smart or (is_insider and t.side == "sell") or (is_dev and t.side == "sell")) and t.signature not in seen_sigs:
                seen_sigs.add(t.signature)
                category = prof.category if prof else WalletCategory.UNKNOWN.value
                if is_dev:
                    category = WalletCategory.DEVELOPER.value
                confidence = prof.category_confidence if prof else 0.0
                if is_dev:
                    confidence = 1.0
                a.activities.append(
                    WhaleActivity(
                        wallet=t.trader,
                        short=_short(t.trader),
                        category=category,
                        confidence=round(confidence, 3),
                        direction=t.side,
                        sol_amount=round(t.sol, 4),
                        usd_amount=round(t.sol * sol_usd, 2) if sol_usd else None,
                        pct_of_liquidity=round(t.sol / liq, 4) if liq else None,
                        timestamp=t.ts,
                        signature=t.signature,
                        performance=_performance(prof),
                        corroborated=False,
                        cluster_id=wallet_to_cluster.get(t.trader),
                    )
                )
        a.whales_buying_5m = len(buying5)
        a.whales_selling_5m = len(selling5)
        a.smart_money_buyers = len(smart_buyers)
        a.smart_money_sellers = len(smart_sellers)
        # independent = distinct clusters (unclustered wallets count individually)
        a.independent_profitable_buyers = len({wallet_to_cluster.get(w, w) for w in smart_buyers})
        # corroboration: a buy is corroborated when another independent whale/smart wallet bought within 5m
        independent_buyers = {wallet_to_cluster.get(w, w) for w in (buying5 | smart_buyers)}
        for act in a.activities:
            if act.direction == "buy" and act.timestamp >= cutoff5:
                others = independent_buyers - {wallet_to_cluster.get(act.wallet, act.wallet)}
                act.corroborated = len(others) >= 1

        a.whale_net_flow_1m = net_flow(60, whales)
        a.whale_net_flow_5m = net_flow(300, whales)
        a.whale_net_flow_15m = net_flow(900, whales)
        a.whale_net_flow_1h = net_flow(3600, whales)
        prev5 = net_flow(600, whales) - a.whale_net_flow_5m
        if a.whale_net_flow_5m > prev5 + max(0.5, 0.02 * (liq or 25)):
            a.accumulation_trend = "increasing"
        elif a.whale_net_flow_5m < prev5 - max(0.5, 0.02 * (liq or 25)):
            a.accumulation_trend = "decreasing"
        a.whale_buys_pct_of_liquidity_5m = (a.whale_buy_sol_5m / liq) if liq else None
        holding = 0
        distributing = 0
        for w in whales:
            agg = state.wallets[w]
            if agg.bought_tokens <= 0:
                continue
            sold_frac = agg.sold_tokens / agg.bought_tokens
            if sold_frac >= s.distribution_exit_fraction:
                distributing += 1
            elif agg.balance_estimate > 0:
                holding += 1
        a.whales_holding = holding
        a.whales_distributing = distributing

        # --- signals ---------------------------------------------------------------------------------
        signals: list[str] = []
        if a.independent_profitable_buyers >= s.consensus_min_wallets and a.smart_money_net_sol > 0:
            signals.append(WhaleSignal.SMART_MONEY_CONSENSUS.value)
        if a.smart_money_buyers >= 1 and a.smart_money_net_sol > 0 and a.smart_money_sellers == 0:
            signals.append(WhaleSignal.SMART_MONEY_ACCUMULATION.value)
        if a.whale_net_flow_5m > 0 and a.whales_buying_5m >= 1 and holding >= max(1, distributing):
            signals.append(WhaleSignal.WHALE_ACCUMULATION.value)
        if a.whale_net_flow_5m < 0 and (distributing >= 1 or a.whales_selling_5m >= 2):
            signals.append(WhaleSignal.WHALE_DISTRIBUTION.value)
        if a.insider_sell_sol > 0 or a.dev_sell_sol >= s.insider_sell_min_sol:
            signals.append(WhaleSignal.INSIDER_SELLING.value)
        if clusters and clusters.coordinated_wallets >= 3 and net_flow(300, set(clusters.wallet_to_cluster)) > 0:
            signals.append(WhaleSignal.COORDINATED_BUYING.value)
        big_exit = liq is not None and a.whale_sell_sol_5m >= 0.05 * liq
        whale_dump = distributing >= 2 and net_flow(300, whales) < 0
        if big_exit or whale_dump:
            signals.append(WhaleSignal.WHALE_EXIT_WARNING.value)
            a.hard_warning = f"whales sold {a.whale_sell_sol_5m:.2f} SOL in 5m ({(a.whale_sell_sol_5m / liq * 100) if liq else 0:.1f}% of liquidity)"
        if not signals:
            signals.append(WhaleSignal.NO_SIGNIFICANT_WHALE_ACTIVITY.value)
        a.signals = signals
        priority = [
            WhaleSignal.WHALE_EXIT_WARNING,
            WhaleSignal.INSIDER_SELLING,
            WhaleSignal.WHALE_DISTRIBUTION,
            WhaleSignal.COORDINATED_BUYING,
            WhaleSignal.SMART_MONEY_CONSENSUS,
            WhaleSignal.SMART_MONEY_ACCUMULATION,
            WhaleSignal.WHALE_ACCUMULATION,
            WhaleSignal.NO_SIGNIFICANT_WHALE_ACTIVITY,
        ]
        a.primary_signal = next(p.value for p in priority if p.value in signals)

        # --- scores -----------------------------------------------------------------------------------
        acc = 0.0
        if liq:
            acc += min(0.4, max(0.0, a.whale_net_flow_5m / liq) * 4)
        acc += min(0.3, 0.12 * a.smart_money_buyers)
        acc += min(0.2, 0.1 * a.independent_profitable_buyers)
        if a.accumulation_trend == "increasing":
            acc += 0.1
        a.accumulation_score = round(min(1.0, acc), 3)
        dist = 0.0
        if liq:
            dist += min(0.5, max(0.0, -a.whale_net_flow_5m / liq) * 5)
        dist += min(0.3, 0.15 * distributing)
        dist += 0.3 if a.insider_sell_sol > 0 else 0.0
        dist += 0.3 if a.dev_sell_sol > 0 else 0.0
        if a.hard_warning:
            dist = max(dist, 0.8)
        a.distribution_score = round(min(1.0, dist), 3)
        profiled = sum(1 for w in (whales | smart_buyers) if w in profiles and profiles[w].tokens_traded > 0)
        a.data_confidence = "high" if (profiled >= 3 and liq) else ("medium" if (profiled >= 1 or liq) else "low")
        if not profiles:
            a.notes.append("no wallet history available yet; smart-money detection inactive")
        a.activities.sort(key=lambda x: x.timestamp, reverse=True)
        return a


def whale_event_rows(mint: str, assessment: WhaleAssessment, liquidity_sol: float | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rows for whale_events and smart_money_events tables (deduped by DB unique constraints)."""
    whale_rows: list[dict[str, Any]] = []
    smart_rows: list[dict[str, Any]] = []
    smart_cats = {WalletCategory.SMART_MONEY.value, WalletCategory.PROFITABLE_EARLY_BUYER.value}
    for act in assessment.activities:
        base = {
            "mint": mint,
            "wallet": act.wallet,
            "signature": act.signature,
            "direction": act.direction,
            "sol_amount": act.sol_amount,
            "occurred_at": act.timestamp,
            "corroborated": act.corroborated,
        }
        if act.category in smart_cats and act.confidence >= 0.5:
            smart_rows.append({**base, "wallet_category": act.category, "wallet_confidence": act.confidence, "wallet_win_rate": act.performance.get("win_rate"), "details": {"performance": act.performance, "cluster_id": act.cluster_id}})
        event_type = "large_buy" if act.direction == "buy" else "large_sell"
        if act.category == WalletCategory.DEVELOPER.value and act.direction == "sell":
            event_type = "dev_sell"
        elif act.category == WalletCategory.INSIDER_ASSOCIATED.value and act.direction == "sell":
            event_type = "insider_sell"
        whale_rows.append(
            {
                **base,
                "event_type": event_type,
                "usd_amount": act.usd_amount,
                "pct_of_liquidity": act.pct_of_liquidity,
                "wallet_category": act.category,
                "wallet_confidence": act.confidence,
                "details": {"performance": act.performance, "cluster_id": act.cluster_id, "liquidity_sol": liquidity_sol},
            }
        )
    return whale_rows, smart_rows
