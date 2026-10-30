"""Live event hub: forwards scanner events from Redis pub/sub to WebSocket clients."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket

from backend.config.logging import get_logger
from backend.services.redis_client import get_redis

log = get_logger(__name__)

EVENTS_CHANNEL = "gemfinder:events"


class LiveHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(payload, default=str)
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001
                await self.remove(ws)

    async def run(self) -> None:
        """Subscribe to Redis and fan out; reconnects forever on failure."""
        backoff = 1.0
        while True:
            try:
                pubsub = get_redis().pubsub()
                await pubsub.subscribe(EVENTS_CHANNEL)
                backoff = 1.0
                async for item in pubsub.listen():
                    if item.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(item["data"])
                    except (TypeError, ValueError):
                        continue
                    await self.broadcast(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("live_hub.reconnecting", error=str(exc)[:200], backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


live_hub = LiveHub()
