"""Data contracts for isolated agent replays."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from contextlens.trace.model import ContextSource


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class ReplayTask:
    """A task to execute from a fixed workspace snapshot."""

    task_id: str
    instruction: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id or not self.instruction:
            raise ValueError("task_id and instruction cannot be empty")
        object.__setattr__(self, "metadata", _mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Settings held constant across comparable replay workers."""

    provider: str
    model: str
    seed: int | None = None
    temperature: float | None = None
    tools: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("provider and model cannot be empty")
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "parameters", _mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ContextVariant:
    """One intentional context intervention."""

    variant_id: str
    removed_source_ids: frozenset[str] = frozenset()
    description: str = ""
    estimated_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if not self.variant_id:
            raise ValueError("variant_id cannot be empty")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd cannot be negative")


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    """Exact input passed to an agent adapter."""

    run_id: str
    task: ReplayTask
    variant: ContextVariant
    context: tuple[ContextSource, ...]
    settings: AgentSettings
    workspace: str
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    """Raw artifacts returned by an agent adapter."""

    output_text: str = ""
    commands: tuple[str, ...] = ()
    test_results: tuple[str, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("cost_usd cannot be negative")
        object.__setattr__(self, "commands", tuple(self.commands))
        object.__setattr__(self, "test_results", tuple(self.test_results))
        object.__setattr__(self, "metadata", _mapping(self.metadata))


class ReplayStatus(StrEnum):
    """Terminal state of one worker attempt."""

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CACHED = "cached"


@dataclass(frozen=True, slots=True)
class FileChange:
    """A file difference produced in an isolated workspace."""

    path: str
    change: str
    before_digest: str | None
    after_digest: str | None
    patch: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Complete evidence from one isolated replay."""

    run_id: str
    task_id: str
    variant_id: str
    removed_source_ids: tuple[str, ...]
    status: ReplayStatus
    attempt: int
    duration_seconds: float
    context_source_ids: tuple[str, ...]
    context_tokens: int
    outcome: AgentOutcome | None = None
    file_changes: tuple[FileChange, ...] = ()
    error: str | None = None
    cache_key: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Hard preflight and per-worker replay limits."""

    max_workers: int = 4
    max_runs: int = 100
    timeout_seconds: float = 300.0
    retries: int = 0
    max_context_tokens: int | None = None
    max_estimated_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.max_workers < 1 or self.max_runs < 1:
            raise ValueError("max_workers and max_runs must be positive")
        if self.timeout_seconds <= 0 or self.retries < 0:
            raise ValueError("timeout_seconds must be positive and retries nonnegative")
        if self.max_context_tokens is not None and self.max_context_tokens < 0:
            raise ValueError("max_context_tokens cannot be negative")
        if (
            self.max_estimated_cost_usd is not None
            and self.max_estimated_cost_usd < 0
        ):
            raise ValueError("max_estimated_cost_usd cannot be negative")

