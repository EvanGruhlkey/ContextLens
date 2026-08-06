"""JSONL trace writer and reader."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import TextIO

from contextlens.trace.artifacts import ArtifactStore
from contextlens.trace.model import (
    SCHEMA_VERSION,
    AgentTrace,
    ContextEvent,
    ContextSource,
    TraceHeader,
    TraceStep,
)
from contextlens.trace.redaction import Redactor


class TraceWriter:
    """Append ordered context events to a new local JSONL trace."""

    def __init__(
        self,
        path: Path,
        *,
        header: TraceHeader | None = None,
        artifact_store: ArtifactStore | None = None,
        artifact_threshold: int = 64 * 1024,
        redactors: Iterable[Redactor] = (),
    ) -> None:
        if artifact_threshold < 0:
            raise ValueError("artifact_threshold cannot be negative")
        self.path = path
        self.header = header or TraceHeader()
        self.artifact_store = artifact_store
        self.artifact_threshold = artifact_threshold
        self.redactors = tuple(redactors)
        self._stream: TextIO | None = None
        self._next_sequence: dict[str, int] = {}
        self._next_step_sequence = 0
        self._trace_written = False

    def __enter__(self) -> TraceWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("x", encoding="utf-8", newline="\n")
        self._write(self.header.to_dict())
        return self

    def __exit__(self, *_: object) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def add(self, request_id: str, source: ContextSource) -> ContextEvent:
        if self._stream is None:
            raise RuntimeError("TraceWriter must be used as a context manager")
        for redactor in self.redactors:
            source = redactor.redact(source)
        source = self._externalize(source)
        sequence = self._next_sequence.get(request_id, 0)
        event = ContextEvent(
            request_id=request_id,
            sequence=sequence,
            source=source,
        )
        self._write(event.to_dict())
        self._next_sequence[request_id] = sequence + 1
        return event

    def set_trace(self, trace: AgentTrace) -> None:
        """Record run metadata and aggregates once in the JSONL stream."""

        if self._stream is None:
            raise RuntimeError("TraceWriter must be used as a context manager")
        if self._trace_written:
            raise RuntimeError("agent trace metadata has already been written")
        if trace.trace_id != self.header.trace_id:
            raise ValueError("agent trace ID must match the trace header")
        self._write(
            {
                "event": "agent_trace",
                "schema_version": SCHEMA_VERSION,
                "trace": trace.to_dict(),
            }
        )
        self._trace_written = True

    def add_step(self, step: TraceStep) -> None:
        """Append one complete model, tool, evaluation, or system step."""

        if self._stream is None:
            raise RuntimeError("TraceWriter must be used as a context manager")
        if step.trace_id != self.header.trace_id:
            raise ValueError("step trace ID must match the trace header")
        if step.sequence != self._next_step_sequence:
            raise ValueError(
                f"expected step sequence {self._next_step_sequence}, "
                f"got {step.sequence}"
            )
        self._write(
            {
                "event": "trace_step",
                "schema_version": SCHEMA_VERSION,
                "step": step.to_dict(),
            }
        )
        self._next_step_sequence += 1

    def _externalize(self, source: ContextSource) -> ContextSource:
        if (
            source.content is None
            or self.artifact_store is None
            or len(source.content.encode("utf-8")) < self.artifact_threshold
        ):
            return source
        reference = self.artifact_store.put(
            source.content.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
        )
        return replace(
            source,
            content=None,
            content_ref=reference,
            content_hash=reference.digest[7:],
        )

    def _write(self, value: dict[str, object]) -> None:
        assert self._stream is not None
        json.dump(value, self._stream, ensure_ascii=False, separators=(",", ":"))
        self._stream.write("\n")
        self._stream.flush()


class TraceReader:
    """Read and validate a local JSONL trace."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read_header(self) -> TraceHeader:
        with self.path.open(encoding="utf-8") as stream:
            first = stream.readline()
        if not first:
            raise ValueError("trace is empty")
        value = self._decode(first, line_number=1)
        header = TraceHeader.from_dict(value)
        self._check_version(header.schema_version)
        return header

    def events(self) -> Iterator[ContextEvent]:
        with self.path.open(encoding="utf-8") as stream:
            first = stream.readline()
            if not first:
                raise ValueError("trace is empty")
            header = TraceHeader.from_dict(self._decode(first, line_number=1))
            self._check_version(header.schema_version)
            expected: dict[str, int] = {}
            for line_number, line in enumerate(stream, start=2):
                if not line.strip():
                    continue
                value = self._decode(line, line_number=line_number)
                self._check_version(str(value.get("schema_version", "")))
                if value.get("event") != "context_added":
                    continue
                event = ContextEvent.from_dict(value)
                sequence = expected.get(event.request_id, 0)
                if event.sequence != sequence:
                    raise ValueError(
                        f"line {line_number}: expected sequence {sequence} "
                        f"for request {event.request_id!r}, got {event.sequence}"
                    )
                expected[event.request_id] = sequence + 1
                yield event

    def read_trace(self) -> AgentTrace | None:
        """Return agent-run metadata when the producer recorded it."""

        for value in self._records():
            if value.get("event") == "agent_trace":
                trace = value.get("trace")
                if not isinstance(trace, dict):
                    raise ValueError("agent_trace record requires a trace object")
                return AgentTrace.from_dict(trace)
        return None

    def steps(self) -> Iterator[TraceStep]:
        """Read ordered execution steps from a complete trace."""

        expected = 0
        for value in self._records():
            if value.get("event") != "trace_step":
                continue
            raw_step = value.get("step")
            if not isinstance(raw_step, dict):
                raise ValueError("trace_step record requires a step object")
            step = TraceStep.from_dict(raw_step)
            if step.sequence != expected:
                raise ValueError(
                    f"expected step sequence {expected}, got {step.sequence}"
                )
            expected += 1
            yield step

    def _records(self) -> Iterator[dict[str, object]]:
        with self.path.open(encoding="utf-8") as stream:
            first = stream.readline()
            if not first:
                raise ValueError("trace is empty")
            header = TraceHeader.from_dict(self._decode(first, line_number=1))
            self._check_version(header.schema_version)
            for line_number, line in enumerate(stream, start=2):
                if not line.strip():
                    continue
                value = self._decode(line, line_number=line_number)
                self._check_version(str(value.get("schema_version", "")))
                yield value

    @staticmethod
    def _decode(line: str, *, line_number: int) -> dict[str, object]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON on trace line {line_number}") from error
        if not isinstance(value, dict):
            raise ValueError(f"trace line {line_number} must be a JSON object")
        return value

    @staticmethod
    def _check_version(version: str) -> None:
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported trace schema {version!r}; expected {SCHEMA_VERSION!r}"
            )
