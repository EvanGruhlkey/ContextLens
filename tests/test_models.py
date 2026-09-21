from __future__ import annotations

import pytest

from contextlens.models import (
    LineRange,
    Message,
    ToolResult,
    ToolUse,
    estimate_state_tokens,
    estimate_tokens,
    transcript_chars,
    transcript_tokens,
)


def test_estimate_tokens_is_monotonic_and_positive() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello") == 1
    assert estimate_tokens("hello world") > estimate_tokens("hello")


def test_estimate_tokens_charges_digits_and_symbols() -> None:
    assert estimate_tokens("1234567890") == 5
    assert estimate_tokens("{}") == 2


def test_estimate_state_tokens_adds_digit_pressure() -> None:
    text = '{"offset": 123456}'
    assert estimate_state_tokens(text) > estimate_tokens(text)


def test_line_range_rejects_inverted_bounds() -> None:
    assert LineRange(3, 5).line_count == 3
    assert LineRange(3, 5).to_dict()["line_count"] == 3
    with pytest.raises(ValueError):
        LineRange(0, 5)
    with pytest.raises(ValueError):
        LineRange(6, 5)


def test_tool_use_rejects_blank_identifiers() -> None:
    with pytest.raises(ValueError):
        ToolUse(" ", "grep")
    with pytest.raises(ValueError):
        ToolUse("t1", " ")
    with pytest.raises(ValueError):
        ToolResult(" ", "text")


def test_tool_use_arguments_are_immutable() -> None:
    use = ToolUse("t1", "grep", {"pattern": "x"})
    with pytest.raises(TypeError):
        use.arguments["pattern"] = "y"  # type: ignore[index]


def test_mutating_tools_are_recognized() -> None:
    assert ToolUse("t1", "apply_patch", {}).mutating
    assert ToolUse("t2", "Write_File", {}).mutating
    assert not ToolUse("t3", "read_file", {}).mutating


def test_message_rejects_unknown_role() -> None:
    with pytest.raises(ValueError):
        Message("tool", "text")


def test_message_emptiness_and_size() -> None:
    assert Message("assistant").empty
    message = Message(
        "assistant",
        "checking",
        (ToolUse("t1", "grep", {"pattern": "abc"}),),
    )
    assert not message.empty
    assert message.chars > len("checking")


def test_transcript_totals_include_results() -> None:
    messages = (
        Message("user", "fix the bug"),
        Message("assistant", "", (ToolUse("t1", "grep", {"pattern": "x"}),)),
        Message("user", "", (), (ToolResult("t1", "a\nb\nc"),)),
    )
    assert transcript_chars(messages) > 0
    assert transcript_tokens(messages) > 0
