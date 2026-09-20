"""Recoverable active and deferred observations for controller sessions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

OBSERVATION_KINDS = frozenset(
    {
        "source",
        "search_result",
        "test_output",
        "traceback",
        "diff",
        "tool_result",
        "configuration",
        "user_constraint",
    }
)


@dataclass(frozen=True, slots=True)
class Observation:
    handle: str
    kind: str
    summary: str
    content: str
    source: str | None
    status: str
    age_steps: int
    pinned: bool

    def descriptor(self) -> dict[str, Any]:
        return {
            "id": self.handle,
            "type": self.kind,
            "summary": self.summary,
            "source": self.source,
            "age_steps": self.age_steps,
        }


class ObservationStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.records = self.root / "records"
        self.records.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def add(
        self,
        *,
        kind: str,
        summary: str,
        content: str,
        source: str | None = None,
        pinned: bool = False,
    ) -> Observation:
        if kind not in OBSERVATION_KINDS:
            raise ValueError("unsupported observation type")
        if not 1 <= len(summary.strip()) <= 1000 or len(content) > 1024 * 1024:
            raise ValueError("observation content is not bounded")
        if source is not None and (not isinstance(source, str) or len(source) > 200):
            raise ValueError("observation source is invalid")
        record = {
            "kind": kind,
            "summary": summary.strip(),
            "content": content,
            "source": source,
            "pinned": bool(pinned or kind == "user_constraint"),
        }
        encoded = json.dumps(record, sort_keys=True, ensure_ascii=False)
        handle = "obs_" + hashlib.sha256(encoded.encode()).hexdigest()[:20]
        self._atomic(self.records / f"{handle}.json", encoded)
        index = self._index()
        index[handle] = {
            "status": "pin" if record["pinned"] else "keep",
            "age_steps": 0,
        }
        self._save_index(index)
        return self.read(handle)

    def read(self, handle: str) -> Observation:
        self._validate_handle(handle)
        path = self.records / f"{handle}.json"
        encoded = path.read_text(encoding="utf-8")
        if "obs_" + hashlib.sha256(encoded.encode()).hexdigest()[:20] != handle:
            raise RuntimeError("observation handle integrity check failed")
        record = json.loads(encoded)
        state = self._index().get(handle)
        if not isinstance(state, dict):
            raise KeyError(handle)
        return Observation(
            handle,
            record["kind"],
            record["summary"],
            record["content"],
            record.get("source"),
            state["status"],
            state["age_steps"],
            bool(record["pinned"] or state["status"] == "pin"),
        )

    def pin(self, handle: str) -> Observation:
        return self._set_status(handle, "pin")

    def defer(self, handle: str) -> Observation:
        current = self.read(handle)
        if current.pinned:
            raise ValueError("pinned observations cannot be deferred")
        return self._set_status(handle, "defer")

    def restore(self, handle: str) -> Observation:
        return self._set_status(handle, "pin" if self.read(handle).pinned else "keep")

    def advance(self) -> None:
        index = self._index()
        for state in index.values():
            state["age_steps"] += 1
        self._save_index(index)

    def active(self) -> list[Observation]:
        return self._matching({"pin", "keep"})

    def deferred(self) -> list[Observation]:
        return self._matching({"defer"})

    def descriptors(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        observations = (
            self.active() if active_only else self._matching({"pin", "keep", "defer"})
        )
        return [item.descriptor() for item in observations]

    def bounded_descriptors(self, limit: int = 20) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("descriptor limit must be positive")
        active = self.active()
        pinned = [item for item in active if item.pinned]
        if len(pinned) > limit:
            raise ValueError("pinned observations exceed the descriptor limit")
        other = [item for item in active if not item.pinned]
        selected = [*pinned, *other[: limit - len(pinned)]]
        return [item.descriptor() for item in selected]

    def _matching(self, statuses: set[str]) -> list[Observation]:
        index = self._index()
        return [
            self.read(handle)
            for handle, state in index.items()
            if state["status"] in statuses
        ]

    def _set_status(self, handle: str, status: str) -> Observation:
        index = self._index()
        if handle not in index:
            raise KeyError(handle)
        index[handle]["status"] = status
        self._save_index(index)
        return replace(self.read(handle), status=status)

    def _index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        value = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("observation index is invalid")
        return value

    def _save_index(self, value: dict[str, dict[str, Any]]) -> None:
        self._atomic(self.index_path, json.dumps(value, sort_keys=True))

    @staticmethod
    def _validate_handle(handle: str) -> None:
        if (
            len(handle) != 24
            or not handle.startswith("obs_")
            or any(char not in "0123456789abcdef" for char in handle[4:])
        ):
            raise ValueError("invalid observation handle")

    @staticmethod
    def _atomic(path: Path, content: str) -> None:
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
