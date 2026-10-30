"""AI spending controls: daily/monthly USD limits, analysis counts and per-token caps.

Counters live in Redis (fast, shared) and are mirrored into ``ai_spend_daily`` for reporting.
When limits are reached analyses are skipped and the reason is recorded - never silently continued.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.models import AISpendDaily
from backend.services import keys
from backend.services.redis_client import get_redis

log = get_logger(__name__)


@dataclass
class SpendDecision:
    allowed: bool
    reason: str | None
    today_cost_usd: float
    month_cost_usd: float
    today_analyses: int


class SpendController:
    @staticmethod
    def _keys() -> tuple[str, str, str]:
        now = datetime.now(UTC)
        day = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        return f"{keys.AI_SPEND_PREFIX}cost:{day}", f"{keys.AI_SPEND_PREFIX}cost:{month}", f"{keys.AI_SPEND_PREFIX}count:{day}"

    @staticmethod
    def estimate_cost(input_tokens: int, output_tokens: int) -> float:
        ai = get_settings().ai
        return input_tokens / 1e6 * ai.input_cost_per_1m_usd + output_tokens / 1e6 * ai.output_cost_per_1m_usd

    async def usage(self) -> tuple[float, float, int]:
        day_key, month_key, count_key = self._keys()
        try:
            r = get_redis()
            day = float(await r.get(day_key) or 0)
            month = float(await r.get(month_key) or 0)
            count = int(await r.get(count_key) or 0)
            return day, month, count
        except Exception as exc:  # noqa: BLE001
            log.warning("spend.redis_unavailable", error=str(exc)[:100])
            return 0.0, 0.0, 0

    async def check(self, mint: str, estimated_cost_usd: float) -> SpendDecision:
        ai = get_settings().ai
        day, month, count = await self.usage()
        if not ai.enabled:
            return SpendDecision(False, "AI analysis disabled (AI_ENABLED=false)", day, month, count)
        if count >= ai.max_analyses_per_day:
            return SpendDecision(False, f"daily analysis cap reached ({ai.max_analyses_per_day})", day, month, count)
        if day + estimated_cost_usd > ai.daily_spend_limit_usd:
            return SpendDecision(False, f"daily spend limit ${ai.daily_spend_limit_usd:.2f} would be exceeded (${day:.3f} used)", day, month, count)
        if month + estimated_cost_usd > ai.monthly_spend_limit_usd:
            return SpendDecision(False, f"monthly spend limit ${ai.monthly_spend_limit_usd:.2f} would be exceeded (${month:.3f} used)", day, month, count)
        try:
            per_token = int(await get_redis().get(f"{keys.AI_SPEND_PREFIX}token:{mint}") or 0)
        except Exception:  # noqa: BLE001
            per_token = 0
        if per_token >= ai.max_analyses_per_token:
            return SpendDecision(False, f"per-token analysis cap reached ({ai.max_analyses_per_token})", day, month, count)
        return SpendDecision(True, None, day, month, count)

    async def record(self, session: AsyncSession | None, mint: str, input_tokens: int, output_tokens: int, cost_usd: float, failed: bool = False) -> None:
        day_key, month_key, count_key = self._keys()
        try:
            r = get_redis()
            pipe = r.pipeline()
            pipe.incrbyfloat(day_key, cost_usd)
            pipe.expire(day_key, 3 * 86400)
            pipe.incrbyfloat(month_key, cost_usd)
            pipe.expire(month_key, 40 * 86400)
            pipe.incr(count_key)
            pipe.expire(count_key, 3 * 86400)
            pipe.incr(f"{keys.AI_SPEND_PREFIX}token:{mint}")
            pipe.expire(f"{keys.AI_SPEND_PREFIX}token:{mint}", 86400)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("spend.record_redis_failed", error=str(exc)[:100])
        if session is None:
            return
        now = datetime.now(UTC)
        stmt = pg_insert(AISpendDaily).values(day=now.date(), analyses=0 if failed else 1, failed=1 if failed else 0, input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost_usd=cost_usd, updated_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[AISpendDaily.day],
            set_={
                "analyses": AISpendDaily.analyses + stmt.excluded.analyses,
                "failed": AISpendDaily.failed + stmt.excluded.failed,
                "input_tokens": AISpendDaily.input_tokens + stmt.excluded.input_tokens,
                "output_tokens": AISpendDaily.output_tokens + stmt.excluded.output_tokens,
                "estimated_cost_usd": AISpendDaily.estimated_cost_usd + stmt.excluded.estimated_cost_usd,
                "updated_at": now,
            },
        )
        await session.execute(stmt)
