"""Optimization policies, candidates, and verification results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from contextlens.experiments.evaluation import Evaluation
from contextlens.experiments.model import ReplayResult


class OptimizationObjective(StrEnum):
    """Primary goal used to construct a candidate context."""

    MAX_QUALITY = "max_quality"
    MIN_COST = "min_cost"
    MIN_LATENCY = "min_latency"
    TOKEN_BUDGET = "token_budget"
    QUALITY_PER_DOLLAR = "quality_per_dollar"


@dataclass(frozen=True, slots=True)
class OptimizationPolicy:
    """Acceptance criteria for a combined candidate."""

    objective: OptimizationObjective
    quality_tolerance: float = 0.0
    token_budget: int | None = None
    max_cost_usd: float | None = None
    max_latency_seconds: float | None = None
    maximize_score: bool = True

    def __post_init__(self) -> None:
        if self.quality_tolerance < 0:
            raise ValueError("quality_tolerance cannot be negative")
        for name, value in (
            ("token_budget", self.token_budget),
            ("max_cost_usd", self.max_cost_usd),
            ("max_latency_seconds", self.max_latency_seconds),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if (
            self.objective is OptimizationObjective.TOKEN_BUDGET
            and self.token_budget is None
        ):
            raise ValueError("token_budget is required for the token-budget objective")


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    """A context configuration proposed for combined verification."""

    candidate_id: str
    removed_source_ids: tuple[str, ...]
    retained_source_ids: tuple[str, ...]
    retained_tokens: int
    removed_tokens: int
    individually_verified_removals: tuple[str, ...]
    predicted_removals: tuple[str, ...]
    objective: OptimizationObjective
    rationale: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    """Fixed-answer support change used only for cheap prioritization."""

    candidate_id: str
    full_context_score: float
    candidate_score: float
    score_change: float
    scorer_id: str
    evidence_scope: str = "screening"


@dataclass(frozen=True, slots=True)
class VerifiedConfiguration:
    """Outcome of testing a combined candidate on the target model."""

    candidate: ContextCandidate
    accepted: bool
    baseline_score: float
    candidate_score: float
    quality_change: float
    replay_result: ReplayResult
    evaluation: Evaluation
    rejection_reasons: tuple[str, ...]
    baseline_objective_value: float | None
    candidate_objective_value: float | None
    objective_improvement: float | None
    evidence_scope: str = "target_model"
