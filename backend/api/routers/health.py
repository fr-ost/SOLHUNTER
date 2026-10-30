"""Liveness and readiness endpoints (unauthenticated, used by Railway health checks)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend import __version__
from backend.api.schemas import HealthResponse, ReadinessResponse
from backend.config.settings import get_settings
from backend.database.engine import check_database, get_engine
from backend.database.migrate import current_revision
from backend.models import utcnow
from backend.services.redis_client import check_redis

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(status="ok", service="api", version=__version__, environment=s.app_env, time=utcnow())


@router.get("/ready", response_model=ReadinessResponse)
async def ready() -> JSONResponse:
    db_ok, db_err = await check_database()
    redis_ok, redis_err = await check_redis()
    revision = None
    if db_ok:
        try:
            revision = await current_revision(get_engine())
        except Exception as exc:  # noqa: BLE001
            db_err = f"schema: {exc}"[:200]
    body = ReadinessResponse(
        status="ready" if (db_ok and redis_ok) else "degraded",
        database=db_ok,
        redis=redis_ok,
        schema_revision=revision,
        details={k: v for k, v in {"database_error": db_err, "redis_error": redis_err}.items() if v},
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if body.status == "ready" else 503)
