"""Paper trading, manual trade journal and risk-management helpers (no execution)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.settings import RiskSettings, get_settings
from backend.models import PaperTrade, TradeJournalEntry, utcnow


def position_size(entry_price_usd: float, stop_pct: float | None = None, risk: RiskSettings | None = None) -> dict[str, Any]:
    """Risk-based position sizing: risk_per_trade% of equity divided by stop distance."""
    risk = risk or get_settings().risk
    stop = stop_pct if stop_pct is not None else risk.stop_loss_pct
    equity = risk.account_equity_usd
    risk_usd = equity * risk.risk_per_trade_pct / 100.0
    size_usd = risk_usd / (stop / 100.0) if stop > 0 else 0.0
    size_usd = min(size_usd, equity)
    return {
        "account_equity_usd": equity,
        "risk_per_trade_pct": risk.risk_per_trade_pct,
        "risk_usd": round(risk_usd, 2),
        "stop_loss_pct": stop,
        "position_size_usd": round(size_usd, 2),
        "token_quantity": round(size_usd / entry_price_usd, 4) if entry_price_usd > 0 else None,
        "stop_price_usd": round(entry_price_usd * (1 - stop / 100.0), 10),
        "targets": [
            {"pct": t, "price_usd": round(entry_price_usd * (1 + t / 100.0), 10), "profit_usd": round(size_usd * t / 100.0, 2)}
            for t in risk.targets
        ],
        "daily_loss_limit_usd": round(equity * risk.daily_loss_limit_pct / 100.0, 2),
        "max_open_positions": risk.max_open_positions,
        "note": "Reference sizing for manual research; not trading advice and not executed by the system.",
    }


async def open_positions_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(PaperTrade).where(PaperTrade.status == "open")) or 0)


async def realized_pnl_today(session: AsyncSession) -> float:
    today = utcnow().date()
    value = await session.scalar(
        select(func.coalesce(func.sum(PaperTrade.realized_pnl_usd), 0.0)).where(
            PaperTrade.status == "closed", func.date(PaperTrade.exit_at) == today
        )
    )
    return float(value or 0.0)


async def risk_state(session: AsyncSession) -> dict[str, Any]:
    risk = get_settings().risk
    open_count = await open_positions_count(session)
    pnl_today = await realized_pnl_today(session)
    limit = risk.account_equity_usd * risk.daily_loss_limit_pct / 100.0
    return {
        "open_positions": open_count,
        "max_open_positions": risk.max_open_positions,
        "can_open_new": open_count < risk.max_open_positions and pnl_today > -limit,
        "realized_pnl_today_usd": round(pnl_today, 2),
        "daily_loss_limit_usd": round(limit, 2),
        "daily_loss_limit_hit": pnl_today <= -limit,
    }


async def create_trade(session: AsyncSession, data: dict[str, Any]) -> PaperTrade:
    trade = PaperTrade(
        mint=data["mint"],
        symbol=data.get("symbol"),
        candidate_score_id=data.get("candidate_score_id"),
        is_paper=data.get("is_paper", True),
        status="open",
        entry_price_usd=float(data["entry_price_usd"]),
        entry_at=data.get("entry_at") or utcnow(),
        entry_liquidity_usd=data.get("entry_liquidity_usd"),
        entry_market_cap_usd=data.get("entry_market_cap_usd"),
        position_size_usd=float(data["position_size_usd"]),
        planned_stop_pct=data.get("planned_stop_pct"),
        planned_targets_pct=data.get("planned_targets_pct"),
        fees_usd=float(data.get("fees_usd") or 0.0),
        slippage_usd=float(data.get("slippage_usd") or 0.0),
        entry_reason=data.get("entry_reason"),
        signal_category=data.get("signal_category"),
        candidate_type=data.get("candidate_type"),
        model_version=data.get("model_version"),
        gpt_recommendation=data.get("gpt_recommendation"),
        extra=data.get("extra"),
    )
    session.add(trade)
    await session.flush()
    return trade


def _close_math(trade: PaperTrade) -> None:
    if trade.exit_price_usd is None or trade.entry_price_usd <= 0:
        return
    gross_return = trade.exit_price_usd / trade.entry_price_usd - 1.0
    gross_pnl = trade.position_size_usd * gross_return
    trade.realized_pnl_usd = round(gross_pnl - trade.fees_usd - trade.slippage_usd, 4)
    trade.realized_return_pct = round(trade.realized_pnl_usd / trade.position_size_usd * 100.0, 4) if trade.position_size_usd else None
    if trade.signal_correct is None and trade.realized_return_pct is not None:
        trade.signal_correct = trade.realized_return_pct > 0


async def update_trade(session: AsyncSession, trade: PaperTrade, data: dict[str, Any]) -> PaperTrade:
    for field in (
        "symbol", "entry_price_usd", "entry_liquidity_usd", "entry_market_cap_usd", "position_size_usd",
        "planned_stop_pct", "planned_targets_pct", "exit_price_usd", "fees_usd", "slippage_usd",
        "entry_reason", "exit_reason", "signal_correct", "signal_category", "candidate_type", "extra",
    ):
        if field in data and data[field] is not None:
            setattr(trade, field, data[field])
    if data.get("exit_at"):
        trade.exit_at = data["exit_at"] if isinstance(data["exit_at"], datetime) else datetime.fromisoformat(data["exit_at"])
    if data.get("status") == "closed" or (trade.exit_price_usd is not None and data.get("status") != "open"):
        trade.status = "closed"
        trade.exit_at = trade.exit_at or utcnow()
        _close_math(trade)
    elif data.get("status") == "open":
        trade.status = "open"
    await session.flush()
    return trade


async def performance(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(PaperTrade).where(PaperTrade.status == "closed"))).scalars().all()

    def _bucket(items: list[PaperTrade]) -> dict[str, Any]:
        n = len(items)
        if not n:
            return {"trades": 0}
        pnls = [t.realized_pnl_usd or 0.0 for t in items]
        rets = [t.realized_return_pct or 0.0 for t in items]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in sorted(items, key=lambda t: t.exit_at or t.entry_at):
            equity += p.realized_pnl_usd or 0.0
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return {
            "trades": n,
            "win_rate": round(len(wins) / n, 4),
            "total_pnl_usd": round(sum(pnls), 2),
            "avg_return_pct": round(sum(rets) / n, 3),
            "median_return_pct": round(sorted(rets)[n // 2], 3),
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
            "max_drawdown_usd": round(max_dd, 2),
            "signal_accuracy": round(sum(1 for t in items if t.signal_correct) / n, 4),
        }

    def _group(attr: str) -> dict[str, Any]:
        groups: dict[str, list[PaperTrade]] = {}
        for t in rows:
            groups.setdefault(getattr(t, attr) or "unspecified", []).append(t)
        return {k: _bucket(v) for k, v in sorted(groups.items())}

    return {
        "overall": _bucket(list(rows)),
        "by_candidate_type": _group("candidate_type"),
        "by_model_version": _group("model_version"),
        "by_signal_category": _group("signal_category"),
        "by_gpt_recommendation": _group("gpt_recommendation"),
        "paper_vs_manual": {
            "paper": _bucket([t for t in rows if t.is_paper]),
            "manual": _bucket([t for t in rows if not t.is_paper]),
        },
    }


async def add_journal_entry(session: AsyncSession, data: dict[str, Any]) -> TradeJournalEntry:
    entry = TradeJournalEntry(
        trade_id=data.get("trade_id"),
        mint=data.get("mint"),
        entry_type=data.get("entry_type") or "note",
        title=data.get("title"),
        content=data["content"],
        tags=data.get("tags"),
    )
    session.add(entry)
    await session.flush()
    return entry
