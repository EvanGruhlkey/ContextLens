"""Public trace recording API."""

from typing import TYPE_CHECKING

from contextlens.trace.artifacts import ArtifactStore
from contextlens.trace.model import (
    SCHEMA_VERSION,
    AgentStatus,
    AgentTrace,
    ContentRef,
    ContextEvent,
    ContextSource,
    SourceKind,
    StepType,
    TokenUsage,
    TraceHeader,
    TraceStep,
)
from contextlens.trace.redaction import RegexRedactor, SecretRedactor
from contextlens.trace.store import TraceReader, TraceWriter

if TYPE_CHECKING:
    from contextlens.trace.replay import RecordedReplayTrace, record_replay_trace

__all__ = [
    "SCHEMA_VERSION",
    "AgentStatus",
    "AgentTrace",
    "ArtifactStore",
    "ContentRef",
    "ContextEvent",
    "ContextSource",
    "RegexRedactor",
    "RecordedReplayTrace",
    "SecretRedactor",
    "SourceKind",
    "StepType",
    "TokenUsage",
    "TraceStep",
    "TraceHeader",
    "TraceReader",
    "TraceWriter",
    "record_replay_trace",
]


def __getattr__(name: str) -> object:
    """Load replay integration lazily to avoid a trace/experiment import cycle."""

    if name == "RecordedReplayTrace":
        from contextlens.trace.replay import RecordedReplayTrace

        return RecordedReplayTrace
    if name == "record_replay_trace":
        from contextlens.trace.replay import record_replay_trace

        return record_replay_trace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
