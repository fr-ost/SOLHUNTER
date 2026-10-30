"""Normalisation, deduplication, token state aggregation and curve maths (no services needed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.ingestion.dedupe import Deduplicator
from backend.ingestion.events import MigrationEvent, NewTokenEvent, TradeEvent
from backend.ingestion.normalizer import normalize_pumpportal
from backend.scanner import pump_math
from backend.scanner.state import TokenState
from backend.tests.fixtures import pumpportal as fx


def test_normalize_create_trade_and_migration() -> None:
    created = normalize_pumpportal(fx.create_event())
    assert isinstance(created, NewTokenEvent)
    assert created.creator == fx.CREATOR and created.symbol == "GEM" and created.initial_buy_sol == 1.8
    trade = normalize_pumpportal(fx.trade_event("sig-1", fx.BUYER_A, "buy", sol=0.5))
    assert isinstance(trade, TradeEvent)
    assert trade.side == "buy" and trade.price_sol and abs(trade.price_sol - 0.5 / 15_000_000) < 1e-15
    mig = normalize_pumpportal(fx.migration_event())
    assert isinstance(mig, MigrationEvent) and mig.pool == "pump-amm"


def test_normalize_rejects_malformed_payloads() -> None:
    assert normalize_pumpportal({"message": "Successfully subscribed"}) is None
    assert normalize_pumpportal({"txType": "buy", "mint": fx.MINT}) is None  # no signature
    assert normalize_pumpportal({"txType": "buy", "mint": fx.MINT, "signature": "s"}) is None  # no trader
    weird = fx.trade_event("sig-x", fx.BUYER_A)
    weird["solAmount"] = "not-a-number"
    ev = normalize_pumpportal(weird)
    assert isinstance(ev, TradeEvent) and ev.sol_amount is None


def test_timestamp_parsing_handles_ms_and_seconds() -> None:
    raw = fx.trade_event("sig-ts", fx.BUYER_A)
    raw["timestamp"] = 1_730_000_000_000
    ev = normalize_pumpportal(raw)
    assert ev is not None and ev.block_time.year == 2024
    raw["timestamp"] = 1_730_000_000
    ev2 = normalize_pumpportal(raw)
    assert ev2 is not None and ev2.block_time == ev.block_time


async def test_dedupe_local_and_counts() -> None:
    d = Deduplicator(local_capacity=3)
    assert await d.is_new("a", use_redis=False)
    assert not await d.is_new("a", use_redis=False)
    assert await d.is_new("b", use_redis=False)
    assert await d.is_new("c", use_redis=False)
    assert await d.is_new("d", use_redis=False)  # evicts "a"
    assert await d.is_new("a", use_redis=False)
    assert d.duplicates == 1


def _state_with_trades(n_buys: int = 10, n_sells: int = 2, now: datetime | None = None) -> TokenState:
    now = now or datetime.now(UTC)
    launch = now - timedelta(minutes=6)
    state = TokenState(fx.MINT, launch)
    create = normalize_pumpportal(fx.create_event())
    assert isinstance(create, NewTokenEvent)
    create = create.model_copy(update={"block_time": launch, "received_at": launch})
    state.apply_create(create)
    v_sol, v_tokens = 31.8, 1_014_000_000.0
    for i in range(n_buys):
        v_sol += 0.4
        v_tokens -= 12_000_000
        ev = normalize_pumpportal(fx.trade_event(f"buy-{i}", f"Buyer{i:040d}", "buy", sol=0.4, tokens=12_000_000, v_sol=v_sol, v_tokens=v_tokens))
        assert isinstance(ev, TradeEvent)
        ts = launch + timedelta(seconds=20 + i * 25)
        state.apply_trade(ev.model_copy(update={"block_time": ts, "received_at": ts}))
    for i in range(n_sells):
        v_sol -= 0.2
        v_tokens += 6_000_000
        ev = normalize_pumpportal(fx.trade_event(f"sell-{i}", f"Buyer{i:040d}", "sell", sol=0.2, tokens=6_000_000, v_sol=v_sol, v_tokens=v_tokens, new_balance=6_000_000))
        assert isinstance(ev, TradeEvent)
        ts = now - timedelta(seconds=30 - i * 5)
        state.apply_trade(ev.model_copy(update={"block_time": ts, "received_at": ts}))
    return state


def test_token_state_windows_and_momentum() -> None:
    now = datetime.now(UTC)
    state = _state_with_trades(now=now)
    assert state.total_trades == 12
    assert len(state.buyers_total) == 11  # 10 buyers + creator initial buy
    w5 = state.window(300, now)
    assert w5.sells == 2 and w5.buys >= 8
    assert w5.unique_sellers == 2
    assert w5.net_sol > 0
    assert state.last_price is not None and state.last_price > 31.8 / 1_014_000_000
    mom = state.momentum_pct(300, now)
    assert mom is not None and mom > 0
    dev = state.dev_activity()
    assert dev["known"] and dev["buys"] == 1 and dev["sold_pct"] == 0.0
    assert state.stream_holder_estimate() == 11
    assert state.window(60, now).top_wallet_share <= 1.0


def test_token_state_dev_sell_and_balance_tracking() -> None:
    now = datetime.now(UTC)
    state = _state_with_trades(now=now)
    sell = normalize_pumpportal(fx.trade_event("dev-sell", fx.CREATOR, "sell", sol=1.0, tokens=30_000_000, new_balance=29_000_000))
    assert isinstance(sell, TradeEvent)
    state.apply_trade(sell.model_copy(update={"block_time": now, "received_at": now}))
    dev = state.dev_activity()
    assert dev["sells"] == 1 and abs(dev["sold_pct"] - 30_000_000 / 59_000_000) < 1e-9
    assert state.wallets[fx.CREATOR].balance_estimate == 29_000_000


def test_pump_math_quotes_are_consistent() -> None:
    assert pump_math.price_sol(30.0, 1_073_000_000.0) == 30.0 / 1_073_000_000.0
    assert pump_math.bonding_progress(1_073_000_000.0) == 0.0
    assert abs(pump_math.bonding_progress(1_073_000_000.0 - 793_100_000.0) - 1.0) < 1e-9
    q = pump_math.buy_quote(30.0, 1_073_000_000.0, 1.0, fee_bps=100)
    assert q.available and q.amount_out > 0 and q.price_impact_pct > 0
    # selling back what was bought returns slightly less than paid (fees + impact)
    s = pump_math.sell_quote(30.0 + 0.99, 1_073_000_000.0 - q.amount_out, q.amount_out, fee_bps=100)
    assert 0.9 < s.amount_out < 1.0
    assert not pump_math.buy_quote(0, 0, 1.0, 100).available
