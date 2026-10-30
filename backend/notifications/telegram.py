"""Telegram Bot API client (sendMessage) with retries, rate-limit handling and usage tracking."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.providers.base import ProviderTracker, backoff_delay

log = get_logger(__name__)


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str | None = None, chat_id: str | None = None, base_url: str | None = None) -> None:
        cfg = get_settings().telegram
        self.token = token or cfg.bot_token
        self.chat_id = chat_id or cfg.chat_id
        self.base_url = (base_url or cfg.api_base_url).rstrip("/")
        self.timeout = cfg.timeout_seconds
        self.tracker = ProviderTracker.get("telegram")
        self.enabled = bool(cfg.enabled and self.token and self.chat_id)
        if not self.enabled:
            self.tracker.set_status("disabled")

    def _url(self, method: str) -> str:
        return f"{self.base_url}/bot{self.token}/{method}"

    async def _post(self, method: str, payload: dict[str, Any], retries: int = 3) -> dict[str, Any]:
        last: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(retries + 1):
                try:
                    response = await client.post(self._url(method), json=payload)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = exc
                    self.tracker.record_error(f"{type(exc).__name__}")
                    await asyncio.sleep(backoff_delay(attempt, base=1.0, cap=15.0))
                    continue
                if response.status_code == 429:
                    retry_after = 1.0
                    try:
                        retry_after = float((response.json().get("parameters") or {}).get("retry_after") or 1.0)
                    except ValueError:
                        pass
                    self.tracker.record_error("429", rate_limited=True)
                    await asyncio.sleep(min(retry_after, 30.0))
                    last = TelegramError("rate limited")
                    continue
                if response.status_code >= 500:
                    self.tracker.record_error(f"HTTP {response.status_code}")
                    last = TelegramError(f"HTTP {response.status_code}")
                    await asyncio.sleep(backoff_delay(attempt, base=1.0, cap=15.0))
                    continue
                try:
                    data = response.json()
                except ValueError as exc:
                    raise TelegramError("invalid JSON from Telegram") from exc
                if response.status_code >= 400 or not data.get("ok", False):
                    desc = str(data.get("description") or response.text)[:200]
                    self.tracker.record_error(f"HTTP {response.status_code}: {desc}")
                    raise TelegramError(desc)
                self.tracker.record_success()
                self.tracker.set_status("connected")
                return data
        raise TelegramError(str(last) if last else "unknown error")

    async def send_message(self, text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> dict[str, Any]:
        if not self.enabled:
            raise TelegramError("Telegram is not configured (TELEGRAM_ENABLED/TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode, "link_preview_options": {"is_disabled": disable_preview}}
        return await self._post("sendMessage", payload)

    async def get_me(self) -> dict[str, Any]:
        if not self.enabled:
            raise TelegramError("Telegram is not configured")
        return await self._post("getMe", {}, retries=1)
