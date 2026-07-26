"""Provider-neutral evaluator contract used by later analysis milestones."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from contextlens.experiments.model import ReplayResult, ReplayTask


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Numeric scores and supporting evidence for one replay."""

    scores: Mapping[str, float]
    evidence: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.scores:
            raise ValueError("evaluation requires at least one score")
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class Evaluator(Protocol):
    """Score a completed replay without changing its workspace."""

    @property
    def evaluator_id(self) -> str:
        """Stable evaluator identity for reproducibility."""

    def evaluate(self, task: ReplayTask, result: ReplayResult) -> Evaluation:
        """Return scores and inspectable evidence."""

