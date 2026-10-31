"""Provider adapters with mocked HTTP responses and a real local WebSocket server."""

from __future__ import annotations

import asyncio
import json

import pytest
import respx
from httpx import Response
from websockets.asyncio.server import serve

from backend.providers.base import AsyncRateLimiter, DataUnavailable, ProviderError
from backend.providers.dexscreener import DexScreenerClient, parse_pair
from backend.providers.helius import HeliusClient
from backend.providers.pumpportal import PumpPortalClient
from backend.tests import conftest
from backend.tests.fixtures import pumpportal as fx


def test_parse_pair_handles_partial_payload() -> None:
    pair = parse_pair(fx.dexscreener_pair())
    assert pair is not None
    assert pair.pool_kind == "pump-amm" and pair.liquidity_usd == 25_000.0 and pair.buys_5m == 40
    partial = parse_pair({"pairAddress": "x", "dexId": "raydium", "baseToken": {"address": fx.MINT}})
    assert partial is not None and partial.liquidity_usd is None and partial.pool_kind == "raydium"


@pytest.mark.skipif(not conftest.SERVICES_AVAILABLE, reason="needs redis for caching")
@respx.mock
async def test_dexscreener_token_pairs_and_primary() -> None:
    respx.get(f"https://api.dexscreener.com/token-pairs/v1/solana/{fx.MINT}").mock(
        return_value=Response(200, json=[fx.dexscreener_pair(liquidity_usd=5_000.0), fx.dexscreener_pair(liquidity_usd=50_000.0)])
    )
    client = DexScreenerClient()
    try:
        pairs = await client.token_pairs(fx.MINT, use_cache=False)
        assert len(pairs) == 2
        primary = DexScreenerClient.primary_pair(pairs)
        assert primary is not None and primary.liquidity_usd == 50_000.0
        assert client.tracker.counters.requests == 1
    finally:
        await client.aclose()


@respx.mock
async def test_http_provider_retries_then_raises(monkeypatch) -> None:
    route = respx.get("https://api.dexscreener.com/latest/dex/search").mock(return_value=Response(500))
    client = DexScreenerClient()
    monkeypatch.setattr("backend.providers.base.backoff_delay", lambda *a, **k: 0.0)
    try:
        with pytest.raises(ProviderError):
            await client.request_json("GET", "/latest/dex/search", params={"q": "x"}, retries=2)
        assert route.call_count == 3
        assert client.tracker.counters.errors == 3
        assert client.tracker.status == "degraded"
    finally:
        await client.aclose()


@respx.mock
async def test_http_provider_handles_429(monkeypatch) -> None:
    calls = {"n": 0}

    def _side(request):  # noqa: ANN001
        calls["n"] += 1
        return Response(429, headers={"Retry-After": "0"}) if calls["n"] == 1 else Response(200, json={"pairs": []})

    respx.get("https://api.dexscreener.com/latest/dex/search").mock(side_effect=_side)
    client = DexScreenerClient()
    try:
        data = await client.request_json("GET", "/latest/dex/search", params={"q": "x"})
        assert data == {"pairs": []}
        assert client.tracker.counters.rate_limited == 1
    finally:
        await client.aclose()


async def test_rate_limiter_spaces_requests() -> None:
    limiter = AsyncRateLimiter(rate_per_second=50, burst=1)
    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(4):
        await limiter.acquire()
    assert loop.time() - start >= 0.05


@pytest.mark.skipif(not conftest.SERVICES_AVAILABLE, reason="needs redis for credit budget")
@respx.mock
async def test_helius_holder_distribution_excludes_infra(monkeypatch) -> None:
    from backend.config.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "helius_api_key", "test-key")

    def _rpc(request):  # noqa: ANN001
        body = json.loads(request.content)
        method = body["method"]
        if method == "getTokenSupply":
            return Response(200, json=fx.helius_supply())
        if method == "getTokenLargestAccounts":
            return Response(200, json=fx.helius_largest_accounts([("curveATA", 600_000_000.0), ("devATA", 50_000_000.0), ("w1", 30_000_000.0), ("w2", 20_000_000.0)]))
        if method == "getMultipleAccounts":
            return Response(200, json=fx.helius_multiple_accounts([fx.CURVE, fx.CREATOR, "WalletOne", "WalletTwo"]))
        if method == "getTokenAccounts":
            return Response(200, json=fx.helius_token_accounts(["WalletOne", "WalletTwo", fx.CREATOR, fx.CURVE, "W3"]))
        return Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})

    respx.post("https://mainnet.helius-rpc.com/").mock(side_effect=_rpc)
    client = HeliusClient()
    try:
        assert client.mode == "helius"
        dist = await client.holder_distribution(fx.MINT, infra_owners={fx.CURVE}, creator=fx.CREATOR)
        assert dist.total_supply == 1_000_000_000.0
        # curve account excluded -> circulating 400M; dev 50M = 12.5%; largest non-infra = dev
        assert dist.circulating_excluding_infra == 400_000_000.0
        assert abs(dist.largest_pct - 0.125) < 1e-9
        assert abs(dist.wallet_pct(fx.CREATOR) - 0.125) < 1e-9
        assert abs(dist.concentration(10) - 0.25) < 1e-9
        assert dist.holder_count == 5 and dist.holder_count_complete
        usage = await client.budget.usage()
        assert usage["day"] > 0
    finally:
        await client.aclose()


async def test_helius_without_key_reports_unavailable() -> None:
    client = HeliusClient()
    try:
        assert client.mode == "public_rpc"
        with pytest.raises(DataUnavailable):
            await client.get_token_accounts(fx.MINT)
        with pytest.raises(DataUnavailable):
            await client.parse_transactions(["sig"])
    finally:
        await client.aclose()


async def test_pumpportal_client_subscribes_and_reconnects() -> None:
    received: list[dict] = []
    subscriptions: list[dict] = []
    connections = {"n": 0}

    async def handler(ws):  # noqa: ANN001
        connections["n"] += 1
        async for message in ws:
            msg = json.loads(message)
            subscriptions.append(msg)
            if msg.get("method") == "subscribeNewToken":
                await ws.send(json.dumps({"message": "Successfully subscribed to token creation events."}))
                await ws.send(json.dumps(fx.create_event(signature=f"sig-{connections['n']}")))
            if msg.get("method") == "subscribeTokenTrade" and connections["n"] == 1:
                await ws.close()  # force a reconnect on the first connection
                return

    async def on_message(data: dict) -> None:
        received.append(data)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = PumpPortalClient(on_message, url=f"ws://127.0.0.1:{port}")
        task = asyncio.create_task(client.run())
        await asyncio.wait_for(client.connected.wait(), 5)
        await client.subscribe_token_trades([fx.MINT])
        # wait for the reconnect: second connection re-subscribes new tokens AND the token trade
        for _ in range(100):
            if connections["n"] >= 2 and len(received) >= 2:
                break
            await asyncio.sleep(0.05)
        await client.stop()
        await asyncio.wait_for(task, 5)
    assert connections["n"] >= 2
    assert len(received) >= 2
    methods = [s.get("method") for s in subscriptions]
    assert methods.count("subscribeNewToken") >= 2
    assert any(s.get("method") == "subscribeTokenTrade" and fx.MINT in s.get("keys", []) for s in subscriptions[methods.index("subscribeNewToken", 1):])
    assert client.tracker.counters.reconnects >= 1
    diag = client.diagnostics()
    assert diag["messages"] >= 4 and diag["service_messages"] >= 2 and diag["samples"]
    assert "Successfully subscribed" in diag["last_service_message"]
