"""Shared pytest fixtures.

Integration tests need PostgreSQL and Redis. Configure with:
  TEST_DATABASE_URL (default postgresql://postgres@localhost:5433/solhunter_test)
  TEST_REDIS_URL    (default redis://localhost:6380/1)
Tests that need them are skipped automatically when the services are unreachable.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@localhost:5433/solhunter_test")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/1")

# Environment must be set before backend.config.settings is imported anywhere.
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": TEST_DATABASE_URL,
        "REDIS_URL": TEST_REDIS_URL,
        "API_KEYS": "test-read-key",
        "API_ADMIN_KEYS": "test-admin-key",
        "LOG_FORMAT": "console",
        "LOG_LEVEL": "WARNING",
        "RUN_MIGRATIONS_ON_START": "false",
        "AI_ENABLED": "false",
        "TELEGRAM_ENABLED": "false",
        "RATE_LIMIT_PER_MINUTE": "6000",
        "RATE_LIMIT_BURST": "1000",
        "CORS_ALLOWED_ORIGINS": "https://dashboard.example",
        "SERVICE_ROLE": "api",
    }
)

from backend.config.settings import reload_settings  # noqa: E402

reload_settings()

from backend.database.engine import dispose_engine, get_engine, reset_engine_for_tests  # noqa: E402
from backend.models import Base  # noqa: E402
from backend.services.redis_client import close_redis, get_redis, reset_redis_for_tests  # noqa: E402


def _port_open(url: str, default_port: int) -> bool:
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


SERVICES_AVAILABLE = _port_open(TEST_DATABASE_URL, 5432) and _port_open(TEST_REDIS_URL, 6379)
requires_services = pytest.mark.skipif(not SERVICES_AVAILABLE, reason="PostgreSQL/Redis test services not reachable")


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema() -> None:
    """Apply migrations once per test session (verifies the migration chain itself)."""
    if not SERVICES_AVAILABLE:
        return
    from alembic import command

    from backend.database.migrate import alembic_config

    url = reload_settings().sqlalchemy_database_url

    async def _reset() -> None:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(_reset())
    reset_engine_for_tests()
    command.upgrade(alembic_config(url), "head")


@pytest_asyncio.fixture(autouse=True)
async def _fresh_clients() -> AsyncIterator[None]:
    """Give every test a fresh engine/redis bound to its own event loop and a clean database."""
    reset_engine_for_tests()
    reset_redis_for_tests()
    reload_settings()
    from backend.providers.base import ProviderTracker

    ProviderTracker._registry.clear()  # noqa: SLF001 - provider status is process-global; isolate tests
    if SERVICES_AVAILABLE:
        engine = get_engine()
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await get_redis().flushdb()
    yield
    if SERVICES_AVAILABLE:
        await dispose_engine()
        await close_redis()
    reset_engine_for_tests()
    reset_redis_for_tests()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from backend.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": "test-read-key"}


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-API-Key": "test-admin-key"}
