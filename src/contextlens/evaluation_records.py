"""Durable, provider-neutral audit records for evaluation invocations."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from contextlens.experiments.model import (
    AgentSettings,
    ReplayResult,
    ReplayTask,
)
from contextlens.trace.model import ContextSource

EVALUATION_RECORD_SCHEMA_VERSION = "1.0"


class InvocationRole(StrEnum):
    """Role performed by one independently invoked model worker."""

    BASELINE_WORKER = "baseline_worker"
    REPLAY_WORKER = "replay_worker"
    CONTROL_WORKER = "control_worker"
    VERIFICATION_WORKER = "verification_worker"
    JUDGE = "judge"
    SUMMARIZER = "summarizer"


@dataclass(frozen=True, slots=True)
class ContextManifestItem:
    """Content-free manifest entry for one available context source."""

    source_id: str
    name: str
    kind: str
    content_hash: str
    token_count: int | None
    token_count_method: str | None
    source_uri: str | None
    tags: tuple[str, ...]
    provenance: Mapping[str, Any]
    inserted_at_step: int
    insertion_position: int
    target_agent_id: str | None
    target_phase: str | None
    content_reference: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.source_id or not self.name or not self.kind:
            raise ValueError("manifest source identity cannot be empty")
        _validate_digest(self.content_hash, "content_hash")
        if self.token_count is not None and self.token_count < 0:
            raise ValueError("manifest token_count cannot be negative")
        if self.token_count is not None and not self.token_count_method:
            raise ValueError("token_count_method is required with token_count")
        if self.inserted_at_step < 0 or self.insertion_position < 0:
            raise ValueError("manifest insertion coordinates cannot be negative")
        _validate_json(self.provenance, "provenance")
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(dict(self.provenance)),
        )
        if self.content_reference is not None:
            _validate_json(self.content_reference, "content_reference")
            object.__setattr__(
                self,
                "content_reference",
                MappingProxyType(dict(self.content_reference)),
            )

    @classmethod
    def from_source(cls, source: ContextSource) -> ContextManifestItem:
        """Build a manifest entry without copying raw source content."""

        assert source.content_hash is not None
        return cls(
            source_id=source.source_id,
            name=source.name,
            kind=source.kind.value,
            content_hash=source.content_hash,
            token_count=source.token_count,
            token_count_method=source.token_count_method,
            source_uri=source.source_uri,
            tags=source.tags,
            provenance=source.provenance,
            inserted_at_step=source.inserted_at_step,
            insertion_position=source.insertion_position,
            target_agent_id=source.target_agent_id,
            target_phase=source.target_phase,
            content_reference=(
                source.content_ref.to_dict() if source.content_ref is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "kind": self.kind,
            "content_hash": self.content_hash,
            "token_count": self.token_count,
            "token_count_method": self.token_count_method,
            "source_uri": self.source_uri,
            "tags": list(self.tags),
            "provenance": _json_copy(self.provenance),
            "inserted_at_step": self.inserted_at_step,
            "insertion_position": self.insertion_position,
            "target_agent_id": self.target_agent_id,
            "target_phase": self.target_phase,
            "content_reference": _json_copy(self.content_reference),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContextManifestItem:
        reference = value.get("content_reference")
        if reference is not None and not isinstance(reference, Mapping):
            raise ValueError("content_reference must be a JSON object or null")
        provenance = value.get("provenance", {})
        if not isinstance(provenance, Mapping):
            raise ValueError("provenance must be a JSON object")
        return cls(
            source_id=str(value["source_id"]),
            name=str(value["name"]),
            kind=str(value["kind"]),
            content_hash=str(value["content_hash"]),
            token_count=_optional_int(value.get("token_count")),
            token_count_method=_optional_string(value.get("token_count_method")),
            source_uri=_optional_string(value.get("source_uri")),
            tags=tuple(str(item) for item in _sequence(value.get("tags", ()))),
            provenance=dict(provenance),
            inserted_at_step=int(value.get("inserted_at_step", 0)),
            insertion_position=int(value.get("insertion_position", 0)),
            target_agent_id=_optional_string(value.get("target_agent_id")),
            target_phase=_optional_string(value.get("target_phase")),
            content_reference=dict(reference) if reference is not None else None,
        )


@dataclass(frozen=True, slots=True)
class EvaluationInvocationRecord:
    """Strict audit record for one model invocation in an evaluation."""

    evaluation_run_id: str
    case_id: str
    trial: int
    policy: str
    role: InvocationRole
    provider: str
    model: str
    started_at: str
    ended_at: str
    workspace_id: str
    task_prompt: str
    rendered_prompt: str
    context_manifest: tuple[ContextManifestItem, ...]
    included_context_sources: tuple[str, ...]
    excluded_context_sources: tuple[str, ...]
    context_hashes: Mapping[str, str]
    raw_response: str
    tool_calls: tuple[Mapping[str, Any], ...]
    commands: tuple[str, ...]
    changed_files: tuple[str, ...]
    test_output: tuple[str, ...]
    latency_seconds: float
    retry_count: int
    status: str
    intervention_id: str | None = None
    parent_run_id: str | None = None
    reasoning_level: str | None = None
    temperature: float | None = None
    random_seed: int | None = None
    grader_input: str | None = None
    raw_grader_response: str | None = None
    parsed_score: Any = None
    provider_input_tokens: int | None = None
    provider_cached_tokens: int | None = None
    provider_output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    record_id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: str = EVALUATION_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        required = {
            "record_id": self.record_id,
            "evaluation_run_id": self.evaluation_run_id,
            "case_id": self.case_id,
            "policy": self.policy,
            "provider": self.provider,
            "model": self.model,
            "workspace_id": self.workspace_id,
            "task_prompt": self.task_prompt,
            "rendered_prompt": self.rendered_prompt,
            "status": self.status,
        }
        empty = [name for name, value in required.items() if not value]
        if empty:
            raise ValueError(f"required record fields cannot be empty: {empty}")
        if self.schema_version != EVALUATION_RECORD_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported evaluation record schema: {self.schema_version}"
            )
        if self.trial < 1:
            raise ValueError("trial must be positive")
        if self.latency_seconds < 0 or self.retry_count < 0:
            raise ValueError("latency and retry count cannot be negative")
        if self.temperature is not None and self.temperature < 0:
            raise ValueError("temperature cannot be negative")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd cannot be negative")
        for name, value in (
            ("provider_input_tokens", self.provider_input_tokens),
            ("provider_cached_tokens", self.provider_cached_tokens),
            ("provider_output_tokens", self.provider_output_tokens),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        started = _timestamp(self.started_at, "started_at")
        ended = _timestamp(self.ended_at, "ended_at")
        if ended < started:
            raise ValueError("ended_at cannot precede started_at")

        source_ids = tuple(item.source_id for item in self.context_manifest)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("context manifest source IDs must be unique")
        included = tuple(self.included_context_sources)
        excluded = tuple(self.excluded_context_sources)
        if len(included) != len(set(included)) or len(excluded) != len(set(excluded)):
            raise ValueError("included and excluded source IDs must be unique")
        if set(included) & set(excluded):
            raise ValueError("included and excluded context must be disjoint")
        if set(included) | set(excluded) != set(source_ids):
            raise ValueError("included and excluded IDs must cover the manifest")
        expected_hashes = {
            item.source_id: item.content_hash for item in self.context_manifest
        }
        if dict(self.context_hashes) != expected_hashes:
            raise ValueError("context_hashes must exactly match the manifest")

        for index, tool_call in enumerate(self.tool_calls):
            _validate_json(tool_call, f"tool_calls[{index}]")
        _validate_json(self.parsed_score, "parsed_score")
        _validate_json(self.metadata, "metadata")
        object.__setattr__(self, "context_manifest", tuple(self.context_manifest))
        object.__setattr__(self, "included_context_sources", included)
        object.__setattr__(self, "excluded_context_sources", excluded)
        object.__setattr__(self, "context_hashes", MappingProxyType(expected_hashes))
        object.__setattr__(
            self,
            "tool_calls",
            tuple(MappingProxyType(dict(item)) for item in self.tool_calls),
        )
        object.__setattr__(self, "commands", tuple(self.commands))
        object.__setattr__(self, "changed_files", tuple(self.changed_files))
        object.__setattr__(self, "test_output", tuple(self.test_output))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @classmethod
    def from_replay_result(
        cls,
        result: ReplayResult,
        *,
        task: ReplayTask,
        context: Sequence[ContextSource],
        settings: AgentSettings,
        evaluation_run_id: str | None = None,
        case_id: str | None = None,
        trial: int | None = None,
        policy: str | None = None,
        role: InvocationRole | str | None = None,
        intervention_id: str | None = None,
        parent_run_id: str | None = None,
        workspace_id: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        rendered_prompt: str | None = None,
        reasoning_level: str | None = None,
        grader_input: str | None = None,
        raw_grader_response: str | None = None,
        parsed_score: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvaluationInvocationRecord:
        """Convert production replay evidence into one strict audit record.

        Explicit arguments take precedence over outcome, task, and settings
        metadata, in that order. Required identifiers that cannot be recovered
        fail instead of being silently invented.
        """

        outcome = result.outcome
        outcome_metadata = {
            **dict(result.metadata),
            **(dict(outcome.metadata) if outcome is not None else {}),
        }
        sources = tuple(context)
        by_id = {source.source_id: source for source in sources}
        if len(by_id) != len(sources):
            raise ValueError("context source IDs must be unique")
        unknown = set(result.context_source_ids) - set(by_id)
        if unknown:
            raise ValueError(
                f"result references sources absent from manifest: {unknown}"
            )
        manifest = tuple(ContextManifestItem.from_source(source) for source in sources)
        included = tuple(result.context_source_ids)
        excluded = tuple(
            source.source_id
            for source in sources
            if source.source_id not in set(included)
        )
        ended_text = _optional_string(
            _fallback(ended_at, "ended_at", outcome_metadata, task.metadata)
        )
        ended = _timestamp(ended_text, "ended_at") if ended_text else datetime.now(UTC)
        started_text = _optional_string(
            _fallback(started_at, "started_at", outcome_metadata, task.metadata)
        )
        started = (
            _timestamp(started_text, "started_at")
            if started_text
            else ended - timedelta(seconds=result.duration_seconds)
        )
        raw_tool_calls = _fallback(
            None,
            "tool_calls",
            outcome_metadata,
            task.metadata,
            settings.parameters,
        )
        tool_calls = _tool_calls(raw_tool_calls)
        if outcome is not None and outcome.tool_calls and not tool_calls:
            raise ValueError("raw tool_calls are required when tool_calls is nonzero")
        role_value = _required_string(
            _fallback(role, "role", outcome_metadata, task.metadata),
            "role",
        )
        invocation_role = InvocationRole(role_value)
        reasoning = _optional_string(
            _fallback(
                reasoning_level,
                "reasoning_level",
                outcome_metadata,
                task.metadata,
                settings.parameters,
            )
        )
        cached_tokens = _optional_int(
            _fallback(
                None,
                "cached_input_tokens",
                outcome_metadata,
                settings.parameters,
            )
        )
        record_metadata = dict(metadata or {})
        record_metadata.setdefault("replay_run_id", result.run_id)
        record_metadata.setdefault("variant_id", result.variant_id)
        record_metadata.setdefault("attempt", result.attempt)
        record_metadata.setdefault("cache_key", result.cache_key)
        record_metadata.setdefault("context_tokens", result.context_tokens)
        record_metadata.setdefault(
            "removed_source_ids", list(result.removed_source_ids)
        )

        return cls(
            evaluation_run_id=_required_string(
                _fallback(
                    evaluation_run_id,
                    "evaluation_run_id",
                    outcome_metadata,
                    task.metadata,
                ),
                "evaluation_run_id",
            ),
            case_id=_required_string(
                _fallback(case_id, "case_id", outcome_metadata, task.metadata),
                "case_id",
            ),
            trial=_required_int(
                _fallback(trial, "trial", outcome_metadata, task.metadata),
                "trial",
            ),
            policy=_required_string(
                _fallback(policy, "policy", outcome_metadata, task.metadata),
                "policy",
            ),
            role=invocation_role,
            provider=settings.provider,
            model=settings.model,
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            workspace_id=_required_string(
                _fallback(
                    workspace_id,
                    "workspace_id",
                    outcome_metadata,
                    task.metadata,
                ),
                "workspace_id",
            ),
            task_prompt=task.instruction,
            rendered_prompt=_required_string(
                _fallback(
                    rendered_prompt,
                    "rendered_prompt",
                    outcome_metadata,
                    task.metadata,
                ),
                "rendered_prompt",
            ),
            context_manifest=manifest,
            included_context_sources=included,
            excluded_context_sources=excluded,
            context_hashes={item.source_id: item.content_hash for item in manifest},
            raw_response=outcome.output_text if outcome is not None else "",
            tool_calls=tool_calls,
            commands=outcome.commands if outcome is not None else (),
            changed_files=tuple(change.path for change in result.file_changes),
            test_output=outcome.test_results if outcome is not None else (),
            latency_seconds=result.duration_seconds,
            retry_count=(
                outcome.retries if outcome is not None else result.attempt - 1
            ),
            status=result.status.value,
            intervention_id=_optional_string(
                _fallback(
                    intervention_id,
                    "intervention_id",
                    outcome_metadata,
                    task.metadata,
                )
            ),
            parent_run_id=_optional_string(
                _fallback(
                    parent_run_id,
                    "parent_run_id",
                    outcome_metadata,
                    task.metadata,
                )
            ),
            reasoning_level=reasoning,
            temperature=settings.temperature,
            random_seed=settings.seed,
            grader_input=_optional_string(
                _fallback(
                    grader_input,
                    "grader_input",
                    outcome_metadata,
                    task.metadata,
                )
            ),
            raw_grader_response=_optional_string(
                _fallback(
                    raw_grader_response,
                    "raw_grader_response",
                    outcome_metadata,
                    task.metadata,
                )
            ),
            parsed_score=_fallback(
                parsed_score,
                "parsed_score",
                outcome_metadata,
                task.metadata,
            ),
            provider_input_tokens=(
                outcome.input_tokens
                if outcome is not None and outcome.input_tokens is not None
                else _optional_int(outcome_metadata.get("provider_input_tokens"))
            ),
            provider_cached_tokens=cached_tokens,
            provider_output_tokens=(
                outcome.output_tokens
                if outcome is not None and outcome.output_tokens is not None
                else _optional_int(outcome_metadata.get("provider_output_tokens"))
            ),
            estimated_cost_usd=(
                outcome.cost_usd
                if outcome is not None and outcome.cost_usd is not None
                else _optional_float(outcome_metadata.get("estimated_cost_usd"))
            ),
            error=result.error,
            metadata=record_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the stable, JSON-serializable schema representation."""

        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "evaluation_run_id": self.evaluation_run_id,
            "case_id": self.case_id,
            "trial": self.trial,
            "policy": self.policy,
            "intervention_id": self.intervention_id,
            "parent_run_id": self.parent_run_id,
            "role": self.role.value,
            "provider": self.provider,
            "model": self.model,
            "reasoning_level": self.reasoning_level,
            "temperature": self.temperature,
            "random_seed": self.random_seed,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "workspace_id": self.workspace_id,
            "task_prompt": self.task_prompt,
            "rendered_prompt": self.rendered_prompt,
            "context_manifest": [item.to_dict() for item in self.context_manifest],
            "included_context_sources": list(self.included_context_sources),
            "excluded_context_sources": list(self.excluded_context_sources),
            "context_hashes": dict(self.context_hashes),
            "raw_response": self.raw_response,
            "tool_calls": _json_copy(self.tool_calls),
            "commands": list(self.commands),
            "changed_files": list(self.changed_files),
            "test_output": list(self.test_output),
            "grader_input": self.grader_input,
            "raw_grader_response": self.raw_grader_response,
            "parsed_score": _json_copy(self.parsed_score),
            "provider_input_tokens": self.provider_input_tokens,
            "provider_cached_tokens": self.provider_cached_tokens,
            "provider_output_tokens": self.provider_output_tokens,
            "latency_seconds": self.latency_seconds,
            "estimated_cost_usd": self.estimated_cost_usd,
            "retry_count": self.retry_count,
            "error": self.error,
            "status": self.status,
            "metadata": _json_copy(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvaluationInvocationRecord:
        """Validate and restore a record from its JSON schema representation."""

        raw_manifest = _sequence(value.get("context_manifest"))
        manifest = tuple(
            ContextManifestItem.from_dict(_mapping(item, "context_manifest item"))
            for item in raw_manifest
        )
        raw_hashes = _mapping(value.get("context_hashes"), "context_hashes")
        raw_tools = _sequence(value.get("tool_calls", ()))
        metadata = _mapping(value.get("metadata", {}), "metadata")
        return cls(
            evaluation_run_id=str(value["evaluation_run_id"]),
            case_id=str(value["case_id"]),
            trial=int(value["trial"]),
            policy=str(value["policy"]),
            role=InvocationRole(str(value["role"])),
            provider=str(value["provider"]),
            model=str(value["model"]),
            started_at=str(value["started_at"]),
            ended_at=str(value["ended_at"]),
            workspace_id=str(value["workspace_id"]),
            task_prompt=str(value["task_prompt"]),
            rendered_prompt=str(value["rendered_prompt"]),
            context_manifest=manifest,
            included_context_sources=tuple(
                str(item) for item in _sequence(value["included_context_sources"])
            ),
            excluded_context_sources=tuple(
                str(item) for item in _sequence(value["excluded_context_sources"])
            ),
            context_hashes={str(key): str(item) for key, item in raw_hashes.items()},
            raw_response=str(value.get("raw_response", "")),
            tool_calls=tuple(dict(_mapping(item, "tool_call")) for item in raw_tools),
            commands=tuple(str(item) for item in _sequence(value.get("commands", ()))),
            changed_files=tuple(
                str(item) for item in _sequence(value.get("changed_files", ()))
            ),
            test_output=tuple(
                str(item) for item in _sequence(value.get("test_output", ()))
            ),
            latency_seconds=float(value["latency_seconds"]),
            retry_count=int(value["retry_count"]),
            status=str(value["status"]),
            intervention_id=_optional_string(value.get("intervention_id")),
            parent_run_id=_optional_string(value.get("parent_run_id")),
            reasoning_level=_optional_string(value.get("reasoning_level")),
            temperature=_optional_float(value.get("temperature")),
            random_seed=_optional_int(value.get("random_seed")),
            grader_input=_optional_string(value.get("grader_input")),
            raw_grader_response=_optional_string(value.get("raw_grader_response")),
            parsed_score=value.get("parsed_score"),
            provider_input_tokens=_optional_int(value.get("provider_input_tokens")),
            provider_cached_tokens=_optional_int(value.get("provider_cached_tokens")),
            provider_output_tokens=_optional_int(value.get("provider_output_tokens")),
            estimated_cost_usd=_optional_float(value.get("estimated_cost_usd")),
            error=_optional_string(value.get("error")),
            metadata=dict(metadata),
            record_id=str(value["record_id"]),
            schema_version=str(value["schema_version"]),
        )


def append_evaluation_record(
    path: Path,
    record: EvaluationInvocationRecord,
) -> None:
    """Append and fsync one record, rejecting duplicate record IDs."""

    existing_ids = (
        {existing.record_id for existing in read_evaluation_records(path)}
        if path.exists()
        else set()
    )
    if record.record_id in existing_ids:
        raise ValueError(f"duplicate evaluation record ID: {record.record_id}")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        record.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_evaluation_records(path: Path) -> tuple[EvaluationInvocationRecord, ...]:
    """Read and validate every non-empty JSONL audit record."""

    records: list[EvaluationInvocationRecord] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid evaluation JSON on line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError(f"evaluation line {line_number} must be an object")
            record = EvaluationInvocationRecord.from_dict(value)
            if record.record_id in seen:
                raise ValueError(f"duplicate evaluation record ID: {record.record_id}")
            seen.add(record.record_id)
            records.append(record)
    return tuple(records)


def _fallback(
    explicit: Any,
    key: str,
    *metadata_sources: Mapping[str, Any],
) -> Any:
    if explicit is not None:
        return explicit
    for source in metadata_sources:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _tool_calls(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    return tuple(dict(_mapping(item, "tool_call")) for item in _sequence(value))


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional string field must be a string or null")
    return value


def _required_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("optional integer field must be an integer or null")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("optional float field must be numeric or null")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("optional float field must be finite")
    return result


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _sequence(value: Any) -> Sequence[Any]:
    if not isinstance(value, list | tuple):
        raise ValueError("field must be a JSON array")
    return value


def _timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _validate_digest(value: str, name: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{name} must contain 64 hexadecimal characters")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(f"{name} must be hexadecimal") from error


def _validate_json(value: Any, path: str) -> None:
    if value is None or isinstance(value, str | int | bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string object key")
            _validate_json(item, f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _validate_json(item, f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains non-JSON value {type(value).__name__}")


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_copy(item) for item in value]
    return value
