"""GPT analyst (fake OpenAI client), spend controls, queue, schema validation and Telegram alerts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import respx
from httpx import Response
from pydantic import ValidationError
from sqlalchemy import select

from backend.ai.analyst import AnalystQueue, GptAnalyst
from backend.ai.context import build_analyst_context, context_hash
from backend.ai.schema import GptAnalysisOutput
from backend.ai.spend import SpendController
from backend.database.engine import session_scope
from backend.filters.pipeline import full_pipeline
from backend.models import AISpendDaily, Alert, CandidateScore, GptAnalysis, Token
from backend.notifications.alerts import AlertService, build_candidate_message
from backend.notifications.telegram import TelegramClient, TelegramError
from backend.ranking.ranker import Ranker
from backend.services import keys
from backend.services.redis_client import get_redis
from backend.tests.conftest import requires_services
from backend.tests.fixtures import pumpportal as fx
from backend.tests.test_filters_ranking import healthy_state, make_ctx


def good_output(recommendation: str = "WATCHLIST", p10: float = 0.4) -> dict:
    return {
        "overall_assessment": "Moderate setup with uncertain holders.",
        "bullish_evidence": ["rising unique buyers", "net inflow"],
        "bearish_evidence": ["holder data from stream estimate"],
        "whale_activity_interpretation": "No significant whale activity.",
        "developer_insider_risk": "Creator history unknown.",
        "liquidity_exit_risk": "Round-trip cost ~3% at $250.",
        "potential_catalysts": [],
        "potential_manipulation": [],
        "probability_estimates": {"p_plus_10_pct": p10, "p_plus_15_pct": p10 * 0.7, "p_plus_20_pct": p10 * 0.5, "p_rug_or_collapse": 0.3, "uncertainty": "high", "rationale": "limited history"},
        "target_scenarios": [{"target_pct": 10, "probability": p10, "conditions": "buyers keep growing", "estimated_time_horizon_minutes": 30}],
        "estimated_time_horizon_minutes": 45,
        "invalidating_conditions": ["developer sells", "liquidity drops"],
        "data_sufficiency": "partial",
        "missing_data": ["holder count from Helius"],
        "final_recommendation": recommendation,
        "recommendation_confidence": "low",
    }


def test_schema_rejects_inconsistent_output() -> None:
    GptAnalysisOutput.model_validate(good_output())
    bad = good_output()
    bad["probability_estimates"]["p_plus_20_pct"] = 0.9  # not monotone
    with pytest.raises(ValidationError):
        GptAnalysisOutput.model_validate(bad)
    bad2 = good_output("STRONG_CANDIDATE")
    bad2["data_sufficiency"] = "insufficient"
    with pytest.raises(ValidationError):
        GptAnalysisOutput.model_validate(bad2)
    bad3 = good_output()
    bad3["extra_field"] = 1
    with pytest.raises(ValidationError):
        GptAnalysisOutput.model_validate(bad3)
    bad4 = good_output()
    bad4["final_recommendation"] = "BUY_NOW"
    with pytest.raises(ValidationError):
        GptAnalysisOutput.model_validate(bad4)


def test_context_builder_is_compact_and_hash_ignores_timestamps() -> None:
    now = datetime.now(UTC)
    ctx = make_ctx(healthy_state(now), now)
    result = full_pipeline().run(ctx)
    ranking = Ranker().rank(ctx, result)
    context = build_analyst_context(ctx, result, ranking)
    text = json.dumps(context)
    assert len(text) < 25_000
    assert context["ml_predictions"]["warning"].startswith("EXPERIMENTAL")
    assert context["sellability"]["estimates"][0]["size_usd"] == 100.0
    assert "mint" in context["token"] and context["data_freshness"]["sol_usd_missing"] is False
    h1 = context_hash(context)
    context2 = dict(context, as_of="2030-01-01T00:00:00+00:00")
    assert context_hash(context2) == h1


class FakeResponses:
    def __init__(self, outputs: list, usage=(1200, 400)):  # noqa: ANN001
        self.outputs = list(outputs)
        self.calls: list[dict] = []
        self.usage = usage

    async def parse(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        parsed = None
        if isinstance(item, dict) and "final_recommendation" in item:
            try:
                parsed = GptAnalysisOutput.model_validate(item)
            except ValidationError:
                parsed = None  # mimic a response whose JSON the SDK could not parse into the model
        return SimpleNamespace(output_parsed=parsed, output_text=json.dumps(item), usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))


class FakeModels:
    def __init__(self, available: bool = True, listed: list[str] | None = None) -> None:
        self.available = available
        self.listed = listed or []

    async def retrieve(self, model: str):  # noqa: ANN202
        if not self.available:
            exc = Exception("model not found")
            exc.status_code = 404  # type: ignore[attr-defined]
            raise exc
        return SimpleNamespace(id=model)

    async def list(self):  # noqa: ANN202
        return SimpleNamespace(data=[SimpleNamespace(id=m) for m in self.listed])


def fake_client(outputs: list, available: bool = True, listed: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(responses=FakeResponses(outputs), models=FakeModels(available, listed))


async def _seed_token_and_score() -> int:
    async with session_scope() as s:
        s.add(Token(mint=fx.MINT, symbol="GEM", name="Test Gem", status="candidate", first_seen_at=datetime.now(UTC), launch_time=datetime.now(UTC) - timedelta(minutes=5)))
        await s.flush()
        score = CandidateScore(mint=fx.MINT, scored_at=datetime.now(UTC), status="candidate", overall_score=80.0, confidence="medium", components={"safety": 80.0}, is_latest=True)
        s.add(score)
        await s.flush()
        return score.id


@requires_services
async def test_analyst_success_path_records_spend_and_caches(monkeypatch) -> None:
    from backend.config.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s.ai, "enabled", True)
    score_id = await _seed_token_and_score()
    client = fake_client([good_output("STRONG_CANDIDATE", 0.6)])
    analyst = GptAnalyst(client=client)
    assert analyst.enabled and await analyst.verify_model() is True and analyst.model_verified is True
    context = {"token": {"mint": fx.MINT}, "market": {"price_usd": 0.01}}
    row = await analyst.analyze(fx.MINT, context, score_id)
    assert row.status == "ok" and row.recommendation == "STRONG_CANDIDATE"
    assert row.input_tokens == 1200 and row.output_tokens == 400
    expected_cost = 1200 / 1e6 * s.ai.input_cost_per_1m_usd + 400 / 1e6 * s.ai.output_cost_per_1m_usd
    assert abs(row.estimated_cost_usd - expected_cost) < 1e-9
    call = client.responses.calls[0]
    assert call["model"] == s.ai.model and call["text_format"] is GptAnalysisOutput and call["max_output_tokens"] == s.ai.max_output_tokens
    async with session_scope() as sess:
        spend = await sess.get(AISpendDaily, datetime.now(UTC).date())
        assert spend is not None and spend.analyses == 1 and spend.input_tokens == 1200
        updated = await sess.get(CandidateScore, score_id)
        assert updated.gpt_recommendation == "STRONG_CANDIDATE" and updated.gpt_analysis_id == row.id
    # same context again -> served from cache, no second API call
    row2 = await analyst.analyze(fx.MINT, context, score_id)
    assert row2.status == "cached" and len(client.responses.calls) == 1
    day, month, count = await analyst.spend.usage()
    assert count == 1 and day > 0 and month == day


@requires_services
async def test_analyst_invalid_output_is_stored_and_not_used(monkeypatch) -> None:
    from backend.config.settings import get_settings

    monkeypatch.setattr(get_settings().ai, "enabled", True)
    score_id = await _seed_token_and_score()
    bad = good_output()
    bad["probability_estimates"]["p_plus_20_pct"] = 0.95
    analyst = GptAnalyst(client=fake_client([bad]))
    row = await analyst.analyze(fx.MINT, {"token": {"mint": fx.MINT}}, score_id)
    assert row.status == "invalid" and row.analysis is None and "schema validation failed" in row.error
    async with session_scope() as sess:
        score = await sess.get(CandidateScore, score_id)
        assert score.gpt_recommendation is None
        spend = await sess.get(AISpendDaily, datetime.now(UTC).date())
        assert spend.failed == 1


@requires_services
async def test_spend_limits_block_analysis(monkeypatch) -> None:
    from backend.config.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s.ai, "enabled", True)
    monkeypatch.setattr(s.ai, "daily_spend_limit_usd", 0.0001)
    score_id = await _seed_token_and_score()
    client = fake_client([good_output()])
    analyst = GptAnalyst(client=client)
    row = await analyst.analyze(fx.MINT, {"token": {"mint": fx.MINT}}, score_id)
    assert row.status == "skipped" and "daily spend limit" in row.error
    assert client.responses.calls == []  # no API call made
    monkeypatch.setattr(s.ai, "daily_spend_limit_usd", 10.0)
    monkeypatch.setattr(s.ai, "max_analyses_per_token", 1)
    await get_redis().set(f"{keys.AI_SPEND_PREFIX}token:{fx.MINT}", 1)
    row2 = await analyst.analyze(fx.MINT, {"token": {"mint": fx.MINT, "x": 1}}, score_id)
    assert row2.status == "skipped" and "per-token" in row2.error
    decision = await SpendController().check("other", 0.01)
    assert decision.allowed


@requires_services
async def test_sdk_validation_error_is_invalid_not_retried(monkeypatch) -> None:
    from backend.config.settings import get_settings

    monkeypatch.setattr(get_settings().ai, "enabled", True)
    score_id = await _seed_token_and_score()
    try:
        GptAnalysisOutput.model_validate({})
    except ValidationError as exc:
        err = exc
    client = fake_client([err, good_output()])
    analyst = GptAnalyst(client=client)
    row = await analyst.analyze(fx.MINT, {"token": {"mint": fx.MINT}}, score_id)
    assert row.status == "invalid" and len(client.responses.calls) == 1  # no retry after a schema failure


@requires_services
async def test_model_unavailable_disables_without_substitution(monkeypatch) -> None:
    from backend.config.settings import get_settings

    monkeypatch.setattr(get_settings().ai, "enabled", True)
    await _seed_token_and_score()
    analyst = GptAnalyst(client=fake_client([], available=False))
    assert await analyst.verify_model() is False
    assert not analyst.enabled and "not available" in analyst.disabled_reason and analyst.model_verified is False
    row = await analyst.analyze(fx.MINT, {"token": {"mint": fx.MINT}}, None)
    assert row.status == "failed" and row.model == get_settings().ai.model


@requires_services
async def test_queue_retries_then_dead_letters(monkeypatch) -> None:
    from backend.config.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s.ai, "enabled", True)
    monkeypatch.setattr(s.ai, "max_retries", 0)
    monkeypatch.setattr(s.ai, "queue_max_attempts", 2)
    await _seed_token_and_score()
    client = fake_client([RuntimeError("boom"), RuntimeError("boom again")])
    analyst = GptAnalyst(client=client)
    results: list[str] = []

    async def on_result(mint, row):  # noqa: ANN001
        results.append(row.status)

    queue = AnalystQueue(analyst, on_result=on_result)
    assert await queue.enqueue(fx.MINT, {"token": {"mint": fx.MINT}}, None, None)
    assert not await queue.enqueue(fx.MINT, {"token": {"mint": fx.MINT}}, None, None)  # already in flight
    import asyncio

    task = asyncio.create_task(queue.run())
    for _ in range(100):
        if queue.failed >= 1:
            break
        await asyncio.sleep(0.1)
    await queue.stop()
    await asyncio.wait_for(task, 10)
    assert queue.failed == 1 and results == []
    dead = await get_redis().lrange(keys.GPT_DEAD_LETTER, 0, -1)
    assert len(dead) == 1 and json.loads(dead[0])["attempts"] == 2
    assert await get_redis().scard(keys.GPT_INFLIGHT) == 0
    async with session_scope() as sess:
        rows = (await sess.execute(select(GptAnalysis))).scalars().all()
        assert len(rows) == 2 and all(r.status == "failed" for r in rows)


# ----------------------------------------------------------------------------- telegram / alerts
@respx.mock
async def test_telegram_client_sends_and_handles_rate_limit(monkeypatch) -> None:
    from backend.config.settings import get_settings

    cfg = get_settings().telegram
    monkeypatch.setattr(cfg, "enabled", True)
    calls = {"n": 0}

    def _side(request):  # noqa: ANN001
        calls["n"] += 1
        body = json.loads(request.content)
        assert body["chat_id"] == "123" and body["parse_mode"] == "HTML" and body["link_preview_options"]["is_disabled"] is True
        if calls["n"] == 1:
            return Response(429, json={"ok": False, "parameters": {"retry_after": 0}})
        return Response(200, json={"ok": True, "result": {"message_id": 1}})

    respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(side_effect=_side)
    client = TelegramClient(token="TOKEN", chat_id="123")
    data = await client.send_message("<b>hi</b>")
    assert data["ok"] and calls["n"] == 2 and client.tracker.counters.rate_limited == 1
    respx.post("https://api.telegram.org/botTOKEN/getMe").mock(return_value=Response(400, json={"ok": False, "description": "Unauthorized"}))
    with pytest.raises(TelegramError):
        await client.get_me()
    disabled = TelegramClient(token=None, chat_id=None)
    with pytest.raises(TelegramError):
        await disabled.send_message("x")


def test_candidate_message_formatting() -> None:
    now = datetime.now(UTC)
    state = healthy_state(now)
    score = CandidateScore(id=1, mint=fx.MINT, scored_at=now, status="candidate", overall_score=81.4, confidence="medium", components={"safety": 70.0, "momentum": 80.0}, penalties={"unreliable_data": 5.0}, rug_risk=0.2, breakout_p10=0.4, breakout_p15=0.3, breakout_p20=0.2, ml_is_experimental=True, whale_signal="WHALE_ACCUMULATION", whale_summary={"smart_money_buyers": 1, "whale_net_flow_5m": 2.5}, execution={"estimates": [{"size_usd": 250.0, "available": True, "sell_impact_pct": 3.1, "round_trip_cost_pct": 4.2}]})
    snapshot = {"age_seconds": 400, "market_cap_usd": 55_000.0, "liquidity_usd": 9_000.0, "volume_sol_5m": 12.3, "unique_buyers_5m": 40, "holder_count": 120, "top10_pct": 0.3, "dev_holding_pct": 0.05}
    msg = build_candidate_message(state, score, snapshot)
    assert "GEM" in msg and fx.MINT in msg and "81/100" in msg and "(experimental)" in msg
    assert "dexscreener.com/solana/" in msg and "Exec $250" in msg and "WHALE_ACCUMULATION" in msg
    gpt = GptAnalysis(id=5, mint=fx.MINT, model="gpt-5.6", created_at=now, context_hash="x", status="ok", recommendation="STRONG_CANDIDATE", analysis=good_output("STRONG_CANDIDATE", 0.6))
    msg2 = build_candidate_message(state, score, snapshot, gpt)
    assert "GPT: <b>STRONG_CANDIDATE</b>" in msg2 and "✅" in msg2 and "⚠️" in msg2
    assert len(msg2) < 4000


@requires_services
@respx.mock
async def test_alert_service_cooldown_dedupe_and_risk_alerts(monkeypatch) -> None:
    from backend.config.settings import get_settings

    cfg = get_settings().telegram
    monkeypatch.setattr(cfg, "enabled", True)
    monkeypatch.setattr(cfg, "alert_min_score", 70.0)
    route = respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(return_value=Response(200, json={"ok": True, "result": {}}))
    service = AlertService(TelegramClient(token="TOKEN", chat_id="1"))
    now = datetime.now(UTC)
    state = healthy_state(now)
    async with session_scope() as s:
        s.add(Token(mint=fx.MINT, symbol="GEM", status="candidate", first_seen_at=now, launch_time=now))
    score = CandidateScore(id=1, mint=fx.MINT, scored_at=now, status="candidate", overall_score=75.0, confidence="low", components={}, ml_is_experimental=True)
    snapshot = {"age_seconds": 300}
    assert await service.send_candidate_alert(state, score, snapshot) == "sent"
    assert state.alerted and route.call_count == 1
    assert await service.send_candidate_alert(state, score, snapshot) == "cooldown"
    low = CandidateScore(id=2, mint=fx.MINT, scored_at=now, status="candidate", overall_score=50.0, confidence="low", components={}, ml_is_experimental=True)
    assert await service.send_candidate_alert(state, low, snapshot) == "skipped_score"
    gpt_bad = GptAnalysis(id=9, mint=fx.MINT, model="m", created_at=now, context_hash="h", status="ok", recommendation="REJECT", analysis={})
    assert await service.send_candidate_alert(state, score, snapshot, gpt_bad) == "skipped_gpt_downgrade"
    # urgent warnings only for surfaced tokens, with their own cooldown
    assert await service.send_risk_alert(state, "whale_exit", "whales sold 3 SOL") == "sent"
    assert await service.send_risk_alert(state, "whale_exit", "whales sold 3 SOL") == "cooldown"
    fresh = healthy_state(now)
    fresh.mint = "Other" + "o" * 39
    assert await service.send_risk_alert(fresh, "whale_exit", "x") == "skipped_not_surfaced"
    async with session_scope() as s:
        alerts = (await s.execute(select(Alert).order_by(Alert.id))).scalars().all()
        assert [a.status for a in alerts] == ["sent", "sent"]
        assert alerts[1].urgent and alerts[1].alert_type == "whale_exit"
    # Telegram disabled -> suppressed but recorded
    service2 = AlertService(TelegramClient(token=None, chat_id=None))
    fresh.alerted = True
    assert await service2.send_risk_alert(fresh, "rug_pull", "rugged") == "suppressed"


@requires_services
async def test_model_unavailable_lists_alternatives_or_accepts_listed(monkeypatch) -> None:
    from backend.config.settings import get_settings

    monkeypatch.setattr(get_settings().ai, "enabled", True)
    a = GptAnalyst(client=fake_client([], available=False, listed=["gpt-5.6-sol", "gpt-5-mini", "whisper-1"]))
    assert await a.verify_model() is False
    assert "gpt-5.6-sol" in a.disabled_reason and "gpt-5-mini" in a.disabled_reason and "whisper" not in a.disabled_reason
    b = GptAnalyst(client=fake_client([], available=False, listed=[get_settings().ai.model]))
    assert await b.verify_model() is True and b.enabled and b.model_verified


def test_model_suggestions_newest_first() -> None:
    from backend.ai.analyst import rank_model_suggestions

    avail = {"gpt-4.1", "gpt-4.1-2025-04-14", "gpt-5", "gpt-5-mini", "gpt-5-codex", "gpt-5.5", "gpt-5.6-terra", "gpt-5.6-luna", "whisper-1", "gpt-5-2025-08-07"}
    out = rank_model_suggestions(avail)
    assert out[:2] == ["gpt-5.6-luna", "gpt-5.6-terra"] and out[2] == "gpt-5.5"
    assert "whisper-1" not in out and "gpt-5-codex" not in out
    assert out.index("gpt-5") < out.index("gpt-5-2025-08-07") and out[-1] == "gpt-4.1-2025-04-14"
