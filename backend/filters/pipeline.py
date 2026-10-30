"""Runs the stage functions and turns their results into an explainable decision."""

from __future__ import annotations

from collections.abc import Callable

from backend.config.logging import get_logger
from backend.filters.base import EvaluationContext, PipelineResult, StageResult
from backend.filters.stages import ALL_STAGES, CHEAP_STAGES

log = get_logger(__name__)

Stage = Callable[[EvaluationContext], StageResult]


class FilterPipeline:
    def __init__(self, stages: list[Stage] | None = None) -> None:
        self.stages = stages or ALL_STAGES

    def run(self, ctx: EvaluationContext) -> PipelineResult:
        results: list[StageResult] = []
        for stage in self.stages:
            try:
                results.append(stage(ctx))
            except Exception as exc:  # noqa: BLE001 - a bug in one stage must not hide the others
                log.exception("filters.stage_error", stage=stage.__name__)
                results.append(StageResult(stage.__name__, stage.__name__, passed=False, hard_fail=False, score=0.0, reasons=[f"stage error: {type(exc).__name__}"], data_available=False))
        hard_fail = any(r.hard_fail for r in results)
        safety_failures = [r for r in results if not r.passed and r.safety]
        quality_failures = [r for r in results if not r.passed and not r.safety]
        rejection_reasons = [f"[{r.name}] {reason}" for r in safety_failures for reason in r.reasons]
        quality_reasons = [f"[{r.name}] {reason}" for r in quality_failures for reason in r.reasons]
        rejected = hard_fail or bool(safety_failures)
        return PipelineResult(
            results=results,
            rejected=rejected,
            hard_fail=hard_fail,
            candidate_eligible=not rejected and not quality_failures,
            rejection_reasons=rejection_reasons,
            quality_failures=quality_reasons,
            stage_scores={r.stage: r.score for r in results},
        )


def cheap_pipeline() -> FilterPipeline:
    return FilterPipeline(CHEAP_STAGES)


def full_pipeline() -> FilterPipeline:
    return FilterPipeline(ALL_STAGES)
