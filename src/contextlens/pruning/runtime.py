"""Task-scoped middleware for pruning environment observations."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from contextlens.pruning.model import ObservationKind, PruneRequest, PruneResult
from contextlens.pruning.pipeline import ContextPruner

_CODE_TOOLS = frozenset(
    {
        "cat",
        "get_file",
        "open_file",
        "read",
        "read_file",
        "view_file",
    }
)
_SEARCH_TOOLS = frozenset({"find", "grep", "rg", "search", "search_code"})
_TEST_TOOLS = frozenset(
    {
        "pytest",
        "unittest",
        "tox",
        "nox",
        "cargo_test",
        "go_test",
        "npm_test",
        "run_tests",
        "test",
    }
)
_LOG_TOOLS = frozenset(
    {"bash", "shell", "exec", "exec_command", "run_terminal_cmd", "command"}
)
_CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".java",
        ".js",
        ".jsx",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".swift",
        ".ts",
        ".tsx",
    }
)


@dataclass(frozen=True, slots=True)
class TaskGoal:
    """Stable task objective shared by all observations in one run."""

    objective: str
    focus: str | None = None
    goal_id: str = field(init=False)

    def __post_init__(self) -> None:
        objective = " ".join(self.objective.split())
        if not objective:
            raise ValueError("objective cannot be empty")
        focus = " ".join(self.focus.split()) if self.focus else None
        digest = hashlib.sha256(objective.encode("utf-8")).hexdigest()[:16]
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "focus", focus)
        object.__setattr__(self, "goal_id", f"goal_{digest}")

    def narrowed(self, focus: str | None) -> TaskGoal:
        """Keep task identity while changing the current retrieval focus."""

        return replace(self, focus=focus)


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """One tool result before task-conditioned pruning."""

    content: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    kind: ObservationKind | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        if not self.tool.strip():
            raise ValueError("tool cannot be empty")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True, slots=True)
class PrunedObservation:
    """Runtime-facing text and its complete pruning record."""

    goal_id: str
    text: str
    result: PruneResult


@dataclass(frozen=True, slots=True)
class TrajectorySummary:
    """Aggregate pruning behavior across one complete task."""

    goal_id: str
    observations: int
    pruned_observations: int
    original_tokens: int
    retained_tokens: int
    latency_ms: float
    backend_counts: Mapping[str, int]
    bypass_counts: Mapping[str, int]
    reason_counts: Mapping[str, int]

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
            "goal_id": self.goal_id,
            "observations": self.observations,
            "pruned_observations": self.pruned_observations,
            "bypassed_observations": self.observations - self.pruned_observations,
            "original_tokens": self.original_tokens,
            "retained_tokens": self.retained_tokens,
            "saved_tokens": self.saved_tokens,
            "reduction_fraction": self.reduction_fraction,
            "latency_ms": self.latency_ms,
            "backend_counts": dict(self.backend_counts),
            "bypass_counts": dict(self.bypass_counts),
            "reason_counts": dict(self.reason_counts),
        }


class PruningSession:
    """Apply one stable task goal to a sequence of tool observations."""

    def __init__(
        self,
        pruner: ContextPruner,
        task: str,
        *,
        threshold: float = 0.5,
        minimum_tokens: int = 256,
        dependency_hops: int = 2,
        context_radius: int = 1,
    ) -> None:
        self.pruner = pruner
        self.goal = TaskGoal(task)
        self.threshold = threshold
        self.minimum_tokens = minimum_tokens
        self.dependency_hops = dependency_hops
        self.context_radius = context_radius
        self._results: list[PruneResult] = []

    def set_focus(self, focus: str | None) -> None:
        self.goal = self.goal.narrowed(focus)

    def observe(self, observation: ToolObservation) -> PrunedObservation:
        kind, language = classify_observation(observation)
        request = PruneRequest(
            task=self.goal.objective,
            focus=self.goal.focus,
            content=observation.content,
            tool=observation.tool,
            arguments=observation.arguments,
            kind=kind,
            language=language,
            threshold=self.threshold,
            minimum_tokens=self.minimum_tokens,
            dependency_hops=self.dependency_hops,
            context_radius=self.context_radius,
        )
        result = self.pruner.prune(request)
        self._results.append(result)
        return PrunedObservation(self.goal.goal_id, result.text, result)

    def summary(self) -> TrajectorySummary:
        return summarize_trajectory(self.goal.goal_id, self._results)


def summarize_trajectory(
    goal_id: str,
    results: Sequence[PruneResult],
) -> TrajectorySummary:
    """Reduce observation records into task-level measurements."""

    backend_counts = Counter(result.backend for result in results)
    bypass_counts = Counter(
        result.bypass_reason for result in results if result.bypass_reason is not None
    )
    reason_counts = Counter(
        reason.value
        for result in results
        for decision in result.decisions
        for reason in decision.reasons
    )
    return TrajectorySummary(
        goal_id=goal_id,
        observations=len(results),
        pruned_observations=sum(result.bypass_reason is None for result in results),
        original_tokens=sum(result.original_tokens for result in results),
        retained_tokens=sum(result.retained_tokens for result in results),
        latency_ms=sum(result.latency_ms for result in results),
        backend_counts=MappingProxyType(dict(sorted(backend_counts.items()))),
        bypass_counts=MappingProxyType(dict(sorted(bypass_counts.items()))),
        reason_counts=MappingProxyType(dict(sorted(reason_counts.items()))),
    )


def classify_observation(
    observation: ToolObservation,
) -> tuple[ObservationKind, str | None]:
    """Infer observation shape conservatively from tool and path metadata."""

    if observation.kind is not None:
        return observation.kind, observation.language
    tool = observation.tool.lower()
    if tool in _SEARCH_TOOLS:
        return ObservationKind.SEARCH, None
    if tool in _TEST_TOOLS:
        return ObservationKind.TEST, None
    if tool in _LOG_TOOLS:
        return ObservationKind.LOG, None
    if tool not in _CODE_TOOLS:
        return ObservationKind.TEXT, None
    raw_path = observation.arguments.get("path")
    if not isinstance(raw_path, str):
        return ObservationKind.TEXT, None
    suffix = Path(raw_path).suffix.lower()
    if suffix not in _CODE_SUFFIXES:
        return ObservationKind.TEXT, None
    language = observation.language or suffix.removeprefix(".")
    if language == "py":
        language = "python"
    return ObservationKind.CODE, language
