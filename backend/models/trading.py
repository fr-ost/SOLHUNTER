"""Paper trading and manual trade journal."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class PaperTrade(Base, TimestampMixin):
    __tablename__ = "paper_trades"
    __table_args__ = (Index("ix_paper_trades_status_entry", "status", "entry_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    candidate_score_id: Mapped[int | None] = mapped_column(BigInteger)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    entry_price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_liquidity_usd: Mapped[float | None] = mapped_column(Float)
    entry_market_cap_usd: Mapped[float | None] = mapped_column(Float)
    position_size_usd: Mapped[float] = mapped_column(Float, nullable=False)
    planned_stop_pct: Mapped[float | None] = mapped_column(Float)
    planned_targets_pct: Mapped[list[float] | None] = mapped_column(JSONB)
    exit_price_usd: Mapped[float | None] = mapped_column(Float)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fees_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    slippage_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    realized_pnl_usd: Mapped[float | None] = mapped_column(Float)
    realized_return_pct: Mapped[float | None] = mapped_column(Float)
    entry_reason: Mapped[str | None] = mapped_column(Text)
    exit_reason: Mapped[str | None] = mapped_column(Text)
    signal_correct: Mapped[bool | None] = mapped_column(Boolean)
    signal_category: Mapped[str | None] = mapped_column(String(48))
    candidate_type: Mapped[str | None] = mapped_column(String(48))
    model_version: Mapped[str | None] = mapped_column(String(64))
    gpt_recommendation: Mapped[str | None] = mapped_column(String(32))
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class TradeJournalEntry(Base, TimestampMixin):
    __tablename__ = "trade_journal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("paper_trades.id", ondelete="SET NULL"), index=True)
    mint: Mapped[str | None] = mapped_column(String(64), index=True)
    entry_type: Mapped[str] = mapped_column(String(24), default="note", nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(JSONB)
