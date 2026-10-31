"""PumpPortal real-time data API client (WebSocket).

Endpoint: wss://pumpportal.fun/api/data (no API key for the data stream).
Methods: subscribeNewToken, subscribeMigration, subscribeTokenTrade{keys}, subscribeAccountTrade{keys}
and the matching unsubscribe* methods. PumpPortal asks clients to use a SINGLE connection and
send all subscriptions over it; we honour that and never open parallel sockets.

Message shapes are not formally documented, so parsing is defensive (see ingestion.normalizer)
and every raw payload for tracked tokens is persisted.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.providers.base import ProviderTracker, backoff_delay

log = get_logger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class PumpPortalClient:
    def __init__(self, handler: MessageHandler, url: str | None = None) -> None:
        settings = get_settings()
        self.url = url or settings.pumpportal_ws_url
        self.handler = handler
        self.tracker = ProviderTracker.get("pumpportal")
        self._ws: ClientConnection | None = None
        self._token_subs: set[str] = set()
        self._account_subs: set[str] = set()
        self._want_new_tokens = True
        self._want_migrations = True
        self._stop = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self.connected = asyncio.Event()
        self.messages_received = 0
        self.service_messages = 0
        self.non_json_messages = 0
        self.handler_errors = 0
        self.last_service_message: str | None = None
        self.last_handler_error: str | None = None
        self.samples: list[str] = []  # first raw messages, for verifying the field mapping
        self.last_message_at: float | None = None

    # ------------------------------------------------------------------ control
    async def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _send(self, payload: dict[str, Any]) -> bool:
        ws = self._ws
        if ws is None:
            return False
        async with self._send_lock:
            try:
                await ws.send(json.dumps(payload))
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("pumpportal.send_failed", error=str(exc)[:200])
                return False

    async def subscribe_token_trades(self, mints: list[str]) -> None:
        new = [m for m in mints if m not in self._token_subs]
        if not new:
            return
        self._token_subs.update(new)
        for chunk in _chunks(new, 50):
            await self._send({"method": "subscribeTokenTrade", "keys": chunk})

    async def unsubscribe_token_trades(self, mints: list[str]) -> None:
        existing = [m for m in mints if m in self._token_subs]
        if not existing:
            return
        self._token_subs.difference_update(existing)
        for chunk in _chunks(existing, 50):
            await self._send({"method": "unsubscribeTokenTrade", "keys": chunk})

    async def subscribe_account_trades(self, accounts: list[str]) -> None:
        new = [a for a in accounts if a not in self._account_subs]
        if not new:
            return
        self._account_subs.update(new)
        for chunk in _chunks(new, 50):
            await self._send({"method": "subscribeAccountTrade", "keys": chunk})

    async def unsubscribe_account_trades(self, accounts: list[str]) -> None:
        existing = [a for a in accounts if a in self._account_subs]
        if not existing:
            return
        self._account_subs.difference_update(existing)
        for chunk in _chunks(existing, 50):
            await self._send({"method": "unsubscribeAccountTrade", "keys": chunk})

    @property
    def account_subscription_count(self) -> int:
        return len(self._account_subs)

    @property
    def token_subscription_count(self) -> int:
        return len(self._token_subs)

    # ------------------------------------------------------------------ main loop
    async def run(self) -> None:
        """Connect and process messages forever, reconnecting with exponential backoff."""
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url, ping_interval=20, ping_timeout=20, max_size=4 * 1024 * 1024, open_timeout=15
                ) as ws:
                    self._ws = ws
                    log.info("pumpportal.connected", url=self.url, resubscribe_tokens=len(self._token_subs))
                    attempt = 0
                    # Base subscriptions go out before anyone else may send on this socket.
                    await self._resubscribe()
                    self.connected.set()
                    self.tracker.set_status("connected", token_subscriptions=len(self._token_subs))
                    await self.tracker.publish()
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        await self._on_raw(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.tracker.record_error(f"{type(exc).__name__}: {exc}")
                self.tracker.set_status("down")
                log.warning("pumpportal.disconnected", error=f"{type(exc).__name__}: {exc}"[:200], attempt=attempt)
            finally:
                self.connected.clear()
                self._ws = None
            if self._stop.is_set():
                break
            self.tracker.record_reconnect()
            await self.tracker.publish()
            delay = backoff_delay(attempt, base=1.0, cap=60.0)
            attempt = min(attempt + 1, 8)
            log.info("pumpportal.reconnecting", delay=round(delay, 1))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass
        log.info("pumpportal.stopped")

    async def _resubscribe(self) -> None:
        if self._want_new_tokens:
            await self._send({"method": "subscribeNewToken"})
        if self._want_migrations:
            await self._send({"method": "subscribeMigration"})
        for chunk in _chunks(sorted(self._token_subs), 50):
            await self._send({"method": "subscribeTokenTrade", "keys": chunk})
        for chunk in _chunks(sorted(self._account_subs), 50):
            await self._send({"method": "subscribeAccountTrade", "keys": chunk})

    async def _on_raw(self, raw: str | bytes) -> None:
        self.messages_received += 1
        self.last_message_at = asyncio.get_running_loop().time()
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        if len(self.samples) < 5:
            self.samples.append(text[:600])
            log.info("pumpportal.sample", n=len(self.samples), raw=text[:600])
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            self.non_json_messages += 1
            log.warning("pumpportal.non_json_message", raw=text[:200])
            return
        if not isinstance(data, dict):
            return
        if "message" in data and "txType" not in data and "mint" not in data:
            # Service messages, e.g. {"message": "Successfully subscribed to token creation events."}
            self.service_messages += 1
            self.last_service_message = str(data.get("message"))[:200]
            log.info("pumpportal.service_message", message=self.last_service_message)
            if "error" in str(data.get("message", "")).lower():
                self.tracker.record_error(str(data.get("message"))[:200])
            else:
                self.tracker.record_success()
            return
        self.tracker.record_success()
        try:
            await self.handler(data)
        except Exception as exc:  # noqa: BLE001 - a handler bug must not kill the stream
            self.handler_errors += 1
            self.last_handler_error = f"{type(exc).__name__}: {exc}"[:200]
            log.exception("pumpportal.handler_error")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "connected": self.connected.is_set(),
            "messages": self.messages_received,
            "service_messages": self.service_messages,
            "non_json": self.non_json_messages,
            "handler_errors": self.handler_errors,
            "last_service_message": self.last_service_message,
            "last_handler_error": self.last_handler_error,
            "subscriptions": len(self._token_subs),
            "wallet_subscriptions": len(self._account_subs),
            "samples": self.samples[:3],
        }


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
