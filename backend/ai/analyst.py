"""GPT research analyst (OpenAI Responses API, structured output).

* Model name is configurable (``AI_MODEL``, default gpt-5.6) and verified at start-up with
  ``models.retrieve``; if unavailable the analyst is DISABLED with a clear status - never substituted.
* Only candidates that passed the inexpensive filters are analysed (enqueued by the evaluator hook).
* Output is validated against a strict schema; invalid output is stored as ``invalid`` and unused.
* Spend controls, caching by context hash, retries with backoff, timeouts and a dead-letter list.
* GPT never overrides hard safety filters: it only contributes one weighted ranking component.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from backend.ai.context import context_hash
from backend.ai.schema import SYSTEM_INSTRUCTIONS, GptAnalysisOutput
from backend.ai.spend import SpendController
from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.models import CandidateHistory, CandidateScore, GptAnalysis
from backend.providers.base import ProviderTracker
from backend.services import keys
from backend.services.redis_client import cache_get_json, cache_set_json, get_redis, publish_event

log = get_logger(__name__)


class AnalystUnavailable(Exception):
    pass


def rank_model_suggestions(available: set[str], limit: int = 20) -> list[str]:
    """Chat-capable models, newest family first, dated snapshots and special variants last."""
    import re

    def is_chat(m: str) -> bool:
        return m.startswith(("gpt-5", "gpt-4.1", "o3", "o4")) and not any(x in m for x in ("audio", "realtime", "transcribe", "tts", "image", "search", "codex"))

    def key(m: str) -> tuple:
        ver = re.match(r"gpt-(\d+)(?:\.(\d+))?", m)
        major = int(ver.group(1)) if ver else 0
        minor = int(ver.group(2) or 0) if ver else 0
        dated = bool(re.search(r"\d{4}-\d{2}-\d{2}", m))
        return (-major, -minor, dated, len(m), m)

    return sorted((m for m in available if is_chat(m)), key=key)[:limit]


def _extract_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)


class GptAnalyst:
    def __init__(self, client: Any | None = None) -> None:
        self.settings = get_settings()
        self.ai = self.settings.ai
        self.spend = SpendController()
        self.tracker = ProviderTracker.get("openai")
        self.client = client
        self.enabled = bool(self.ai.enabled and self.settings.openai_api_key) or client is not None
        self.model_verified: bool | None = None
        self.disabled_reason: str | None = None
        if not self.enabled:
            self.disabled_reason = "AI disabled or OPENAI_API_KEY missing"
            self.tracker.set_status("disabled", reason=self.disabled_reason)
        elif self.client is None:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(api_key=self.settings.openai_api_key, base_url=self.settings.openai_base_url or None, timeout=self.ai.timeout_seconds, max_retries=0)

    # ------------------------------------------------------------------ availability
    async def verify_model(self) -> bool:
        if not self.enabled:
            return False
        if not self.ai.verify_model_on_start:
            self.model_verified = None
            return True
        try:
            await self.client.models.retrieve(self.ai.model)
            self.model_verified = True
            self.tracker.set_status("connected", model=self.ai.model, verified=True)
            log.info("analyst.model_verified", model=self.ai.model)
            return True
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            status = getattr(exc, "status_code", None)
            if status == 404 or "NotFound" in name:
                available = await self._list_models()
                if self.ai.model in available:
                    self.model_verified = True
                    self.tracker.set_status("connected", model=self.ai.model, verified=True)
                    log.info("analyst.model_verified", model=self.ai.model, via="models.list")
                    return True
                self.enabled = False
                suggestions = rank_model_suggestions(available)
                hint = f" Models this key can use: {', '.join(suggestions)}." if suggestions else ""
                self.disabled_reason = f"configured model '{self.ai.model}' is not available for this API key - analysis disabled (no substitution). Set AI_MODEL to one of your models.{hint}"
                self.model_verified = False
                self.tracker.set_status("down", model=self.ai.model, reason=self.disabled_reason)
                log.error("analyst.model_unavailable", model=self.ai.model)
                return False
            # transient error: keep enabled, verify lazily on first call
            self.model_verified = None
            self.tracker.record_error(f"{name}: {exc}")
            log.warning("analyst.model_verification_deferred", error=f"{name}: {exc}"[:200])
            return True

    async def _list_models(self) -> set[str]:
        try:
            page = await self.client.models.list()
            data = getattr(page, "data", page)
            return {str(getattr(m, "id", m)) for m in data}
        except Exception as exc:  # noqa: BLE001
            log.warning("analyst.models_list_failed", error=f"{type(exc).__name__}: {exc}"[:200])
            return set()

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "model": self.ai.model, "model_verified": self.model_verified, "disabled_reason": self.disabled_reason}

    # ------------------------------------------------------------------ analysis
    async def analyze(self, mint: str, context: dict[str, Any], candidate_score_id: int | None = None) -> GptAnalysis:
        """Run (or fetch cached) analysis and persist a gpt_analyses row. Returns the row."""
        ch = context_hash(context)
        cache_key = keys.cache_key("gpt", f"{mint}:{ch}")
        cached = await cache_get_json(cache_key)
        if cached:
            row = await self._store(mint, ch, "cached", cached.get("analysis"), cached.get("recommendation"), 0, 0, 0.0, 0, None, context, candidate_score_id)
            return row
        if not self.enabled:
            return await self._store(mint, ch, "failed", None, None, 0, 0, 0.0, 0, self.disabled_reason or "analyst disabled", context, candidate_score_id)
        prompt = json.dumps(context, separators=(",", ":"), default=str)
        est_in = len(SYSTEM_INSTRUCTIONS) // 4 + len(prompt) // 4 + 200
        est_cost = self.spend.estimate_cost(est_in, self.ai.max_output_tokens)
        decision = await self.spend.check(mint, est_cost)
        if not decision.allowed:
            log.warning("analyst.skipped_spend_limit", mint=mint, reason=decision.reason)
            self.tracker.set_status("degraded", reason=decision.reason)
            return await self._store(mint, ch, "skipped", None, None, 0, 0, 0.0, 0, decision.reason, context, candidate_score_id)
        started = time.perf_counter()
        last_error: str | None = None
        for attempt in range(self.ai.max_retries + 1):
            try:
                response = await asyncio.wait_for(self._call(prompt), timeout=self.ai.timeout_seconds + 5)
                latency = int((time.perf_counter() - started) * 1000)
                in_tok, out_tok = _extract_usage(response)
                cost = self.spend.estimate_cost(in_tok, out_tok)
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    text = getattr(response, "output_text", None) or ""
                    try:
                        parsed = GptAnalysisOutput.model_validate_json(text)
                    except ValidationError as exc:
                        async with session_scope() as session:
                            await self.spend.record(session, mint, in_tok, out_tok, cost, failed=True)
                        self.tracker.record_error("schema validation failed")
                        return await self._store(mint, ch, "invalid", None, None, in_tok, out_tok, cost, latency, f"schema validation failed: {str(exc)[:300]}", context, candidate_score_id)
                else:
                    try:
                        parsed = GptAnalysisOutput.model_validate(parsed if isinstance(parsed, dict) else parsed.model_dump())
                    except ValidationError as exc:
                        async with session_scope() as session:
                            await self.spend.record(session, mint, in_tok, out_tok, cost, failed=True)
                        return await self._store(mint, ch, "invalid", None, None, in_tok, out_tok, cost, latency, f"schema validation failed: {str(exc)[:300]}", context, candidate_score_id)
                analysis = parsed.model_dump()
                self.tracker.record_success()
                self.tracker.set_status("connected", model=self.ai.model)
                async with session_scope() as session:
                    await self.spend.record(session, mint, in_tok, out_tok, cost)
                await cache_set_json(cache_key, {"analysis": analysis, "recommendation": parsed.final_recommendation}, self.ai.cache_ttl_seconds)
                return await self._store(mint, ch, "ok", analysis, parsed.final_recommendation, in_tok, out_tok, cost, latency, None, context, candidate_score_id)
            except TimeoutError:
                last_error = "timeout"
            except ValidationError as exc:
                # The SDK validated the model's JSON against our schema and it failed a semantic check:
                # not retryable, and the output is never used.
                latency = int((time.perf_counter() - started) * 1000)
                async with session_scope() as session:
                    await self.spend.record(session, mint, est_in, self.ai.max_output_tokens // 2, est_cost / 2, failed=True)
                self.tracker.record_error("schema validation failed")
                return await self._store(mint, ch, "invalid", None, None, est_in, 0, est_cost / 2, latency, f"schema validation failed: {str(exc)[:300]}", context, candidate_score_id)
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                status = getattr(exc, "status_code", None)
                if status in {400, 401, 403, 404}:
                    break  # not retryable
            self.tracker.record_error(last_error or "error", rate_limited="429" in (last_error or ""))
            await asyncio.sleep(min(20.0, 2.0 * (2**attempt)))
        latency = int((time.perf_counter() - started) * 1000)
        async with session_scope() as session:
            await self.spend.record(session, mint, 0, 0, 0.0, failed=True)
        return await self._store(mint, ch, "failed", None, None, 0, 0, 0.0, latency, last_error, context, candidate_score_id)

    async def _call(self, prompt: str) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.ai.model,
            "instructions": SYSTEM_INSTRUCTIONS,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            "text_format": GptAnalysisOutput,
            "max_output_tokens": self.ai.max_output_tokens,
            "store": False,
        }
        if self.ai.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.ai.reasoning_effort}
        return await self.client.responses.parse(**kwargs)

    async def _store(self, mint: str, ch: str, status: str, analysis: dict[str, Any] | None, recommendation: str | None, in_tok: int, out_tok: int, cost: float, latency: int, error: str | None, context: dict[str, Any], candidate_score_id: int | None) -> GptAnalysis:
        try:
            return await self._store_row(mint, ch, status, analysis, recommendation, in_tok, out_tok, cost, latency, error, context, candidate_score_id)
        except IntegrityError as exc:
            log.warning("analyst.store_failed", mint=mint, error=str(exc.orig)[:160] if getattr(exc, "orig", None) else str(exc)[:160])
            return GptAnalysis(mint=mint, candidate_score_id=candidate_score_id, model=self.ai.model, created_at=datetime.now(UTC), context_hash=ch, status=status, recommendation=recommendation, analysis=analysis, input_tokens=in_tok, output_tokens=out_tok, estimated_cost_usd=round(cost, 6), latency_ms=latency, error=error, context=context)

    async def _store_row(self, mint: str, ch: str, status: str, analysis: dict[str, Any] | None, recommendation: str | None, in_tok: int, out_tok: int, cost: float, latency: int, error: str | None, context: dict[str, Any], candidate_score_id: int | None) -> GptAnalysis:
        async with session_scope() as session:
            row = GptAnalysis(
                mint=mint,
                candidate_score_id=candidate_score_id,
                model=self.ai.model,
                created_at=datetime.now(UTC),
                context_hash=ch,
                status=status,
                recommendation=recommendation,
                analysis=analysis,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=round(cost, 6),
                latency_ms=latency,
                error=error,
                context=context,
            )
            session.add(row)
            await session.flush()
            if candidate_score_id is not None and status in {"ok", "cached"}:
                await session.execute(update(CandidateScore).where(CandidateScore.id == candidate_score_id).values(gpt_recommendation=recommendation, gpt_analysis_id=row.id))
                session.add(CandidateHistory(mint=mint, event="gpt_analyzed", score=None, reason=recommendation, details={"analysis_id": row.id, "status": status}, occurred_at=datetime.now(UTC)))
            return row


class AnalystQueue:
    """Redis-backed job queue with retries and a dead-letter list, consumed inside the scanner."""

    def __init__(self, analyst: GptAnalyst, on_result: Any | None = None) -> None:
        self.analyst = analyst
        self.on_result = on_result  # async callable(mint, GptAnalysis)
        self._stop = asyncio.Event()
        self.processed = 0
        self.failed = 0

    async def enqueue(self, mint: str, context: dict[str, Any], candidate_score_id: int | None, snapshot_id: int | None) -> bool:
        try:
            r = get_redis()
            inflight = await r.sadd(keys.GPT_INFLIGHT, mint)
            if not inflight:
                return False  # already queued/in progress for this mint
            await r.expire(keys.GPT_INFLIGHT, 3600)
            await r.rpush(keys.GPT_QUEUE, json.dumps({"mint": mint, "context": context, "candidate_score_id": candidate_score_id, "snapshot_id": snapshot_id, "attempts": 0, "queued_at": datetime.now(UTC).isoformat()}, default=str))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("analyst_queue.enqueue_failed", error=str(exc)[:200])
            return False

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        max_attempts = get_settings().ai.queue_max_attempts
        while not self._stop.is_set():
            try:
                raw = await get_redis().lpop(keys.GPT_QUEUE)
            except Exception as exc:  # noqa: BLE001
                log.warning("analyst_queue.redis_error", error=str(exc)[:200])
                raw = None
            if raw is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=3)
                except TimeoutError:
                    pass
                continue
            try:
                job = json.loads(raw)
            except ValueError:
                continue
            mint = job.get("mint")
            try:
                row = await self.analyst.analyze(mint, job["context"], job.get("candidate_score_id"))
                if row.status == "failed" and self.analyst.enabled:
                    raise RuntimeError(row.error or "analysis failed")
                self.processed += 1
                await publish_event(keys.EVENTS_CHANNEL, {"type": "gpt.analysis", "mint": mint, "status": row.status, "recommendation": row.recommendation, "analysis_id": row.id, "at": datetime.now(UTC).isoformat()})
                if self.on_result is not None:
                    await self.on_result(mint, row)
                await get_redis().srem(keys.GPT_INFLIGHT, mint)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                job["attempts"] = int(job.get("attempts", 0)) + 1
                job["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                try:
                    if job["attempts"] >= max_attempts:
                        self.failed += 1
                        await get_redis().rpush(keys.GPT_DEAD_LETTER, json.dumps(job, default=str))
                        await get_redis().ltrim(keys.GPT_DEAD_LETTER, -100, -1)
                        await get_redis().srem(keys.GPT_INFLIGHT, mint)
                        log.error("analyst_queue.dead_lettered", mint=mint, error=job["last_error"])
                    else:
                        await get_redis().rpush(keys.GPT_QUEUE, json.dumps(job, default=str))
                        await asyncio.sleep(2.0 * job["attempts"])
                except Exception as redis_exc:  # noqa: BLE001
                    log.error("analyst_queue.requeue_failed", error=str(redis_exc)[:200])
