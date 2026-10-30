"""HTTP middleware: request ids, security headers, Redis-backed rate limiting, error hiding."""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.services import keys
from backend.services.redis_client import get_redis

log = get_logger(__name__)

_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_sec = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = capacity; ts = now end
local delta = math.max(0, now - ts)
tokens = math.min(capacity, tokens + delta * refill_per_sec)
local allowed = 0
if tokens >= 1 then tokens = tokens - 1; allowed = 1 end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, 120)
return {allowed, math.floor(tokens)}
"""

_EXEMPT_PATHS = {"/health", "/ready", "/", "/docs", "/openapi.json", "/redoc"}


def _client_identity(request: Request) -> str:
    key = request.headers.get("x-api-key") or request.headers.get("authorization")
    if key:
        return "key:" + key[-12:]
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return "ip:" + forwarded.split(",")[0].strip()
    return "ip:" + (request.client.host if request.client else "unknown")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        settings = get_settings()
        if request.url.path in _EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        capacity = settings.rate_limit_burst
        refill = settings.rate_limit_per_minute / 60.0
        identity = _client_identity(request)
        try:
            allowed, remaining = await get_redis().eval(
                _RATE_LIMIT_SCRIPT, 1, f"ratelimit:{identity}", capacity, refill, time.time()
            )
        except Exception as exc:  # noqa: BLE001 - Redis outage must not take the API down
            log.warning("ratelimit.redis_unavailable", error=str(exc)[:200])
            return await call_next(request)
        if not int(allowed):
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "1", "X-RateLimit-Remaining": "0"},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(int(remaining))
        try:
            await get_redis().hincrby(keys.daily_stats_key(), "api_requests", 1)
        except Exception:  # noqa: BLE001
            pass
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            log.exception("http.unhandled_error", method=request.method)
            response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path not in _EXEMPT_PATHS:
            log.info("http.request", method=request.method, status=response.status_code, duration_ms=duration_ms)
        structlog.contextvars.unbind_contextvars("request_id", "path")
        return response


def install_middleware(app: FastAPI) -> None:
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
