"""Redis key / channel names shared between the API and the scanner (single source of truth)."""

from __future__ import annotations

from datetime import UTC, datetime

PREFIX = "gemfinder"

EVENTS_CHANNEL = f"{PREFIX}:events"
SCANNER_LEADER_LOCK = f"{PREFIX}:scanner:leader"
SCANNER_HEARTBEAT = f"{PREFIX}:scanner:heartbeat"
SCANNER_LOGS = f"{PREFIX}:logs"
TRACK_REQUESTS = f"{PREFIX}:track_requests"
GPT_QUEUE = f"{PREFIX}:gpt:queue"
GPT_DEAD_LETTER = f"{PREFIX}:gpt:dead"
GPT_INFLIGHT = f"{PREFIX}:gpt:inflight"
SOL_PRICE = f"{PREFIX}:sol_usd"
SETTINGS_OVERRIDES = f"{PREFIX}:settings:overrides"
PROVIDER_STATUS_PREFIX = f"{PREFIX}:provider:"
RATE_LIMIT_PREFIX = f"{PREFIX}:rl:"
DEDUPE_PREFIX = f"{PREFIX}:seen:"
CACHE_PREFIX = f"{PREFIX}:cache:"
ALERT_COOLDOWN_PREFIX = f"{PREFIX}:alert:cooldown:"
AI_SPEND_PREFIX = f"{PREFIX}:ai:spend:"
TRACKED_SET = f"{PREFIX}:scanner:tracked"


def daily_stats_key(day: datetime | None = None) -> str:
    day = day or datetime.now(UTC)
    return f"{PREFIX}:stats:daily:{day.strftime('%Y-%m-%d')}"


def provider_status_key(provider: str) -> str:
    return f"{PROVIDER_STATUS_PREFIX}{provider}"


def dedupe_key(kind: str, value: str) -> str:
    return f"{DEDUPE_PREFIX}{kind}:{value}"


def cache_key(kind: str, value: str) -> str:
    return f"{CACHE_PREFIX}{kind}:{value}"
