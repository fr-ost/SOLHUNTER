"""Token, launch, pool, snapshot and transaction tables."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin, TokenStatus


class Token(Base, TimestampMixin):
    __tablename__ = "tokens"

    mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256))
    symbol: Mapped[str | None] = mapped_column(String(64))
    uri: Mapped[str | None] = mapped_column(String(512))
    decimals: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    creator_wallet: Mapped[str | None] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), default="pumpportal", nullable=False)
    launch_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    bonding_curve_key: Mapped[str | None] = mapped_column(String(64))
    pool_kind: Mapped[str] = mapped_column(String(32), default="pump", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=TokenStatus.DISCOVERED.value, nullable=False, index=True)
    status_reason: Mapped[str | None] = mapped_column(String(512))
    migrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    tracking_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_supply: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB(none_as_null=True))

    launches: Mapped[list[TokenLaunch]] = relationship(back_populates="token", cascade="all, delete-orphan")
    pools: Mapped[list[TokenPool]] = relationship(back_populates="token", cascade="all, delete-orphan")


class TokenLaunch(Base):
    __tablename__ = "token_launches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False, index=True)
    signature: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    creator_wallet: Mapped[str | None] = mapped_column(String(64), index=True)
    launch_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_buy_tokens: Mapped[float | None] = mapped_column(Float)
    initial_buy_sol: Mapped[float | None] = mapped_column(Float)
    v_sol_in_curve: Mapped[float | None] = mapped_column(Float)
    v_tokens_in_curve: Mapped[float | None] = mapped_column(Float)
    market_cap_sol: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="pumpportal", nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    token: Mapped[Token] = relationship(back_populates="launches")


class TokenPool(Base, TimestampMixin):
    __tablename__ = "token_pools"
    __table_args__ = (UniqueConstraint("pool_address", name="uq_token_pools_pool_address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False, index=True)
    pool_address: Mapped[str] = mapped_column(String(64), nullable=False)
    dex_id: Mapped[str] = mapped_column(String(32), nullable=False)
    pool_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_mint: Mapped[str | None] = mapped_column(String(64))
    pool_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_base: Mapped[float | None] = mapped_column(Float)
    liquidity_quote: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)
    price_native: Mapped[float | None] = mapped_column(Float)
    fdv_usd: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="dexscreener", nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    token: Mapped[Token] = relationship(back_populates="pools")


class TokenSnapshot(Base):
    """Point-in-time market state used for filtering, ranking and ML training."""

    __tablename__ = "token_snapshots"
    __table_args__ = (
        Index("ix_token_snapshots_mint_captured", "mint", "captured_at"),
        Index("ix_token_snapshots_kind_captured", "snapshot_kind", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # m1, m3, m5, m10, m15, periodic
    age_seconds: Mapped[int | None] = mapped_column(Integer)
    pool_kind: Mapped[str | None] = mapped_column(String(32))
    price_sol: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)
    sol_usd: Mapped[float | None] = mapped_column(Float)
    market_cap_sol: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    fdv_usd: Mapped[float | None] = mapped_column(Float)
    liquidity_sol: Mapped[float | None] = mapped_column(Float)
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    bonding_progress: Mapped[float | None] = mapped_column(Float)
    v_sol_in_curve: Mapped[float | None] = mapped_column(Float)
    v_tokens_in_curve: Mapped[float | None] = mapped_column(Float)
    trades_total: Mapped[int | None] = mapped_column(Integer)
    buys_1m: Mapped[int | None] = mapped_column(Integer)
    sells_1m: Mapped[int | None] = mapped_column(Integer)
    buys_5m: Mapped[int | None] = mapped_column(Integer)
    sells_5m: Mapped[int | None] = mapped_column(Integer)
    buys_15m: Mapped[int | None] = mapped_column(Integer)
    sells_15m: Mapped[int | None] = mapped_column(Integer)
    buy_volume_sol_5m: Mapped[float | None] = mapped_column(Float)
    sell_volume_sol_5m: Mapped[float | None] = mapped_column(Float)
    volume_sol_1m: Mapped[float | None] = mapped_column(Float)
    volume_sol_5m: Mapped[float | None] = mapped_column(Float)
    volume_sol_15m: Mapped[float | None] = mapped_column(Float)
    net_sol_flow_5m: Mapped[float | None] = mapped_column(Float)
    unique_buyers_5m: Mapped[int | None] = mapped_column(Integer)
    unique_sellers_5m: Mapped[int | None] = mapped_column(Integer)
    unique_buyers_total: Mapped[int | None] = mapped_column(Integer)
    holder_count: Mapped[int | None] = mapped_column(Integer)
    largest_holder_pct: Mapped[float | None] = mapped_column(Float)
    top10_pct: Mapped[float | None] = mapped_column(Float)
    top20_pct: Mapped[float | None] = mapped_column(Float)
    dev_holding_pct: Mapped[float | None] = mapped_column(Float)
    momentum_1m_pct: Mapped[float | None] = mapped_column(Float)
    momentum_3m_pct: Mapped[float | None] = mapped_column(Float)
    momentum_5m_pct: Mapped[float | None] = mapped_column(Float)
    momentum_10m_pct: Mapped[float | None] = mapped_column(Float)
    momentum_15m_pct: Mapped[float | None] = mapped_column(Float)
    smart_money_buyers: Mapped[int | None] = mapped_column(Integer)
    whale_net_flow_sol_5m: Mapped[float | None] = mapped_column(Float)
    data_sources: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    data_quality: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    features: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class TokenTransaction(Base):
    """Normalised trade events (buys/sells/creates/migrations) for tracked tokens."""

    __tablename__ = "token_transactions"
    __table_args__ = (
        UniqueConstraint("signature", "mint", "wallet", "tx_type", name="uq_token_transactions_event"),
        Index("ix_token_transactions_mint_time", "mint", "block_time"),
        Index("ix_token_transactions_wallet_time", "wallet", "block_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False)
    wallet: Mapped[str] = mapped_column(String(64), nullable=False)
    tx_type: Mapped[str] = mapped_column(String(24), nullable=False)
    sol_amount: Mapped[float | None] = mapped_column(Float)
    token_amount: Mapped[float | None] = mapped_column(Float)
    price_sol: Mapped[float | None] = mapped_column(Float)
    block_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slot: Mapped[int | None] = mapped_column(BigInteger)
    pool_kind: Mapped[str | None] = mapped_column(String(32))
    v_sol_in_curve: Mapped[float | None] = mapped_column(Float)
    v_tokens_in_curve: Mapped[float | None] = mapped_column(Float)
    market_cap_sol: Mapped[float | None] = mapped_column(Float)
    new_token_balance: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="pumpportal", nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class HolderSnapshot(Base):
    __tablename__ = "holder_snapshots"
    __table_args__ = (Index("ix_holder_snapshots_mint_captured", "mint", "captured_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    holder_count: Mapped[int | None] = mapped_column(Integer)
    total_supply: Mapped[float | None] = mapped_column(Float)
    largest_holder_pct: Mapped[float | None] = mapped_column(Float)
    top10_pct: Mapped[float | None] = mapped_column(Float)
    top20_pct: Mapped[float | None] = mapped_column(Float)
    dev_holding_pct: Mapped[float | None] = mapped_column(Float)
    infra_excluded_pct: Mapped[float | None] = mapped_column(Float)
    top_holders: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB(none_as_null=True))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
