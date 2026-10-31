"""Configuration tests (no services required)."""

from __future__ import annotations

from backend.config.settings import Settings


def test_database_url_is_converted_to_asyncpg(monkeypatch) -> None:
    s = Settings(database_url="postgres://u:p@host:5432/db", _env_file=None)
    assert s.sqlalchemy_database_url == "postgresql+asyncpg://u:p@host:5432/db"
    s2 = Settings(database_url="postgresql://u:p@host/db", _env_file=None)
    assert s2.sqlalchemy_database_url.startswith("postgresql+asyncpg://")


def test_allowed_origins_parsed() -> None:
    s = Settings(cors_allowed_origins="https://a.example, https://b.example", _env_file=None)
    assert s.allowed_origins == ["https://a.example", "https://b.example"]


def test_production_validation_flags_problems() -> None:
    s = Settings(app_env="production", database_url="postgresql://x@localhost/db", redis_url="redis://localhost", api_keys="", api_admin_keys="", _env_file=None)
    problems = s.validate_for_production()
    assert any("API_KEYS" in p for p in problems)
    assert any("DATABASE_URL" in p for p in problems)
    assert any("REDIS_URL" in p for p in problems)


def test_production_validation_passes_with_proper_config() -> None:
    s = Settings(
        app_env="production",
        database_url="postgresql://u:p@postgres.railway.internal:5432/railway",
        redis_url="redis://default:pw@redis.railway.internal:6379",
        api_keys="k1",
        openai_api_key="sk",
        _env_file=None,
    )
    s.ai.enabled = True
    assert s.validate_for_production() == []


def test_nested_group_env_prefixes(monkeypatch) -> None:
    monkeypatch.setenv("FILTER_MIN_LIQUIDITY_USD", "9999")
    monkeypatch.setenv("RANK_CANDIDATE_MIN_SCORE", "80")
    monkeypatch.setenv("SCANNER_SNAPSHOT_SCHEDULE_MINUTES", "1,5,15")
    s = Settings(_env_file=None)
    assert s.filters.min_liquidity_usd == 9999
    assert s.ranking.candidate_min_score == 80
    assert s.scanner.snapshot_schedule == [1, 5, 15]
    assert s.execution.trade_sizes == [100.0, 250.0, 500.0]
