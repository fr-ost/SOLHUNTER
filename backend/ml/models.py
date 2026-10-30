"""XGBoost models, chronological/walk-forward evaluation, calibration and artifact (de)serialisation.

Models
  rug_risk         binary: rugged | liquidity_removed | dead within the horizon
  breakout         three binaries: hit +10 / +15 / +20 net of execution cost
  expected_return  regressors for close return & worst drawdown, time-to-target (hits only) and P(stop)
No deep learning: gradient boosting is the baseline and stays unless evidence says otherwise.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, precision_score, recall_score, roc_auc_score
from xgboost import XGBClassifier, XGBRegressor

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.ml.features import FEATURE_NAMES

log = get_logger(__name__)

CLASSIFIER_PARAMS: dict[str, Any] = {
    "n_estimators": 250,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "reg_lambda": 2.0,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "n_jobs": 2,
    "tree_method": "hist",
}
REGRESSOR_PARAMS: dict[str, Any] = {**{k: v for k, v in CLASSIFIER_PARAMS.items() if k not in {"objective", "eval_metric"}}, "objective": "reg:squarederror"}


class PlattCalibrator:
    """Sigmoid calibration fitted on out-of-sample predicted probabilities."""

    def __init__(self) -> None:
        self.model: LogisticRegression | None = None

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        p = np.clip(p, 1e-5, 1 - 1e-5)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, probs: np.ndarray, y: np.ndarray) -> PlattCalibrator:
        if len(np.unique(y)) < 2 or len(y) < 20:
            self.model = None
            return self
        self.model = LogisticRegression(C=1.0).fit(self._logit(probs), y)
        return self

    def transform(self, probs: np.ndarray) -> np.ndarray:
        if self.model is None:
            return probs
        return self.model.predict_proba(self._logit(probs))[:, 1]


@dataclass
class Dataset:
    X: np.ndarray
    y: dict[str, np.ndarray]
    times: np.ndarray  # snapshot timestamps (epoch seconds)
    mints: np.ndarray
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))

    def __len__(self) -> int:
        return int(self.X.shape[0])


def dataset_from_samples(samples: list[dict[str, Any]], feature_names: list[str] | None = None) -> Dataset:
    names = feature_names or list(FEATURE_NAMES)
    X = np.full((len(samples), len(names)), np.nan, dtype=np.float32)
    for i, s in enumerate(samples):
        feats = s.get("features") or {}
        for j, n in enumerate(names):
            v = feats.get(n)
            if v is not None and isinstance(v, int | float) and math.isfinite(float(v)):
                X[i, j] = float(v)
    y = {
        "rug": np.array([1 if (s.get("rugged") or s.get("liquidity_removed") or s.get("dead")) else 0 for s in samples], dtype=np.int8),
        "hit_10": np.array([1 if s.get("hit_10") else 0 for s in samples], dtype=np.int8),
        "hit_15": np.array([1 if s.get("hit_15") else 0 for s in samples], dtype=np.int8),
        "hit_20": np.array([1 if s.get("hit_20") else 0 for s in samples], dtype=np.int8),
        "hit_stop": np.array([1 if s.get("hit_stop") else 0 for s in samples], dtype=np.int8),
        "close_return": np.array([float(s.get("close_return_pct") if s.get("close_return_pct") is not None else -100.0) for s in samples], dtype=np.float32),
        "min_return": np.array([float(s.get("min_return_pct") if s.get("min_return_pct") is not None else -100.0) for s in samples], dtype=np.float32),
        "time_to_10": np.array([float(s["time_to_10_s"]) if s.get("time_to_10_s") is not None else np.nan for s in samples], dtype=np.float32),
    }
    times = np.array([s["snapshot_at"].timestamp() if isinstance(s["snapshot_at"], datetime) else float(s["snapshot_at"]) for s in samples])
    mints = np.array([s["mint"] for s in samples])
    return Dataset(X, y, times, mints, names)


def group_time_folds(ds: Dataset, n_folds: int) -> list[np.ndarray]:
    """Chronological folds keyed by each token's FIRST snapshot so a token never straddles folds."""
    first_time: dict[str, float] = {}
    for m, t in zip(ds.mints, ds.times, strict=False):
        first_time[m] = min(first_time.get(m, t), t)
    tokens_sorted = sorted(first_time, key=lambda m: first_time[m])
    chunk = max(1, math.ceil(len(tokens_sorted) / n_folds))
    fold_of = {m: min(n_folds - 1, i // chunk) for i, m in enumerate(tokens_sorted)}
    assign = np.array([fold_of[m] for m in ds.mints])
    return [np.where(assign == k)[0] for k in range(n_folds)]


def classification_metrics(y: np.ndarray, p: np.ndarray, threshold: float, returns: np.ndarray | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"n": int(len(y)), "positives": int(y.sum()), "base_rate": float(y.mean()) if len(y) else None}
    if len(y) == 0 or len(np.unique(y)) < 2:
        out["note"] = "not enough class variety for ranking metrics"
        return out
    pred = (p >= threshold).astype(int)
    out.update(
        {
            "auc": round(float(roc_auc_score(y, p)), 4),
            "brier": round(float(brier_score_loss(y, p)), 4),
            "log_loss": round(float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))), 4),
            "precision_at_threshold": round(float(precision_score(y, pred, zero_division=0)), 4),
            "recall_at_threshold": round(float(recall_score(y, pred, zero_division=0)), 4),
            "selected": int(pred.sum()),
            "threshold": threshold,
        }
    )
    if returns is not None and pred.sum() > 0:
        sel = returns[pred == 1]
        equity = np.cumsum(sel)
        peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
        out["expected_value_pct"] = round(float(sel.mean()), 4)
        out["max_drawdown_pct"] = round(float((peak - equity).max()), 4)
        out["net_return_pct_sum"] = round(float(sel.sum()), 4)
    return out


