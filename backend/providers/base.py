"""Provider infrastructure: rate limiting, usage accounting, status tracking, HTTP retries."""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.services import keys
from backend.services.redis_client import get_redis

log = get_logger(__name__)


class ProviderError(Exception):
    """Base class for provider failures."""


class RateLimitedError(ProviderError):
    """Provider returned 429 (or our own budget refused the call)."""


class BudgetExceededError(ProviderError):
    """A configured credit/spend budget would be exceeded."""


class DataUnavailable(ProviderError):
    """The provider cannot supply the requested data (distinct from a zero value)."""


class AsyncRateLimiter:
    """Token bucket limiter for a steady rate (per second) with a burst capacity."""

    def __init__(self, rate_per_second: float, burst: int | None = None) -> None:
        self.rate = max(rate_per_second, 0.001)
        self.capacity = float(burst or max(1, int(self.rate)))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate
                await asyncio.sleep(wait)


@dataclass
class UsageCounters:
    requests: int = 0
    errors: int = 0
    rate_limited: int = 0
    credits: float = 0.0
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    reconnects: int = 0
    details: dict[str, Any] = field(default_factory=dict)


class ProviderTracker:
    """Tracks provider health + usage; mirrors to Redis (live) and lets the scanner persist daily totals."""

    _registry: dict[str, ProviderTracker] = {}

    def __init__(self, name: str) -> None:
        self.name = name
        self.counters = UsageCounters()
        self.status = "unknown"
        self._dirty_since_flush = UsageCounters()
        ProviderTracker._registry[name] = self

    @classmethod
    def all(cls) -> dict[str, ProviderTracker]:
        return dict(cls._registry)

    @classmethod
    def get(cls, name: str) -> ProviderTracker:
        return cls._registry.get(name) or ProviderTracker(name)

    def record_success(self, credits: float = 0.0) -> None:
        now = datetime.now(UTC)
        self.counters.requests += 1
        self.counters.credits += credits
        self.counters.last_success_at = now
        self._dirty_since_flush.requests += 1
        self._dirty_since_flush.credits += credits
        self.status = "connected"

    def record_error(self, error: str, rate_limited: bool = False) -> None:
        now = datetime.now(UTC)
        self.counters.requests += 1
        self.counters.errors += 1
        self._dirty_since_flush.requests += 1
        self._dirty_since_flush.errors += 1
        if rate_limited:
            self.counters.rate_limited += 1
            self._dirty_since_flush.rate_limited += 1
        self.counters.last_error_at = now
        self.counters.last_error = error[:300]
        if self.status != "down":
            self.status = "degraded"

    def set_status(self, status: str, **details: Any) -> None:
        self.status = status
        if details:
            self.counters.details.update(details)

    def record_reconnect(self) -> None:
        self.counters.reconnects += 1

    def snapshot(self) -> dict[str, Any]:
        c = self.counters
        return {
            "provider": self.name,
            "status": self.status,
            "requests": c.requests,
            "errors": c.errors,
            "rate_limited": c.rate_limited,
            "credits_used": round(c.credits, 1),
            "reconnect_count": c.reconnects,
            "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
            "last_error_at": c.last_error_at.isoformat() if c.last_error_at else None,
            "last_error": c.last_error,
            "details": c.details,
        }

    def take_delta(self) -> UsageCounters:
        delta = self._dirty_since_flush
        self._dirty_since_flush = UsageCounters()
        return delta

    async def publish(self) -> None:
        try:
            await get_redis().set(keys.provider_status_key(self.name), json.dumps(self.snapshot(), default=str), ex=300)
        except Exception as exc:  # noqa: BLE001
            log.debug("provider.publish_failed", provider=self.name, error=str(exc)[:100])


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential backoff with full jitter."""
    return random.uniform(0, min(cap, base * (2**attempt)))


class HttpProvider:
    """httpx-based client with rate limiting, retries with exponential backoff and usage accounting."""

    def __init__(self, name: str, base_url: str, rate_per_second: float, burst: int | None = None, timeout: float | None = None) -> None:
        self.name = name
        self.tracker = ProviderTracker.get(name)
        self.limiter = AsyncRateLimiter(rate_per_second, burst)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout or get_settings().provider_http_timeout_seconds,
            headers={"User-Agent": "SolHunterAI/0.1 (+research tool)", "Accept": "application/json"},
            http2=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        retries: int = 3,
        credits: float = 0.0,
        headers: dict[str, str] | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            await self.limiter.acquire()
            try:
                response = await self._client.request(method, url, params=params, json=json_body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                self.tracker.record_error(f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(backoff_delay(attempt))
                continue
            if response.status_code == 429:
                self.tracker.record_error("429 Too Many Requests", rate_limited=True)
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff_delay(attempt, base=2.0)
                last_error = RateLimitedError(f"{self.name} rate limited")
                await asyncio.sleep(min(delay, 30))
                continue
            if response.status_code >= 500:
                self.tracker.record_error(f"HTTP {response.status_code}")
                last_error = ProviderError(f"{self.name} HTTP {response.status_code}")
                await asyncio.sleep(backoff_delay(attempt))
                continue
            if response.status_code >= 400:
                self.tracker.record_error(f"HTTP {response.status_code}: {response.text[:120]}")
                raise ProviderError(f"{self.name} HTTP {response.status_code}: {response.text[:200]}")
            self.tracker.record_success(credits=credits)
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                self.tracker.record_error("invalid JSON")
                raise ProviderError(f"{self.name} returned invalid JSON") from exc
        assert last_error is not None
        raise last_error
