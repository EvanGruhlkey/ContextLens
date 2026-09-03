"""Task-scoped middleware for pruning environment observations."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
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
        return PrunedObservation(self.goal.goal_id, result.text, result)


def classify_observation(
    observation: ToolObservation,
) -> tuple[ObservationKind, str | None]:
    """Infer observation shape conservatively from tool and path metadata."""

    if observation.kind is not None:
        return observation.kind, observation.language
    tool = observation.tool.lower()
    if tool in _SEARCH_TOOLS:
        return ObservationKind.SEARCH, None
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
