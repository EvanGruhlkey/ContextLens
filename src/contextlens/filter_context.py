"""Default MCP session: filter, read, recover, pin, and list."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from contextlens.context_tools import RepositoryContext, _integer, _string
from contextlens.filtering import (
    FilterConfig,
    FilterRequest,
    FilterSession,
    ObservationFilter,
    RelevanceJudge,
)
from contextlens.observations import ObservationStore
from contextlens.pruning.model import ObservationKind, estimate_tokens
from contextlens.pruning.runtime import ToolObservation


class FilterContext(RepositoryContext):
    """Filter tool output before it reaches the coding model.

    This is not an action controller. The host agent still chooses tools,
    commands, and edits. ContextLens only reduces large observations.
    """

    mcp_profile = "filter"
    selection_enabled = False
    action_enabled = False

    def __init__(
        self,
        root: Path,
        state: Path,
        *,
        encoding: str = "o200k_base",
        task: str = "",
        judge: RelevanceJudge | None = None,
        config: FilterConfig | None = None,
    ) -> None:
        super().__init__(root, state, encoding=encoding)
        self.task = " ".join(task.split())
        self.focus = ""
        self.config = config or FilterConfig.from_env()
        self.observations = ObservationStore(self.state / "observations")
        self.pipeline = ObservationFilter(
            self.receipts, judge=judge, config=self.config
        )
        self.session = FilterSession(
            self.receipts,
            self.observations,
            task=self.task or "repository task",
            judge=judge,
            config=self.config,
        )
        self.judge = judge

    def call(self, operation: str, arguments: Mapping[str, Any]) -> str:
        if operation == "filter":
            return self._filter(arguments)
        if operation == "recover":
            return self._recover(arguments)
        if operation == "pin":
            return self._pin(arguments)
        if operation == "list":
            if arguments:
                raise ValueError("list accepts no arguments")
            return json.dumps(self.session.listing(), ensure_ascii=False)
        if operation in {"read", "expand", "find"}:
            if operation == "read" and "task" in arguments:
                self.task = _string(arguments, "task")
                self.session.task = self.task or self.session.task
            return super().call(operation, arguments)
        raise ValueError("unknown repository context operation")

    def read(self, arguments: Mapping[str, Any], *, budget: int, snapshot: bool) -> str:
        if snapshot or "start_line" in arguments or "end_line" in arguments:
            return super().read(arguments, budget=budget, snapshot=snapshot)
        task = self.task or _string(arguments, "task", "")
        if not task:
            return super().read(arguments, budget=budget, snapshot=snapshot)
        handle = _string(arguments, "handle", "")
        if handle:
            record = self._load(handle)
            path = record["spans"][0]["path"]
        else:
            path = _string(arguments, "path")
        source = self._source(path)
        if estimate_tokens(source) < self.config.minimum_tokens:
            return super().read(arguments, budget=budget, snapshot=snapshot)
        result = self.pipeline.filter(
            FilterRequest(
                task=task,
                content=source,
                focus=self.focus,
                tool="read",
                arguments={"path": path},
                kind=ObservationKind.CODE,
                path=path,
            )
        )
        if result.bypass_reason:
            return super().read(arguments, budget=budget, snapshot=snapshot)
        header = "Exact current source; omitted spans remain recoverable."
        response = f"{header}\n{result.text}\n{result.recovery_hint}"
        if self.count(response) > budget:
            return (
                "Filtered source exceeds the response budget. "
                "Recover omitted spans or request an explicit smaller range."
            )
        return response

    def _filter(self, arguments: Mapping[str, Any]) -> str:
        if set(arguments) - {
            "task",
            "content",
            "focus",
            "kind",
            "tool",
            "path",
            "start_line",
            "end_line",
            "symbol",
            "pin",
        }:
            raise ValueError("unknown filter argument")
        task = _string(arguments, "task", self.task)
        if not task.strip():
            raise ValueError("task must be nonempty")
        self.task = " ".join(task.split())
        self.session.task = self.task
        focus = _string(arguments, "focus", self.focus)
        kind_name = _string(arguments, "kind", "")
        kind = ObservationKind(kind_name) if kind_name else None
        pin = arguments.get("pin", False)
        if not isinstance(pin, bool):
            raise ValueError("pin must be a boolean")
        observation = ToolObservation(
            _string(arguments, "content"),
            _string(arguments, "tool", "tool"),
            {
                key: arguments[key]
                for key in ("path", "start_line", "end_line", "symbol")
                if key in arguments
            },
            kind=kind,
        )
        if focus:
            self.session.set_focus(focus)
        result = self.session.observe(observation, pin=pin)
        notice = result.recovery_hint or result.receipt_id
        return result.text + (f"\n{notice}" if result.omitted_ranges else "")

    def _recover(self, arguments: Mapping[str, Any]) -> str:
        handle = _string(arguments, "handle")
        start = arguments.get("start_line")
        end = arguments.get("end_line")
        if start is not None:
            start = _integer(arguments, "start_line", 1)
        if end is not None:
            end = _integer(arguments, "end_line", start or 1)
        if handle.startswith("h_"):
            return super().call("expand", arguments)
        return self.session.recover(handle, start, end)

    def _pin(self, arguments: Mapping[str, Any]) -> str:
        handle = _string(arguments, "handle", "")
        if handle:
            item = self.session.pin(handle)
            return json.dumps({"handle": item.handle, "status": "pinned"})
        item = self.observations.add(
            kind=_string(arguments, "type", "user_constraint"),
            summary=_string(arguments, "summary"),
            content=_string(arguments, "content", _string(arguments, "summary")),
            source=_string(arguments, "source", "") or None,
            pinned=True,
        )
        return json.dumps({"handle": item.handle, "status": "pinned"})
