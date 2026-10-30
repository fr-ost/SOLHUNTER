"""Paper trading / journal / risk endpoints."""

from __future__ import annotations

from backend.tests.conftest import requires_services

pytestmark = requires_services

MINT = "So11111111111111111111111111111111111111112"


async def test_position_sizing_math(client, auth_headers) -> None:
    r = await client.get("/api/v1/risk/position-size", params={"entry_price_usd": 0.001, "stop_pct": 5}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # equity 500 * 1% = $5 risk / 5% stop = $100 position
    assert body["risk_usd"] == 5.0
    assert body["position_size_usd"] == 100.0
    assert body["targets"][0]["pct"] == 10.0


async def test_paper_trade_lifecycle(client, auth_headers) -> None:
    create = await client.post(
        "/api/v1/trades",
        headers=auth_headers,
        json={"mint": MINT, "symbol": "TEST", "entry_price_usd": 0.01, "position_size_usd": 100, "planned_stop_pct": 6, "planned_targets_pct": [10, 15, 20], "entry_reason": "test", "candidate_type": "momentum"},
    )
    assert create.status_code == 201, create.text
    trade = create.json()["trade"]
    assert trade["status"] == "open"
    state = await client.get("/api/v1/risk/state", headers=auth_headers)
    assert state.json()["open_positions"] == 1

    close = await client.patch(
        f"/api/v1/trades/{trade['id']}",
        headers=auth_headers,
        json={"status": "closed", "exit_price_usd": 0.0115, "fees_usd": 0.5, "slippage_usd": 0.5, "exit_reason": "target"},
    )
    assert close.status_code == 200, close.text
    closed = close.json()["trade"]
    assert closed["status"] == "closed"
    # 15% gross on $100 = $15 minus $1 costs
    assert abs(closed["realized_pnl_usd"] - 14.0) < 1e-6
    assert closed["signal_correct"] is True

    perf = await client.get("/api/v1/trades/performance", headers=auth_headers)
    assert perf.status_code == 200
    body = perf.json()
    assert body["overall"]["trades"] == 1 and body["overall"]["win_rate"] == 1.0
    assert "momentum" in body["by_candidate_type"]

    journal = await client.post("/api/v1/journal", headers=auth_headers, json={"trade_id": trade["id"], "mint": MINT, "content": "Took profit at target 1"})
    assert journal.status_code == 201
    detail = await client.get(f"/api/v1/trades/{trade['id']}", headers=auth_headers)
    assert len(detail.json()["journal"]) == 1

    deleted = await client.delete(f"/api/v1/trades/{trade['id']}", headers=auth_headers)
    assert deleted.status_code == 204


async def test_trade_validation(client, auth_headers) -> None:
    r = await client.post("/api/v1/trades", headers=auth_headers, json={"mint": "short", "entry_price_usd": 0, "position_size_usd": -1})
    assert r.status_code == 422
