"""Provider-neutral evaluator contract used by later analysis milestones."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from contextlens.experiments.model import ReplayResult, ReplayTask
from contextlens.trace.model import TokenUsage


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Numeric scores and supporting evidence for one replay."""

    scores: Mapping[str, float]
    evidence: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    success: bool | None = None
    dimensions: Mapping[str, float] = field(default_factory=dict)
    utility_score: float | None = None
    tokens: TokenUsage = field(default_factory=TokenUsage)
    runtime_ms: int = 0
    tool_calls: int = 0
    retries: int = 0

    def __post_init__(self) -> None:
        if not self.scores:
            raise ValueError("evaluation requires at least one score")
        for name, score in self.dimensions.items():
            if not 0 <= score <= 1:
                raise ValueError(
                    f"evaluation dimension {name!r} must be between 0 and 1"
                )
        if self.utility_score is not None and not 0 <= self.utility_score <= 1:
            raise ValueError("utility_score must be between 0 and 1")
        if min(self.runtime_ms, self.tool_calls, self.retries) < 0:
            raise ValueError("evaluation counters cannot be negative")
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(
            self,
            "dimensions",
            MappingProxyType(dict(self.dimensions)),
        )


class Evaluator(Protocol):
    """Score a completed replay without changing its workspace."""

    @property
    def evaluator_id(self) -> str:
        """Stable evaluator identity for reproducibility."""

    def evaluate(self, task: ReplayTask, result: ReplayResult) -> Evaluation:
        """Return scores and inspectable evidence."""