@dataclass
class TrainedBundle:
    name: str
    version: str
    feature_names: list[str]
    estimators: dict[str, Any]
    calibrators: dict[str, PlattCalibrator]
    metrics: dict[str, Any]
    training_samples: int
    positive_samples: int
    is_experimental: bool
    trained_at: datetime

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        joblib.dump({"name": self.name, "version": self.version, "feature_names": self.feature_names, "estimators": self.estimators, "calibrators": self.calibrators, "metrics": self.metrics, "training_samples": self.training_samples, "positive_samples": self.positive_samples, "is_experimental": self.is_experimental, "trained_at": self.trained_at.isoformat()}, buf, compress=3)
        return buf.getvalue()

    @staticmethod
    def from_bytes(data: bytes) -> TrainedBundle:
        d = joblib.load(io.BytesIO(data))
        return TrainedBundle(d["name"], d["version"], d["feature_names"], d["estimators"], d["calibrators"], d["metrics"], d["training_samples"], d["positive_samples"], d["is_experimental"], datetime.fromisoformat(d["trained_at"]))


def _fit_classifier(X: np.ndarray, y: np.ndarray) -> XGBClassifier:
    pos = max(1, int(y.sum()))
    neg = max(1, int(len(y) - y.sum()))
    clf = XGBClassifier(**CLASSIFIER_PARAMS, scale_pos_weight=min(10.0, neg / pos))
    clf.fit(X, y)
    return clf


def _fit_regressor(X: np.ndarray, y: np.ndarray) -> XGBRegressor:
    reg = XGBRegressor(**REGRESSOR_PARAMS)
    reg.fit(X, y)
    return reg


def walk_forward(ds: Dataset, target: str, n_folds: int, threshold: float, returns_key: str = "close_return") -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    """Train on folds < k, test on fold k for k >= 1. Returns aggregated metrics and OOS predictions."""
    folds = group_time_folds(ds, n_folds)
    oos_p: list[np.ndarray] = []
    oos_y: list[np.ndarray] = []
    oos_r: list[np.ndarray] = []
    per_fold: list[dict[str, Any]] = []
    for k in range(1, n_folds):
        train_idx = np.concatenate(folds[:k]) if k > 0 else np.array([], dtype=int)
        test_idx = folds[k]
        if len(train_idx) < 30 or len(test_idx) < 10 or len(np.unique(ds.y[target][train_idx])) < 2:
            continue
        clf = _fit_classifier(ds.X[train_idx], ds.y[target][train_idx])
        p = clf.predict_proba(ds.X[test_idx])[:, 1]
        oos_p.append(p)
        oos_y.append(ds.y[target][test_idx])
        oos_r.append(ds.y[returns_key][test_idx])
        per_fold.append({"fold": k, **classification_metrics(ds.y[target][test_idx], p, threshold, ds.y[returns_key][test_idx])})
    if not oos_p:
        return {"note": "insufficient data for walk-forward validation", "folds": []}, np.array([]), np.array([])
    P, Y, R = np.concatenate(oos_p), np.concatenate(oos_y), np.concatenate(oos_r)
    agg = classification_metrics(Y, P, threshold, R)
    agg["folds"] = per_fold
    return agg, P, Y


