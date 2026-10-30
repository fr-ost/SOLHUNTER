"""API routers. ``register_routers`` wires every feature router under ``/api/v1``."""

from __future__ import annotations

from fastapi import FastAPI

API_PREFIX = "/api/v1"


def register_routers(app: FastAPI) -> None:
    from backend.api.routers import (
        candidates,
        dashboard,
        history,
        logs,
        status,
        tokens,
        trading,
        wallets,
        whales,
    )
    from backend.api.routers import (
        settings as settings_router,
    )
    from backend.api.websocket import router as ws_router

    for module in (dashboard, candidates, tokens, wallets, whales, history, trading, settings_router, status, logs):
        app.include_router(module.router, prefix=API_PREFIX)
    app.include_router(ws_router)
