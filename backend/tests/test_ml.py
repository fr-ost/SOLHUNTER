"""ML: outcome labelling, grouped chronological folds, training on synthetic data, registry round-trip.

Synthetic data here only exercises the code paths; it is NOT evidence of real predictive power.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import numpy as np

from backend.ml.features import FEATURE_NAMES
from backend.ml.labeler import compute_outcome
from backend.ml.models import (
    PlattCalibrator,
    TrainedBundle,
    dataset_from_samples,
    group_time_folds,
    train_all,
)
from backend.ml.registry import ModelPredictor, is_better


def test_compute_outcome_targets_stop_and_rug() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    obs = [
        (t0 + timedelta(seconds=30), 1.05, 30.0),
        (t0 + timedelta(seconds=60), 1.16, 32.0),  # +16% gross -> hits +10 net of 3% cost, +13 needed for 10
        (t0 + timedelta(seconds=120), 1.25, 33.0),  # hits +20 (needs 23%)? no: 25 >= 23 yes
        (t0 + timedelta(seconds=300), 0.90, 31.0),
    ]
    o = compute_outcome(1.0, 30.0, obs, t0, cost_pct=3.0, stop_pct=6.0)
    assert o.hit_10 and o.hit_15 and o.hit_20
    assert o.time_to_10_s == 60 and o.time_to_20_s == 120
    assert not o.hit_stop and not o.rugged and not o.dead
    assert abs(o.max_return_pct - 25.0) < 1e-6 and abs(o.close_return_pct + 10.0) < 1e-6
    # stop hit before any target counts; a later recovery still records the stop
    obs2 = [(t0 + timedelta(seconds=10), 0.92, 30.0), (t0 + timedelta(seconds=90), 1.20, 30.0)]
    o2 = compute_outcome(1.0, 30.0, obs2, t0, 3.0, 6.0)
    assert o2.hit_stop and o2.hit_10
    # liquidity removal -> rugged
    obs3 = [(t0 + timedelta(seconds=10), 0.5, 4.0)]
    o3 = compute_outcome(1.0, 30.0, obs3, t0, 3.0, 6.0)
    assert o3.liquidity_removed and o3.rugged
    # no observations -> dead, total loss, nothing hit
    o4 = compute_outcome(1.0, 30.0, [], t0, 3.0, 6.0)
    assert o4.dead and o4.close_return_pct == -100.0 and not o4.hit_10 and o4.observations == 0


def _synthetic_samples(n_tokens: int = 160, per_token: int = 3, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    samples = []
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n_tokens):
        base_time = t0 + timedelta(minutes=10 * i)
        quality = rng.random()  # hidden "true" quality driving outcomes
        for k in range(per_token):
            feats = {name: rng.gauss(0, 1) for name in FEATURE_NAMES}
            feats["accumulation_score"] = quality + rng.gauss(0, 0.2)
            feats["creator_risk_score"] = (1 - quality) + rng.gauss(0, 0.2)
            feats["largest_cluster_pct"] = None  # missing feature exercised
            hit = rng.random() < 0.15 + 0.7 * quality
            rug = rng.random() < 0.6 * (1 - quality)
            samples.append(
                {
                    "mint": f"mint{i}",
                    "snapshot_at": base_time + timedelta(minutes=k),
                    "features": feats,
                    "rugged": rug,
                    "liquidity_removed": False,
                    "dead": False,
                    "hit_10": hit,
                    "hit_15": hit and rng.random() < 0.7,
                    "hit_20": hit and rng.random() < 0.5,
                    "hit_stop": (not hit) and rng.random() < 0.5,
                    "close_return_pct": (15.0 if hit else -8.0) + rng.gauss(0, 5),
                    "min_return_pct": -3.0 if hit else -12.0,
                    "time_to_10_s": 300.0 if hit else None,
                }
            )
    return samples


def test_dataset_and_group_time_folds_keep_tokens_together() -> None:
    samples = _synthetic_samples(n_tokens=40, per_token=3)
    ds = dataset_from_samples(samples)
    assert ds.X.shape == (120, len(FEATURE_NAMES))
    assert np.isnan(ds.X[:, FEATURE_NAMES.index("largest_cluster_pct")]).all()
    folds = group_time_folds(ds, 4)
    assert sum(len(f) for f in folds) == 120
    for f in folds:
        mints = set(ds.mints[f])
        for other in folds:
            if other is f:
                continue
            assert mints.isdisjoint(set(ds.mints[other]))
    # chronological: every time in fold k is <= times in fold k+1 (by token first snapshot)
    assert ds.times[folds[0]].max() <= ds.times[folds[-1]].min()


def test_train_all_produces_bundles_with_walk_forward_metrics() -> None:
    samples = _synthetic_samples()
    bundles = train_all(samples, version="test-v1")
    names = {b.name for b in bundles}
    assert names == {"rug_risk", "breakout", "expected_return"}
    rug = next(b for b in bundles if b.name == "rug_risk")
    wf = rug.metrics["walk_forward"]
    assert wf["n"] > 0 and "auc" in wf and 0.5 < wf["auc"] <= 1.0  # synthetic signal is learnable
    assert wf["folds"] and "brier" in wf and "expected_value_pct" in wf
    breakout = next(b for b in bundles if b.name == "breakout")
    assert {"hit_10", "hit_15", "hit_20"} <= set(breakout.estimators)
    assert breakout.is_experimental  # below ML_EXPERIMENTAL_UNTIL_SAMPLES -> experimental regardless of AUC
    er = next(b for b in bundles if b.name == "expected_return")
    assert "close_return" in er.estimators and "hit_stop" in er.estimators and "close_return_holdout" in er.metrics
    # round trip through bytes
    restored = TrainedBundle.from_bytes(breakout.to_bytes())
    assert restored.version == "test-v1" and restored.feature_names == FEATURE_NAMES
    predictor = ModelPredictor({b.name: b for b in bundles})
    good = {name: 0.0 for name in FEATURE_NAMES}
    good["accumulation_score"] = 0.95
    good["creator_risk_score"] = 0.05
    bad = dict(good, accumulation_score=0.05, creator_risk_score=0.95)
    pg, pb = predictor.predict(good), predictor.predict(bad)
    assert pg.p10 > pb.p10 and pg.rug_risk < pb.rug_risk
    assert pg.is_experimental and "test-v1" in pg.model_version
    assert pg.expected_return_pct > pb.expected_return_pct
    assert 0 <= pg.p_stop_loss <= 1 and pg.expected_downside_pct <= 0
    assert pg.uncertainty["breakout_auc_p10"] is not None


def test_platt_calibrator_and_is_better() -> None:
    rng = np.random.default_rng(1)
    p = rng.uniform(0, 1, 200)
    y = (rng.uniform(0, 1, 200) < p**2).astype(int)
    cal = PlattCalibrator().fit(p, y)
    out = cal.transform(p)
    assert out.shape == p.shape and (out >= 0).all() and (out <= 1).all()
    assert PlattCalibrator().fit(p[:5], y[:5]).model is None  # too small -> identity
    bundle = TrainedBundle("breakout", "v2", FEATURE_NAMES, {}, {}, {"hit_10": {"auc": 0.7}}, 100, 20, True, datetime.now(UTC))

    class _Row:
        name = "breakout"
        metrics = {"hit_10": {"auc": 0.65}}

    assert is_better(bundle, None) and is_better(bundle, _Row())  # type: ignore[arg-type]
    worse = TrainedBundle("breakout", "v3", FEATURE_NAMES, {}, {}, {"hit_10": {"auc": 0.6}}, 100, 20, True, datetime.now(UTC))
    assert not is_better(worse, _Row())  # type: ignore[arg-type]
