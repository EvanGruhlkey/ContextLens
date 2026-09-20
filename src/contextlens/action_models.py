"""Bounded action candidates offered to the ContextLens controller."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

ACTION_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ActionKind(StrEnum):
    SEARCH_REPOSITORY = "search_repository"
    READ_SOURCE = "read_source"
    READ_DEFERRED = "read_deferred"
    RUN_TARGETED_TEST = "run_targeted_test"
    INSPECT_DIFF = "inspect_diff"
    INSPECT_FAILURE = "inspect_failure"
    CONTINUE_INVESTIGATION = "continue_investigation"
    READY_TO_EDIT = "ready_to_edit"
    STOP = "stop"


_TOOLS: dict[ActionKind, frozenset[str | None]] = {
    ActionKind.SEARCH_REPOSITORY: frozenset({None, "context_select"}),
    ActionKind.READ_SOURCE: frozenset({None, "context_read"}),
    ActionKind.READ_DEFERRED: frozenset(
        {None, "context_read", "context_expand", "context_recall"}
    ),
    ActionKind.RUN_TARGETED_TEST: frozenset({None, "exec_command"}),
    ActionKind.INSPECT_DIFF: frozenset({None, "exec_command"}),
    ActionKind.INSPECT_FAILURE: frozenset({None, "exec_command"}),
    ActionKind.CONTINUE_INVESTIGATION: frozenset({None}),
    ActionKind.READY_TO_EDIT: frozenset({None}),
    ActionKind.STOP: frozenset({None}),
}

_ARGUMENTS: dict[ActionKind, frozenset[str]] = {
    ActionKind.SEARCH_REPOSITORY: frozenset({"task", "focus", "limit"}),
    ActionKind.READ_SOURCE: frozenset(
        {"handle", "path", "start_line", "end_line", "budget"}
    ),
    ActionKind.READ_DEFERRED: frozenset({"handle", "budget"}),
    ActionKind.RUN_TARGETED_TEST: frozenset(),
    ActionKind.INSPECT_DIFF: frozenset(),
    ActionKind.INSPECT_FAILURE: frozenset(),
    ActionKind.CONTINUE_INVESTIGATION: frozenset(),
    ActionKind.READY_TO_EDIT: frozenset(),
    ActionKind.STOP: frozenset(),
}


@dataclass(frozen=True, slots=True)
class CandidateAction:
    action_id: str
    kind: ActionKind
    description: str
    tool: str | None = None
    arguments: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not ACTION_ID.fullmatch(self.action_id):
            raise ValueError("action id must be a bounded snake-case identifier")
        if not 1 <= len(self.description.strip()) <= 500:
            raise ValueError("action description must contain 1 to 500 characters")
        if self.tool not in _TOOLS[self.kind]:
            raise ValueError("tool is not allowed for this action kind")
        arguments = self.arguments or {}
        if set(arguments) - _ARGUMENTS[self.kind]:
            raise ValueError("action contains unsupported arguments")
        if not _bounded_json(arguments):
            raise ValueError("action arguments must contain bounded JSON values")

    def to_state(self) -> dict[str, Any]:
        return {
            "id": self.action_id,
            "kind": self.kind.value,
            "description": self.description.strip(),
            "tool": self.tool,
            "arguments": dict(self.arguments or {}),
        }


def _bounded_json(value: Any, depth: int = 0) -> bool:
    if depth > 3:
        return False
    if value is None or isinstance(value, bool | int | float):
        return True
    if isinstance(value, str):
        return len(value) <= 1000
    if isinstance(value, list):
        return len(value) <= 20 and all(
            _bounded_json(item, depth + 1) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= 20 and all(
            isinstance(key, str)
            and len(key) <= 64
            and _bounded_json(item, depth + 1)
            for key, item in value.items()
        )
    return False
