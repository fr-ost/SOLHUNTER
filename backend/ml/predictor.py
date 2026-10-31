"""Prediction interface used by the evaluator.

Two implementations share one output type:
  * HeuristicPredictor - transparent rules, always flagged ``is_experimental`` with version ``heuristic-v0``.
    Used until trained models with validated metrics exist.
  * ModelPredictor (backend.ml.models) - XGBoost models loaded from the ``model_versions`` table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.config.settings import get_settings

HEURISTIC_VERSION = "heuristic-v0"


@dataclass
class Prediction:
    rug_risk: float | None
    p10: float | None
    p15: float | None
    p20: float | None
    expected_return_pct: float | None
    expected_downside_pct: float | None
    expected_time_to_target_s: float | None
    p_stop_loss: float | None
    confidence: float
    is_experimental: bool
    model_version: str
    uncertainty: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    p_2x_24h: float | None = None  # probability of +100% within 24h (moonshot target)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class PredictorProtocol(Protocol):
    def predict(self, features: dict[str, float]) -> Prediction: ...


def _v(features: dict[str, float], name: str, default: float) -> float:
    x = features.get(name)
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return default
    return float(x)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class HeuristicPredictor:
    """Rule-based baseline. Outputs are NOT validated probabilities and are labelled experimental."""

    version = HEURISTIC_VERSION

    def predict(self, features: dict[str, float]) -> Prediction:
        f = features
        cost = get_settings().ml.fee_and_slippage_pct
        # --- rug risk -----------------------------------------------------------------------------
        rug = 0.15
        rug += 0.35 * _v(f, "creator_risk_score", 0.4)
        rug += 0.25 * max(0.0, _v(f, "top10_pct", 0.3) - 0.3)
        rug += 0.5 * max(0.0, _v(f, "largest_holder_pct", 0.1) - 0.12)
        rug += 0.3 * _v(f, "dev_sold_pct", 0.0)
        rug += 0.2 * _v(f, "manipulation_overall", 0.2)
        rug += 0.15 * _v(f, "distribution_score", 0.0)
        rug += 0.1 if _v(f, "insider_sell_sol", 0.0) > 0 else 0.0
        rug -= 0.1 * min(1.0, _v(f, "log_holder_count", 3.0) / 6.0)
        rug = max(0.02, min(0.98, rug))
        # --- breakout -------------------------------------------------------------------------------
        z = -1.2
        z += 0.6 * min(1.0, _v(f, "accumulation_score", 0.0))
        z += 0.5 * min(3.0, _v(f, "smart_money_buyers", 0.0)) / 3.0
        z += 0.4 * min(2.0, _v(f, "volume_acceleration", 1.0) - 1.0) / 2.0
        z += 0.3 * min(1.0, _v(f, "unique_buyers_5m", 0.0) / 30.0)
        z += 0.3 * min(1.0, max(0.0, _v(f, "net_flow_pct_liquidity_5m", 0.0)) * 5)
        z += 0.2 * min(1.0, max(-1.0, _v(f, "momentum_5m_pct", 0.0) / 40.0))
        z -= 0.8 * rug
        z -= 0.5 * _v(f, "manipulation_overall", 0.2)
        z -= 0.3 * max(0.0, _v(f, "sell_impact_pct_ref", 5.0) - 5.0) / 10.0
        p10 = _sigmoid(z)
        p15 = p10 * 0.72
        p20 = p10 * 0.52
        # --- expected return (net of configured execution cost) ------------------------------------
        expected = p10 * 12.0 + (1 - p10) * (-7.0) - cost
        downside = -(5.0 + 20.0 * rug)
        p_stop = max(0.05, min(0.9, 0.25 + 0.5 * rug - 0.3 * p10))
        ttt = 600.0 + 1200.0 * (1 - p10)
        # moonshot prior: survivorship + growth evidence, heavily discounted
        growth_s = _v(f, "growth_score", 0.0)
        p2x = _sigmoid(-3.2 + 2.0 * growth_s + 1.0 * min(1.0, _v(f, "migrated", 0.0)) + 0.8 * p10 - 1.5 * rug)
        return Prediction(
            rug_risk=round(rug, 4),
            p10=round(p10, 4),
            p15=round(p15, 4),
            p20=round(p20, 4),
            expected_return_pct=round(expected, 3),
            expected_downside_pct=round(downside, 3),
            expected_time_to_target_s=round(ttt, 0),
            p_stop_loss=round(p_stop, 4),
            confidence=0.2,
            is_experimental=True,
            model_version=self.version,
            uncertainty={"note": "heuristic baseline, no calibration data", "interval_p10": [max(0.0, p10 - 0.25), min(1.0, p10 + 0.25)]},
            notes=["Experimental: rule-based baseline used until validated models are trained."],
            p_2x_24h=round(p2x, 4),
        )
