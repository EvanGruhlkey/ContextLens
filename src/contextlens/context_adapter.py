"""Explicit host-side observation boundary for callback-based coding agents.

This adapter owns tool execution and appends only transformed observations to
solver history. It does not intercept native CLI tools or modify other agents.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from contextlens.pruning.model import PruneRequest
from contextlens.pruning.receipts import ReceiptStore
from contextlens.pruning.runtime import PruningSession, ToolObservation


class RepositoryTools(Protocol):
    def call(self, operation: str, arguments: Mapping[str, Any]) -> str: ...


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    tool: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Answer:
    content: str


class Solver(Protocol):
    """Host supplies inference; the adapter has no provider or billing behavior."""

    def __call__(self, messages: Sequence[Message]) -> ToolCall | Answer: ...


@dataclass(frozen=True)
class HostTool:
    execute: Callable[[Mapping[str, Any]], str]
    prune: bool = False


@dataclass(frozen=True)
class ObservationAudit:
    tool: str
    receipt_id: str | None
    error: str | None = None


class TurnLimitExceeded(RuntimeError):
    """The host may inspect history and resume with a new bounded run."""


class ContextAdapter:
    """Run one solver with recoverable observations outside its transcript.

    Register repository-reading host tools with ``prune=True`` to transform
    their outputs through the supplied task-scoped PruningSession. Other tools
    (edits, tests, etc.) retain their normal output. The already-budgeted
    ``find``, ``read`` and ``expand`` repository service bypasses double pruning.
    Full recovery is a host method, never a tool that asks the solver to resend
    an observation. Tool and pruning failures return generic errors, preserving
    the raw observation locally without leaking exception text to the solver.
    Repository implementations may expose ``begin_context`` and ``acknowledge``
    hooks to track evidence actually appended to this owned history. A host that
    compacts or removes history must reset that visibility epoch with
    ``begin_context`` before issuing further repository reads.
    """

    def __init__(
        self,
        repository: RepositoryTools,
        receipts: ReceiptStore,
        *,
        session: PruningSession | None = None,
        tools: Mapping[str, HostTool] | None = None,
    ) -> None:
        self.repository = repository
        self.receipts = receipts
        self.session = session
        self.tools = dict(tools or {})
        if set(self.tools) & {"find", "read", "expand"}:
            raise ValueError("repository operation names are reserved")
        if session is None and any(tool.prune for tool in self.tools.values()):
            raise ValueError("pruned host tools require a PruningSession")
        self.history: list[Message] = []
        self.audit: list[ObservationAudit] = []

    def recover(self, receipt_id: str) -> str:
        """Recover exact original output for host inspection or downstream tools."""
        return self.receipts.read(receipt_id)

    def run(
        self,
        solver: Solver,
        task: str,
        *,
        constraints: str = "",
        max_turns: int = 20,
    ) -> str:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        if not task.strip():
            raise ValueError("task cannot be empty")
        if self.session is not None and self.session.goal.objective != " ".join(
            task.split()
        ):
            raise ValueError("task must match the PruningSession objective")
        self.history = [Message("system", constraints), Message("user", task)]
        self.audit = []
        begin_context = getattr(self.repository, "begin_context", None)
        if callable(begin_context):
            begin_context()
        for _ in range(max_turns):
            action = solver(tuple(self.history))
            if isinstance(action, Answer):
                self.history.append(Message("assistant", action.content))
                return action.content
            if not isinstance(action, ToolCall):
                raise TypeError("solver must return ToolCall or Answer")
            arguments = dict(action.arguments)
            self.history.append(Message("assistant", "", action.name, arguments))
            self.history.append(
                Message("tool", self._execute(action.name, arguments), action.name)
            )
            if action.name in {"find", "read", "expand"}:
                acknowledge = getattr(self.repository, "acknowledge", None)
                if callable(acknowledge):
                    try:
                        acknowledge(self.history[-1].content)
                    except Exception as error:
                        # Evidence already reached the solver history. Never
                        # repeat an executor to repair visibility bookkeeping.
                        self.audit.append(
                            ObservationAudit(
                                action.name, None, f"acknowledge:{type(error).__name__}"
                            )
                        )
        raise TurnLimitExceeded(f"solver exceeded {max_turns} turns")

    def _execute(self, name: str, arguments: Mapping[str, Any]) -> str:
        receipt_id = None
        try:
            if name in {"find", "read", "expand"}:
                raw = self.repository.call(name, arguments)
                prune = False
            else:
                tool = self.tools.get(name)
                if tool is None:
                    self.audit.append(ObservationAudit(name, None, "unknown_tool"))
                    return "Tool unavailable. Choose a registered tool."
                raw = tool.execute(arguments)
                prune = tool.prune
            if not isinstance(raw, str):
                raise TypeError("tool output must be text")
            receipt_id = self.receipts.save(
                PruneRequest(content=raw, task="audit", tool=name)
            ).receipt_id
            text = raw
            if prune:
                assert self.session is not None
                text = self.session.observe(ToolObservation(raw, name, arguments)).text
            self.audit.append(ObservationAudit(name, receipt_id))
            return text
        except Exception as error:
            self.audit.append(ObservationAudit(name, receipt_id, type(error).__name__))
            return (
                "Tool observation unavailable. Adjust the request or use another tool."
            )
