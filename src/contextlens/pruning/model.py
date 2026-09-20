"""Stable request and result types for observation pruning."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ObservationKind(StrEnum):
    """Supported observation shapes."""

    CODE = "code"
    SEARCH = "search"
    TEST = "test"
    LOG = "log"
    JSON = "json"
    TEXT = "text"


class LineReason(StrEnum):
    """Independent reasons a source line survives pruning."""

    SEMANTIC = "semantic"
    DEPENDENCY = "dependency"
    SCOPE = "scope"
    CONTROL_FLOW = "control_flow"
    SYNTAX = "syntax"
    LOCAL_CONTEXT = "local_context"


@dataclass(frozen=True, slots=True)
class PruneRequest:
    """One task and one environment observation to reduce."""

    task: str
    content: str
    focus: str | None = None
    tool: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    kind: ObservationKind = ObservationKind.CODE
    language: str | None = None
    threshold: float = 0.5
    minimum_tokens: int = 256
    dependency_hops: int = 2
    context_radius: int = 1

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("task cannot be empty")
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold must be between zero and one")
        if self.minimum_tokens < 0:
            raise ValueError("minimum_tokens cannot be negative")
        if self.dependency_hops < 0:
            raise ValueError("dependency_hops cannot be negative")
        if self.context_radius < 0:
            raise ValueError("context_radius cannot be negative")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))

    @property
    def goal_hint(self) -> str:
        """Create the self-contained goal question consumed by the skimmer."""

        task = " ".join(self.task.split()).rstrip(".?!")
        target = self.arguments.get("path")
        location = (
            f" in {target}" if isinstance(target, str) and target.strip() else ""
        )
        if self.focus:
            focus = " ".join(self.focus.split()).rstrip(".?!")
            return (
                f"For the coding task '{task}', what code{location} is needed "
                f"to answer: {focus}?"
            )
        return f"What code{location} is needed to complete the coding task: {task}?"

    @property
    def query(self) -> str:
        """Return the paper-style task goal passed to the neural skimmer."""

        return self.goal_hint

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LineDecision:
    """Score and retention reasons for one original line."""

    line_number: int
    semantic_score: float
    reasons: tuple[LineReason, ...]
    dependency_score: float = 0.0
    combined_score: float = 0.0

    def __post_init__(self) -> None:
        if self.line_number < 1:
            raise ValueError("line_number must be positive")
        if not 0 <= self.semantic_score <= 1:
            raise ValueError("semantic_score must be between zero and one")
        if not 0 <= self.dependency_score <= 1:
            raise ValueError("dependency_score must be between zero and one")
        if not 0 <= self.combined_score <= 1:
            raise ValueError("combined_score must be between zero and one")
        if not self.reasons:
            raise ValueError("a kept line requires at least one reason")
        object.__setattr__(self, "reasons", tuple(dict.fromkeys(self.reasons)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line_number,
            "semantic_score": self.semantic_score,
            "dependency_score": self.dependency_score,
            "combined_score": self.combined_score,
            "reasons": [reason.value for reason in self.reasons],
        }


@dataclass(frozen=True, slots=True)
class OmittedRange:
    """A contiguous range removed from the rendered observation."""

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid omitted range")

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def to_dict(self) -> dict[str, int]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "line_count": self.line_count,
        }


@dataclass(frozen=True, slots=True)
class PruneResult:
    """Rendered observation plus an auditable retention record."""

    text: str
    receipt_id: str
    content_hash: str
    backend: str
    decisions: tuple[LineDecision, ...]
    omitted_ranges: tuple[OmittedRange, ...]
    original_lines: int
    retained_lines: int
    original_tokens: int
    retained_tokens: int
    latency_ms: float
    bypass_reason: str | None = None
    goal_hint: str | None = None

    def __post_init__(self) -> None:
        if self.original_lines < 0 or self.retained_lines < 0:
            raise ValueError("line counts cannot be negative")
        if self.retained_lines > self.original_lines:
            raise ValueError("retained lines cannot exceed original lines")
        if self.original_tokens < 0 or self.retained_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.latency_ms < 0:
            raise ValueError("latency cannot be negative")
        object.__setattr__(self, "decisions", tuple(self.decisions))
        object.__setattr__(self, "omitted_ranges", tuple(self.omitted_ranges))

    @property
    def saved_tokens(self) -> int:
        return self.original_tokens - self.retained_tokens

    @property
    def reduction_fraction(self) -> float:
        if not self.original_tokens:
            return 0.0
        return self.saved_tokens / self.original_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "text": self.text,
            "receipt_id": self.receipt_id,
            "content_hash": self.content_hash,
            "backend": self.backend,
            "kept_lines": [item.to_dict() for item in self.decisions],
            "omitted_ranges": [item.to_dict() for item in self.omitted_ranges],
            "original_lines": self.original_lines,
            "retained_lines": self.retained_lines,
            "original_tokens": self.original_tokens,
            "retained_tokens": self.retained_tokens,
            "saved_tokens": self.saved_tokens,
            "reduction_fraction": self.reduction_fraction,
            "latency_ms": self.latency_ms,
            "bypass_reason": self.bypass_reason,
            "goal_hint": self.goal_hint,
        }


def estimate_tokens(content: str) -> int:
    """Return a deterministic estimate when no tokenizer is available."""

    return math.ceil(len(content.encode("utf-8")) / 4)
