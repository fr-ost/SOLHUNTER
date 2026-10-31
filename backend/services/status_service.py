"""System status aggregation shared by the API dashboard and the Telegram/health code."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.settings import get_settings
from backend.models import (
    AISpendDaily,
    Alert,
    CandidateScore,
    ModelVersion,
    ProviderStatus,
    ProviderUsageDaily,
    Token,
    WorkerHeartbeat,
)
from backend.services import keys
from backend.services.redis_client import get_redis


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def scanner_status() -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(UTC)
    try:
        data = await get_redis().hgetall(keys.SCANNER_HEARTBEAT)
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "error": str(exc)[:200], "stale": True}
    if not data:
        return {"status": "offline", "stale": True, "last_heartbeat_at": None}
    last = _parse_ts(data.get("ts"))
    age = (now - last).total_seconds() if last else None
    stale = age is None or age > settings.scanner.stale_after_seconds
    stats: dict[str, Any] = {}
    try:
        stats = json.loads(data.get("stats") or "{}")
    except ValueError:
        stats = {}
    return {
        "status": "stale" if stale else data.get("status", "running"),
        "worker_id": data.get("worker_id"),
        "is_leader": data.get("is_leader") == "1",
        "last_heartbeat_at": last.isoformat() if last else None,
        "heartbeat_age_seconds": round(age, 1) if age is not None else None,
        "stale": stale,
        "started_at": data.get("started_at"),
        "stats": stats,
    }


async def provider_statuses(session: AsyncSession) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rows = (await session.execute(select(ProviderStatus))).scalars().all()
    by_name = {r.provider: r for r in rows}
    redis_live: dict[str, dict[str, Any]] = {}
    try:
        r = get_redis()
        for name in ("pumpportal", "dexscreener", "helius", "openai", "telegram", "solana_rpc"):
            raw = await r.get(keys.provider_status_key(name))
            if raw:
                redis_live[name] = json.loads(raw)
    except Exception:  # noqa: BLE001
        pass
    names = sorted(set(by_name) | set(redis_live))
    for name in names:
        row = by_name.get(name)
        live = redis_live.get(name, {})
        out.append(
            {
                "provider": name,
                "status": live.get("status") or (row.status if row else "unknown"),
                "last_success_at": live.get("last_success_at") or (row.last_success_at.isoformat() if row and row.last_success_at else None),
                "last_error_at": live.get("last_error_at") or (row.last_error_at.isoformat() if row and row.last_error_at else None),
                "last_error": live.get("last_error") or (row.last_error if row else None),
                "reconnect_count": live.get("reconnect_count", row.reconnect_count if row else 0),
                "details": live.get("details") or (row.details if row else {}) or {},
            }
        )
    return out


async def daily_counters() -> dict[str, int]:
    try:
        data = await get_redis().hgetall(keys.daily_stats_key())
    except Exception:  # noqa: BLE001
        return {}
    return {k: int(v) for k, v in data.items() if str(v).lstrip("-").isdigit()}


async def counts_24h(session: AsyncSession) -> dict[str, int]:
    since = datetime.now(UTC) - timedelta(hours=24)
    discovered = await session.scalar(select(func.count()).select_from(Token).where(Token.first_seen_at >= since)) or 0
    rejected = await session.scalar(
        select(func.count()).select_from(Token).where(Token.first_seen_at >= since, Token.status.in_(["rejected", "dead"]))
    ) or 0
    screening = await session.scalar(
        select(func.count()).select_from(Token).where(Token.first_seen_at >= since, Token.status.in_(["screening", "tracking"]), Token.phase != "dead")
    ) or 0
    candidates = await session.scalar(
        select(func.count(func.distinct(CandidateScore.mint))).where(
            CandidateScore.scored_at >= since, CandidateScore.status == "candidate"
        )
    ) or 0
    watchlist = await session.scalar(
        select(func.count(func.distinct(CandidateScore.mint))).where(
            CandidateScore.scored_at >= since, CandidateScore.status == "watchlist"
        )
    ) or 0
    high_risk_alerts = await session.scalar(
        select(func.count()).select_from(Alert).where(Alert.created_at >= since, Alert.urgent.is_(True))
    ) or 0
    return {
        "discovered": int(discovered),
        "rejected": int(rejected),
        "screening": int(screening),
        "candidates": int(candidates),
        "watchlist": int(watchlist),
        "high_risk_alerts": int(high_risk_alerts),
    }


async def queue_backlog() -> dict[str, int]:
    try:
        r = get_redis()
        return {
            "gpt_queue": int(await r.llen(keys.GPT_QUEUE)),
            "gpt_dead_letter": int(await r.llen(keys.GPT_DEAD_LETTER)),
            "track_requests": int(await r.llen(keys.TRACK_REQUESTS)),
        }
    except Exception:  # noqa: BLE001
        return {}


async def ai_spend_summary(session: AsyncSession) -> dict[str, Any]:
    settings = get_settings()
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    today_row = await session.get(AISpendDaily, today)
    month = (
        await session.execute(
            select(
                func.coalesce(func.sum(AISpendDaily.estimated_cost_usd), 0.0),
                func.coalesce(func.sum(AISpendDaily.analyses), 0),
                func.coalesce(func.sum(AISpendDaily.input_tokens), 0),
                func.coalesce(func.sum(AISpendDaily.output_tokens), 0),
            ).where(AISpendDaily.day >= month_start)
        )
    ).one()
    return {
        "enabled": settings.ai.enabled and bool(settings.openai_api_key),
        "model": settings.ai.model,
        "today": {
            "analyses": today_row.analyses if today_row else 0,
            "failed": today_row.failed if today_row else 0,
            "input_tokens": today_row.input_tokens if today_row else 0,
            "output_tokens": today_row.output_tokens if today_row else 0,
            "estimated_cost_usd": round(today_row.estimated_cost_usd, 4) if today_row else 0.0,
            "limit_usd": settings.ai.daily_spend_limit_usd,
            "max_analyses": settings.ai.max_analyses_per_day,
        },
        "month": {
            "estimated_cost_usd": round(float(month[0]), 4),
            "analyses": int(month[1]),
            "input_tokens": int(month[2]),
            "output_tokens": int(month[3]),
            "limit_usd": settings.ai.monthly_spend_limit_usd,
        },
        "pricing_assumption": {
            "input_per_1m_usd": settings.ai.input_cost_per_1m_usd,
            "output_per_1m_usd": settings.ai.output_cost_per_1m_usd,
            "note": "Configured via AI_INPUT_COST_PER_1M_USD / AI_OUTPUT_COST_PER_1M_USD - verify against current OpenAI pricing",
        },
    }


async def cost_estimate(session: AsyncSession) -> dict[str, Any]:
    settings = get_settings()
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    days_elapsed = max(1, (today - month_start).days + 1)
    ai = await ai_spend_summary(session)
    usage_rows = (
        await session.execute(
            select(
                ProviderUsageDaily.provider,
                func.sum(ProviderUsageDaily.requests),
                func.sum(ProviderUsageDaily.credits_used),
                func.sum(ProviderUsageDaily.estimated_cost_usd),
            )
            .where(ProviderUsageDaily.day >= month_start)
            .group_by(ProviderUsageDaily.provider)
        )
    ).all()
    providers = [
        {
            "provider": p,
            "requests_month": int(req or 0),
            "credits_month": float(cred or 0),
            "estimated_cost_usd_month": round(float(cost or 0), 4),
        }
        for p, req, cred, cost in usage_rows
    ]
    ai_month = ai["month"]["estimated_cost_usd"]
    ai_projected = ai_month / days_elapsed * 30
    provider_cost = sum(p["estimated_cost_usd_month"] for p in providers)
    helius = next((p for p in providers if p["provider"] == "helius"), None)
    return {
        "month_to_date_usd": round(ai_month + provider_cost, 4),
        "projected_month_usd": round(ai_projected + provider_cost / days_elapsed * 30 + settings.cost_railway_estimate_usd_month + settings.cost_helius_plan_usd_month, 2),
        "breakdown": {
            "railway_estimate_usd": settings.cost_railway_estimate_usd_month,
            "helius_plan_usd": settings.cost_helius_plan_usd_month,
            "openai_month_to_date_usd": ai_month,
            "openai_projected_usd": round(ai_projected, 2),
            "providers": providers,
        },
        "helius_credits": {
            "month_to_date": helius["credits_month"] if helius else 0,
            "monthly_budget": settings.helius_monthly_credit_budget,
            "daily_budget": settings.helius_daily_credit_budget,
        },
        "note": "Estimates only. Railway charges by actual usage; provider costs depend on plan.",
    }


async def model_status(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(select(ModelVersion).order_by(ModelVersion.trained_at.desc()).limit(30))).scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "version": r.version,
            "trained_at": r.trained_at.isoformat(),
            "training_samples": r.training_samples,
            "positive_samples": r.positive_samples,
            "metrics": r.metrics or {},
            "is_active": r.is_active,
            "is_experimental": r.is_experimental,
            "notes": r.notes,
        }
        for r in rows
    ]


async def worker_heartbeats(session: AsyncSession) -> list[dict[str, Any]]:
    settings = get_settings()
    now = datetime.now(UTC)
    recent = datetime.now(UTC) - timedelta(hours=1)
    rows = (await session.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.last_heartbeat_at >= recent).order_by(WorkerHeartbeat.last_heartbeat_at.desc()).limit(5))).scalars().all()
    return [
        {
            "worker_id": r.worker_id,
            "role": r.role,
            "hostname": r.hostname,
            "started_at": r.started_at.isoformat(),
            "last_heartbeat_at": r.last_heartbeat_at.isoformat(),
            "is_leader": r.is_leader,
            "status": ("stopped" if r.status == "stopping" else "stale") if (now - r.last_heartbeat_at).total_seconds() > settings.scanner.stale_after_seconds else r.status,
            "stale": (now - r.last_heartbeat_at).total_seconds() > settings.scanner.stale_after_seconds,
            "stats": r.stats or {},
        }
        for r in rows
    ]
