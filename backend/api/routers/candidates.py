"""Candidate explorer and candidate detail endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_api_key
from backend.api.serializers import orm_to_dict, rows_to_dicts
from backend.database.engine import get_db_session
from backend.models import (
    CandidateHistory,
    CandidateScore,
    GptAnalysis,
    HolderSnapshot,
    ModelPrediction,
    SmartMoneyEvent,
    Token,
    TokenLaunch,
    TokenPool,
    TokenRiskEvent,
    TokenSnapshot,
    WalletProfile,
    WhaleEvent,
)

router = APIRouter(prefix="/candidates", tags=["candidates"], dependencies=[Depends(require_api_key)])


def _score_summary(score: CandidateScore, token: Token) -> dict[str, Any]:
    data = orm_to_dict(score, exclude={"filter_results", "execution", "whale_summary", "components", "penalties"})
    data["components"] = score.components
    data["symbol"] = token.symbol
    data["name"] = token.name
    data["launch_time"] = token.launch_time.isoformat() if token.launch_time else None
    data["pool_kind"] = token.pool_kind
    data["token_status"] = token.status
    return data


@router.get("")
async def list_candidates(
    status: str = Query("candidate", pattern="^(candidate|watchlist|rejected|all)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    min_score: float | None = Query(None, ge=0, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    stmt = select(CandidateScore, Token).join(Token, Token.mint == CandidateScore.mint).where(CandidateScore.is_latest.is_(True))
    if status != "all":
        stmt = stmt.where(CandidateScore.status == status)
    if min_score is not None:
        stmt = stmt.where(CandidateScore.overall_score >= min_score)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await session.execute(stmt.order_by(CandidateScore.overall_score.desc(), CandidateScore.scored_at.desc()).limit(limit).offset(offset))).all()
    latest_snaps: dict[str, TokenSnapshot] = {}
    mints = [t.mint for _, t in rows]
    if mints:
        sub = (
            select(TokenSnapshot.mint, func.max(TokenSnapshot.captured_at).label("mx"))
            .where(TokenSnapshot.mint.in_(mints))
            .group_by(TokenSnapshot.mint)
            .subquery()
        )
        snaps = (
            await session.execute(
                select(TokenSnapshot).join(sub, (TokenSnapshot.mint == sub.c.mint) & (TokenSnapshot.captured_at == sub.c.mx))
            )
        ).scalars().all()
        latest_snaps = {s.mint: s for s in snaps}
    items = []
    for score, token in rows:
        item = _score_summary(score, token)
        snap = latest_snaps.get(token.mint)
        item["latest_snapshot"] = orm_to_dict(snap, exclude={"features", "data_sources"}) if snap else None
        items.append(item)
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@router.get("/{mint}")
async def candidate_detail(mint: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    token = await session.get(Token, mint)
    if token is None:
        raise HTTPException(404, "Token not found")
    latest = (
        await session.execute(
            select(CandidateScore).where(CandidateScore.mint == mint).order_by(CandidateScore.scored_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    score_history = (
        await session.execute(
            select(CandidateScore.id, CandidateScore.scored_at, CandidateScore.overall_score, CandidateScore.status, CandidateScore.confidence)
            .where(CandidateScore.mint == mint)
            .order_by(CandidateScore.scored_at.asc())
            .limit(200)
        )
    ).all()
    snapshots = (
        await session.execute(select(TokenSnapshot).where(TokenSnapshot.mint == mint).order_by(TokenSnapshot.captured_at.asc()).limit(500))
    ).scalars().all()
    holders = (
        await session.execute(select(HolderSnapshot).where(HolderSnapshot.mint == mint).order_by(HolderSnapshot.captured_at.desc()).limit(1))
    ).scalar_one_or_none()
    launches = (await session.execute(select(TokenLaunch).where(TokenLaunch.mint == mint))).scalars().all()
    pools = (await session.execute(select(TokenPool).where(TokenPool.mint == mint))).scalars().all()
    whale_events = (
        await session.execute(select(WhaleEvent).where(WhaleEvent.mint == mint).order_by(WhaleEvent.occurred_at.desc()).limit(100))
    ).scalars().all()
    smart_events = (
        await session.execute(select(SmartMoneyEvent).where(SmartMoneyEvent.mint == mint).order_by(SmartMoneyEvent.occurred_at.desc()).limit(100))
    ).scalars().all()
    risk_events = (
        await session.execute(select(TokenRiskEvent).where(TokenRiskEvent.mint == mint).order_by(TokenRiskEvent.occurred_at.desc()).limit(50))
    ).scalars().all()
    predictions = (
        await session.execute(select(ModelPrediction).where(ModelPrediction.mint == mint).order_by(ModelPrediction.predicted_at.desc()).limit(20))
    ).scalars().all()
    gpt = (
        await session.execute(select(GptAnalysis).where(GptAnalysis.mint == mint, GptAnalysis.status.in_(["ok", "cached"])).order_by(GptAnalysis.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    history = (
        await session.execute(select(CandidateHistory).where(CandidateHistory.mint == mint).order_by(CandidateHistory.occurred_at.desc()).limit(50))
    ).scalars().all()
    wallet_addresses = {e.wallet for e in whale_events} | {e.wallet for e in smart_events}
    profiles: list[WalletProfile] = []
    if wallet_addresses:
        profiles = (await session.execute(select(WalletProfile).where(WalletProfile.address.in_(wallet_addresses)))).scalars().all()
    creator_history: dict[str, Any] | None = None
    if token.creator_wallet:
        prior = (
            await session.execute(
                select(Token.mint, Token.symbol, Token.status, Token.launch_time, Token.status_reason)
                .where(Token.creator_wallet == token.creator_wallet, Token.mint != mint)
                .order_by(Token.launch_time.desc())
                .limit(25)
            )
        ).all()
        creator_profile = await session.get(WalletProfile, token.creator_wallet)
        creator_history = {
            "creator_wallet": token.creator_wallet,
            "profile": orm_to_dict(creator_profile) if creator_profile else None,
            "prior_launches": [
                {"mint": m, "symbol": s, "status": st, "launch_time": lt.isoformat() if lt else None, "status_reason": sr}
                for m, s, st, lt, sr in prior
            ],
        }
    return {
        "token": orm_to_dict(token),
        "latest_score": orm_to_dict(latest) if latest else None,
        "score_history": [
            {"id": i, "scored_at": t.isoformat(), "overall_score": s, "status": st, "confidence": c} for i, t, s, st, c in score_history
        ],
        "snapshots": rows_to_dicts(list(snapshots), exclude={"features", "data_sources"}),
        "holders": orm_to_dict(holders) if holders else None,
        "launches": rows_to_dicts(list(launches), exclude={"raw"}),
        "pools": rows_to_dicts(list(pools), exclude={"raw"}),
        "whale_events": rows_to_dicts(list(whale_events)),
        "smart_money_events": rows_to_dicts(list(smart_events)),
        "risk_events": rows_to_dicts(list(risk_events)),
        "predictions": rows_to_dicts(list(predictions)),
        "gpt_analysis": orm_to_dict(gpt, exclude={"context"}) if gpt else None,
        "history": rows_to_dicts(list(history)),
        "wallet_profiles": rows_to_dicts(list(profiles), exclude={"stats"}),
        "creator": creator_history,
        "execution": latest.execution if latest else None,
        "whale_summary": latest.whale_summary if latest else None,
        "filter_results": latest.filter_results if latest else None,
    }
