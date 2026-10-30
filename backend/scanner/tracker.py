"""Token lifecycle: discovery -> tracking (trade subscription) -> evaluation -> expiry.

The tracker owns the in-memory ``TokenState`` objects and keeps the PumpPortal
trade subscriptions bounded (``SCANNER_MAX_TRACKED_TOKENS``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.ingestion.dedupe import Deduplicator
from backend.ingestion.events import IngestionEvent, MigrationEvent, NewTokenEvent, TradeEvent
from backend.ingestion.normalizer import normalize_pumpportal
from backend.ingestion.persistence import PersistenceWriter
from backend.providers.pumpportal import PumpPortalClient
from backend.scanner.state import TokenState
from backend.services import keys
from backend.services.redis_client import get_redis, publish_event

log = get_logger(__name__)


class TokenTracker:
    def __init__(self, persistence: PersistenceWriter, pumpportal: PumpPortalClient | None, dedupe: Deduplicator | None = None) -> None:
        self.persistence = persistence
        self.pumpportal = pumpportal
        self.dedupe = dedupe or Deduplicator()
        self.states: dict[str, TokenState] = {}
        self.untracked_due_to_capacity = 0
        self.events_processed = 0
        self.trades_processed = 0
        self.unknown_mint_trades = 0
        self._pending_subscribe: list[str] = []
        self._pending_unsubscribe: list[str] = []

    # ------------------------------------------------------------------ raw entry
    async def handle_raw(self, raw: dict[str, Any]) -> None:
        event = normalize_pumpportal(raw)
        if event is None:
            return
        if not await self.dedupe.is_new(event.dedupe_key, use_redis=isinstance(event, NewTokenEvent | MigrationEvent)):
            return
        await self.handle_event(event)

    async def handle_event(self, event: IngestionEvent) -> None:
        self.events_processed += 1
        if isinstance(event, NewTokenEvent):
            await self.on_create(event)
        elif isinstance(event, TradeEvent):
            await self.on_trade(event)
        elif isinstance(event, MigrationEvent):
            await self.on_migration(event)

    # ------------------------------------------------------------------ handlers
    async def on_create(self, ev: NewTokenEvent) -> None:
        settings = get_settings().scanner
        if ev.mint in self.states:
            return
        state = TokenState(ev.mint, ev.received_at)
        state.apply_create(ev)
        self.persistence.incr_stat("discovered")
        if len(self.states) >= settings.max_tracked_tokens and not self._evict_one():
            self.untracked_due_to_capacity += 1
            self.persistence.incr_stat("untracked_capacity")
            state.status = "archived"
            state.status_reason = "scanner at tracking capacity"
            self.persistence.add_token(state)
            self.persistence.add_launch(ev)
            return
        state.status = "tracking"
        state.tracking_until = ev.received_at + timedelta(minutes=settings.track_duration_minutes)
        self.states[ev.mint] = state
        self.persistence.add_token(state)
        self.persistence.add_launch(ev)
        self.persistence.add_raw(ev.source, "create", ev.dedupe_key, ev.mint, ev.raw)
        self._pending_subscribe.append(ev.mint)
        await self._apply_subscriptions()
        await publish_event(keys.EVENTS_CHANNEL, {"type": "token.discovered", "mint": ev.mint, "symbol": ev.symbol, "name": ev.name, "creator": ev.creator, "at": ev.received_at.isoformat()})

    async def on_trade(self, ev: TradeEvent) -> None:
        state = self.states.get(ev.mint)
        if state is None:
            self.unknown_mint_trades += 1
            return
        state.apply_trade(ev)
        self.trades_processed += 1
        self.persistence.incr_stat("trades")
        self.persistence.add_trade(ev)

    async def on_migration(self, ev: MigrationEvent) -> None:
        state = self.states.get(ev.mint)
        self.persistence.incr_stat("migrations")
        if state is None:
            return
        state.apply_migration(ev)
        self.persistence.add_migration(ev)
        self.persistence.add_raw(ev.source, "migrate", ev.dedupe_key, ev.mint, ev.raw)
        self.persistence.add_token(state)
        await publish_event(keys.EVENTS_CHANNEL, {"type": "token.migrated", "mint": ev.mint, "symbol": state.symbol, "pool": ev.pool, "at": ev.block_time.isoformat()})

    # ------------------------------------------------------------------ manual tracking requests (from API)
    async def process_track_requests(self) -> int:
        processed = 0
        try:
            r = get_redis()
            while True:
                raw = await r.lpop(keys.TRACK_REQUESTS)
                if raw is None:
                    break
                try:
                    req = json.loads(raw)
                except ValueError:
                    continue
                mint = str(req.get("mint") or "").strip()
                if not mint or mint in self.states:
                    continue
                await self.track_manually(mint, reason=str(req.get("reason") or "manual request"))
                processed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("tracker.track_requests_failed", error=str(exc)[:200])
        return processed

    async def track_manually(self, mint: str, reason: str) -> TokenState:
        settings = get_settings().scanner
        now = datetime.now(UTC)
        state = TokenState(mint, now)
        state.status = "tracking"
        state.status_reason = reason
        state.flags["manual"] = True
        state.tracking_until = now + timedelta(minutes=settings.track_duration_minutes)
        if len(self.states) >= settings.max_tracked_tokens:
            self._evict_one()
        self.states[mint] = state
        self.persistence.add_token(state)
        self._pending_subscribe.append(mint)
        await self._apply_subscriptions()
        log.info("tracker.manual_track", mint=mint, reason=reason)
        return state

    # ------------------------------------------------------------------ capacity & expiry
    def _evict_one(self) -> bool:
        """Evict the least active token older than 10 minutes to make room; returns True if evicted."""
        now = datetime.now(UTC)
        candidates = [s for s in self.states.values() if s.age_at(now) > 600 and s.status not in {"candidate"}]
        if not candidates:
            return False
        victim = min(candidates, key=lambda s: (s.window(300, now).trades, s.last_trade_at or s.discovered_at))
        self._retire(victim, "evicted for capacity")
        return True

    def _retire(self, state: TokenState, reason: str, status: str = "archived") -> None:
        state.status = status if state.status not in {"candidate", "watchlist"} else state.status
        state.status_reason = reason
        self.states.pop(state.mint, None)
        self._pending_unsubscribe.append(state.mint)
        self.persistence.add_token(state)
        self.persistence.incr_stat("retired")

    async def expire(self) -> int:
        now = datetime.now(UTC)
        retired = 0
        for state in list(self.states.values()):
            idle = (now - (state.last_trade_at or state.discovered_at)).total_seconds()
            if state.tracking_until and now > state.tracking_until:
                self._retire(state, "tracking window elapsed")
                retired += 1
            elif state.age_at(now) > 300 and state.total_trades < 3 and idle > 240:
                self._retire(state, "no trading activity", status="dead")
                retired += 1
            elif idle > 1500 and state.status not in {"candidate", "watchlist"}:
                self._retire(state, "inactive for 25 minutes", status="dead")
                retired += 1
        if retired:
            await self._apply_subscriptions()
        return retired

    async def _apply_subscriptions(self) -> None:
        if self.pumpportal is None:
            self._pending_subscribe.clear()
            self._pending_unsubscribe.clear()
            return
        if self._pending_unsubscribe:
            batch, self._pending_unsubscribe = self._pending_unsubscribe, []
            await self.pumpportal.unsubscribe_token_trades(batch)
            for m in batch:
                st = self.states.get(m)
                if st:
                    st.subscribed = False
        if self._pending_subscribe:
            batch, self._pending_subscribe = self._pending_subscribe, []
            await self.pumpportal.subscribe_token_trades(batch)
            for m in batch:
                st = self.states.get(m)
                if st:
                    st.subscribed = True

    async def resubscribe_all(self) -> None:
        if self.pumpportal is None:
            return
        await self.pumpportal.subscribe_token_trades(list(self.states))

    def stats(self) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        for s in self.states.values():
            statuses[s.status] = statuses.get(s.status, 0) + 1
        return {
            "tracked": len(self.states),
            "by_status": statuses,
            "events_processed": self.events_processed,
            "trades_processed": self.trades_processed,
            "untracked_capacity": self.untracked_due_to_capacity,
            "duplicates": self.dedupe.duplicates,
            "unknown_mint_trades": self.unknown_mint_trades,
        }

    async def persist_checkpoint(self) -> None:
        await self.persistence.save_checkpoint(
            "tracker",
            {"tracked_mints": list(self.states), "at": datetime.now(UTC).isoformat(), "events_processed": self.events_processed},
        )

    async def restore_checkpoint(self) -> int:
        """After a restart, re-track mints that were still inside their tracking window."""
        cp = await self.persistence.load_checkpoint("tracker")
        if not cp:
            return 0
        try:
            at = datetime.fromisoformat(cp.get("at"))
        except (TypeError, ValueError):
            return 0
        settings = get_settings().scanner
        if datetime.now(UTC) - at > timedelta(minutes=settings.track_duration_minutes):
            return 0
        restored = 0
        for mint in cp.get("tracked_mints", [])[: settings.max_tracked_tokens]:
            if mint not in self.states:
                await self.track_manually(mint, reason="restored after restart")
                self.states[mint].flags["manual"] = False
                self.states[mint].flags["restored"] = True
                restored += 1
        log.info("tracker.checkpoint_restored", restored=restored)
        return restored
