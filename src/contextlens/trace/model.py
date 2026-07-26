"""Versioned, provider-neutral trace data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
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
        )


@dataclass(frozen=True, slots=True)
class TraceHeader:
    """First event in every trace."""

    trace_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=_now)
    producer: str = "contextlens"
    producer_version: str = "0.0.1"
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

