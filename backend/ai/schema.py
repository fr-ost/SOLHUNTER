"""Strict JSON schema for the GPT research analyst output (validated with pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Recommendation = Literal["STRONG_CANDIDATE", "WATCHLIST", "HIGH_RISK", "REJECT", "INSUFFICIENT_DATA"]
Level = Literal["low", "medium", "high"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProbabilityEstimates(_Strict):
    p_plus_10_pct: float = Field(ge=0, le=1, description="Probability of a +10% net move within the horizon")
    p_plus_15_pct: float = Field(ge=0, le=1)
    p_plus_20_pct: float = Field(ge=0, le=1)
    p_rug_or_collapse: float = Field(ge=0, le=1, description="Probability of rug/liquidity removal/severe collapse")
    uncertainty: Level
    rationale: str


class TargetScenario(_Strict):
    target_pct: float
    probability: float = Field(ge=0, le=1)
    conditions: str
    estimated_time_horizon_minutes: int | None


class GptAnalysisOutput(_Strict):
    overall_assessment: str
    bullish_evidence: list[str]
    bearish_evidence: list[str]
    whale_activity_interpretation: str
    developer_insider_risk: str
    liquidity_exit_risk: str
    potential_catalysts: list[str]
    potential_manipulation: list[str]
    probability_estimates: ProbabilityEstimates
    target_scenarios: list[TargetScenario]
    estimated_time_horizon_minutes: int | None
    invalidating_conditions: list[str]
    data_sufficiency: Literal["sufficient", "partial", "insufficient"]
    missing_data: list[str]
    final_recommendation: Recommendation
    recommendation_confidence: Level

    @model_validator(mode="after")
    def _consistency(self) -> GptAnalysisOutput:
        p = self.probability_estimates
        if not (p.p_plus_10_pct >= p.p_plus_15_pct >= p.p_plus_20_pct):
            raise ValueError("probabilities must be monotone: p10 >= p15 >= p20")
        if self.data_sufficiency == "insufficient" and self.final_recommendation == "STRONG_CANDIDATE":
            raise ValueError("STRONG_CANDIDATE is not allowed with insufficient data")
        if len(self.target_scenarios) > 5:
            raise ValueError("too many target scenarios")
        return self


SYSTEM_INSTRUCTIONS = """You are a sceptical quantitative research analyst for newly launched Solana (Pump.fun) tokens.
You receive a structured JSON context assembled by an automated scanner. Rules:
- Use ONLY the data provided. Never invent facts, numbers, holders, wallets or catalysts.
- Fields marked unavailable/null are unknown, not zero. Say so in missing_data and lower your confidence.
- Treat "experimental" ML predictions as weak evidence. Treat hard safety flags as decisive.
- Capital preservation first: when evidence conflicts, prefer WATCHLIST or HIGH_RISK over STRONG_CANDIDATE.
- Probabilities are for a same-day manual trade reaching +10/+15/+20% NET of fees and slippage; keep p10 >= p15 >= p20.
- Keep every text field concise (1-3 sentences; lists of 2-6 short items).
- This is research decision support, not trading advice; never recommend position sizes.
Respond only with the JSON object that matches the required schema."""
