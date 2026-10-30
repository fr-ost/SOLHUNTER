"""Historical candidates, alerts and realised outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_api_key
from backend.api.serializers import orm_to_dict, rows_to_dicts
from backend.database.engine import get_db_session
from backend.models import Alert, CandidateScore, Token, TrainingSample

router = APIRouter(prefix="/history", tags=["history"], dependencies=[Depends(require_api_key)])


@router.get("/candidates")
async def candidate_history(
    status: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    stmt = (
        select(CandidateScore, Token.symbol, Token.name, TrainingSample)
        .join(Token, Token.mint == CandidateScore.mint)
        .join(TrainingSample, TrainingSample.snapshot_id == CandidateScore.snapshot_id, isouter=True)
        .where(CandidateScore.status.in_(["candidate", "watchlist"]))
    )
    if status:
        stmt = stmt.where(CandidateScore.status == status)
    if since:
        stmt = stmt.where(CandidateScore.scored_at >= since)
    if until:
        stmt = stmt.where(CandidateScore.scored_at <= until)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await session.execute(stmt.order_by(CandidateScore.scored_at.desc()).limit(limit).offset(offset))).all()
    items = []
    for score, symbol, name, sample in rows:
        item = orm_to_dict(score, exclude={"filter_results", "execution", "whale_summary", "components", "penalties"})
        item["symbol"] = symbol
        item["name"] = name
        item["outcome"] = (
            {
                "label_complete": sample.label_complete,
                "max_return_pct": sample.max_return_pct,
                "min_return_pct": sample.min_return_pct,
                "close_return_pct": sample.close_return_pct,
                "hit_10": sample.hit_10,
                "hit_15": sample.hit_15,
                "hit_20": sample.hit_20,
                "hit_stop": sample.hit_stop,
                "rugged": sample.rugged,
                "horizon_minutes": sample.horizon_minutes,
            }
            if sample
            else None
        )
        items.append(item)
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@router.get("/outcomes/summary")
async def outcome_summary(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """Realised outcome statistics for past candidates (from labelled training samples)."""
    rows = (
        await session.execute(
            select(CandidateScore.status, TrainingSample)
            .join(TrainingSample, TrainingSample.snapshot_id == CandidateScore.snapshot_id)
            .where(CandidateScore.status.in_(["candidate", "watchlist"]), TrainingSample.label_complete.is_(True))
        )
    ).all()
    buckets: dict[str, list[TrainingSample]] = {}
    for status, sample in rows:
        buckets.setdefault(status, []).append(sample)

    def _stats(samples: list[TrainingSample]) -> dict[str, Any]:
        n = len(samples)
        if not n:
            return {"count": 0}
        return {
            "count": n,
            "hit_10_rate": round(sum(1 for s in samples if s.hit_10) / n, 4),
            "hit_15_rate": round(sum(1 for s in samples if s.hit_15) / n, 4),
            "hit_20_rate": round(sum(1 for s in samples if s.hit_20) / n, 4),
            "stop_rate": round(sum(1 for s in samples if s.hit_stop) / n, 4),
            "rug_rate": round(sum(1 for s in samples if s.rugged) / n, 4),
            "avg_max_return_pct": round(sum((s.max_return_pct or 0.0) for s in samples) / n, 3),
            "avg_close_return_pct": round(sum((s.close_return_pct or 0.0) for s in samples) / n, 3),
        }

    return {status: _stats(samples) for status, samples in buckets.items()} | {"note": "Outcomes measured over the labelling horizon after the scoring snapshot; not trade results."}


@router.get("/alerts")
async def alert_history(
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    total = await session.scalar(select(func.count()).select_from(Alert)) or 0
    rows = (await session.execute(select(Alert).order_by(Alert.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"items": rows_to_dicts(list(rows), exclude={"payload"}), "total": int(total), "limit": limit, "offset": offset}
