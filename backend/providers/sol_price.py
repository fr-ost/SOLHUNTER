"""SOL/USD reference price with Redis caching and a configurable fallback."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.providers.dexscreener import DexScreenerClient, fetch_sol_usd
from backend.services import keys
from backend.services.redis_client import get_redis

log = get_logger(__name__)


class SolPriceService:
    def __init__(self, dex: DexScreenerClient) -> None:
        self.dex = dex
        self._price: float | None = None
        self._updated: float = 0.0
        self.source = "none"

    async def get(self) -> float | None:
        settings = get_settings()
        ttl = settings.sol_usd_price_refresh_seconds
        if self._price and time.monotonic() - self._updated < ttl:
            return self._price
        try:
            raw = await get_redis().get(keys.SOL_PRICE)
            if raw:
                data = json.loads(raw)
                if time.time() - float(data.get("ts", 0)) < ttl:
                    self._price, self._updated, self.source = float(data["price"]), time.monotonic(), "redis"
                    return self._price
        except Exception:  # noqa: BLE001
            pass
        price = await fetch_sol_usd(self.dex)
        if price:
            self._price, self._updated, self.source = price, time.monotonic(), "dexscreener"
            try:
                await get_redis().set(keys.SOL_PRICE, json.dumps({"price": price, "ts": time.time(), "at": datetime.now(UTC).isoformat()}), ex=ttl * 10)
            except Exception:  # noqa: BLE001
                pass
            return price
        if self._price:
            self.source = "stale"
            return self._price
        if settings.sol_usd_fallback_price:
            self.source = "fallback_config"
            return settings.sol_usd_fallback_price
        log.warning("sol_price.unavailable")
        return None
