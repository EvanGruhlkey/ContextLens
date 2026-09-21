"""Shared value types for live pruning and transcript compaction.

Two layers share these types: live pruning reduces one tool result before the
coding model reads it, and compaction removes stale tool interactions from a
long transcript. Nothing here talks to a model.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

MUTATING_TOOLS = frozenset(
    {
        "apply_patch",
        "create_file",
        "edit",
        "edit_file",
        "patch",
        "str_replace",
        "write",
        "write_file",
    }
)

_TOKEN_PIECES = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]")
_DIGIT = re.compile(r"\d")


def estimate_tokens(text: str) -> int:
    """Estimate tokens without a tokenizer.

    A word costs one token per six letters, a digit half a token, and any
    other symbol nine tenths. Calibrated by `fast-jev-compaction` against the
    usage Jev reports for real transcripts, where it lands a little above the
    true count. A flat characters-per-token ratio undercounts JSON-heavy
    states badly, which would push requests over the provider limit.
    """

    total = 0.0
    for piece in _TOKEN_PIECES.findall(text):
        first = piece[0]
        if first.isdigit():
            total += len(piece) / 2
        elif first.isascii() and first.isalpha():
            total += 1 + (len(piece) - 1) // 6
        else:
            total += 0.9
    return math.ceil(total)


def estimate_state_tokens(text: str) -> int:
    """Estimate tokens for a JSON state, where digits are tokenized densely."""

    return estimate_tokens(text) + len(_DIGIT.findall(text)) // 2


class OutputCategory(StrEnum):
    """What a tool result looks like, which changes the guidance Jev gets."""

    BUILD = "build"
    SEARCH = "search"
    SOURCE = "source"
    STRUCTURED = "structured"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LineRange:
    """A contiguous 1-based line range removed from a rendered observation."""

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid line range")

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def to_dict(self) -> dict[str, int]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "line_count": self.line_count,
        }


@dataclass(frozen=True, slots=True)
class ToolUse:
    """One tool call made by the assistant."""

    tool_use_id: str
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_use_id.strip():
            raise ValueError("tool_use_id cannot be empty")
        if not self.tool.strip():
            raise ValueError("tool cannot be empty")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))

    @property
    def mutating(self) -> bool:
        return self.tool.lower() in MUTATING_TOOLS


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The observation one tool call produced, paired by ``tool_use_id``."""

    tool_use_id: str
    text: str
    is_error: bool = False
    receipt_id: str | None = None

    def __post_init__(self) -> None:
        if not self.tool_use_id.strip():
            raise ValueError("tool_use_id cannot be empty")


@dataclass(frozen=True, slots=True)
class Message:
    """One transcript message.

    ``pinned`` marks content compaction must never touch, such as an explicit
    user constraint or information that cannot be regenerated.
    """

    role: str
    text: str = ""
    tool_uses: tuple[ToolUse, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    pinned: bool = False

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ValueError(f"unsupported role: {self.role!r}")
        object.__setattr__(self, "tool_uses", tuple(self.tool_uses))
        object.__setattr__(self, "tool_results", tuple(self.tool_results))

    @property
    def empty(self) -> bool:
        return (
            not self.text.strip() and not self.tool_uses and not self.tool_results
        )

    @property
    def chars(self) -> int:
        """Characters of text, tool arguments, and tool output this message holds."""

        total = len(self.text)
        for use in self.tool_uses:
            total += len(repr(dict(use.arguments)))
        for result in self.tool_results:
            total += len(result.text)
        return total


def transcript_chars(messages: tuple[Message, ...]) -> int:
    return sum(message.chars for message in messages)


def transcript_tokens(messages: tuple[Message, ...]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(message.text)
        for use in message.tool_uses:
            total += estimate_tokens(repr(dict(use.arguments)))
        for result in message.tool_results:
            total += estimate_tokens(result.text)
    return total
