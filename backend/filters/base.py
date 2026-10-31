"""Filter pipeline primitives: evaluation context and stage results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.intelligence.developer import CreatorAnalysis
from backend.intelligence.holders import HolderAnalysis
from backend.intelligence.manipulation import ManipulationAssessment
from backend.intelligence.relationships import ClusterAnalysis
from backend.scanner.state import TokenState
from backend.whale_engine.engine import WhaleAssessment


@dataclass
class EvaluationContext:
    state: TokenState
    snapshot: dict[str, Any]
    now: datetime
    sol_usd: float | None
    snapshot_id: int | None = None
    deep: bool = False
    creator: CreatorAnalysis | None = None
    holders: HolderAnalysis | None = None
    clusters: ClusterAnalysis | None = None
    manipulation: ManipulationAssessment | None = None
    whales: WhaleAssessment | None = None
    execution: Any | None = None  # execution.analyzer.ExecutionReport
    prediction: Any | None = None  # ml.predictor.Prediction
    features: dict[str, float] = field(default_factory=dict)

    @property
    def liquidity_sol(self) -> float | None:
        return self.snapshot.get("liquidity_sol")

    @property
    def liquidity_usd(self) -> float | None:
        return self.snapshot.get("liquidity_usd")


@dataclass
class StageResult:
    stage: str
    name: str
    passed: bool
    hard_fail: bool = False
    safety: bool = True  # safety stages reject; quality stages only block candidate status
    score: float = 0.5  # 0..1 quality signal for ranking
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    data_available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "name": self.name,
            "passed": self.passed,
            "hard_fail": self.hard_fail,
            "safety": self.safety,
            "score": round(self.score, 4),
            "reasons": self.reasons,
            "metrics": self.metrics,
            "data_available": self.data_available,
        }


@dataclass
class PipelineResult:
    results: list[StageResult]
    rejected: bool  # not qualified right now (soft) OR hard failure
    hard_fail: bool  # final: rug / dump / bundle / manipulation - never re-qualifies
    candidate_eligible: bool
    rejection_reasons: list[str]
    quality_failures: list[str]
    stage_scores: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rejected": self.rejected,
            "hard_fail": self.hard_fail,
            "candidate_eligible": self.candidate_eligible,
            "rejection_reasons": self.rejection_reasons,
            "quality_failures": self.quality_failures,
            "stage_scores": {k: round(v, 4) for k, v in self.stage_scores.items()},
            "stages": [r.to_dict() for r in self.results],
        }

    def result(self, stage: str) -> StageResult | None:
        return next((r for r in self.results if r.stage == stage), None)
