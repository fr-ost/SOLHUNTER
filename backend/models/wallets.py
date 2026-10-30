"""Wallet intelligence tables: profiles, transactions, positions and relationships."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin, WalletCategory


class WalletProfile(Base, TimestampMixin):
    __tablename__ = "wallet_profiles"

    address: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), default=WalletCategory.UNKNOWN.value, nullable=False, index=True)
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    is_infrastructure: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    realized_pnl_sol: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unrealized_pnl_sol: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Float)
    avg_return_pct: Mapped[float | None] = mapped_column(Float)
    median_return_pct: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    avg_holding_seconds: Mapped[float | None] = mapped_column(Float)
    early_entry_win_rate: Mapped[float | None] = mapped_column(Float)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float)
    rug_exposure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_traded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    closed_positions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_sol_bought: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_sol_sold: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    classification_reasons: Mapped[list[str] | None] = mapped_column(JSONB)


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        UniqueConstraint("signature", "wallet", "mint", "direction", name="uq_wallet_transactions_event"),
        Index("ix_wallet_transactions_wallet_time", "wallet", "block_time"),
        Index("ix_wallet_transactions_mint_time", "mint", "block_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False)
    mint: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # buy / sell / transfer_in / transfer_out
    sol_amount: Mapped[float | None] = mapped_column(Float)
    token_amount: Mapped[float | None] = mapped_column(Float)
    block_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    counterparty: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class WalletTokenPosition(Base, TimestampMixin):
    __tablename__ = "wallet_token_positions"
    __table_args__ = (
        UniqueConstraint("wallet", "mint", name="uq_wallet_token_positions_wallet_mint"),
        Index("ix_wallet_token_positions_mint", "mint"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String(64), nullable=False)
    mint: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_bought: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tokens_sold: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tokens_transferred_out: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tokens_transferred_in: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sol_spent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sol_received: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tokens_held_estimate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_cost_sol_per_token: Mapped[float | None] = mapped_column(Float)
    realized_pnl_sol: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unrealized_pnl_sol: Mapped[float | None] = mapped_column(Float)
    last_mark_price_sol: Mapped[float | None] = mapped_column(Float)
    buy_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sell_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_buy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_trade_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_age_seconds: Mapped[int | None] = mapped_column(Integer)  # token age at first buy
    is_early_buyer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)  # open / partial / closed
    cost_basis_confidence: Mapped[str] = mapped_column(String(16), default="high", nullable=False)
    outcome_rugged: Mapped[bool | None] = mapped_column(Boolean)


class WalletRelationship(Base, TimestampMixin):
    __tablename__ = "wallet_relationships"
    __table_args__ = (
        UniqueConstraint("wallet_a", "wallet_b", "relationship_type", name="uq_wallet_relationships_pair_type"),
        Index("ix_wallet_relationships_b", "wallet_b"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    wallet_a: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    wallet_b: Mapped[str] = mapped_column(String(64), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    observations: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
