"""WebSocket endpoint streaming live scanner events to authenticated clients."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.deps import authenticate_websocket
from backend.api.live import live_hub
from backend.config.logging import get_logger
from backend.config.settings import get_settings

log = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket) -> None:
    settings = get_settings()
    principal = authenticate_websocket(websocket, settings)
    if principal is None:
        await websocket.close(code=4401, reason="unauthorized")
        return
    if live_hub.client_count >= settings.ws_max_connections:
        await websocket.close(code=4429, reason="too many connections")
        return
    await websocket.accept()
    await live_hub.add(websocket)
    try:
        await websocket.send_json({"type": "hello", "service": "gemfinder-api"})
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if msg == "ping":
                    await websocket.send_json({"type": "pong"})
            except TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("ws.closed", error=str(exc)[:100])
    finally:
        await live_hub.remove(websocket)
