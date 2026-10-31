"""Manual wallet tracker: API CRUD, PumpPortal subscription sync, tracked-trade handling and alerts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import respx
from httpx import Response
from sqlalchemy import func, select

from backend.database.engine import session_scope
from backend.ingestion.dedupe import Deduplicator
from backend.ingestion.persistence import PersistenceWriter
from backend.models import Alert, Token, TokenTransaction, TrackedWallet
from backend.scanner.tracker import TokenTracker
from backend.services.wallet_watch import build_wallet_trade_message
from backend.tests.conftest import requires_services
from backend.tests.fixtures import pumpportal as fx

pytestmark = requires_services

WALLET = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
OTHER_MINT = "9wFFyRfZBsuAha4YcuxcXLKwMxJR43S7fPfQLusDBzvT"


class FakePumpPortal:
    def __init__(self) -> None:
        self.tokens: set[str] = set()
        self.accounts: set[str] = set()

    async def subscribe_token_trades(self, mints: list[str]) -> None:
        self.tokens.update(mints)

    async def unsubscribe_token_trades(self, mints: list[str]) -> None:
        self.tokens.difference_update(mints)

    async def subscribe_account_trades(self, accounts: list[str]) -> None:
        self.accounts.update(accounts)

    async def unsubscribe_account_trades(self, accounts: list[str]) -> None:
        self.accounts.difference_update(accounts)

    @property
    def token_subscription_count(self) -> int:
        return len(self.tokens)


async def test_api_crud_and_validation(client, auth_headers) -> None:
    bad = await client.post("/api/v1/tracked-wallets", headers=auth_headers, json={"address": "not-a-wallet"})
    assert bad.status_code == 422
    r = await client.post("/api/v1/tracked-wallets", headers=auth_headers, json={"address": WALLET, "label": "Whale-1", "min_alert_sol": 1.5})
    assert r.status_code == 201, r.text
    assert r.json()["wallet"]["label"] == "Whale-1"
    again = await client.post("/api/v1/tracked-wallets", headers=auth_headers, json={"address": WALLET, "label": "Renamed"})
    assert again.status_code == 201 and again.json()["wallet"]["label"] == "Renamed"  # upsert, not duplicate
    lst = await client.get("/api/v1/tracked-wallets", headers=auth_headers)
    assert lst.status_code == 200 and len(lst.json()["items"]) == 1 and lst.json()["limit"] == 100
    p = await client.patch(f"/api/v1/tracked-wallets/{WALLET}", headers=auth_headers, json={"alert_on_sell": False, "active": False})
    assert p.status_code == 200 and p.json()["wallet"]["alert_on_sell"] is False and p.json()["wallet"]["active"] is False
    trades = await client.get("/api/v1/tracked-wallets/trades/recent", headers=auth_headers)
    assert trades.status_code == 200 and trades.json()["items"] == []
    d = await client.delete(f"/api/v1/tracked-wallets/{WALLET}", headers=auth_headers)
    assert d.status_code == 204
    assert (await client.delete(f"/api/v1/tracked-wallets/{WALLET}", headers=auth_headers)).status_code == 404
    assert (await client.get("/api/v1/tracked-wallets")).status_code == 401


async def test_sync_subscribes_and_unsubscribes() -> None:
    async with session_scope() as s:
        s.add(TrackedWallet(address=WALLET, label="W1"))
    pp = FakePumpPortal()
    tracker = TokenTracker(PersistenceWriter(), pp, Deduplicator())  # type: ignore[arg-type]
    async with session_scope() as s:
        assert await tracker.sync_tracked_wallets(s) == 1
    assert pp.accounts == {WALLET} and tracker.tracked_wallets[WALLET]["label"] == "W1"
    async with session_scope() as s:
        row = await s.get(TrackedWallet, WALLET)
        row.active = False
    async with session_scope() as s:
        assert await tracker.sync_tracked_wallets(s) == 0
    assert pp.accounts == set() and tracker.tracked_wallets == {}


async def test_tracked_wallet_buy_on_unknown_token_starts_tracking_and_alerts(monkeypatch, client, auth_headers) -> None:
    async with session_scope() as s:
        s.add(TrackedWallet(address=WALLET, label="Whale-1", min_alert_sol=0.5))
    writer = PersistenceWriter()
    pp = FakePumpPortal()
    tracker = TokenTracker(writer, pp, Deduplicator())  # type: ignore[arg-type]
    async with session_scope() as s:
        await tracker.sync_tracked_wallets(s)
    seen: list[tuple[str, str]] = []

    async def listener(state, ev, cfg):  # noqa: ANN001
        seen.append((ev.mint, cfg["label"]))

    tracker.wallet_listeners.append(listener)
    await tracker.handle_raw(fx.trade_event("tw-buy-1", WALLET, "buy", sol=2.0, mint=OTHER_MINT))
    assert OTHER_MINT in tracker.states and OTHER_MINT in pp.tokens  # auto-tracked for full analysis
    assert tracker.states[OTHER_MINT].flags["source"] == "wallet_tracker"
    assert seen == [(OTHER_MINT, "Whale-1")] and tracker.tracked_wallet_trades == 1
    # a sell on a token that is not tracked and auto-track disabled is still recorded
    tracker.tracked_wallets[WALLET]["auto_track_tokens"] = False
    third = "3wFFyRfZBsuAha4YcuxcXLKwMxJR43S7fPfQLusDBzvT"
    await tracker.handle_raw(fx.trade_event("tw-sell-1", WALLET, "sell", sol=1.0, mint=third))
    assert third not in tracker.states
    await writer.flush()
    async with session_scope() as s:
        assert await s.scalar(select(func.count()).select_from(TokenTransaction).where(TokenTransaction.wallet == WALLET)) == 2
        assert (await s.get(Token, third)).status == "archived"
    feed = await client.get("/api/v1/tracked-wallets/trades/recent", headers=auth_headers)
    items = feed.json()["items"]
    assert len(items) == 2 and items[0]["label"] == "Whale-1"
    listed = await client.get("/api/v1/tracked-wallets", headers=auth_headers)
    assert listed.json()["items"][0]["trades_24h"] == 2


@respx.mock
async def test_wallet_watch_alert_respects_settings_and_cooldown(monkeypatch) -> None:
    from types import SimpleNamespace

    from backend.config.settings import get_settings
    from backend.ingestion.normalizer import normalize_pumpportal
    from backend.notifications.alerts import AlertService
    from backend.notifications.telegram import TelegramClient
    from backend.scanner.state import TokenState
    from backend.services.wallet_watch import WalletWatch

    monkeypatch.setattr(get_settings().telegram, "enabled", True)
    route = respx.post("https://api.telegram.org/botT/sendMessage").mock(return_value=Response(200, json={"ok": True, "result": {}}))
    async with session_scope() as s:
        s.add(TrackedWallet(address=WALLET, label="Whale-1"))
        s.add(Token(mint=OTHER_MINT, symbol="XYZ", status="tracking", first_seen_at=datetime.now(UTC)))

    class _Price:
        async def get(self) -> float:
            return 150.0

    worker = SimpleNamespace(evaluator=SimpleNamespace(alerts=AlertService(TelegramClient(token="T", chat_id="1"))), sol_price=_Price())
    watch = WalletWatch(worker)  # type: ignore[arg-type]
    state = TokenState(OTHER_MINT, datetime.now(UTC))
    state.symbol = "XYZ"
    cfg = {"label": "Whale-1", "alert_on_buy": True, "alert_on_sell": False, "min_alert_sol": 1.0}
    small = normalize_pumpportal(fx.trade_event("a1", WALLET, "buy", sol=0.4, mint=OTHER_MINT))
    big = normalize_pumpportal(fx.trade_event("a2", WALLET, "buy", sol=2.5, mint=OTHER_MINT))
    big2 = normalize_pumpportal(fx.trade_event("a3", WALLET, "buy", sol=3.0, mint=OTHER_MINT))
    sell = normalize_pumpportal(fx.trade_event("a4", WALLET, "sell", sol=3.0, mint=OTHER_MINT))
    assert await watch._maybe_alert(state, small, cfg) == "below_min"  # noqa: SLF001
    assert await watch._maybe_alert(state, big, cfg) == "sent"  # noqa: SLF001
    assert await watch._maybe_alert(state, big2, cfg) == "cooldown"  # noqa: SLF001 - same wallet/token/side within 5 min
    assert await watch._maybe_alert(state, sell, cfg) == "disabled"  # noqa: SLF001
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert "Whale-1 BOUGHT 2.50 SOL ($375) of XYZ" in body["text"] and OTHER_MINT in body["text"]
    await watch.on_tracked_trade(state, big, cfg)
    async with session_scope() as s:
        row = await s.get(TrackedWallet, WALLET)
        assert row.trades_seen == 1 and row.last_trade_at is not None
        alerts = (await s.execute(select(Alert).where(Alert.alert_type == "wallet_trade"))).scalars().all()
        assert len(alerts) == 1 and alerts[0].status == "sent"


def test_wallet_trade_message_without_price_or_label() -> None:
    from backend.ingestion.normalizer import normalize_pumpportal

    ev = normalize_pumpportal(fx.trade_event("m1", WALLET, "sell", sol=1.25, mint=OTHER_MINT))
    msg = build_wallet_trade_message({}, ev, None, None)  # type: ignore[arg-type]
    assert "SOLD 1.25 SOL" in msg and "5Q54…e4j1" in msg and "$" not in msg.split("\n")[0]
