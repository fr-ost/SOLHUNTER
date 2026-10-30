"""System, provider, cost, model and AI-spend status endpoints."""

from __future__ import annotations

import os
import resource
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend import __version__
from backend.api.deps import require_api_key
from backend.api.live import live_hub
from backend.config.settings import get_settings
from backend.database.engine import check_database, get_db_session
from backend.services import status_service
from backend.services.redis_client import check_redis

router = APIRouter(prefix="/status", tags=["status"], dependencies=[Depends(require_api_key)])


def _memory_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / 1024.0, 1)


@router.get("/system")
async def system_status(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    settings = get_settings()
    db_ok, db_err = await check_database()
    redis_ok, redis_err = await check_redis()
    scanner = await status_service.scanner_status()
    providers = await status_service.provider_statuses(session)
    degraded = [p["provider"] for p in providers if p["status"] in {"down", "degraded"}]
    return {
        "api": {"status": "ok", "version": __version__, "environment": settings.app_env, "memory_mb": _memory_mb(), "ws_clients": live_hub.client_count, "pid": os.getpid()},
        "database": {"ok": db_ok, "error": db_err},
        "redis": {"ok": redis_ok, "error": redis_err},
        "scanner": scanner,
        "workers": await status_service.worker_heartbeats(session),
        "providers": providers,
        "queue": await status_service.queue_backlog(),
        "live_counters": await status_service.daily_counters(),
        "incomplete_data_warning": (
            f"Operating with degraded providers: {', '.join(degraded)}" if degraded else None
        ),
        "config_warnings": settings.validate_for_production(),
    }


@router.get("/providers")
async def providers(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return {"items": await status_service.provider_statuses(session)}


@router.get("/costs")
async def costs(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return await status_service.cost_estimate(session)


@router.get("/ai")
async def ai_status(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return await status_service.ai_spend_summary(session)


@router.get("/models")
async def models(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return {"items": await status_service.model_status(session)}
