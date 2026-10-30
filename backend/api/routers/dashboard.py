"""Dashboard summary endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_api_key
from backend.api.serializers import orm_to_dict, rows_to_dicts
from backend.database.engine import get_db_session
from backend.models import Alert, CandidateScore, SmartMoneyEvent, Token, WhaleEvent
from backend.services import status_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_api_key)])


@router.get("")
async def dashboard(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    scanner = await status_service.scanner_status()
    providers = await status_service.provider_statuses(session)
    counts = await status_service.counts_24h(session)
    live = await status_service.daily_counters()
    top = (
        await session.execute(
            select(CandidateScore, Token)
            .join(Token, Token.mint == CandidateScore.mint)
            .where(CandidateScore.is_latest.is_(True), CandidateScore.status.in_(["candidate", "watchlist"]))
            .order_by(CandidateScore.overall_score.desc())
            .limit(10)
        )
    ).all()
    whales = (await session.execute(select(WhaleEvent).order_by(WhaleEvent.occurred_at.desc()).limit(15))).scalars().all()
    smart = (await session.execute(select(SmartMoneyEvent).order_by(SmartMoneyEvent.occurred_at.desc()).limit(10))).scalars().all()
    alerts = (await session.execute(select(Alert).order_by(Alert.created_at.desc()).limit(10))).scalars().all()
    costs = await status_service.cost_estimate(session)
    return {
        "scanner": scanner,
        "providers": providers,
        "counts_24h": counts,
        "live_counters": live,
        "top_candidates": [
            {
                **orm_to_dict(score, exclude={"filter_results", "execution", "whale_summary", "penalties"}),
                "symbol": token.symbol,
                "name": token.name,
                "launch_time": token.launch_time.isoformat() if token.launch_time else None,
            }
            for score, token in top
        ],
        "recent_whale_activity": rows_to_dicts(list(whales)),
        "recent_smart_money": rows_to_dicts(list(smart)),
        "recent_alerts": rows_to_dicts(list(alerts), exclude={"payload", "message"}),
        "costs": costs,
        "queue": await status_service.queue_backlog(),
    }
