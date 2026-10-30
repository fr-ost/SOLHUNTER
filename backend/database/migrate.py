"""Safe migration helpers.

Only one service (the API) runs ``alembic upgrade head`` at start-up, guarded by a
PostgreSQL advisory lock so concurrent replicas/deploys cannot race. The scanner
waits until the schema is at the latest revision before it starts processing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.config.logging import get_logger

log = get_logger(__name__)

MIGRATION_LOCK_ID = 7_391_244_001  # arbitrary, project-wide constant
ROOT = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def head_revision() -> str | None:
    script = ScriptDirectory.from_config(alembic_config())
    return script.get_current_head()


async def current_revision(engine: AsyncEngine) -> str | None:
    def _get(sync_conn):  # type: ignore[no-untyped-def]
        ctx = MigrationContext.configure(sync_conn)
        return ctx.get_current_revision()

    async with engine.connect() as conn:
        return await conn.run_sync(_get)


async def run_migrations_with_lock(engine: AsyncEngine, database_url: str) -> str | None:
    """Upgrade to head while holding an advisory lock. Returns the resulting revision."""

    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        try:
            log.info("migrations.lock_acquired")
            await asyncio.to_thread(command.upgrade, alembic_config(database_url), "head")
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
            await conn.commit()
    rev = await current_revision(engine)
    log.info("migrations.completed", revision=rev)
    return rev


async def wait_for_schema(engine: AsyncEngine, timeout_seconds: int, poll_seconds: float = 3.0) -> bool:
    """Block until the database schema matches the code's head revision (or timeout)."""
    head = head_revision()
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            current = await current_revision(engine)
        except Exception as exc:  # noqa: BLE001
            current = None
            log.warning("migrations.wait_error", error=str(exc)[:200])
        if current == head:
            log.info("migrations.schema_ready", revision=current)
            return True
        if asyncio.get_running_loop().time() > deadline:
            log.error("migrations.schema_not_ready", current=current, head=head)
            return False
        log.info("migrations.waiting_for_schema", current=current, head=head)
        await asyncio.sleep(poll_seconds)
