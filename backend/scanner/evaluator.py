"""Evaluator: turns a snapshot into an explainable decision.

Flow per snapshot:
  cheap pass (stream data only) -> [enrichment when promising: Helius holders, creator history,
  wallet profiles, relationships/clusters] -> whale engine -> features -> ML prediction ->
  full filter pass -> ranking -> persistence (candidate_scores, model_predictions, events) ->
  hooks (GPT analyst, alerts) registered by later components.
Hard safety failures always win over positive momentum, whale or GPT signals.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ai.analyst import AnalystQueue, GptAnalyst
from backend.ai.context import build_analyst_context
from backend.config.logging import get_logger
from backend.database.engine import session_scope
from backend.execution.analyzer import analyze_execution
from backend.filters.base import EvaluationContext, PipelineResult
from backend.filters.pipeline import cheap_pipeline, full_pipeline
from backend.ingestion.events import TradeEvent
from backend.intelligence.developer import analyze_creator
from backend.intelligence.holders import analyze_holders
from backend.intelligence.manipulation import assess_manipulation
from backend.intelligence.maturity import phase_for_age
from backend.intelligence.relationships import (
    analyze_clusters,
    discover_funders,
    load_edges,
    persist_edges,
    shared_funder_edges,
    timing_edges,
)
from backend.intelligence.wallets import PositionUpdater, TradeInput, WalletProfiler, mark_token_rugged
from backend.ml.features import build_features, features_json_safe
from backend.ml.predictor import HeuristicPredictor, Prediction, PredictorProtocol
from backend.ml.scheduler import MLScheduler
from backend.models import (
    CandidateHistory,
    CandidateScore,
    GptAnalysis,
    ModelPrediction,
    TokenSnapshot,
    WhaleSignal,
)
from backend.notifications.alerts import AlertService
from backend.providers.base import BudgetExceededError, DataUnavailable, ProviderError
from backend.ranking.ranker import Ranker, RankingResult
from backend.scanner.state import TokenState
from backend.services import keys
from backend.services.redis_client import publish_event
from backend.whale_engine.engine import WhaleEngine, whale_event_rows

if TYPE_CHECKING:
    from backend.scanner.main import ScannerWorker

log = get_logger(__name__)

DecisionHook = Callable[[EvaluationContext, PipelineResult, RankingResult, int], Awaitable[None]]


class Evaluator:
    def __init__(self, worker: ScannerWorker) -> None:
        self.worker = worker
        self.settings = worker.settings
        self.helius = worker.helius
        self.persistence = worker.persistence
        self.profiler = WalletProfiler()
        self.positions = PositionUpdater()
        self.whale_engine = WhaleEngine()
        self.predictor: PredictorProtocol = HeuristicPredictor()
        self.ml_scheduler = MLScheduler(self)
        self.ranker = Ranker()
        self.cheap = cheap_pipeline()
        self.full = full_pipeline()
        self.hooks: list[DecisionHook] = []
        self.analyst = GptAnalyst()
        self.analyst_queue = AnalystQueue(self.analyst, on_result=self.on_gpt_result)
        self.alerts = AlertService()
        self.counters: dict[str, int] = {"evaluations": 0, "enrichments": 0, "candidates": 0, "watchlist": 0, "rejected": 0, "screening": 0, "hard_fails": 0, "errors": 0, "gpt_enqueued": 0, "alerts_sent": 0}
        self._recent_candidates: deque[datetime] = deque(maxlen=100)
        self._stop = asyncio.Event()
        if worker.tracker is not None:
            worker.tracker.trade_listeners.append(self.record_trade)

    # ------------------------------------------------------------------ wiring
    def stats(self) -> dict[str, Any]:
        return {
            **self.counters,
            "positions_pending": self.positions.pending,
            "predictor": getattr(self.predictor, "version", "unknown"),
            "predictor_experimental": getattr(self.predictor, "is_experimental", True),
            "ml": self.ml_scheduler.stats(),
            "gpt": {**self.analyst.status(), "processed": self.analyst_queue.processed, "dead_lettered": self.analyst_queue.failed},
            "alerts": self.alerts.stats(),
        }

    def background_tasks(self) -> list[asyncio.Task[Any]]:
        return [
            asyncio.create_task(self._positions_loop(), name="positions"),
            asyncio.create_task(self.ml_scheduler.run(), name="ml-scheduler"),
            asyncio.create_task(self._analyst_startup_then_run(), name="gpt-queue"),
        ]

    async def _analyst_startup_then_run(self) -> None:
        await self.analyst.verify_model()
        await self.analyst.tracker.publish()
        await self.analyst_queue.run()

    async def shutdown(self) -> None:
        self._stop.set()
        await self.ml_scheduler.stop()
        await self.analyst_queue.stop()
        try:
            await self._flush_positions()
        except Exception:  # noqa: BLE001
            log.exception("evaluator.final_flush_failed")

    def record_trade(self, state: TokenState, ev: TradeEvent) -> None:
        if ev.sol_amount is None or ev.token_amount is None:
            return
        self.positions.add(
            TradeInput(ev.trader, ev.mint, ev.side, ev.sol_amount, ev.token_amount, ev.block_time, state.launch_time, ev.new_token_balance, ev.price_sol)
        )

    async def _positions_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=5)
            except TimeoutError:
                pass
            try:
                await self._flush_positions()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.counters["errors"] += 1
                log.exception("evaluator.positions_flush_error")

    async def _flush_positions(self) -> None:
        if not self.positions.pending:
            return
        touched = {t.wallet for t in self.positions._pending}  # noqa: SLF001
        async with session_scope() as session:
            await self.positions.flush(session)
        mark_prices = {s.mint: s.last_price for s in (self.worker.tracker.states.values() if self.worker.tracker else []) if s.last_price}
        async with session_scope() as session:
            await self.profiler.recompute(session, sorted(touched)[:200], mark_prices=mark_prices)

    # ------------------------------------------------------------------ main entry
    async def on_snapshot(self, state: TokenState, kind: str, row: dict[str, Any], snapshot_id: int) -> None:
        try:
            await self.evaluate(state, kind, row, snapshot_id)
        except Exception:  # noqa: BLE001
            self.counters["errors"] += 1
            log.exception("evaluator.failed", mint=state.mint, kind=kind)

    async def evaluate(self, state: TokenState, kind: str, row: dict[str, Any], snapshot_id: int) -> tuple[PipelineResult, RankingResult]:
        self.counters["evaluations"] += 1
        now: datetime = row["captured_at"]
        sol_usd = row.get("sol_usd")
        ctx = EvaluationContext(state=state, snapshot=row, now=now, sol_usd=sol_usd, snapshot_id=snapshot_id)
        ctx.manipulation = assess_manipulation(state, now)
        ctx.execution = analyze_execution(state, sol_usd)
        async with session_scope() as session:
            ctx.creator = await analyze_creator(state, session, helius=None, deep=False)
            ctx.holders = analyze_holders(state, state.holders, now)
            cheap = self.cheap.run(ctx)
            if self._promising(cheap, state, kind):
                await self._enrich(ctx, session)
            profiles = await self.profiler.load(session, self._wallets_of_interest(state, now))
            ctx.whales = self.whale_engine.assess(state, profiles, ctx.clusters, ctx.liquidity_sol, sol_usd, now)
            ctx.features = build_features(ctx)
            ctx.prediction = self.predictor.predict(ctx.features)
            result = self.full.run(ctx)
            gpt_prev = state.flags.get("gpt") or {}
            ranking = self.ranker.rank(ctx, result, gpt_recommendation=gpt_prev.get("recommendation"), gpt_probability=gpt_prev.get("p10"))
            self._apply_rate_limit(ranking, now)
            score_id = await self._persist(ctx, result, ranking, session)
        await self._publish(ctx, result, ranking)
        await self._decide_gpt_and_alerts(ctx, result, ranking, score_id)
        for hook in self.hooks:
            try:
                await hook(ctx, result, ranking, snapshot_id)
            except Exception:  # noqa: BLE001
                log.exception("evaluator.hook_error", mint=state.mint)
        return result, ranking

    # ------------------------------------------------------------------ enrichment
    def _promising(self, cheap: PipelineResult, state: TokenState, kind: str) -> bool:
        if cheap.hard_fail or state.flags.get("hard_rejected"):
            return False
        if kind == "m1" and not (state.flags.get("manual") or state.flags.get("source") == "growth"):
            return False
        if len(cheap.rejection_reasons) > 2:
            return False
        scores = [v for k, v in cheap.stage_scores.items() if k != "s1"]
        return bool(scores) and sum(scores) / len(scores) >= 0.4

    def _wallets_of_interest(self, state: TokenState, now: datetime) -> list[str]:
        top = [w for w, _ in state.top_wallets(15)]
        cutoff = now - timedelta(seconds=300)
        recent = [t.trader for t in reversed(state.trades) if t.ts >= cutoff]
        seen: list[str] = []
        for w in top + recent:
            if w not in seen:
                seen.append(w)
            if len(seen) >= 60:
                break
        return seen

    async def _enrich(self, ctx: EvaluationContext, session: AsyncSession) -> None:
        state, now = ctx.state, ctx.now
        self.counters["enrichments"] += 1
        ctx.deep = True
        s = self.settings.scanner
        # holders (Helius, cached in Redis for 60s, respects credit budget)
        if state.holders_fetched_at is None or (now - state.holders_fetched_at).total_seconds() >= s.holder_refresh_interval_seconds:
            infra = {x for x in (state.bonding_curve_key, state.pool_address) if x}
            try:
                state.holders = await self.helius.holder_distribution(state.mint, infra_owners=infra, creator=state.creator)
                state.holders_fetched_at = now
                self.persistence.add_holder_snapshot(
                    {
                        "mint": state.mint,
                        "captured_at": now,
                        "holder_count": state.holders.holder_count,
                        "total_supply": state.holders.total_supply,
                        "largest_holder_pct": state.holders.largest_pct,
                        "top10_pct": state.holders.concentration(10),
                        "top20_pct": state.holders.concentration(20),
                        "dev_holding_pct": state.holders.wallet_pct(state.creator),
                        "infra_excluded_pct": (1.0 - (state.holders.circulating_excluding_infra or 0) / state.holders.total_supply) if state.holders.total_supply else None,
                        "top_holders": [h.__dict__ for h in state.holders.top_holders[:20]],
                        "source": state.holders.source,
                        "is_complete": state.holders.holder_count_complete,
                    }
                )
            except (DataUnavailable, BudgetExceededError) as exc:
                state.flags["holders_unavailable"] = str(exc)[:120]
            except ProviderError as exc:
                log.debug("evaluator.holders_failed", mint=state.mint, error=str(exc)[:120])
        ctx.holders = analyze_holders(state, state.holders, now)
        # creator history (Helius enhanced, cached 1h per creator, once per token)
        if not state.flags.get("creator_deep_done"):
            ctx.creator = await analyze_creator(state, session, helius=self.helius, deep=True)
            state.creator_analysis = ctx.creator.to_dict()
            state.flags["creator_deep_done"] = True
        # relationships: timing edges from this token + persisted graph + (optional) shared funders
        edges = timing_edges(state)
        if edges:
            await persist_edges(session, edges)
        wallets = list(state.wallets)[:200]
        if self.helius.enhanced_available and not state.flags.get("funders_done"):
            top_buyers = [w for w, _ in state.top_wallets(8) if w != state.creator]
            try:
                funders = await discover_funders(self.helius, top_buyers, max_wallets=8)
                funder_edges = shared_funder_edges(funders, state.mint)
                if funder_edges:
                    await persist_edges(session, funder_edges)
                    edges.extend(funder_edges)
            except Exception as exc:  # noqa: BLE001
                log.debug("evaluator.funders_failed", error=str(exc)[:120])
            state.flags["funders_done"] = True
        stored = await load_edges(session, wallets, min_confidence=0.2)
        ctx.clusters = analyze_clusters(state, stored + edges)

    # ------------------------------------------------------------------ decisions & persistence
    def _apply_rate_limit(self, ranking: RankingResult, now: datetime) -> None:
        limit = self.settings.ranking.max_candidates_per_hour
        recent = [t for t in self._recent_candidates if (now - t).total_seconds() < 3600]
        if ranking.status == "candidate" and len(recent) >= limit:
            ranking.status = "watchlist"
            ranking.explanation.append(f"demoted to watchlist: {limit} candidates already promoted this hour")

    @staticmethod
    def _compact(result: PipelineResult, ctx: EvaluationContext, surfaced: bool) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        """Full detail for surfaced tokens; compact (reasons + scores only) for rejected ones to bound storage."""
        filters = result.to_dict()
        whales = ctx.whales.to_dict() if ctx.whales else None
        execution = ctx.execution.to_dict() if ctx.execution else None
        if surfaced:
            return filters, whales, execution
        filters["stages"] = [{k: v for k, v in st.items() if k != "metrics"} for st in filters["stages"]]
        if whales is not None:
            whales = {k: v for k, v in whales.items() if k not in {"activities"}}
        if execution is not None:
            ref = next((e for e in execution.get("estimates", []) if e.get("size_usd") == 250.0), None)
            execution = {"venue": execution.get("venue"), "source": execution.get("source"), "reference_250": ref, "max_size_usd_within_slippage": execution.get("max_size_usd_within_slippage")}
        return filters, whales, execution

    def _should_persist_score(self, kind: str, prev_status: str, new_status: str, result: PipelineResult) -> bool:
        if kind != "periodic":
            return True  # scheduled m1/m3/m5/m10/m15 and growth snapshots are the designed training/decision points
        return new_status in {"candidate", "watchlist"} or new_status != prev_status or result.hard_fail

    async def _persist(self, ctx: EvaluationContext, result: PipelineResult, ranking: RankingResult, session: AsyncSession) -> int:
        state = ctx.state
        now = ctx.now
        prev_status = state.status
        new_status = ranking.status  # candidate / watchlist / screening / rejected (hard)
        if result.hard_fail:
            state.flags["hard_rejected"] = True
            self.counters["hard_fails"] += 1
        self.counters[{"candidate": "candidates", "watchlist": "watchlist", "rejected": "rejected"}.get(new_status, "screening")] += 1
        state.phase = phase_for_age(state.age_at(now)) if not result.hard_fail else state.phase
        if new_status == "candidate" and prev_status != "candidate":
            self._recent_candidates.append(now)
        kind = str(ctx.snapshot.get("snapshot_kind") or "periodic")
        persist_score = self._should_persist_score(kind, prev_status, new_status, result)
        p: Prediction | None = ctx.prediction
        if not persist_score:
            # Keep the in-memory decision, counters and events, but skip the (large) score/prediction rows.
            state.status = new_status
            state.status_reason = "; ".join(result.rejection_reasons[:3])[:500] if result.rejected else f"score {ranking.overall_score:.0f} ({ranking.confidence})"
            state.last_evaluation = {"score": ranking.overall_score, "status": new_status, "confidence": ranking.confidence, "at": now.isoformat(), "whale_signal": ctx.whales.primary_signal if ctx.whales else None, "score_id": (state.last_evaluation or {}).get("score_id")}
            self.persistence.add_token(state)
            await self._risk_events(ctx, result, session)
            return int(state.last_evaluation.get("score_id") or 0)
        surfaced = new_status in {"candidate", "watchlist"}
        filters_json, whales_json, execution_json = self._compact(result, ctx, surfaced)
        await session.execute(update(CandidateScore).where(CandidateScore.mint == state.mint, CandidateScore.is_latest.is_(True)).values(is_latest=False))
        score = CandidateScore(
            mint=state.mint,
            snapshot_id=ctx.snapshot_id,
            scored_at=now,
            status=new_status,
            phase=state.phase,
            growth_score=state.growth_score,
            p_2x_24h=getattr(p, "p_2x_24h", None) if p else None,
            overall_score=ranking.overall_score,
            confidence=ranking.confidence,
            components=ranking.components,
            penalties=ranking.penalties,
            filter_results=filters_json,
            rejection_reasons=result.rejection_reasons + result.quality_failures,
            hard_fail=result.hard_fail,
            whale_signal=ctx.whales.primary_signal if ctx.whales else None,
            whale_summary=whales_json,
            rug_risk=p.rug_risk if p else None,
            breakout_p10=p.p10 if p else None,
            breakout_p15=p.p15 if p else None,
            breakout_p20=p.p20 if p else None,
            expected_return_pct=p.expected_return_pct if p else None,
            ml_is_experimental=p.is_experimental if p else True,
            model_version=p.model_version if p else None,
            execution=execution_json,
            explanation=ranking.explanation,
            is_latest=True,
        )
        session.add(score)
        if p is not None:
            session.add(
                ModelPrediction(
                    mint=state.mint,
                    snapshot_id=ctx.snapshot_id,
                    predicted_at=now,
                    model_version=p.model_version,
                    is_experimental=p.is_experimental,
                    rug_risk=p.rug_risk,
                    breakout_p10=p.p10,
                    breakout_p15=p.p15,
                    breakout_p20=p.p20,
                    expected_return_pct=p.expected_return_pct,
                    p_2x_24h=getattr(p, "p_2x_24h", None),
                    expected_downside_pct=p.expected_downside_pct,
                    expected_time_to_target_s=p.expected_time_to_target_s,
                    p_stop_loss=p.p_stop_loss,
                    confidence=p.confidence,
                    uncertainty=p.uncertainty,
                )
            )
        if ctx.snapshot_id is not None and ctx.features:
            await session.execute(update(TokenSnapshot).where(TokenSnapshot.id == ctx.snapshot_id).values(features=features_json_safe(ctx.features), smart_money_buyers=ctx.whales.smart_money_buyers if ctx.whales else None, whale_net_flow_sol_5m=ctx.whales.whale_net_flow_5m if ctx.whales else None))
        if new_status != prev_status:
            session.add(CandidateHistory(mint=state.mint, event="status_change", from_status=prev_status, to_status=new_status, score=ranking.overall_score, reason="; ".join((result.rejection_reasons or result.quality_failures or ranking.explanation)[:3])[:500], details={"kind": ctx.snapshot.get("snapshot_kind")}, occurred_at=now))
        await session.flush()
        state.status = new_status
        state.status_reason = "; ".join(result.rejection_reasons[:3])[:500] if result.rejected else f"score {ranking.overall_score:.0f} ({ranking.confidence})"
        state.last_evaluation = {"score": ranking.overall_score, "status": new_status, "confidence": ranking.confidence, "at": now.isoformat(), "whale_signal": ctx.whales.primary_signal if ctx.whales else None, "score_id": None}
        self.persistence.add_token(state)
        if ctx.whales:
            whale_rows, smart_rows = whale_event_rows(state.mint, ctx.whales, ctx.liquidity_sol)
            for r in whale_rows:
                self.persistence.add_whale_event(r)
            for r in smart_rows:
                self.persistence.add_smart_money_event(r)
        await self._risk_events(ctx, result, session)
        state.last_evaluation["score_id"] = score.id
        return score.id

    async def _risk_events(self, ctx: EvaluationContext, result: PipelineResult, session: AsyncSession) -> None:
        state, now = ctx.state, ctx.now
        kind = ctx.snapshot.get("snapshot_kind", "periodic")
        events: list[tuple[str, str, str]] = []
        mp = ctx.manipulation
        if mp is not None and mp.hard_fail:
            events.append(("liquidity_withdrawal", "critical", mp.hard_fail_reason or "liquidity withdrawn"))
        dev_sold = float(state.dev_activity().get("sold_pct") or 0.0)
        if dev_sold >= self.settings.filters.reject_if_dev_sold_pct_over:
            events.append(("dev_dump", "critical", f"developer sold {dev_sold:.0%} of their tokens"))
        if ctx.whales is not None:
            if WhaleSignal.WHALE_EXIT_WARNING.value in ctx.whales.signals:
                events.append(("whale_exit", "critical", ctx.whales.hard_warning or "whale exit warning"))
            if WhaleSignal.INSIDER_SELLING.value in ctx.whales.signals:
                events.append(("insider_selling", "warning", f"insider/developer selling {ctx.whales.insider_sell_sol + ctx.whales.dev_sell_sol:.2f} SOL"))
            if WhaleSignal.WHALE_DISTRIBUTION.value in ctx.whales.signals:
                events.append(("whale_distribution", "warning", f"whales net selling {abs(ctx.whales.whale_net_flow_5m):.2f} SOL over 5m"))
        if mp is not None and mp.pump_dump_score >= 0.5:
            events.append(("pump_and_dump", "warning", "spike-and-collapse pattern"))
        if ctx.holders is not None and ctx.holders.largest_holder_pct is not None and ctx.holders.largest_holder_pct > self.settings.filters.max_largest_holder_pct * 2:
            events.append(("concentration", "warning", f"largest holder {ctx.holders.largest_holder_pct:.0%}"))
        collapse = state.momentum_pct(900, now)
        rugged = (mp is not None and mp.hard_fail) or (dev_sold >= 0.5 and collapse is not None and collapse < -70)
        if rugged and not state.flags.get("rugged"):
            state.flags["rugged"] = True
            events.append(("rug_pull", "critical", "liquidity withdrawal / developer dump with price collapse"))
            await mark_token_rugged(session, state.mint)
        for risk_type, severity, title in events:
            self.persistence.add_risk_event(
                {
                    "mint": state.mint,
                    "risk_type": risk_type,
                    "severity": severity,
                    "title": title[:256],
                    "details": {"snapshot_kind": kind, "score": state.last_evaluation.get("score") if state.last_evaluation else None},
                    "dedupe_key": f"{state.mint}:{risk_type}:{kind}:{now.strftime('%Y%m%d%H%M')}"[:256],
                    "occurred_at": now,
                    "alerted": False,
                }
            )
            state.flags.setdefault("risk_events", []).append({"type": risk_type, "severity": severity, "title": title, "at": now.isoformat()})

    # ------------------------------------------------------------------ GPT analyst + alerts
    def _gpt_wanted(self, ctx: EvaluationContext, result: PipelineResult, ranking: RankingResult) -> bool:
        state = ctx.state
        cfg = self.settings
        if not self.analyst.enabled or result.rejected or result.hard_fail:
            return False
        if ranking.overall_score < cfg.ranking.gpt_min_score or not result.candidate_eligible:
            return False
        if state.gpt_analyses >= cfg.ai.max_analyses_per_token:
            return False
        last = state.flags.get("gpt_at")
        if last and (ctx.now - datetime.fromisoformat(last)).total_seconds() < cfg.ai.cache_ttl_seconds:
            return False
        return True

    async def _decide_gpt_and_alerts(self, ctx: EvaluationContext, result: PipelineResult, ranking: RankingResult, score_id: int) -> None:
        state = ctx.state
        # urgent warnings for tokens we surfaced before
        for ev in list(state.flags.get("risk_events", []))[-5:]:
            if ev.get("alerted"):
                continue
            ev["alerted"] = True
            if ev.get("severity") == "critical" or ev.get("type") in {"insider_selling", "whale_distribution"}:
                outcome = await self.alerts.send_risk_alert(state, ev["type"], ev["title"], {"snapshot_kind": ctx.snapshot.get("snapshot_kind"), "score": ranking.overall_score})
                if outcome.startswith("sent"):
                    self.counters["alerts_sent"] += 1
        if state.alerted and ranking.status == "rejected" and state.flags.get("last_alert_status") in {"candidate", "watchlist"}:
            await self.alerts.send_risk_alert(state, "risk_escalation", "Previously alerted token no longer passes safety filters: " + "; ".join(result.rejection_reasons[:2]), {"from_status": state.flags.get("last_alert_status"), "to_status": "rejected"})
            state.flags["last_alert_status"] = "rejected"
        if ranking.status not in {"candidate", "watchlist"}:
            return
        if self._gpt_wanted(ctx, result, ranking):
            context = build_analyst_context(ctx, result, ranking)
            if await self.analyst_queue.enqueue(state.mint, context, score_id, ctx.snapshot_id):
                state.gpt_analyses += 1
                state.flags["gpt_at"] = ctx.now.isoformat()
                state.flags["alert_pending"] = {"score_id": score_id, "snapshot": {k: v for k, v in ctx.snapshot.items() if k not in {"features", "data_sources"}}}
                self.counters["gpt_enqueued"] += 1
                return  # the candidate alert is sent once the GPT verdict is in (or on failure)
        await self._send_candidate_alert(state, score_id, ctx.snapshot, None)

    async def _send_candidate_alert(self, state: TokenState, score_id: int, snapshot: dict[str, Any], gpt: GptAnalysis | None) -> None:
        async with session_scope() as session:
            score = await session.get(CandidateScore, score_id)
            if score is None:
                return
            outcome = await self.alerts.send_candidate_alert(state, score, snapshot, gpt)
            if outcome.startswith("sent"):
                score.alerted = True
                state.flags["last_alert_status"] = score.status
                self.counters["alerts_sent"] += 1
                session.add(CandidateHistory(mint=state.mint, event="alerted", to_status=score.status, score=score.overall_score, reason=outcome, details={"gpt": gpt.recommendation if gpt else None}, occurred_at=datetime.now(UTC)))

    async def on_gpt_result(self, mint: str, row: GptAnalysis) -> None:
        state = self.worker.tracker.states.get(mint) if self.worker.tracker else None
        if state is None:
            return
        if row.status in {"ok", "cached"} and row.analysis:
            p = (row.analysis.get("probability_estimates") or {})
            state.flags["gpt"] = {"recommendation": row.recommendation, "p10": p.get("p_plus_10_pct"), "analysis_id": row.id, "at": row.created_at.isoformat()}
            if row.recommendation in {"REJECT", "HIGH_RISK"}:
                async with session_scope() as session:
                    session.add(CandidateHistory(mint=mint, event="gpt_downgrade", from_status=state.status, to_status="watchlist", reason=row.recommendation, details={"analysis_id": row.id}, occurred_at=datetime.now(UTC)))
                if state.status == "candidate":
                    state.status = "watchlist"
                    state.status_reason = f"GPT verdict {row.recommendation}"
                    self.persistence.add_token(state)
        pending = state.flags.pop("alert_pending", None)
        if pending:
            await self._send_candidate_alert(state, pending["score_id"], pending["snapshot"], row if row.status in {"ok", "cached"} else None)

    async def _publish(self, ctx: EvaluationContext, result: PipelineResult, ranking: RankingResult) -> None:
        state = ctx.state
        await publish_event(
            keys.EVENTS_CHANNEL,
            {
                "type": "token.evaluated",
                "mint": state.mint,
                "symbol": state.symbol,
                "status": ranking.status,
                "score": ranking.overall_score,
                "confidence": ranking.confidence,
                "whale_signal": ctx.whales.primary_signal if ctx.whales else None,
                "hard_fail": result.hard_fail,
                "reasons": (result.rejection_reasons or result.quality_failures)[:3],
                "kind": ctx.snapshot.get("snapshot_kind"),
                "at": ctx.now.isoformat(),
            },
        )
        self.persistence.incr_stat(f"evaluated_{ranking.status}")
