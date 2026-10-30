"""ML model registry, predictions and training samples."""

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
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_versions_name_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(48), nullable=False, index=True)  # rug_risk / breakout / expected_return
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    training_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    positive_samples: Mapped[int | None] = mapped_column(Integer)
    feature_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    artifact: Mapped[bytes | None] = mapped_column(LargeBinary)
    artifact_format: Mapped[str] = mapped_column(String(16), default="joblib", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_experimental: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1024))


class ModelPrediction(Base):
    __tablename__ = "model_predictions"
    __table_args__ = (Index("ix_model_predictions_mint_time", "mint", "predicted_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("token_snapshots.id", ondelete="SET NULL"))
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_experimental: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rug_risk: Mapped[float | None] = mapped_column(Float)
    breakout_p10: Mapped[float | None] = mapped_column(Float)
    breakout_p15: Mapped[float | None] = mapped_column(Float)
    breakout_p20: Mapped[float | None] = mapped_column(Float)
    expected_return_pct: Mapped[float | None] = mapped_column(Float)
    expected_downside_pct: Mapped[float | None] = mapped_column(Float)
    expected_time_to_target_s: Mapped[float | None] = mapped_column(Float)
    p_stop_loss: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class TrainingSample(Base):
    __tablename__ = "training_samples"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_training_samples_snapshot_id"),
        Index("ix_training_samples_snapshot_at", "snapshot_at"),
        Index("ix_training_samples_labelled", "label_complete", "snapshot_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("token_snapshots.id", ondelete="CASCADE"), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    entry_price_sol: Mapped[float | None] = mapped_column(Float)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_return_pct: Mapped[float | None] = mapped_column(Float)
    min_return_pct: Mapped[float | None] = mapped_column(Float)
    close_return_pct: Mapped[float | None] = mapped_column(Float)
    hit_10: Mapped[bool | None] = mapped_column(Boolean)
    hit_15: Mapped[bool | None] = mapped_column(Boolean)
    hit_20: Mapped[bool | None] = mapped_column(Boolean)
    hit_stop: Mapped[bool | None] = mapped_column(Boolean)
    time_to_10_s: Mapped[float | None] = mapped_column(Float)
    time_to_15_s: Mapped[float | None] = mapped_column(Float)
    time_to_20_s: Mapped[float | None] = mapped_column(Float)
    rugged: Mapped[bool | None] = mapped_column(Boolean)
    liquidity_removed: Mapped[bool | None] = mapped_column(Boolean)
    dead: Mapped[bool | None] = mapped_column(Boolean)
    observations: Mapped[int | None] = mapped_column(Integer)
    label_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    labelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
