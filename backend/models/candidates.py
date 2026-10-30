"""Candidate scoring, history and GPT analyses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class CandidateScore(Base):
    __tablename__ = "candidate_scores"
    __table_args__ = (
        Index("ix_candidate_scores_mint_time", "mint", "scored_at"),
        Index("ix_candidate_scores_status_score", "status", "overall_score"),
        Index("ix_candidate_scores_scored_at", "scored_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("token_snapshots.id", ondelete="SET NULL"))
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)  # candidate / watchlist / rejected
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    penalties: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    filter_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    rejection_reasons: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    hard_fail: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whale_signal: Mapped[str | None] = mapped_column(String(48))
    whale_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    rug_risk: Mapped[float | None] = mapped_column(Float)
    breakout_p10: Mapped[float | None] = mapped_column(Float)
    breakout_p15: Mapped[float | None] = mapped_column(Float)
    breakout_p20: Mapped[float | None] = mapped_column(Float)
    expected_return_pct: Mapped[float | None] = mapped_column(Float)
    ml_is_experimental: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64))
    gpt_recommendation: Mapped[str | None] = mapped_column(String(32))
    gpt_analysis_id: Mapped[int | None] = mapped_column(BigInteger)
    execution: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    rank: Mapped[int | None] = mapped_column(Integer)
    explanation: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    alerted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class CandidateHistory(Base):
    __tablename__ = "candidate_history"
    __table_args__ = (Index("ix_candidate_history_mint_time", "mint", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    score: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GptAnalysis(Base):
    __tablename__ = "gpt_analyses"
    __table_args__ = (Index("ix_gpt_analyses_mint_time", "mint", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), nullable=False)
    candidate_score_id: Mapped[int | None] = mapped_column(BigInteger)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok / invalid / failed / cached
    recommendation: Mapped[str | None] = mapped_column(String(32))
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
