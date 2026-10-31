"""Structured logging configuration (structlog -> stdout).

Production emits one JSON object per line so Railway's log explorer can index
fields. Development uses a readable console renderer. Secrets are scrubbed
from log events by key name.
"""

from __future__ import annotations

import logging
import sys
from collections import deque
from typing import Any

import structlog

_SENSITIVE_KEYS = {"api_key", "apikey", "api-key", "token", "bot_token", "authorization", "password", "secret"}

_recent_logs: deque[dict[str, Any]] = deque(maxlen=500)


def _scrub(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS and event_dict[key]:
            event_dict[key] = "***"
    return event_dict


def _railway_message(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Railway's log explorer displays the ``message`` field; mirror the structlog ``event`` into it."""
    if "event" in event_dict and "message" not in event_dict:
        event_dict["message"] = event_dict["event"]
    return event_dict


def _capture_recent(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    try:
        _recent_logs.append({k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v)) for k, v in event_dict.items()})
    except Exception:  # pragma: no cover - never let logging break the app
        pass
    return event_dict


def recent_log_records(limit: int = 200) -> list[dict[str, Any]]:
    items = list(_recent_logs)
    return items[-limit:]


def configure_logging(level: str = "INFO", fmt: str = "json", service: str = "api") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _scrub,
        _railway_message,
        _capture_recent,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any
    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared_processors, processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        renderer,
    ])
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(max(log_level, logging.WARNING))
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[return-value]