def train_all(samples: list[dict[str, Any]], version: str | None = None) -> list[TrainedBundle]:
    """Train the three model groups with walk-forward validation; returns bundles ready to store."""
    cfg = get_settings().ml
    filt = get_settings().filters
    ds = dataset_from_samples(samples)
    n = len(ds)
    version = version or datetime.now(UTC).strftime("xgb-%Y%m%d-%H%M%S")
    bundles: list[TrainedBundle] = []
    experimental_base = n < cfg.experimental_until_samples
    n_folds = max(2, min(cfg.walk_forward_folds, n // 50))

    # --- rug risk -----------------------------------------------------------------------------
    rug_metrics, P, Y = walk_forward(ds, "rug", n_folds, threshold=filt.max_rug_risk)
    rug_cal = PlattCalibrator().fit(P, Y) if len(P) else PlattCalibrator()
    rug_clf = _fit_classifier(ds.X, ds.y["rug"])
    rug_exp = experimental_base or rug_metrics.get("auc") is None or rug_metrics.get("auc", 0) < 0.6 or int(ds.y["rug"].sum()) < cfg.min_positive_samples
    bundles.append(TrainedBundle("rug_risk", version, ds.feature_names, {"rug": rug_clf}, {"rug": rug_cal}, {"walk_forward": rug_metrics, "folds": n_folds}, n, int(ds.y["rug"].sum()), rug_exp, datetime.now(UTC)))

    # --- breakout -----------------------------------------------------------------------------
    estimators: dict[str, Any] = {}
    calibrators: dict[str, PlattCalibrator] = {}
    metrics: dict[str, Any] = {"folds": n_folds}
    exp = experimental_base
    for key in ("hit_10", "hit_15", "hit_20"):
        m, P, Y = walk_forward(ds, key, n_folds, threshold=filt.min_breakout_p10)
        calibrators[key] = PlattCalibrator().fit(P, Y) if len(P) else PlattCalibrator()
        if len(np.unique(ds.y[key])) < 2:
            metrics[key] = {"note": "single class - model skipped"}
            exp = True
            continue
        estimators[key] = _fit_classifier(ds.X, ds.y[key])
        metrics[key] = m
        if m.get("auc") is None or m.get("auc", 0) < 0.6 or int(ds.y[key].sum()) < cfg.min_positive_samples:
            exp = True
    bundles.append(TrainedBundle("breakout", version, ds.feature_names, estimators, calibrators, metrics, n, int(ds.y["hit_10"].sum()), exp, datetime.now(UTC)))

    # --- expected return ----------------------------------------------------------------------
    reg_est: dict[str, Any] = {"close_return": _fit_regressor(ds.X, ds.y["close_return"]), "min_return": _fit_regressor(ds.X, ds.y["min_return"])}
    hits = ~np.isnan(ds.y["time_to_10"])
    if hits.sum() >= 20:
        reg_est["time_to_10"] = _fit_regressor(ds.X[hits], ds.y["time_to_10"][hits])
    stop_metrics, P, Y = walk_forward(ds, "hit_stop", n_folds, threshold=0.5)
    reg_cal = {"hit_stop": PlattCalibrator().fit(P, Y) if len(P) else PlattCalibrator()}
    if len(np.unique(ds.y["hit_stop"])) >= 2:
        reg_est["hit_stop"] = _fit_classifier(ds.X, ds.y["hit_stop"])
    # regression holdout error on the last fold
    folds = group_time_folds(ds, n_folds)
    reg_metrics: dict[str, Any] = {"hit_stop": stop_metrics, "folds": n_folds}
    if len(folds[-1]) >= 10 and len(np.concatenate(folds[:-1])) >= 30:
        tr, te = np.concatenate(folds[:-1]), folds[-1]
        tmp = _fit_regressor(ds.X[tr], ds.y["close_return"][tr])
        pred = tmp.predict(ds.X[te])
        reg_metrics["close_return_holdout"] = {"mae_pct": round(float(np.mean(np.abs(pred - ds.y["close_return"][te]))), 3), "n": int(len(te)), "mean_actual_pct": round(float(ds.y["close_return"][te].mean()), 3)}
    bundles.append(TrainedBundle("expected_return", version, ds.feature_names, reg_est, reg_cal, reg_metrics, n, int(hits.sum()), experimental_base or hits.sum() < cfg.min_positive_samples, datetime.now(UTC)))
    return bundles
