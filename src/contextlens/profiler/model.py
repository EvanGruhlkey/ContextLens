"""Result types for deterministic one-run profiling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EvidenceLevel(StrEnum):
    """Strength and origin of a ContextLens finding."""

    OBSERVED = "observed"
    PREDICTED = "predicted"
    VERIFIED = "verified"


class UsageLabel(StrEnum):
    """Summary of apparent utilization in one recorded run."""

    USED = "used"
    UNUSED = "unused"
    DUPLICATED = "duplicated"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class UsageSignal:
    """One piece of evidence associated with a context source."""

    name: str
    value: float | int | str | bool
    detail: str
    evidence_level: EvidenceLevel = EvidenceLevel.OBSERVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "detail": self.detail,
            "evidence_level": self.evidence_level.value,
        }


@dataclass(frozen=True, slots=True)
class RunObservation:
    """Observable products of the original agent run."""

    output_text: str = ""
    accessed_source_ids: frozenset[str] = frozenset()
    commands: tuple[str, ...] = ()
    tool_inputs: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    task_text: str = ""
    searched_queries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileReason:
    """Inspectible explanation for a candidate score."""

    code: str
    description: str
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "description": self.description,
            "evidence": self.evidence or {},
        }


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """One-run utilization profile for a single context source."""

    source_id: str
    name: str
    kind: str
    label: UsageLabel
    token_count: int
    token_count_method: str
    position: float
    output_overlap: float | None
    duplicated_by: tuple[str, ...]
    age_seconds: float | None
    retrieval_rank: int | None
    matched_output_spans: tuple[str, ...]
    signals: tuple[UsageSignal, ...]
    evidence_level: EvidenceLevel = EvidenceLevel.OBSERVED
    relevance_score: float = 0.0
    observed_usage_score: float = 0.0
    redundancy_score: float = 0.0
    contradiction_score: float = 0.0
    staleness_score: float = 0.0
    token_cost_score: float = 0.0
    experiment_priority: float = 0.0
    reasons: tuple[ProfileReason, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "kind": self.kind,
            "label": self.label.value,
            "evidence_level": self.evidence_level.value,
            "token_count": self.token_count,
            "token_count_method": self.token_count_method,
            "position": self.position,
            "output_overlap": self.output_overlap,
            "duplicated_by": list(self.duplicated_by),
            "age_seconds": self.age_seconds,
            "retrieval_rank": self.retrieval_rank,
            "matched_output_spans": list(self.matched_output_spans),
            "signals": [signal.to_dict() for signal in self.signals],
            "relevance_score": self.relevance_score,
            "observed_usage_score": self.observed_usage_score,
            "redundancy_score": self.redundancy_score,
            "contradiction_score": self.contradiction_score,
            "staleness_score": self.staleness_score,
            "token_cost_score": self.token_cost_score,
            "experiment_priority": self.experiment_priority,
            "reasons": [reason.to_dict() for reason in self.reasons],
        }
