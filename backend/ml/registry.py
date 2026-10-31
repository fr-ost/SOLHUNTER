"""Model registry: store/load model versions in PostgreSQL and build the production predictor."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.logging import get_logger
from backend.ml.models import TrainedBundle
from backend.ml.predictor import HeuristicPredictor, Prediction, PredictorProtocol
from backend.models import ModelVersion

log = get_logger(__name__)

MODEL_NAMES = ("rug_risk", "breakout", "expected_return")


async def store_bundle(session: AsyncSession, bundle: TrainedBundle, activate: bool, notes: str | None = None) -> ModelVersion:
    row = ModelVersion(
        name=bundle.name,
        version=bundle.version,
        trained_at=bundle.trained_at,
        training_samples=bundle.training_samples,
        positive_samples=bundle.positive_samples,
        feature_names=bundle.feature_names,
        params={"estimators": sorted(bundle.estimators)},
        metrics=bundle.metrics,
        artifact=bundle.to_bytes(),
        artifact_format="joblib",
        is_active=False,
        is_experimental=bundle.is_experimental,
        notes=notes,
    )
    session.add(row)
    await session.flush()
    if activate:
        await activate_version(session, row)
    return row


async def activate_version(session: AsyncSession, row: ModelVersion) -> None:
    await session.execute(update(ModelVersion).where(ModelVersion.name == row.name, ModelVersion.id != row.id).values(is_active=False))
    row.is_active = True
    await session.flush()


async def active_versions(session: AsyncSession) -> dict[str, ModelVersion]:
    rows = (await session.execute(select(ModelVersion).where(ModelVersion.is_active.is_(True)))).scalars().all()
    return {r.name: r for r in rows}


def _score(metrics: dict[str, Any], name: str) -> float | None:
    if name == "rug_risk":
        return metrics.get("walk_forward", {}).get("auc")
    if name == "breakout":
        return metrics.get("hit_10", {}).get("auc")
    if name == "expected_return":
        h = metrics.get("close_return_holdout")
        return -h["mae_pct"] if h else None
    return None


def is_better(candidate: TrainedBundle, current: ModelVersion | None) -> bool:
    if current is None:
        return True
    new, old = _score(candidate.metrics, candidate.name), _score(current.metrics or {}, current.name)
    if new is None:
        return False
    if old is None:
        return True
    return new >= old


class ModelPredictor:
    """Production predictor backed by trained bundles (falls back per-model to the heuristic)."""

    def __init__(self, bundles: dict[str, TrainedBundle]) -> None:
        self.bundles = bundles
        self.heuristic = HeuristicPredictor()
        versions = sorted({b.version for b in bundles.values()})
        self.version = "+".join(versions) if versions else "heuristic-v0"
        self.is_experimental = any(b.is_experimental for b in bundles.values()) or len(bundles) < len(MODEL_NAMES)

    def _vector(self, bundle: TrainedBundle, features: dict[str, float]) -> np.ndarray:
        row = np.full((1, len(bundle.feature_names)), np.nan, dtype=np.float32)
        for j, n in enumerate(bundle.feature_names):
            v = features.get(n)
            if v is not None and isinstance(v, int | float) and math.isfinite(float(v)):
                row[0, j] = float(v)
        return row

    def _prob(self, bundle: TrainedBundle, key: str, X: np.ndarray) -> float | None:
        est = bundle.estimators.get(key)
        if est is None:
            return None
        p = est.predict_proba(X)[:, 1]
        cal = bundle.calibrators.get(key)
        if cal is not None:
            p = cal.transform(p)
        return float(np.clip(p[0], 0.0, 1.0))

    def predict(self, features: dict[str, float]) -> Prediction:
        base = self.heuristic.predict(features)
        notes: list[str] = []
        rug = base.rug_risk
        p10, p15, p20 = base.p10, base.p15, base.p20
        p2x = base.p_2x_24h
        er, downside, ttt, p_stop = base.expected_return_pct, base.expected_downside_pct, base.expected_time_to_target_s, base.p_stop_loss
        uncertainty: dict[str, Any] = {}
        rb = self.bundles.get("rug_risk")
        if rb:
            rug = self._prob(rb, "rug", self._vector(rb, features))
            uncertainty["rug_auc"] = rb.metrics.get("walk_forward", {}).get("auc")
        else:
            notes.append("rug model missing: heuristic used")
        bb = self.bundles.get("breakout")
        if bb:
            X = self._vector(bb, features)
            p10 = self._prob(bb, "hit_10", X) if "hit_10" in bb.estimators else p10
            p15 = self._prob(bb, "hit_15", X) if "hit_15" in bb.estimators else min(p10 or 0, p15 or 0)
            p20 = self._prob(bb, "hit_20", X) if "hit_20" in bb.estimators else min(p15 or 0, p20 or 0)
            uncertainty["breakout_auc_p10"] = bb.metrics.get("hit_10", {}).get("auc")
            uncertainty["breakout_brier_p10"] = bb.metrics.get("hit_10", {}).get("brier")
            if "hit_100_24h" in bb.estimators:
                p2x = self._prob(bb, "hit_100_24h", X)
                uncertainty["moonshot_auc_24h"] = bb.metrics.get("hit_100_24h", {}).get("auc")
            else:
                notes.append("24h moonshot model not trained yet: heuristic prior used")
        else:
            notes.append("breakout model missing: heuristic used")
        eb = self.bundles.get("expected_return")
        if eb:
            X = self._vector(eb, features)
            if "close_return" in eb.estimators:
                er = float(eb.estimators["close_return"].predict(X)[0])
            if "min_return" in eb.estimators:
                downside = float(min(0.0, eb.estimators["min_return"].predict(X)[0]))
            if "time_to_10" in eb.estimators:
                ttt = float(max(30.0, eb.estimators["time_to_10"].predict(X)[0]))
            if "hit_stop" in eb.estimators:
                p_stop = self._prob(eb, "hit_stop", X)
            uncertainty["close_return_mae_pct"] = eb.metrics.get("close_return_holdout", {}).get("mae_pct")
        else:
            notes.append("expected-return model missing: heuristic used")
        auc = uncertainty.get("breakout_auc_p10") or 0.5
        confidence = max(0.1, min(0.95, (auc - 0.5) * 2)) if not self.is_experimental else 0.3
        if self.is_experimental:
            notes.append("Experimental: models trained on limited or weakly validated history.")
        return Prediction(
            rug_risk=round(rug, 4) if rug is not None else None,
            p10=round(p10, 4) if p10 is not None else None,
            p15=round(p15, 4) if p15 is not None else None,
            p20=round(p20, 4) if p20 is not None else None,
            expected_return_pct=round(er, 3) if er is not None else None,
            expected_downside_pct=round(downside, 3) if downside is not None else None,
            expected_time_to_target_s=round(ttt, 0) if ttt is not None else None,
            p_stop_loss=round(p_stop, 4) if p_stop is not None else None,
            confidence=round(confidence, 3),
            is_experimental=self.is_experimental,
            model_version=self.version,
            uncertainty=uncertainty,
            notes=notes,
            p_2x_24h=round(p2x, 4) if p2x is not None else None,
        )


async def load_predictor(session: AsyncSession) -> PredictorProtocol:
    versions = await active_versions(session)
    bundles: dict[str, TrainedBundle] = {}
    for name, row in versions.items():
        if row.artifact is None:
            continue
        try:
            bundles[name] = TrainedBundle.from_bytes(row.artifact)
        except Exception as exc:  # noqa: BLE001
            log.error("registry.artifact_load_failed", name=name, version=row.version, error=str(exc)[:200])
    if not bundles:
        return HeuristicPredictor()
    predictor = ModelPredictor(bundles)
    log.info("registry.predictor_loaded", version=predictor.version, experimental=predictor.is_experimental, models=sorted(bundles))
    return predictor


async def training_samples_rows(session: AsyncSession, limit: int = 200_000) -> list[dict[str, Any]]:
    from backend.models import TrainingSample

    rows = (await session.execute(select(TrainingSample).where(TrainingSample.label_complete.is_(True)).order_by(TrainingSample.snapshot_at.asc()).limit(limit))).scalars().all()
    return [
        {
            "mint": r.mint,
            "snapshot_at": r.snapshot_at,
            "features": r.features,
            "rugged": r.rugged,
            "liquidity_removed": r.liquidity_removed,
            "dead": r.dead,
            "hit_10": r.hit_10,
            "hit_15": r.hit_15,
            "hit_20": r.hit_20,
            "hit_stop": r.hit_stop,
            "close_return_pct": r.close_return_pct,
            "min_return_pct": r.min_return_pct,
            "time_to_10_s": r.time_to_10_s,
            "label_24h_complete": r.label_24h_complete,
            "hit_100_24h": r.hit_100_24h,
            "max_return_24h_pct": r.max_return_24h_pct,
        }
        for r in rows
    ]


async def train_and_store(session: AsyncSession, activate_if_better: bool = True, force_activate: bool = False) -> dict[str, Any]:
    """Train from labelled samples (if enough) and store versions; returns a summary."""
    from backend.config.settings import get_settings
    from backend.ml.models import train_all

    cfg = get_settings().ml
    samples = await training_samples_rows(session)
    if len(samples) < cfg.min_training_samples:
        return {"trained": False, "reason": f"{len(samples)} labelled samples < ML_MIN_TRAINING_SAMPLES={cfg.min_training_samples}", "samples": len(samples)}
    import asyncio

    bundles = await asyncio.to_thread(train_all, samples)
    current = await active_versions(session)
    summary: dict[str, Any] = {"trained": True, "samples": len(samples), "version": bundles[0].version, "models": {}}
    for b in bundles:
        better = is_better(b, current.get(b.name))
        activate = force_activate or (activate_if_better and better)
        row = await store_bundle(session, b, activate=activate, notes="auto-trained" if not force_activate else "forced activation")
        summary["models"][b.name] = {"id": row.id, "activated": activate, "experimental": b.is_experimental, "better_than_current": better, "metrics": b.metrics}
    summary["trained_at"] = datetime.now(UTC).isoformat()
    return summary
