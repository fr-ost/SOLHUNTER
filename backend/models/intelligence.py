"""Whale, smart-money and risk event tables."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class WhaleEvent(Base):
    __tablename__ = "whale_events"
    __table_args__ = (
        UniqueConstraint("mint", "wallet", "signature", "event_type", name="uq_whale_events_event"),
        Index("ix_whale_events_mint_time", "mint", "occurred_at"),
        Index("ix_whale_events_time", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    wallet: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature: Mapped[str | None] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # large_buy, large_sell, accumulation, distribution
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # buy / sell
    sol_amount: Mapped[float | None] = mapped_column(Float)
    usd_amount: Mapped[float | None] = mapped_column(Float)
    pct_of_liquidity: Mapped[float | None] = mapped_column(Float)
    wallet_category: Mapped[str | None] = mapped_column(String(32))
    wallet_confidence: Mapped[float | None] = mapped_column(Float)
    corroborated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class SmartMoneyEvent(Base):
    __tablename__ = "smart_money_events"
    __table_args__ = (
        UniqueConstraint("mint", "wallet", "signature", "direction", name="uq_smart_money_events_event"),
        Index("ix_smart_money_events_mint_time", "mint", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    wallet: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature: Mapped[str | None] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    sol_amount: Mapped[float | None] = mapped_column(Float)
    wallet_category: Mapped[str] = mapped_column(String(32), nullable=False)
    wallet_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    wallet_win_rate: Mapped[float | None] = mapped_column(Float)
    corroborated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class TokenRiskEvent(Base):
    __tablename__ = "token_risk_events"
    __table_args__ = (Index("ix_token_risk_events_mint_time", "mint", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # info / warning / critical
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(256), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
