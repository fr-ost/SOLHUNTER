"""Runtime settings (admin only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import Principal, require_admin, require_api_key
from backend.database.engine import get_db_session
from backend.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    values: dict[str, Any]


@router.get("", dependencies=[Depends(require_api_key)])
async def get_settings_view(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    overrides = await settings_service.load_overrides(session)
    return {
        "effective": settings_service.effective_settings_view(),
        "overridable": settings_service.overridable_keys(),
        "overrides": overrides,
    }


@router.put("")
async def update_settings(
    body: SettingUpdate, principal: Principal = Depends(require_admin), session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    applied: dict[str, Any] = {}
    for key, value in body.values.items():
        try:
            row = await settings_service.set_override(session, key, value, updated_by=principal.key_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        applied[key] = row.value
    return {"applied": applied}


@router.delete("/{key}")
async def reset_setting(key: str, _: Principal = Depends(require_admin), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    removed = await settings_service.delete_override(session, key)
    if not removed:
        raise HTTPException(404, "No override for that key")
    return {"reset": key}
