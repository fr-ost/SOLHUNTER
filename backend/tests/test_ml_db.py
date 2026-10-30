"""Labeler + registry against the database (needs services)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.database.engine import session_scope
from backend.ml.labeler import label_pending_snapshots
from backend.ml.models import train_all
from backend.ml.registry import active_versions, load_predictor, store_bundle, train_and_store
from backend.models import (
    ModelVersion,
    Token,
    TokenSnapshot,
    TokenTransaction,
    TrainingSample,
    WalletTokenPosition,
)
from backend.tests.conftest import requires_services
from backend.tests.fixtures import pumpportal as fx
from backend.tests.test_ml import _synthetic_samples

pytestmark = requires_services


async def _seed_snapshot(mint: str, captured_at: datetime, price: float, liquidity: float, features: dict | None) -> int:
    async with session_scope() as s:
        if await s.get(Token, mint) is None:
            s.add(Token(mint=mint, symbol="T", status="tracking", first_seen_at=captured_at, launch_time=captured_at - timedelta(minutes=2)))
            await s.flush()
        snap = TokenSnapshot(mint=mint, captured_at=captured_at, snapshot_kind="m5", price_sol=price, liquidity_sol=liquidity, features=features)
        s.add(snap)
        await s.flush()
        return snap.id


async def test_labeler_labels_only_ready_snapshots_and_marks_rugs() -> None:
    now = datetime.now(UTC)
    old = now - timedelta(minutes=120)
    feats = {"age_seconds": 300.0, "momentum_5m_pct": 5.0}
    winner_id = await _seed_snapshot(fx.MINT, old, 1.0, 30.0, feats)
    rug_mint = "Rug" + "r" * 41
    rug_id = await _seed_snapshot(rug_mint, old, 1.0, 30.0, feats)
    recent_id = await _seed_snapshot("New" + "n" * 41, now - timedelta(minutes=10), 1.0, 30.0, feats)
    nofeat_id = await _seed_snapshot("NoF" + "f" * 41, old, 1.0, 30.0, None)
    async with session_scope() as s:
        s.add(TokenTransaction(mint=fx.MINT, signature="w1", wallet="A", tx_type="buy", price_sol=1.2, block_time=old + timedelta(minutes=5), v_sol_in_curve=62.0))
        s.add(TokenTransaction(mint=fx.MINT, signature="w2", wallet="B", tx_type="buy", price_sol=1.3, block_time=old + timedelta(minutes=20), v_sol_in_curve=64.0))
        s.add(TokenTransaction(mint=fx.MINT, signature="w3", wallet="C", tx_type="sell", price_sol=1.1, block_time=old + timedelta(minutes=50), v_sol_in_curve=60.0))
        s.add(TokenTransaction(mint=rug_mint, signature="r1", wallet="D", tx_type="sell", price_sol=0.1, block_time=old + timedelta(minutes=3), v_sol_in_curve=33.0))
        s.add(WalletTokenPosition(wallet="Holder" + "h" * 37, mint=rug_mint, tokens_bought=100.0, tokens_held_estimate=100.0, sol_spent=1.0))
    async with session_scope() as s:
        n = await label_pending_snapshots(s, limit=100, now=now)
    assert n == 2
    async with session_scope() as s:
        samples = {r.snapshot_id: r for r in (await s.execute(select(TrainingSample))).scalars().all()}
        assert set(samples) == {winner_id, rug_id}
        w = samples[winner_id]
        assert w.hit_10 and w.hit_15 and w.hit_20 and not w.rugged and w.observations == 3
        assert abs(w.max_return_pct - 30.0) < 1e-6 and abs(w.close_return_pct - 10.0) < 1e-6
        r = samples[rug_id]
        assert r.rugged and r.liquidity_removed and r.dead and not r.hit_10
        pos = (await s.execute(select(WalletTokenPosition).where(WalletTokenPosition.mint == rug_mint))).scalar_one()
        assert pos.outcome_rugged is True
        assert recent_id not in samples and nofeat_id not in samples  # JSON null features never become samples
    async with session_scope() as s:
        assert await label_pending_snapshots(s, limit=100, now=now) == 0  # idempotent


async def test_registry_store_activate_and_load() -> None:
    bundles = train_all(_synthetic_samples(n_tokens=60, per_token=2), version="reg-v1")
    async with session_scope() as s:
        for b in bundles:
            await store_bundle(s, b, activate=True)
    async with session_scope() as s:
        active = await active_versions(s)
        assert set(active) == {"rug_risk", "breakout", "expected_return"}
        predictor = await load_predictor(s)
    assert "reg-v1" in predictor.version and predictor.is_experimental
    p = predictor.predict({})
    assert p.model_version == predictor.version and p.p10 is not None
    # a newer, better version replaces the active one; a worse one is stored inactive
    better = train_all(_synthetic_samples(n_tokens=60, per_token=2, seed=9), version="reg-v2")
    for b in better:
        b.metrics = {**b.metrics, "walk_forward": {**b.metrics.get("walk_forward", {}), "auc": 0.99}, "hit_10": {**b.metrics.get("hit_10", {}), "auc": 0.99}, "close_return_holdout": {"mae_pct": 0.1}}
    async with session_scope() as s:
        from backend.ml.registry import is_better

        current = await active_versions(s)
        for b in better:
            assert is_better(b, current[b.name])
            await store_bundle(s, b, activate=True)
    async with session_scope() as s:
        rows = (await s.execute(select(ModelVersion).order_by(ModelVersion.id))).scalars().all()
        assert len(rows) == 6 and sum(1 for r in rows if r.is_active) == 3
        assert all(r.version == "reg-v2" for r in rows if r.is_active)


async def test_train_and_store_requires_minimum_samples(monkeypatch) -> None:
    async with session_scope() as s:
        summary = await train_and_store(s)
    assert summary["trained"] is False and "ML_MIN_TRAINING_SAMPLES" in summary["reason"]
