"""Whale and smart-money activity feeds."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_api_key
from backend.api.serializers import rows_to_dicts
from backend.database.engine import get_db_session
from backend.models import CandidateScore, SmartMoneyEvent, Token, WhaleEvent

router = APIRouter(prefix="/whales", tags=["whales"], dependencies=[Depends(require_api_key)])


@router.get("/recent")
async def recent_whale_activity(
    limit: int = Query(50, ge=1, le=300),
    mint: str | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    stmt = select(WhaleEvent, Token.symbol).join(Token, Token.mint == WhaleEvent.mint)
    smart_stmt = select(SmartMoneyEvent, Token.symbol).join(Token, Token.mint == SmartMoneyEvent.mint)
    if mint:
        stmt = stmt.where(WhaleEvent.mint == mint)
        smart_stmt = smart_stmt.where(SmartMoneyEvent.mint == mint)
    whales = (await session.execute(stmt.order_by(WhaleEvent.occurred_at.desc()).limit(limit))).all()
    smart = (await session.execute(smart_stmt.order_by(SmartMoneyEvent.occurred_at.desc()).limit(limit))).all()
    return {
        "whale_events": [{**rows_to_dicts([e])[0], "symbol": s} for e, s in whales],
        "smart_money_events": [{**rows_to_dicts([e])[0], "symbol": s} for e, s in smart],
    }


@router.get("/signals")
async def whale_signals(limit: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(CandidateScore.mint, Token.symbol, CandidateScore.whale_signal, CandidateScore.whale_summary, CandidateScore.scored_at, CandidateScore.overall_score)
            .join(Token, Token.mint == CandidateScore.mint)
            .where(CandidateScore.is_latest.is_(True), CandidateScore.whale_signal.is_not(None), CandidateScore.whale_signal != "NO_SIGNIFICANT_WHALE_ACTIVITY")
            .order_by(CandidateScore.scored_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {"mint": m, "symbol": s, "signal": sig, "summary": summ, "scored_at": t.isoformat(), "overall_score": sc}
            for m, s, sig, summ, t, sc in rows
        ]
    }
