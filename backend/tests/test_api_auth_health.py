"""API authentication, health and middleware behaviour."""

from __future__ import annotations

import pytest

from backend.tests.conftest import requires_services

pytestmark = requires_services


async def test_health_is_public(client) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["service"] == "api"
    assert "X-Request-ID" in r.headers


async def test_ready_reports_database_and_redis(client) -> None:
    r = await client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True and body["redis"] is True
    assert body["schema_revision"]


async def test_protected_endpoint_requires_key(client) -> None:
    r = await client.get("/api/v1/dashboard")
    assert r.status_code == 401
    r = await client.get("/api/v1/dashboard", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


async def test_bearer_token_accepted(client) -> None:
    r = await client.get("/api/v1/status/system", headers={"Authorization": "Bearer test-read-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["database"]["ok"] is True
    assert body["scanner"]["status"] in {"offline", "stale"}


async def test_admin_endpoint_rejects_read_key(client, auth_headers, admin_headers) -> None:
    r = await client.put("/api/v1/settings", headers=auth_headers, json={"values": {"filters.min_liquidity_usd": 1}})
    assert r.status_code == 403
    r = await client.put("/api/v1/settings", headers=admin_headers, json={"values": {"filters.min_liquidity_usd": 12345}})
    assert r.status_code == 200
    assert r.json()["applied"]["filters.min_liquidity_usd"] == 12345.0


async def test_settings_override_rejects_secret_or_unknown_keys(client, admin_headers) -> None:
    r = await client.put("/api/v1/settings", headers=admin_headers, json={"values": {"openai_api_key": "x"}})
    assert r.status_code == 400
    r = await client.put("/api/v1/settings", headers=admin_headers, json={"values": {"telegram.bot_token": "x"}})
    assert r.status_code == 400


async def test_settings_view_masks_secrets(client, auth_headers) -> None:
    r = await client.get("/api/v1/settings", headers=auth_headers)
    assert r.status_code == 200
    eff = r.json()["effective"]
    assert eff["api_keys"] == "***"
    assert eff["database_url"] == "***"


async def test_rate_limit_blocks_bursts(client, auth_headers, monkeypatch) -> None:
    from backend.config.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_burst", 3)
    monkeypatch.setattr(s, "rate_limit_per_minute", 1)
    statuses = []
    for _ in range(5):
        r = await client.get("/api/v1/status/costs", headers=auth_headers)
        statuses.append(r.status_code)
    assert statuses[:3] == [200, 200, 200]
    assert 429 in statuses[3:]


async def test_unknown_route_is_json_404(client, auth_headers) -> None:
    r = await client.get("/api/v1/nope", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]


@pytest.mark.parametrize("path", ["/api/v1/candidates", "/api/v1/tokens/recent", "/api/v1/wallets/top", "/api/v1/whales/recent", "/api/v1/history/candidates", "/api/v1/trades", "/api/v1/journal", "/api/v1/status/providers", "/api/v1/status/models", "/api/v1/status/ai", "/api/v1/logs"])
async def test_list_endpoints_work_on_empty_database(client, auth_headers, path) -> None:
    r = await client.get(path, headers=auth_headers)
    assert r.status_code == 200, r.text
