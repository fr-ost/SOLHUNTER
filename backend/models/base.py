"""Declarative base and shared column helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TokenStatus(StrEnum):
    DISCOVERED = "discovered"
    TRACKING = "tracking"
    CANDIDATE = "candidate"
    WATCHLIST = "watchlist"
    REJECTED = "rejected"
    DEAD = "dead"
    ARCHIVED = "archived"


class PoolKind(StrEnum):
    PUMP_BONDING_CURVE = "pump"
    PUMP_AMM = "pump-amm"
    RAYDIUM = "raydium"
    OTHER = "other"


class TxType(StrEnum):
    CREATE = "create"
    BUY = "buy"
    SELL = "sell"
    MIGRATE = "migrate"
    ADD_LIQUIDITY = "add_liquidity"
    REMOVE_LIQUIDITY = "remove_liquidity"
    TRANSFER = "transfer"


class WalletCategory(StrEnum):
    SMART_MONEY = "smart_money"
    PROFITABLE_EARLY_BUYER = "profitable_early_buyer"
    ACCUMULATING_WHALE = "accumulating_whale"
    DISTRIBUTING_WHALE = "distributing_whale"
    SNIPER = "sniper"
    DEVELOPER = "developer"
    INSIDER_ASSOCIATED = "insider_associated"
    COORDINATED_CLUSTER = "coordinated_cluster"
    MARKET_MAKER = "market_maker"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


class WhaleSignal(StrEnum):
    SMART_MONEY_ACCUMULATION = "SMART_MONEY_ACCUMULATION"
    WHALE_ACCUMULATION = "WHALE_ACCUMULATION"
    WHALE_DISTRIBUTION = "WHALE_DISTRIBUTION"
    SMART_MONEY_CONSENSUS = "SMART_MONEY_CONSENSUS"
    INSIDER_SELLING = "INSIDER_SELLING"
    COORDINATED_BUYING = "COORDINATED_BUYING"
    WHALE_EXIT_WARNING = "WHALE_EXIT_WARNING"
    NO_SIGNIFICANT_WHALE_ACTIVITY = "NO_SIGNIFICANT_WHALE_ACTIVITY"


class GptRecommendation(StrEnum):
    STRONG_CANDIDATE = "STRONG_CANDIDATE"
    WATCHLIST = "WATCHLIST"
    HIGH_RISK = "HIGH_RISK"
    REJECT = "REJECT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertType(StrEnum):
    CANDIDATE = "candidate"
    WATCHLIST = "watchlist"
    WHALE_DISTRIBUTION = "whale_distribution"
    INSIDER_SELLING = "insider_selling"
    LIQUIDITY_WITHDRAWAL = "liquidity_withdrawal"
    RISK_ESCALATION = "risk_escalation"
    SYSTEM = "system"
