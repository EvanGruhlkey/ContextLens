"""Versioned, provider-neutral trace data model."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = "1.0"


class SourceKind(StrEnum):
    """Built-in categories of context supplied to a model."""

    SYSTEM_INSTRUCTION = "system_instruction"
    AGENT_INSTRUCTION = "agent_instruction"
    MEMORY = "memory"
    TOOL_SCHEMA = "tool_schema"
    FILE = "file"
    MESSAGE = "message"
    COMMAND_OUTPUT = "command_output"
    SEARCH_RESULT = "search_result"
    RETRIEVAL = "retrieval"
    GIT_HISTORY = "git_history"
    ARCHITECTURE_DECISION = "architecture_decision"
    CUSTOM = "custom"
    SYSTEM_PROMPT = "system_prompt"
    DEVELOPER_PROMPT = "developer_prompt"
    REPO_INSTRUCTION = "repo_instruction"
    CONVERSATION = "conversation"
    TERMINAL_OUTPUT = "terminal_output"
    RETRIEVED_DOCUMENT = "retrieved_document"
    GENERATED_SUMMARY = "generated_summary"
    OTHER = "other"


class AgentStatus(StrEnum):
    """Lifecycle state for a captured agent run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepType(StrEnum):
    """Kinds of ordered execution event captured in a trace."""

    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    EVALUATION = "evaluation"
    SYSTEM_EVENT = "system_event"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class ContentRef:
    """Reference to an immutable, content-addressed artifact."""

    digest: str
    byte_length: int
    media_type: str = "text/plain; charset=utf-8"

    def __post_init__(self) -> None:
        if not self.digest.startswith("sha256:") or len(self.digest) != 71:
            raise ValueError("digest must be formatted as sha256:<64 hex characters>")
        try:
            int(self.digest[7:], 16)
        except ValueError as error:
            raise ValueError("digest contains non-hexadecimal characters") from error
        if self.byte_length < 0:
            raise ValueError("byte_length cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "byte_length": self.byte_length,
            "media_type": self.media_type,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContentRef:
        return cls(
            digest=str(value["digest"]),
            byte_length=int(value["byte_length"]),
            media_type=str(value.get("media_type", "text/plain; charset=utf-8")),
        )


@dataclass(frozen=True, slots=True)
class ContextSource:
    """One independently removable unit of model context."""

    kind: SourceKind
    name: str
    content: str | None = None
    content_ref: ContentRef | None = None
    source_id: str = field(default_factory=lambda: str(uuid4()))
    token_count: int | None = None
    token_count_method: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    source_uri: str | None = None
    content_hash: str | None = None
    inserted_at_step: int = 0
    insertion_position: int = 0
    target_agent_id: str | None = None
    target_phase: str | None = None

    def __post_init__(self) -> None:
        if bool(self.content is not None) == bool(self.content_ref is not None):
            raise ValueError("exactly one of content or content_ref must be set")
        if not self.name.strip():
            raise ValueError("name cannot be empty")
        if not self.source_id:
            raise ValueError("source_id cannot be empty")
        if self.token_count is not None and self.token_count < 0:
            raise ValueError("token_count cannot be negative")
        if self.token_count is not None and not self.token_count_method:
            raise ValueError("token_count_method is required when token_count is set")
        if self.inserted_at_step < 0 or self.insertion_position < 0:
            raise ValueError("context insertion coordinates cannot be negative")
        digest = self.content_hash
        if digest is None:
            digest = (
                hashlib.sha256(self.content.encode("utf-8")).hexdigest()
                if self.content is not None
                else self.content_ref.digest[7:]
                if self.content_ref is not None
                else ""
            )
        if len(digest) != 64:
            raise ValueError("content_hash must contain 64 hexadecimal characters")
        try:
            int(digest, 16)
        except ValueError as error:
            raise ValueError(
                "content_hash contains non-hexadecimal characters"
            ) from error
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "provenance", _immutable_mapping(self.provenance))
        object.__setattr__(self, "tags", tuple(self.tags))

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "source_id": self.source_id,
            "kind": self.kind.value,
            "name": self.name,
            "token_count": self.token_count,
            "token_count_method": self.token_count_method,
            "provenance": dict(self.provenance),
            "tags": list(self.tags),
            "source_uri": self.source_uri,
            "content_hash": self.content_hash,
            "inserted_at_step": self.inserted_at_step,
            "insertion_position": self.insertion_position,
            "target_agent_id": self.target_agent_id,
            "target_phase": self.target_phase,
        }
        if self.content is not None:
            value["content"] = self.content
        else:
            assert self.content_ref is not None
            value["content_ref"] = self.content_ref.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContextSource:
        raw_ref = value.get("content_ref")
        return cls(
            source_id=str(value["source_id"]),
            kind=SourceKind(str(value["kind"])),
            name=str(value["name"]),
            content=str(value["content"]) if "content" in value else None,
            content_ref=(
                ContentRef.from_dict(raw_ref)
                if isinstance(raw_ref, Mapping)
                else None
            ),
            token_count=(
                int(value["token_count"])
                if value.get("token_count") is not None
                else None
            ),
            token_count_method=(
                str(value["token_count_method"])
                if value.get("token_count_method") is not None
                else None
            ),
            provenance=dict(value.get("provenance", {})),
            tags=tuple(str(tag) for tag in value.get("tags", ())),
            source_uri=(
                str(value["source_uri"])
                if value.get("source_uri") is not None
                else None
            ),
            content_hash=(
                str(value["content_hash"])
                if value.get("content_hash") is not None
                else None
            ),
            inserted_at_step=int(value.get("inserted_at_step", 0)),
            insertion_position=int(value.get("insertion_position", 0)),
            target_agent_id=(
                str(value["target_agent_id"])
                if value.get("target_agent_id") is not None
                else None
            ),
            target_phase=(
                str(value["target_phase"])
                if value.get("target_phase") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Provider-reported token usage for one model step."""

    input: int = 0
    output: int = 0
    cached: int = 0

    def __post_init__(self) -> None:
        if min(self.input, self.output, self.cached) < 0:
            raise ValueError("token usage cannot be negative")

    def to_dict(self) -> dict[str, int]:
        return {"input": self.input, "output": self.output, "cached": self.cached}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TokenUsage:
        return cls(
            input=int(value.get("input", 0)),
            output=int(value.get("output", 0)),
            cached=int(value.get("cached", 0)),
        )


@dataclass(frozen=True, slots=True)
class AgentTrace:
    """Queryable metadata and aggregate usage for one coding-agent run."""

    task: str
    agent_type: str
    model_provider: str
    model_name: str
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    model_version: str | None = None
    repository_url: str | None = None
    repository_commit: str | None = None
    environment_image: str | None = None
    started_at: str = field(default_factory=_now)
    completed_at: str | None = None
    status: AgentStatus = AgentStatus.RUNNING
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    total_tool_calls: int = 0
    total_runtime_ms: int = 0
    baseline_score: float | None = None
    project_id: str = "default"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("trace task cannot be empty")
        if not self.agent_type or not self.model_provider or not self.model_name:
            raise ValueError("agent and model identity cannot be empty")
        if min(
            self.total_input_tokens,
            self.total_output_tokens,
            self.total_cached_tokens,
            self.total_tool_calls,
            self.total_runtime_ms,
        ) < 0:
            raise ValueError("trace totals cannot be negative")
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "project_id": self.project_id,
            "agent_type": self.agent_type,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "task": self.task,
            "repository_url": self.repository_url,
            "repository_commit": self.repository_commit,
            "environment_image": self.environment_image,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status.value,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "total_tool_calls": self.total_tool_calls,
            "total_runtime_ms": self.total_runtime_ms,
            "baseline_score": self.baseline_score,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentTrace:
        return cls(
            trace_id=str(value["trace_id"]),
            project_id=str(value.get("project_id", "default")),
            agent_type=str(value["agent_type"]),
            model_provider=str(value["model_provider"]),
            model_name=str(value["model_name"]),
            model_version=(
                str(value["model_version"])
                if value.get("model_version") is not None
                else None
            ),
            task=str(value["task"]),
            repository_url=(
                str(value["repository_url"])
                if value.get("repository_url") is not None
                else None
            ),
            repository_commit=(
                str(value["repository_commit"])
                if value.get("repository_commit") is not None
                else None
            ),
            environment_image=(
                str(value["environment_image"])
                if value.get("environment_image") is not None
                else None
            ),
            started_at=str(value["started_at"]),
            completed_at=(
                str(value["completed_at"])
                if value.get("completed_at") is not None
                else None
            ),
            status=AgentStatus(str(value["status"])),
            total_input_tokens=int(value.get("total_input_tokens", 0)),
            total_output_tokens=int(value.get("total_output_tokens", 0)),
            total_cached_tokens=int(value.get("total_cached_tokens", 0)),
            total_tool_calls=int(value.get("total_tool_calls", 0)),
            total_runtime_ms=int(value.get("total_runtime_ms", 0)),
            baseline_score=(
                float(value["baseline_score"])
                if value.get("baseline_score") is not None
                else None
            ),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class TraceStep:
    """One ordered model, tool, evaluation, or system event."""

    trace_id: str
    sequence: int
    step_type: StepType
    step_id: str = field(default_factory=lambda: str(uuid4()))
    input_context_item_ids: tuple[str, ...] = ()
    token_usage: TokenUsage | None = None
    duration_ms: int | None = None
    tool_name: str | None = None
    tool_input: Any = None
    tool_output_reference: str | None = None
    content: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trace_id or not self.step_id:
            raise ValueError("step and trace IDs cannot be empty")
        if self.sequence < 0:
            raise ValueError("step sequence cannot be negative")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("step duration cannot be negative")
        object.__setattr__(
            self,
            "input_context_item_ids",
            tuple(self.input_context_item_ids),
        )
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "trace_id": self.trace_id,
            "sequence": self.sequence,
            "step_type": self.step_type.value,
            "input_context_item_ids": list(self.input_context_item_ids),
            "token_usage": (
                self.token_usage.to_dict() if self.token_usage is not None else None
            ),
            "duration_ms": self.duration_ms,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output_reference": self.tool_output_reference,
            "content": self.content,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TraceStep:
        usage = value.get("token_usage")
        return cls(
            step_id=str(value["step_id"]),
            trace_id=str(value["trace_id"]),
            sequence=int(value["sequence"]),
            step_type=StepType(str(value["step_type"])),
            input_context_item_ids=tuple(
                str(item) for item in value.get("input_context_item_ids", ())
            ),
            token_usage=(
                TokenUsage.from_dict(usage)
                if isinstance(usage, Mapping)
                else None
            ),
            duration_ms=(
                int(value["duration_ms"])
                if value.get("duration_ms") is not None
                else None
            ),
            tool_name=(
                str(value["tool_name"])
                if value.get("tool_name") is not None
                else None
            ),
            tool_input=value.get("tool_input"),
            tool_output_reference=(
                str(value["tool_output_reference"])
                if value.get("tool_output_reference") is not None
                else None
            ),
            content=(
                str(value["content"])
                if value.get("content") is not None
                else None
            ),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class TraceHeader:
    """First event in every trace."""

    trace_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=_now)
    producer: str = "contextlens"
    producer_version: str = "0.1.0"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.trace_id:
            raise ValueError("trace_id cannot be empty")
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": "trace_started",
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "created_at": self.created_at,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TraceHeader:
        if value.get("event") != "trace_started":
            raise ValueError("the first trace record must be a trace_started event")
        return cls(
            schema_version=str(value["schema_version"]),
            trace_id=str(value["trace_id"]),
            created_at=str(value["created_at"]),
            producer=str(value["producer"]),
            producer_version=str(value["producer_version"]),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class ContextEvent:
    """A context source in its exact request order."""

    request_id: str
    sequence: int
    source: ContextSource
    recorded_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id cannot be empty")
        if self.sequence < 0:
            raise ValueError("sequence cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": "context_added",
            "schema_version": SCHEMA_VERSION,
            "request_id": self.request_id,
            "sequence": self.sequence,
            "recorded_at": self.recorded_at,
            "source": self.source.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContextEvent:
        if value.get("event") != "context_added":
            raise ValueError(f"unsupported trace event: {value.get('event')!r}")
        return cls(
            request_id=str(value["request_id"]),
            sequence=int(value["sequence"]),
            recorded_at=str(value["recorded_at"]),
            source=ContextSource.from_dict(value["source"]),
        )
