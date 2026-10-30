"""FastAPI dependencies: authentication and common helpers."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, WebSocket, status

from backend.config.settings import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    key_id: str
    is_admin: bool


def _extract_key(request: Request | WebSocket) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _match(candidate: str, keys: list[str]) -> bool:
    return any(hmac.compare_digest(candidate, k) for k in keys)


def authenticate_key(candidate: str | None, settings: Settings) -> Principal | None:
    if not candidate:
        return None
    if _match(candidate, settings.admin_key_list):
        return Principal(key_id=f"admin:{candidate[:4]}…", is_admin=True)
    if _match(candidate, settings.api_key_list):
        return Principal(key_id=f"key:{candidate[:4]}…", is_admin=False)
    return None


def _auth_configured(settings: Settings) -> bool:
    return bool(settings.api_key_list or settings.admin_key_list)


async def require_api_key(request: Request, settings: Settings = Depends(get_settings)) -> Principal:
    if not _auth_configured(settings):
        if settings.is_production:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "API authentication is not configured")
        # Development convenience only: no keys configured -> open access as admin.
        return Principal(key_id="dev-open", is_admin=True)
    principal = authenticate_key(_extract_key(request), settings)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key", headers={"WWW-Authenticate": "ApiKey"})
    request.state.principal = principal
    return principal


async def require_admin(principal: Principal = Depends(require_api_key)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin key required")
    return principal


def authenticate_websocket(websocket: WebSocket, settings: Settings) -> Principal | None:
    """WebSocket auth: header (X-API-Key) or ``?token=`` query parameter."""
    if not _auth_configured(settings):
        return None if settings.is_production else Principal(key_id="dev-open", is_admin=True)
    candidate = _extract_key(websocket) or websocket.query_params.get("token")
    return authenticate_key(candidate, settings)
