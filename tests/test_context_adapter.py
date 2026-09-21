from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from contextlens.context_adapter import (
    Answer,
    ContextAdapter,
    HostTool,
    ToolCall,
    TurnLimitExceeded,
)
from contextlens.pruning.receipts import ReceiptStore
from contextlens.pruning.runtime import PruningSession


class Repository:
    def call(self, operation: str, arguments: Any) -> str:
        return "exact small source"


def test_raw_observations_are_transformed_before_solver_history(tmp_path: Path) -> None:
    raw = "SECRET_RAW_SENTINEL\r\nlarge irrelevant code\n"
    calls = []

    def execute(arguments: Any) -> str:
        calls.append(arguments)
        return raw

    session = cast(
        PruningSession,
        SimpleNamespace(
            goal=SimpleNamespace(objective="fix timeout"),
            observe=lambda observation: SimpleNamespace(text="relevant code"),
        ),
    )
    adapter = ContextAdapter(
        Repository(),
        ReceiptStore(tmp_path),
        session=session,
        tools={"read_file": HostTool(execute, prune=True)},
    )

    def solver(messages: Any) -> ToolCall | Answer:
        assert messages[0].content == "Preserve public API"
        assert "SECRET_RAW_SENTINEL" not in repr(messages)
        if len(messages) == 2:
            return ToolCall("read_file", {"path": "app.py"})
        assert messages[-1].content == "relevant code"
        return Answer("done")

    assert (
        adapter.run(solver, "fix timeout", constraints="Preserve public API") == "done"
    )
    assert len(calls) == 1
    receipt = adapter.audit[0].receipt_id
    assert receipt is not None
    assert adapter.recover(receipt) == raw


def test_repository_operations_skip_double_pruning(tmp_path: Path) -> None:
    adapter = ContextAdapter(Repository(), ReceiptStore(tmp_path))
    actions = iter([ToolCall("find", {"query": "timeout"}), Answer("done")])
    assert adapter.run(lambda messages: next(actions), "fix") == "done"
    assert adapter.history[-2].content == "exact small source"


def test_transform_failure_does_not_expose_raw_or_retry_tool(tmp_path: Path) -> None:
    def fail(observation: Any) -> Any:
        raise RuntimeError("SECRET_RAW_SENTINEL")

    calls = []
    adapter = ContextAdapter(
        Repository(),
        ReceiptStore(tmp_path),
        session=cast(
            PruningSession,
            SimpleNamespace(observe=fail, goal=SimpleNamespace(objective="fix")),
        ),
        tools={
            "read_file": HostTool(
                lambda args: calls.append(args) or "SECRET_RAW_SENTINEL", True
            )
        },
    )
    actions = iter([ToolCall("read_file"), Answer("recovered")])
    assert adapter.run(lambda messages: next(actions), "fix") == "recovered"
    assert "SECRET_RAW_SENTINEL" not in repr(adapter.history)
    assert len(calls) == 1
    assert adapter.audit[0].error == "RuntimeError"
    assert adapter.recover(adapter.audit[0].receipt_id or "") == "SECRET_RAW_SENTINEL"


def test_errors_and_turn_limit_are_bounded(tmp_path: Path) -> None:
    adapter = ContextAdapter(Repository(), ReceiptStore(tmp_path))
    with pytest.raises(TurnLimitExceeded):
        adapter.run(lambda messages: ToolCall("unknown"), "fix", max_turns=2)
    assert len(adapter.audit) == 2
    assert all(record.error == "unknown_tool" for record in adapter.audit)
    with pytest.raises(ValueError, match="FilterSession"):
        ContextAdapter(
            Repository(),
            ReceiptStore(tmp_path),
            tools={"read_file": HostTool(str, True)},
        )


def test_visibility_hooks_acknowledge_only_appended_history(tmp_path: Path) -> None:
    events = []

    class OwnedRepository(Repository):
        def begin_context(self) -> None:
            events.append("begin")

        def call(self, operation: str, arguments: Any) -> str:
            events.append("execute")
            return super().call(operation, arguments)

        def acknowledge(self, text: str) -> None:
            assert adapter.history[-1].role == "tool"
            assert adapter.history[-1].content == text
            assert adapter.history[0].content == "keep interfaces"
            events.append("acknowledge")
            raise RuntimeError("bookkeeping failure")

    adapter = ContextAdapter(OwnedRepository(), ReceiptStore(tmp_path))
    actions = iter([ToolCall("read", {"handle": "h1"}), Answer("done")])
    assert (
        adapter.run(lambda history: next(actions), "fix", constraints="keep interfaces")
        == "done"
    )
    assert events == ["begin", "execute", "acknowledge"]
    assert adapter.audit[-1].error == "acknowledge:RuntimeError"
    assert adapter.history[-2].content == "exact small source"
