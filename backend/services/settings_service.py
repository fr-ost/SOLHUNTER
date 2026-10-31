"""Runtime-overridable settings stored in ``application_settings``.

Only a whitelist of non-secret, operational keys can be changed at runtime
(filter thresholds, ranking weights, alert thresholds...). Secrets and
infrastructure values are environment-only.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.logging import get_logger
from backend.config.settings import Settings, get_settings
from backend.models import ApplicationSetting, utcnow
from backend.services import keys
from backend.services.redis_client import get_redis

log = get_logger(__name__)

OVERRIDABLE_GROUPS = ("filters", "ranking", "whale", "growth", "scanner", "telegram", "execution", "risk", "ml", "ai")
_NEVER_OVERRIDE = {"telegram.bot_token", "telegram.chat_id", "ai.input_cost_per_1m_usd"}
_SECRET_FIELDS = {"openai_api_key", "helius_api_key", "password", "api_keys", "api_admin_keys", "database_url", "redis_url", "bot_token", "chat_id"}


def overridable_keys(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    out: dict[str, Any] = {}
    for group in OVERRIDABLE_GROUPS:
        model = getattr(settings, group)
        for field in type(model).model_fields:
            key = f"{group}.{field}"
            if key in _NEVER_OVERRIDE or field in _SECRET_FIELDS:
                continue
            out[key] = getattr(model, field)
    return out


def _coerce(current: Any, value: Any) -> Any:
    if isinstance(current, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if current is None or isinstance(current, str):
        return None if value is None else str(value)
    return value


async def load_overrides(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(ApplicationSetting))).scalars().all()
    return {row.key: row.value for row in rows}


async def apply_overrides_to_settings(session: AsyncSession, settings: Settings | None = None) -> Settings:
    """Mutate the cached Settings object in place with DB overrides (called by the scanner periodically)."""
    settings = settings or get_settings()
    overrides = await load_overrides(session)
    allowed = overridable_keys(settings)
    applied = 0
    for key, value in overrides.items():
        if key not in allowed:
            continue
        group, field = key.split(".", 1)
        model = getattr(settings, group)
        try:
            setattr(model, field, _coerce(getattr(model, field), value))
            applied += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("settings.override_invalid", key=key, error=str(exc)[:120])
    if applied:
        log.debug("settings.overrides_applied", count=applied)
    try:
        await get_redis().set(keys.SETTINGS_OVERRIDES, json.dumps(overrides, default=str))
    except Exception:  # noqa: BLE001
        pass
    return settings


async def set_override(session: AsyncSession, key: str, value: Any, updated_by: str | None = None) -> ApplicationSetting:
    allowed = overridable_keys()
    if key not in allowed:
        raise ValueError(f"'{key}' is not a runtime-overridable setting")
    coerced = _coerce(allowed[key], value)
    row = await session.get(ApplicationSetting, key)
    if row is None:
        row = ApplicationSetting(key=key, value=coerced, updated_at=utcnow(), updated_by=updated_by)
        session.add(row)
    else:
        row.value = coerced
        row.updated_at = utcnow()
        row.updated_by = updated_by
    await session.flush()
    await apply_overrides_to_settings(session)
    return row


async def delete_override(session: AsyncSession, key: str) -> bool:
    row = await session.get(ApplicationSetting, key)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    # restore the environment default by rebuilding the group from env
    settings = get_settings()
    group, field = key.split(".", 1)
    fresh = type(getattr(settings, group))()
    setattr(getattr(settings, group), field, getattr(fresh, field))
    await apply_overrides_to_settings(session, settings)
    return True


def effective_settings_view(settings: Settings | None = None) -> dict[str, Any]:
    """Settings as exposed to the dashboard with secrets masked."""
    settings = settings or get_settings()
    data = settings.model_dump()
    masked: dict[str, Any] = {}
    for key, value in data.items():
        if key in _SECRET_FIELDS:
            masked[key] = "***" if value else None
        elif isinstance(value, dict):
            masked[key] = {k: ("***" if k in _SECRET_FIELDS and v else v) for k, v in value.items()}
        else:
            masked[key] = value
    return masked
