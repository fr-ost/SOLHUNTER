"""Tiny dependency-free HTTP health endpoint for the scanner worker.

Railway only evaluates health checks at deploy time, but a plain HTTP endpoint
is still useful for manual checks and container HEALTHCHECK directives.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from backend.config.logging import get_logger

log = get_logger(__name__)


class HealthServer:
    def __init__(self, port: int, status_fn: Callable[[], dict[str, Any]], host: str = "0.0.0.0") -> None:
        self.port = port
        self.host = host
        self.status_fn = status_fn
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        try:
            self._server = await asyncio.start_server(self._handle, self.host, self.port)
            log.info("health.listening", port=self.port)
        except OSError as exc:
            log.warning("health.bind_failed", port=self.port, error=str(exc)[:120])

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break
            parts = request_line.decode(errors="ignore").split()
            path = parts[1] if len(parts) > 1 else "/"
            if path.split("?")[0] in {"/health", "/", "/ready"}:
                body = self.status_fn()
                code, reason = (200, "OK") if body.get("healthy", True) else (503, "Service Unavailable")
                payload = json.dumps(body, default=str).encode()
            else:
                code, reason, payload = 404, "Not Found", b'{"detail":"not found"}'
            writer.write(
                f"HTTP/1.1 {code} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload
            )
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()
