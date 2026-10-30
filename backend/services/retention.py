"""Data retention: prune high-volume operational rows while never deleting ML training data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.models import (
    Alert,
    CandidateScore,
    RawEvent,
    TokenSnapshot,
    TokenTransaction,
    TrainingSample,
    WalletTransaction,
    WorkerHeartbeat,
)

log = get_logger(__name__)


async def run_retention() -> dict[str, int]:
    settings = get_settings().retention
    now = datetime.now(UTC)
    deleted: dict[str, int] = {}
    async with session_scope() as session:
        if settings.raw_events_days > 0:
            res = await session.execute(delete(RawEvent).where(RawEvent.received_at < now - timedelta(days=settings.raw_events_days)))
            deleted["raw_events"] = res.rowcount or 0
        if settings.transactions_days > 0:
            res = await session.execute(
                delete(TokenTransaction).where(TokenTransaction.block_time < now - timedelta(days=settings.transactions_days))
            )
            deleted["token_transactions"] = res.rowcount or 0
        if settings.wallet_transactions_days > 0:
            res = await session.execute(
                delete(WalletTransaction).where(WalletTransaction.block_time < now - timedelta(days=settings.wallet_transactions_days))
            )
            deleted["wallet_transactions"] = res.rowcount or 0
        if settings.alerts_days > 0:
            res = await session.execute(delete(Alert).where(Alert.created_at < now - timedelta(days=settings.alerts_days)))
            deleted["alerts"] = res.rowcount or 0
        res = await session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.last_heartbeat_at < now - timedelta(days=2)))
        deleted["worker_heartbeats"] = res.rowcount or 0
        if settings.snapshots_days > 0:
            # Optional: prune only snapshots that are NOT training data (no training sample, no candidate score).
            from sqlalchemy import select

            labelled = select(TrainingSample.snapshot_id)
            scored = select(CandidateScore.snapshot_id).where(CandidateScore.snapshot_id.is_not(None))
            res = await session.execute(
                delete(TokenSnapshot).where(
                    TokenSnapshot.captured_at < now - timedelta(days=settings.snapshots_days),
                    TokenSnapshot.id.not_in(labelled),
                    TokenSnapshot.id.not_in(scored),
                )
            )
            deleted["token_snapshots_unlabelled"] = res.rowcount or 0
        # token_snapshots / training_samples / candidate_scores are intentionally never pruned here
        # (RETENTION_SNAPSHOTS_DAYS=0 by default); they are the model training history.
    if any(deleted.values()):
        log.info("retention.pruned", **deleted)
    return deleted
