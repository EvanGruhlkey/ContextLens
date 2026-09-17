"""On-demand repository discovery and compact, versioned source delivery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from contextlens.context_index import discover_candidates
from contextlens.evidence_session import tokenizer
from contextlens.pruning import PruneRequest, ReceiptStore


class RepositoryContext:
    """Keep provenance local; expose bounded exact evidence through short handles.

    Reads check the same bytes they return. This is not an atomic edit guard.
    Snapshot expansion is explicitly historical and never verifies current source.
    """

    def __init__(self, root: Path, state: Path, *, encoding: str = "estimate") -> None:
        self.root = root.resolve()
        self.state = state.resolve()
        self.handles = self.state / "handles"
        self.handles.mkdir(parents=True, exist_ok=True)
        self.receipts = ReceiptStore(self.state / "source")
        self.count, self.count_method = tokenizer(encoding)
        self.visible: dict[tuple[str, str], list[tuple[int, int]]] | None = None
        self.pending: dict[str, list[tuple[str, str, int, int]]] = {}

    def begin_context(self) -> None:
        """Start/reset an owned conversation epoch, including after compaction."""
        self.visible = {}
        self.pending.clear()

    def acknowledge(self, response: str) -> None:
        """Record coverage only after the owner appends this response to history."""
        spans = self.pending.pop(_digest(response), [])
        if self.visible is not None:
            for path, version, start, end in spans:
                self.visible.setdefault((path, version), []).append((start, end))

    def call(self, operation: str, arguments: Mapping[str, Any]) -> str:
        """Return exactly the text the integration should append to model history."""
        budget = _integer(arguments, "budget", 1200 if operation == "find" else 3000)
        if not 128 <= budget <= 16000:
            raise ValueError("budget must be between 128 and 16000 tokens")
        if operation == "find":
            result = self.find(
                _string(arguments, "query"),
                focus=_string(arguments, "focus", ""),
                limit=_integer(arguments, "limit", 5),
                budget=budget,
            )
        elif operation in {"read", "expand"}:
            result = self.read(arguments, budget=budget, snapshot=operation == "expand")
        else:
            raise ValueError("unknown repository context operation")
        if self.count(result) > budget:
            raise RuntimeError("context response exceeded its rendered budget")
        audit = {
            "operation": operation,
            "arguments": dict(arguments),
            "response": result,
            "response_tokens": self.count(result),
            "token_count_method": self.count_method,
        }
        with (self.state / "calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(audit, ensure_ascii=False) + "\n")
        return result

    def find(self, query: str, *, focus: str, limit: int, budget: int) -> str:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        if not query.strip():
            raise ValueError("query must be nonempty")
        lines = [
            "Repository matches (locations only; read handles for exact evidence):"
        ]
        candidates = discover_candidates(self.root, self.state, query, focus)
        for candidate in candidates[:limit]:
            unit = candidate.unit
            spans = [
                self._capture(
                    unit.path,
                    unit.start_line,
                    unit.end_line,
                    expected_hash=candidate.content_hash,
                )
            ]
            for support in candidate.support:
                spans.append(
                    self._capture(
                        support.path,
                        support.start_line,
                        support.end_line,
                        expected_text=support.text,
                    )
                )
            handle = self._save(
                {
                    "spans": spans,
                    "unresolved": list(candidate.unresolved),
                    "repository_version": candidate.repository_version,
                }
            )
            label = ", ".join(unit.bindings) or unit.language
            row = f"{handle} {unit.path}:{unit.start_line}-{unit.end_line} {label}"
            if candidate.unresolved:
                row += " [support unresolved]"
            if self.count("\n".join([*lines, row])) > budget:
                break
            lines.append(row)
        if len(lines) > 1:
            return "\n".join(lines)
        if candidates:
            return "Matching locations exceed the response budget; increase budget."
        return "No matching evidence found."

    def read(self, arguments: Mapping[str, Any], *, budget: int, snapshot: bool) -> str:
        handle = _string(arguments, "handle", "")
        if handle and arguments.get("path"):
            raise ValueError("supply either a handle or a path")
        if handle:
            record = self._load(handle)
        else:
            if snapshot:
                raise ValueError("snapshot expansion requires a handle")
            path = _string(arguments, "path")
            span = self._capture(
                path, _integer(arguments, "start_line", 1), arguments.get("end_line")
            )
            record = {"spans": [span], "unresolved": []}
            handle = self._save(record)
        chunks = [
            "HISTORICAL SNAPSHOT; verify current source before editing."
            if snapshot
            else "Exact current source; freshness checked on read."
        ]
        delivered: list[tuple[str, str, int, int]] = []
        force = arguments.get("reread", False)
        if not isinstance(force, bool):
            raise ValueError("reread must be a boolean")
        has_range = "start_line" in arguments or "end_line" in arguments
        if has_range and ("start_line" not in arguments or "end_line" not in arguments):
            raise ValueError("start_line and end_line must be supplied together")
        if has_range and len(record["spans"]) > 1:
            chunks.append(
                "Explicit range; support omitted. "
                "Read the handle without bounds for grouped evidence."
            )
        for position, span in enumerate(record["spans"]):
            if has_range and position > 0:
                continue
            if snapshot:
                source = self.receipts.read(span["receipt_id"])
            else:
                source = self._source(span["path"])
                if _digest(source) != span["content_hash"]:
                    return (
                        f"Stale evidence {handle}. "
                        "Source changed; find again or read the current path."
                    )
            start = _integer(arguments, "start_line", span["start_line"])
            end = (
                _integer(arguments, "end_line", span["end_line"])
                if span["end_line"] or "end_line" in arguments
                else 0
            )
            content = _range(source, start, end)
            intervals = [(start, end)]
            for path, version, seen_start, seen_end in delivered:
                if (path, version) == (span["path"], span["content_hash"]):
                    intervals = _subtract(intervals, seen_start, seen_end)
            if self.visible is not None and not force and not snapshot:
                for seen_start, seen_end in self.visible.get(
                    (span["path"], span["content_hash"]), []
                ):
                    intervals = _subtract(intervals, seen_start, seen_end)
            if not intervals:
                chunks.append(f"{handle} {span['path']}:{start}-{end} already visible.")
            for first, last in intervals:
                content = _range(source, first, last)
                chunks.append(f"{handle} {span['path']}:{first}-{last}\n{content}")
                delivered.append((span["path"], span["content_hash"], first, last))
        if record["unresolved"]:
            chunks.append("Support unresolved: " + "; ".join(record["unresolved"]))
        response = "\n".join(chunks)
        if self.count(response) > budget:
            return (
                f"Evidence {handle} and its support exceed the response budget. "
                "Request an explicit smaller range or a larger budget."
            )
        if self.visible is not None:
            self.pending[_digest(response)] = delivered
        return response

    def _source(self, path: str) -> str:
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("source path is outside the repository")
        if target.stat().st_size > 1024 * 1024:
            raise ValueError("source exceeds 1 MiB")
        with target.open("rb") as stream:
            content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("source exceeds 1 MiB")
        return content.decode("utf-8")

    def _capture(
        self,
        path: str,
        start: int,
        end: Any,
        *,
        expected_hash: str = "",
        expected_text: str | None = None,
    ) -> dict[str, Any]:
        source = self._source(path)
        path = (self.root / path).resolve().relative_to(self.root).as_posix()
        end = len(source.splitlines()) if end is None else end
        content = _range(source, start, end)
        if expected_hash and _digest(source) != expected_hash:
            raise ValueError("source changed during discovery; retry")
        if expected_text is not None and content != expected_text:
            raise ValueError("support changed during discovery; retry")
        receipt = self.receipts.save(
            PruneRequest(task="Repository evidence", content=source)
        )
        return {
            "path": path,
            "start_line": start,
            "end_line": end,
            "content_hash": receipt.content_hash,
            "receipt_id": receipt.receipt_id,
        }

    def _save(self, record: dict[str, Any]) -> str:
        record = record | {"root": str(self.root)}
        encoded = json.dumps(record, sort_keys=True)
        handle = "h_" + hashlib.sha256(encoded.encode()).hexdigest()[:16]
        target = self.handles / (handle + ".json")
        temporary = self.handles / (handle + "." + uuid4().hex + ".tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return handle

    def _load(self, handle: str) -> dict[str, Any]:
        if (
            len(handle) != 18
            or not handle.startswith("h_")
            or any(character not in "0123456789abcdef" for character in handle[2:])
        ):
            raise ValueError("invalid evidence handle")
        encoded = (self.handles / (handle + ".json")).read_text(encoding="utf-8")
        if "h_" + hashlib.sha256(encoded.encode()).hexdigest()[:16] != handle:
            raise RuntimeError("evidence handle integrity check failed")
        record: dict[str, Any] = json.loads(encoded)
        if record.get("root") != str(self.root):
            raise ValueError("evidence belongs to another repository")
        return record


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _subtract(
    intervals: list[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    remaining = []
    for first, last in intervals:
        if end < first or start > last:
            remaining.append((first, last))
            continue
        if first < start:
            remaining.append((first, start - 1))
        if last > end:
            remaining.append((end + 1, last))
    return remaining


def _range(source: str, start: int, end: int) -> str:
    lines = source.splitlines(keepends=True)
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
    ):
        raise ValueError("line bounds must be integers")
    if not source and start == 1 and end == 0:
        return ""
    if start < 1 or end < start or end > len(lines):
        raise ValueError("line range is outside the source")
    return "".join(lines[start - 1 : end])


def _string(arguments: Mapping[str, Any], key: str, default: str | None = None) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _integer(arguments: Mapping[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value
