"""Operational tables: alerts, provider status/usage, AI spend, checkpoints, heartbeats, settings, raw events."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_created", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str | None] = mapped_column(String(64), index=True)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="telegram", nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # sent / failed / suppressed / pending
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    urgent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProviderStatus(Base):
    __tablename__ = "provider_status"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # connected / degraded / down / disabled
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    reconnect_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderUsageDaily(Base):
    __tablename__ = "provider_usage_daily"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    requests: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    errors: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rate_limited: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    credits_used: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AISpendDaily(Base):
    __tablename__ = "ai_spend_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    analyses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScannerCheckpoint(Base):
    __tablename__ = "scanner_checkpoints"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(256))
    version: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    is_leader: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64))


class RawEvent(Base):
    """Raw provider payloads for tracked tokens (bounded by retention policy)."""

    __tablename__ = "raw_events"
    __table_args__ = (Index("ix_raw_events_received", "received_at"), Index("ix_raw_events_mint", "mint"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    mint: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
