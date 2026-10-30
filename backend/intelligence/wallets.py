"""Wallet intelligence: per-token positions with cost basis, performance profiles and classification.

Design notes
* Realised and unrealised PnL are kept separate.
* Average-cost basis per (wallet, mint). Tokens sold in excess of tokens bought came from
  transfers we did not observe: that portion is excluded from realised PnL and the position's
  ``cost_basis_confidence`` drops, instead of inventing a cost.
* A wallet is never called "smart money" for being large; only for a track record.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.models import Token, WalletCategory, WalletProfile, WalletRelationship, WalletTokenPosition
from backend.providers.helius import KNOWN_INFRA_OWNERS
from backend.services import keys
from backend.services.redis_client import get_redis

log = get_logger(__name__)


@dataclass
class WalletProfileSummary:
    address: str
    category: str = WalletCategory.UNKNOWN.value
    category_confidence: float = 0.0
    realized_pnl_sol: float = 0.0
    unrealized_pnl_sol: float = 0.0
    win_rate: float | None = None
    profit_factor: float | None = None
    avg_return_pct: float | None = None
    median_return_pct: float | None = None
    closed_positions: int = 0
    tokens_traded: int = 0
    rug_exposure_count: int = 0
    early_entry_win_rate: float | None = None
    avg_holding_seconds: float | None = None
    is_infrastructure: bool = False
    is_developer: bool = False
    cluster_id: str | None = None
    insider_associated: bool = False

    @property
    def is_smart_money(self) -> bool:
        return self.category in {WalletCategory.SMART_MONEY.value, WalletCategory.PROFITABLE_EARLY_BUYER.value} and self.category_confidence >= 0.5

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# ----------------------------------------------------------------------------- positions
@dataclass
class TradeInput:
    wallet: str
    mint: str
    side: str  # buy / sell
    sol: float
    tokens: float
    block_time: datetime
    launch_time: datetime | None = None
    new_token_balance: float | None = None
    price_sol: float | None = None


class PositionUpdater:
    """Applies trades to ``wallet_token_positions`` with average-cost accounting (batched)."""

    def __init__(self) -> None:
        self._pending: list[TradeInput] = []

    def add(self, trade: TradeInput) -> None:
        self._pending.append(trade)

    @property
    def pending(self) -> int:
        return len(self._pending)

    async def flush(self, session: AsyncSession) -> int:
        if not self._pending:
            return 0
        trades, self._pending = self._pending, []
        trades.sort(key=lambda t: t.block_time)
        pairs = {(t.wallet, t.mint) for t in trades}
        rows: dict[tuple[str, str], WalletTokenPosition] = {}
        wallets = {w for w, _ in pairs}
        mints = {m for _, m in pairs}
        existing = (
            await session.execute(select(WalletTokenPosition).where(WalletTokenPosition.wallet.in_(wallets), WalletTokenPosition.mint.in_(mints)))
        ).scalars().all()
        for row in existing:
            rows[(row.wallet, row.mint)] = row
        early_window = get_settings().whale.early_buyer_window_seconds
        for t in trades:
            key = (t.wallet, t.mint)
            pos = rows.get(key)
            if pos is None:
                pos = WalletTokenPosition(wallet=t.wallet, mint=t.mint)
                session.add(pos)
                rows[key] = pos
            apply_trade_to_position(pos, t, early_window)
        await session.flush()
        return len(trades)


_NUMERIC_DEFAULTS = {
    "tokens_bought": 0.0,
    "tokens_sold": 0.0,
    "tokens_transferred_out": 0.0,
    "tokens_transferred_in": 0.0,
    "sol_spent": 0.0,
    "sol_received": 0.0,
    "tokens_held_estimate": 0.0,
    "realized_pnl_sol": 0.0,
    "buy_count": 0,
    "sell_count": 0,
}


def _init_defaults(pos: WalletTokenPosition) -> None:
    """ORM column defaults only apply at INSERT time; a fresh object has None attributes."""
    for name, default in _NUMERIC_DEFAULTS.items():
        if getattr(pos, name) is None:
            setattr(pos, name, default)
    if pos.status is None:
        pos.status = "open"
    if pos.cost_basis_confidence is None:
        pos.cost_basis_confidence = "high"
    if pos.is_early_buyer is None:
        pos.is_early_buyer = False


def apply_trade_to_position(pos: WalletTokenPosition, t: TradeInput, early_window: int = 120) -> None:
    _init_defaults(pos)
    pos.last_trade_at = t.block_time if pos.last_trade_at is None else max(pos.last_trade_at, t.block_time)
    if t.price_sol:
        pos.last_mark_price_sol = t.price_sol
    if t.side == "buy":
        held_before = pos.tokens_held_estimate
        cost_before = (pos.avg_cost_sol_per_token or 0.0) * held_before
        pos.tokens_bought += t.tokens
        pos.sol_spent += t.sol
        pos.buy_count += 1
        pos.tokens_held_estimate = held_before + t.tokens
        if pos.tokens_held_estimate > 0:
            pos.avg_cost_sol_per_token = (cost_before + t.sol) / pos.tokens_held_estimate
        if pos.first_buy_at is None or t.block_time < pos.first_buy_at:
            pos.first_buy_at = t.block_time
            if t.launch_time is not None:
                pos.entry_age_seconds = max(0, int((t.block_time - t.launch_time).total_seconds()))
                pos.is_early_buyer = pos.entry_age_seconds <= early_window
    else:
        known = min(t.tokens, pos.tokens_held_estimate)
        unknown = t.tokens - known
        avg_cost = pos.avg_cost_sol_per_token or 0.0
        if t.tokens > 0:
            proceeds_known = t.sol * (known / t.tokens)
            pos.realized_pnl_sol += proceeds_known - known * avg_cost
        if unknown > 1e-9:
            pos.cost_basis_confidence = "low"
            pos.tokens_transferred_in += unknown
        pos.tokens_sold += t.tokens
        pos.sol_received += t.sol
        pos.sell_count += 1
        pos.tokens_held_estimate = max(0.0, pos.tokens_held_estimate - t.tokens)
    if t.new_token_balance is not None:
        delta = t.new_token_balance - pos.tokens_held_estimate
        if abs(delta) > max(1.0, 0.001 * max(pos.tokens_bought, 1.0)):
            if delta > 0:
                pos.tokens_transferred_in += delta
            else:
                pos.tokens_transferred_out += -delta
            if pos.cost_basis_confidence == "high":
                pos.cost_basis_confidence = "medium"
        pos.tokens_held_estimate = max(0.0, t.new_token_balance)
    if pos.tokens_held_estimate > 0 and pos.last_mark_price_sol and pos.avg_cost_sol_per_token is not None:
        pos.unrealized_pnl_sol = pos.tokens_held_estimate * (pos.last_mark_price_sol - pos.avg_cost_sol_per_token)
    elif pos.tokens_held_estimate <= 0:
        pos.unrealized_pnl_sol = 0.0
    if pos.tokens_bought > 0 and pos.tokens_held_estimate <= 0.01 * pos.tokens_bought and pos.sell_count > 0:
        pos.status = "closed"
    elif pos.sell_count > 0:
        pos.status = "partial"
    else:
        pos.status = "open"


async def mark_token_rugged(session: AsyncSession, mint: str) -> int:
    res = await session.execute(update(WalletTokenPosition).where(WalletTokenPosition.mint == mint).values(outcome_rugged=True))
    return res.rowcount or 0


# ----------------------------------------------------------------------------- profiles
def compute_profile_stats(positions: list[WalletTokenPosition], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    realized = sum(p.realized_pnl_sol for p in positions)
    unrealized = sum(p.unrealized_pnl_sol or 0.0 for p in positions)
    sold = [p for p in positions if p.sell_count > 0]
    returns: list[float] = []
    holds: list[float] = []
    gross_profit = 0.0
    gross_loss = 0.0
    early_wins = 0
    early_total = 0
    for p in sold:
        cost_of_sold = p.sol_received - p.realized_pnl_sol
        if cost_of_sold > 0:
            returns.append(p.realized_pnl_sol / cost_of_sold * 100.0)
        if p.realized_pnl_sol > 0:
            gross_profit += p.realized_pnl_sol
        else:
            gross_loss += -p.realized_pnl_sol
        if p.first_buy_at and p.last_trade_at:
            holds.append((p.last_trade_at - p.first_buy_at).total_seconds())
        if p.is_early_buyer:
            early_total += 1
            early_wins += 1 if p.realized_pnl_sol > 0 else 0
    wins = sum(1 for p in sold if p.realized_pnl_sol > 0)
    # drawdown of cumulative realised pnl in chronological order
    equity = peak = max_dd = 0.0
    for p in sorted(sold, key=lambda p: p.last_trade_at or now):
        equity += p.realized_pnl_sol
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    rug_exposure = sum(1 for p in positions if p.outcome_rugged and p.tokens_held_estimate > 0)
    return {
        "realized_pnl_sol": realized,
        "unrealized_pnl_sol": unrealized,
        "win_rate": (wins / len(sold)) if sold else None,
        "avg_return_pct": statistics.fmean(returns) if returns else None,
        "median_return_pct": statistics.median(returns) if returns else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else 99.0),
        "avg_holding_seconds": statistics.fmean(holds) if holds else None,
        "early_entry_win_rate": (early_wins / early_total) if early_total else None,
        "max_drawdown_sol": max_dd,
        "rug_exposure_count": rug_exposure,
        "tokens_traded": len(positions),
        "closed_positions": sum(1 for p in positions if p.status == "closed"),
        "positions_with_sales": len(sold),
        "trades_count": sum(p.buy_count + p.sell_count for p in positions),
        "total_sol_bought": sum(p.sol_spent for p in positions),
        "total_sol_sold": sum(p.sol_received for p in positions),
        "early_entries": sum(1 for p in positions if p.is_early_buyer),
        "sniper_entries": sum(1 for p in positions if p.entry_age_seconds is not None and p.entry_age_seconds <= get_settings().whale.sniper_window_seconds),
        "low_confidence_positions": sum(1 for p in positions if p.cost_basis_confidence == "low"),
        "first_seen_at": min((p.first_buy_at for p in positions if p.first_buy_at), default=None),
        "last_active_at": max((p.last_trade_at for p in positions if p.last_trade_at), default=None),
    }


def classify_wallet(stats: dict[str, Any], context: dict[str, Any] | None = None) -> tuple[str, float, list[str]]:
    """Return (category, confidence, reasons). Context: is_developer, insider_associated, cluster_id, is_infrastructure."""
    s = get_settings().whale
    ctx = context or {}
    reasons: list[str] = []
    if ctx.get("is_infrastructure"):
        return WalletCategory.INFRASTRUCTURE.value, 1.0, ["known program/pool/burn account"]
    if ctx.get("is_developer"):
        reasons.append("creator of at least one tracked token")
        return WalletCategory.DEVELOPER.value, 0.9, reasons
    if ctx.get("insider_associated"):
        reasons.append("relationship to a developer wallet")
        return WalletCategory.INSIDER_ASSOCIATED.value, float(ctx.get("insider_confidence", 0.6)), reasons
    sold = int(stats.get("positions_with_sales") or 0)
    win_rate = stats.get("win_rate")
    pf = stats.get("profit_factor")
    pnl = float(stats.get("realized_pnl_sol") or 0.0)
    sniper = int(stats.get("sniper_entries") or 0)
    tokens = int(stats.get("tokens_traded") or 0)
    trades = int(stats.get("trades_count") or 0)
    avg_hold = stats.get("avg_holding_seconds")
    if sniper >= 3 and avg_hold is not None and avg_hold < 180 and sniper / max(1, tokens) > 0.5:
        reasons.append(f"{sniper} entries within {s.sniper_window_seconds}s of launch with short holds")
        return WalletCategory.SNIPER.value, min(0.95, 0.5 + sniper * 0.08), reasons
    if sold >= s.smart_money_min_closed_positions and win_rate is not None and win_rate >= s.smart_money_min_win_rate and (pf or 0) >= s.smart_money_min_profit_factor and pnl >= s.smart_money_min_realized_pnl_sol:
        sample_conf = min(1.0, sold / (s.smart_money_min_closed_positions * 3))
        conf = 0.5 + 0.5 * sample_conf
        if (stats.get("low_confidence_positions") or 0) > sold * 0.3:
            conf *= 0.8
            reasons.append("some cost bases uncertain")
        reasons.append(f"{sold} realised positions, win rate {win_rate:.0%}, profit factor {pf:.1f}, pnl {pnl:.1f} SOL")
        return WalletCategory.SMART_MONEY.value, round(conf, 3), reasons
    early = int(stats.get("early_entries") or 0)
    early_wr = stats.get("early_entry_win_rate")
    if early >= 5 and early_wr is not None and early_wr >= 0.5 and pnl > 0:
        reasons.append(f"{early} early entries with {early_wr:.0%} win rate and positive pnl")
        return WalletCategory.PROFITABLE_EARLY_BUYER.value, round(min(0.9, 0.4 + early * 0.05), 3), reasons
    if ctx.get("cluster_id") and int(ctx.get("cluster_size") or 0) >= 3:
        reasons.append(f"member of a {ctx.get('cluster_size')}-wallet coordinated cluster")
        return WalletCategory.COORDINATED_CLUSTER.value, float(ctx.get("cluster_confidence", 0.6)), reasons
    bought = float(stats.get("total_sol_bought") or 0.0)
    sold_sol = float(stats.get("total_sol_sold") or 0.0)
    if trades >= 200 and tokens >= 20 and bought > 0 and abs(bought - sold_sol) / bought < 0.1:
        reasons.append("very high two-sided trade count with balanced flow")
        return WalletCategory.MARKET_MAKER.value, 0.5, reasons
    net_flow = float(ctx.get("net_flow_24h_sol") or 0.0)
    if net_flow >= s.min_whale_position_sol * 5:
        reasons.append(f"net inflow {net_flow:.1f} SOL over 24h")
        return WalletCategory.ACCUMULATING_WHALE.value, 0.5, reasons
    if net_flow <= -s.min_whale_position_sol * 5:
        reasons.append(f"net outflow {abs(net_flow):.1f} SOL over 24h")
        return WalletCategory.DISTRIBUTING_WHALE.value, 0.5, reasons
    return WalletCategory.UNKNOWN.value, 0.0, reasons or ["insufficient history"]


class WalletProfiler:
    def __init__(self) -> None:
        self._last_profiled: dict[str, float] = {}
        self._cache: dict[str, tuple[float, WalletProfileSummary]] = {}

    async def context_for(self, session: AsyncSession, addresses: list[str]) -> dict[str, dict[str, Any]]:
        if not addresses:
            return {}
        s = get_settings().whale
        dev_rows = (await session.execute(select(Token.creator_wallet, func.count()).where(Token.creator_wallet.in_(addresses)).group_by(Token.creator_wallet))).all()
        developers = {r[0] for r in dev_rows}
        all_devs = {r[0] for r in (await session.execute(select(Token.creator_wallet).where(Token.creator_wallet.is_not(None)).distinct())).all()} if addresses else set()
        rel_rows = (
            await session.execute(
                select(WalletRelationship).where(
                    ((WalletRelationship.wallet_a.in_(addresses)) | (WalletRelationship.wallet_b.in_(addresses))), WalletRelationship.confidence >= s.cluster_min_confidence
                )
            )
        ).scalars().all()
        ctx: dict[str, dict[str, Any]] = {a: {"is_developer": a in developers, "is_infrastructure": a in KNOWN_INFRA_OWNERS} for a in addresses}
        for rel in rel_rows:
            for me, other in ((rel.wallet_a, rel.wallet_b), (rel.wallet_b, rel.wallet_a)):
                if me in ctx and other in all_devs and not ctx[me]["is_developer"]:
                    ctx[me]["insider_associated"] = True
                    ctx[me]["insider_confidence"] = max(ctx[me].get("insider_confidence", 0.0), rel.confidence)
        return ctx

    async def recompute(self, session: AsyncSession, addresses: list[str], mark_prices: dict[str, float] | None = None, force: bool = False) -> dict[str, WalletProfileSummary]:
        s = get_settings().whale
        now_ts = datetime.now(UTC).timestamp()
        todo = [a for a in addresses if force or now_ts - self._last_profiled.get(a, 0) > s.profile_cache_seconds]
        out: dict[str, WalletProfileSummary] = {}
        if not todo:
            return out
        positions = (await session.execute(select(WalletTokenPosition).where(WalletTokenPosition.wallet.in_(todo)))).scalars().all()
        by_wallet: dict[str, list[WalletTokenPosition]] = {a: [] for a in todo}
        for p in positions:
            if mark_prices and p.mint in mark_prices and p.tokens_held_estimate > 0 and p.avg_cost_sol_per_token is not None:
                p.last_mark_price_sol = mark_prices[p.mint]
                p.unrealized_pnl_sol = p.tokens_held_estimate * (mark_prices[p.mint] - p.avg_cost_sol_per_token)
            by_wallet.setdefault(p.wallet, []).append(p)
        contexts = await self.context_for(session, todo)
        now = datetime.now(UTC)
        for address in todo:
            stats = compute_profile_stats(by_wallet.get(address, []), now)
            category, confidence, reasons = classify_wallet(stats, contexts.get(address))
            values = {
                "address": address,
                "category": category,
                "category_confidence": confidence,
                "is_infrastructure": bool(contexts.get(address, {}).get("is_infrastructure")),
                "realized_pnl_sol": stats["realized_pnl_sol"],
                "unrealized_pnl_sol": stats["unrealized_pnl_sol"],
                "win_rate": stats["win_rate"],
                "avg_return_pct": stats["avg_return_pct"],
                "median_return_pct": stats["median_return_pct"],
                "profit_factor": stats["profit_factor"],
                "avg_holding_seconds": stats["avg_holding_seconds"],
                "early_entry_win_rate": stats["early_entry_win_rate"],
                "max_drawdown_pct": None,
                "rug_exposure_count": stats["rug_exposure_count"],
                "tokens_traded": stats["tokens_traded"],
                "closed_positions": stats["closed_positions"],
                "trades_count": stats["trades_count"],
                "total_sol_bought": stats["total_sol_bought"],
                "total_sol_sold": stats["total_sol_sold"],
                "first_seen_at": stats["first_seen_at"],
                "last_active_at": stats["last_active_at"],
                "stats": json.loads(json.dumps({k: v for k, v in stats.items() if k not in {"first_seen_at", "last_active_at"}}, default=str)),
                "classification_reasons": reasons,
                "created_at": now,
                "updated_at": now,
            }
            stmt = pg_insert(WalletProfile).values(**values)
            update_cols = {k: getattr(stmt.excluded, k) for k in values if k not in {"address", "created_at"}}
            await session.execute(stmt.on_conflict_do_update(index_elements=[WalletProfile.address], set_=update_cols))
            summary = WalletProfileSummary(
                address=address,
                category=category,
                category_confidence=confidence,
                realized_pnl_sol=stats["realized_pnl_sol"],
                unrealized_pnl_sol=stats["unrealized_pnl_sol"],
                win_rate=stats["win_rate"],
                profit_factor=stats["profit_factor"],
                avg_return_pct=stats["avg_return_pct"],
                median_return_pct=stats["median_return_pct"],
                closed_positions=stats["closed_positions"],
                tokens_traded=stats["tokens_traded"],
                rug_exposure_count=stats["rug_exposure_count"],
                early_entry_win_rate=stats["early_entry_win_rate"],
                avg_holding_seconds=stats["avg_holding_seconds"],
                is_infrastructure=values["is_infrastructure"],
                is_developer=bool(contexts.get(address, {}).get("is_developer")),
                insider_associated=bool(contexts.get(address, {}).get("insider_associated")),
            )
            out[address] = summary
            self._last_profiled[address] = now_ts
            self._cache[address] = (now_ts, summary)
            try:
                await get_redis().set(keys.cache_key("wallet_profile", address), json.dumps(summary.to_dict(), default=str), ex=s.profile_cache_seconds)
            except Exception:  # noqa: BLE001
                pass
        return out

    async def load(self, session: AsyncSession, addresses: list[str]) -> dict[str, WalletProfileSummary]:
        """Profiles for the given wallets (memory -> Redis -> DB), without recomputation."""
        s = get_settings().whale
        now_ts = datetime.now(UTC).timestamp()
        out: dict[str, WalletProfileSummary] = {}
        missing: list[str] = []
        for a in addresses:
            cached = self._cache.get(a)
            if cached and now_ts - cached[0] < s.profile_cache_seconds:
                out[a] = cached[1]
            else:
                missing.append(a)
        if missing:
            try:
                r = get_redis()
                raws = await r.mget([keys.cache_key("wallet_profile", a) for a in missing])
            except Exception:  # noqa: BLE001
                raws = [None] * len(missing)
            still_missing = []
            for a, raw in zip(missing, raws, strict=False):
                if raw:
                    try:
                        data = json.loads(raw)
                        out[a] = WalletProfileSummary(**{k: v for k, v in data.items() if k in WalletProfileSummary.__dataclass_fields__})
                        self._cache[a] = (now_ts, out[a])
                        continue
                    except (TypeError, ValueError):
                        pass
                still_missing.append(a)
            if still_missing:
                rows = (await session.execute(select(WalletProfile).where(WalletProfile.address.in_(still_missing)))).scalars().all()
                for row in rows:
                    summary = WalletProfileSummary(
                        address=row.address,
                        category=row.category,
                        category_confidence=row.category_confidence,
                        realized_pnl_sol=row.realized_pnl_sol,
                        unrealized_pnl_sol=row.unrealized_pnl_sol,
                        win_rate=row.win_rate,
                        profit_factor=row.profit_factor,
                        avg_return_pct=row.avg_return_pct,
                        median_return_pct=row.median_return_pct,
                        closed_positions=row.closed_positions,
                        tokens_traded=row.tokens_traded,
                        rug_exposure_count=row.rug_exposure_count,
                        early_entry_win_rate=row.early_entry_win_rate,
                        avg_holding_seconds=row.avg_holding_seconds,
                        is_infrastructure=row.is_infrastructure,
                        is_developer=row.category == WalletCategory.DEVELOPER.value,
                        insider_associated=row.category == WalletCategory.INSIDER_ASSOCIATED.value,
                    )
                    out[row.address] = summary
                    self._cache[row.address] = (now_ts, summary)
        for a in addresses:
            out.setdefault(a, WalletProfileSummary(address=a, is_infrastructure=a in KNOWN_INFRA_OWNERS))
        return out
