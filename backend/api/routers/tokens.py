"""Token research endpoints: search, detail, snapshots, transactions, holders, manual tracking."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import Principal, require_api_key
from backend.api.serializers import orm_to_dict, rows_to_dicts
from backend.database.engine import get_db_session
from backend.models import (
    CandidateScore,
    HolderSnapshot,
    Token,
    TokenPool,
    TokenRiskEvent,
    TokenSnapshot,
    TokenTransaction,
    utcnow,
)
from backend.services import keys
from backend.services.redis_client import get_redis

router = APIRouter(prefix="/tokens", tags=["tokens"], dependencies=[Depends(require_api_key)])


class TrackRequest(BaseModel):
    mint: str = Field(min_length=32, max_length=64)
    reason: str | None = Field(default=None, max_length=256)


@router.get("/search")
async def search_tokens(
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    pattern = f"%{q}%"
    stmt = (
        select(Token)
        .where(or_(Token.mint == q, Token.symbol.ilike(pattern), Token.name.ilike(pattern)))
        .order_by(Token.first_seen_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {"items": rows_to_dicts(list(rows), exclude={"metadata_json"}), "query": q}


@router.get("/recent")
async def recent_tokens(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    stmt = select(Token)
    if status:
        stmt = stmt.where(Token.status == status)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await session.execute(stmt.order_by(Token.first_seen_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"items": rows_to_dicts(list(rows), exclude={"metadata_json"}), "total": int(total), "limit": limit, "offset": offset}


@router.get("/{mint}")
async def token_detail(mint: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    token = await session.get(Token, mint)
    if token is None:
        raise HTTPException(404, "Token not found")
    latest_snapshot = (
        await session.execute(select(TokenSnapshot).where(TokenSnapshot.mint == mint).order_by(TokenSnapshot.captured_at.desc()).limit(1))
    ).scalar_one_or_none()
    latest_score = (
        await session.execute(select(CandidateScore).where(CandidateScore.mint == mint).order_by(CandidateScore.scored_at.desc()).limit(1))
    ).scalar_one_or_none()
    pools = (await session.execute(select(TokenPool).where(TokenPool.mint == mint))).scalars().all()
    return {
        "token": orm_to_dict(token),
        "latest_snapshot": orm_to_dict(latest_snapshot, exclude={"features"}) if latest_snapshot else None,
        "latest_score": orm_to_dict(latest_score, exclude={"filter_results", "components", "execution", "whale_summary"}) if latest_score else None,
        "pools": rows_to_dicts(list(pools), exclude={"raw"}),
    }


@router.get("/{mint}/snapshots")
async def token_snapshots(
    mint: str, limit: int = Query(300, ge=1, le=2000), session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    rows = (
        await session.execute(select(TokenSnapshot).where(TokenSnapshot.mint == mint).order_by(TokenSnapshot.captured_at.asc()).limit(limit))
    ).scalars().all()
    return {"items": rows_to_dicts(list(rows), exclude={"features", "data_sources"})}


@router.get("/{mint}/transactions")
async def token_transactions(
    mint: str, limit: int = Query(200, ge=1, le=1000), session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(TokenTransaction).where(TokenTransaction.mint == mint).order_by(TokenTransaction.block_time.desc()).limit(limit)
        )
    ).scalars().all()
    return {"items": rows_to_dicts(list(rows), exclude={"raw"})}


@router.get("/{mint}/holders")
async def token_holders(mint: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    row = (
        await session.execute(select(HolderSnapshot).where(HolderSnapshot.mint == mint).order_by(HolderSnapshot.captured_at.desc()).limit(1))
    ).scalar_one_or_none()
    if row is None:
        return {"snapshot": None, "available": False, "reason": "No holder snapshot captured for this token"}
    return {"snapshot": orm_to_dict(row), "available": True}


@router.get("/{mint}/risk-events")
async def token_risk_events(mint: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    rows = (
        await session.execute(select(TokenRiskEvent).where(TokenRiskEvent.mint == mint).order_by(TokenRiskEvent.occurred_at.desc()).limit(100))
    ).scalars().all()
    return {"items": rows_to_dicts(list(rows))}


@router.post("/track", status_code=202)
async def request_tracking(body: TrackRequest, principal: Principal = Depends(require_api_key)) -> dict[str, Any]:
    """Ask the scanner to start tracking a mint (processed asynchronously by the worker)."""
    payload = {"mint": body.mint, "reason": body.reason, "requested_by": principal.key_id, "requested_at": utcnow().isoformat()}
    try:
        await get_redis().rpush(keys.TRACK_REQUESTS, json.dumps(payload))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Queue unavailable: {type(exc).__name__}") from exc
    return {"queued": True, "mint": body.mint}
