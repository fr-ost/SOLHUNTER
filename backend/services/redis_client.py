"""Shared async Redis client with helpers for locks, counters and pub/sub."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis

from backend.config.logging import get_logger
from backend.config.settings import get_settings

log = get_logger(__name__)

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
            health_check_interval=30,
            retry_on_timeout=True,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None


def reset_redis_for_tests() -> None:
    global _client
    _client = None


async def check_redis(timeout: float = 5.0) -> tuple[bool, str | None]:
    try:
        async with asyncio.timeout(timeout):
            pong = await get_redis().ping()
        return bool(pong), None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:300]


async def publish_event(channel: str, payload: dict[str, Any]) -> None:
    try:
        await get_redis().publish(channel, json.dumps(payload, default=str))
    except Exception as exc:  # noqa: BLE001
        log.warning("redis.publish_failed", channel=channel, error=str(exc)[:200])


async def cache_get_json(key: str) -> Any | None:
    try:
        raw = await get_redis().get(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("redis.get_failed", key=key, error=str(exc)[:200])
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    try:
        await get_redis().set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        log.warning("redis.set_failed", key=key, error=str(exc)[:200])


async def mark_seen(key: str, ttl_seconds: int) -> bool:
    """Return True if the key was not seen before (and mark it). Used for deduplication."""
    try:
        return bool(await get_redis().set(key, "1", ex=ttl_seconds, nx=True))
    except Exception as exc:  # noqa: BLE001
        # If Redis is unavailable we fall through to database unique constraints.
        log.warning("redis.mark_seen_failed", key=key, error=str(exc)[:200])
        return True


class RedisLock:
    """Simple leader lock using SET NX PX + heartbeat refresh."""

    def __init__(self, name: str, owner: str, ttl_seconds: int) -> None:
        self.name = name
        self.owner = owner
        self.ttl_ms = int(ttl_seconds * 1000)

    async def acquire(self) -> bool:
        return bool(await get_redis().set(self.name, self.owner, px=self.ttl_ms, nx=True))

    async def refresh(self) -> bool:
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
        )
        return bool(await get_redis().eval(script, 1, self.name, self.owner, self.ttl_ms))

    async def release(self) -> None:
        script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        try:
            await get_redis().eval(script, 1, self.name, self.owner)
        except Exception as exc:  # noqa: BLE001
            log.warning("redis.lock_release_failed", lock=self.name, error=str(exc)[:200])

    async def holder(self) -> str | None:
        return await get_redis().get(self.name)
