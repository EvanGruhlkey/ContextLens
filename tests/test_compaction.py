from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from contextlens.compaction import (
    STATE_CONTEXT,
    CompactionConfig,
    apply_decisions,
    collect_interactions,
    compact_transcript,
    decide,
    fit_state,
    is_protected,
    questions_for,
    reduction_percent,
    task_from,
    truncated_result,
)
from contextlens.jev import Evaluation, JevError
from contextlens.models import Message, ToolResult, ToolUse, transcript_tokens
from contextlens.receipts import ReceiptStore


class FakeJudge:
    """Answers every question with a fixed probability per question prefix."""

    def __init__(self, call: float = 1.0, result: float = 1.0) -> None:
        self.call = call
        self.result = result
        self.states: list[Mapping[str, Any]] = []
        self.batches: list[list[str]] = []

    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation:
        self.states.append(state)
        self.batches.append(sorted(questions))
        probabilities = {
            name: self.call if name.startswith("call_") else self.result
            for name in questions
        }
        return Evaluation(probabilities, "fake", 10, 2, "0.001", 1.0)


class BrokenJudge:
    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation:
        raise JevError("gateway down")


def transcript(
    interactions: int = 14,
    *,
    result_chars: int = 3000,
    tool: str = "shell",
) -> tuple[Message, ...]:
    messages: list[Message] = [Message("user", "Fix the failing parser test.")]
    for index in range(interactions):
        identifier = f"call-{index}"
        messages.append(
            Message(
                "assistant",
                "",
                (ToolUse(identifier, tool, {"command": f"pytest -k case{index}"}),),
            )
        )
        messages.append(
            Message(
                "user",
                "",
                (),
                (ToolResult(identifier, f"output {index}\n" * (result_chars // 10)),),
            )
        )
    return tuple(messages)


def test_protection_covers_the_task_and_recent_messages() -> None:
    assert is_protected(0, 10, 4)
    assert is_protected(6, 10, 4)
    assert not is_protected(5, 10, 4)


def test_collect_interactions_pairs_calls_with_results() -> None:
    messages = transcript(6)
    config = CompactionConfig(preserve_recent_messages=2)
    interactions = collect_interactions(messages, config)
    assert [item.id for item in interactions] == [f"t{n}" for n in range(1, 7)]
    assert all(item.result_chars > 0 for item in interactions)
    assert interactions[-1].pinned and interactions[-1].pin_reason == "recent"
    assert not interactions[0].pinned


def test_calls_without_a_result_are_not_candidates() -> None:
    messages = (
        Message("user", "task"),
        Message("assistant", "", (ToolUse("t1", "grep", {}),)),
        Message("assistant", "still working"),
        Message("assistant", "done"),
    )
    assert collect_interactions(messages, CompactionConfig()) == ()


def test_edits_failures_and_pins_are_protected() -> None:
    messages = (
        Message("user", "task"),
        Message("assistant", "", (ToolUse("a", "apply_patch", {"patch": "x"}),)),
        Message("user", "", (), (ToolResult("a", "patch applied"),)),
        Message("assistant", "", (ToolUse("c", "grep", {"pattern": "x"}),)),
        Message("user", "", (), (ToolResult("c", "hit"),), pinned=True),
        Message("assistant", "", (ToolUse("d", "grep", {"pattern": "y"}),)),
        Message("user", "", (), (ToolResult("d", "hit"),)),
        Message("assistant", "", (ToolUse("b", "shell", {"command": "pytest"}),)),
        Message("user", "", (), (ToolResult("b", "boom", is_error=True),)),
        Message("assistant", "", (ToolUse("e", "grep", {"pattern": "z"}),)),
        Message("user", "", (), (ToolResult("e", "hit"),)),
    )
    config = CompactionConfig(preserve_recent_messages=2)
    reasons = {
        item.tool_use_id: item.pin_reason
        for item in collect_interactions(messages, config)
    }
    assert reasons["a"] == "edit"
    assert reasons["b"] == "recent_failure"
    assert reasons["c"] == "pinned"
    assert reasons["d"] is None
    assert reasons["e"] == "recent"


def test_questions_ask_only_about_the_call_and_its_result() -> None:
    interaction = collect_interactions(transcript(6), CompactionConfig(
        preserve_recent_messages=2
    ))[0]
    questions = questions_for(interaction)
    assert set(questions) == {f"call_{interaction.id}", f"result_{interaction.id}"}
    assert all(question["type"] == "boolean" for question in questions.values())


def test_decision_table_is_deterministic() -> None:
    config = CompactionConfig(keep_threshold=0.5)
    interaction = collect_interactions(
        transcript(6), CompactionConfig(preserve_recent_messages=2)
    )[0]
    assert decide(interaction, 0.9, 0.9, config).action == "keep"
    assert decide(interaction, 0.9, 0.1, config).action == "truncate"
    assert decide(interaction, 0.1, 0.1, config).action == "drop"


def test_pinned_interactions_are_kept_whatever_jev_says() -> None:
    interactions = collect_interactions(
        transcript(6), CompactionConfig(preserve_recent_messages=2)
    )
    pinned = next(item for item in interactions if item.pinned)
    decision = decide(pinned, 0.0, 0.0, CompactionConfig())
    assert decision.action == "keep"
    assert decision.reason == "recent"


def test_truncated_result_keeps_a_prefix_and_a_recovery_hint() -> None:
    text = "first line\n" + "noise\n" * 500
    truncated = truncated_result(
        text, is_error=False, head_chars=20, receipt_id="cl_" + "a" * 24
    )
    assert truncated.startswith(text[:20])
    assert "cl_" + "a" * 24 in truncated
    assert len(truncated) < len(text)


def test_short_results_are_not_truncated() -> None:
    text = "short"
    assert truncated_result(
        text, is_error=False, head_chars=300, receipt_id=None
    ) == text


def test_truncated_error_results_say_so() -> None:
    text = "boom\n" + "x" * 900
    truncated = truncated_result(
        text, is_error=True, head_chars=10, receipt_id=None
    )
    assert "failed tool result" in truncated
    assert "re-run the tool" in truncated


def test_apply_decisions_drops_calls_with_their_results() -> None:
    messages = transcript(4)
    config = CompactionConfig(preserve_recent_messages=0)
    interactions = collect_interactions(messages, config)
    decisions = tuple(decide(item, 0.0, 0.0, config) for item in interactions)
    rebuilt = apply_decisions(messages, decisions, interactions, config)
    assert all(not message.tool_uses for message in rebuilt)
    assert all(not message.tool_results for message in rebuilt)
    assert rebuilt[0].text == "Fix the failing parser test."


def test_apply_decisions_returns_untouched_messages_unchanged() -> None:
    messages = transcript(4)
    config = CompactionConfig(preserve_recent_messages=0)
    interactions = collect_interactions(messages, config)
    decisions = tuple(decide(item, 1.0, 1.0, config) for item in interactions)
    assert apply_decisions(messages, decisions, interactions, config) == messages


def test_apply_decisions_records_a_receipt_for_truncated_output(
    tmp_path: Path,
) -> None:
    messages = transcript(4)
    config = CompactionConfig(preserve_recent_messages=0, truncate_head_chars=30)
    interactions = collect_interactions(messages, config)
    decisions = tuple(decide(item, 1.0, 0.0, config) for item in interactions)
    receipts = ReceiptStore(tmp_path)
    rebuilt = apply_decisions(messages, decisions, interactions, config, receipts)
    truncated = [
        result for message in rebuilt for result in message.tool_results
    ]
    assert truncated and all(item.receipt_id for item in truncated)
    original = next(
        result
        for message in messages
        for result in message.tool_results
        if result.tool_use_id == truncated[0].tool_use_id
    )
    assert receipts.read(str(truncated[0].receipt_id)) == original.text


def test_fit_state_shrinks_until_the_ceiling_is_met() -> None:
    messages = transcript(20, result_chars=40_000)
    config = CompactionConfig(max_state_tokens=1500, preserve_recent_messages=2)
    interactions = collect_interactions(messages, config)
    state, tokens, stage = fit_state(messages, interactions, config, "task")
    assert state["context"] == STATE_CONTEXT
    assert tokens <= config.max_state_tokens or stage == "over budget"


def test_fit_state_sends_descriptors_not_results() -> None:
    messages = transcript(6, result_chars=5000)
    config = CompactionConfig(preserve_recent_messages=2)
    interactions = collect_interactions(messages, config)
    state, _tokens, stage = fit_state(messages, interactions, config, "task")
    assert stage == "full"
    serialized = repr(state)
    assert "output 0\noutput 0" not in serialized
    assert "result_chars" in serialized


def test_task_from_uses_the_first_and_newest_user_prompts() -> None:
    messages = (
        Message("user", "original task"),
        Message("assistant", "ok"),
        Message("user", "also keep the public api"),
    )
    assert task_from(messages).startswith("original task")
    assert "public api" in task_from(messages)
    assert task_from((Message("assistant", "hi"),)) == "unknown task"


def test_small_transcripts_pass_through() -> None:
    judge = FakeJudge()
    result = compact_transcript(transcript(2), judge, config=CompactionConfig())
    assert not result.compacted
    assert result.reason == "below_trigger_tokens"
    assert judge.states == []


def test_compaction_drops_stale_interactions() -> None:
    messages = transcript(14)
    judge = FakeJudge(call=0.05, result=0.05)
    result = compact_transcript(
        messages,
        judge,
        config=CompactionConfig(trigger_tokens=100, preserve_recent_messages=4),
    )
    assert result.compacted
    assert result.dropped > 0
    assert result.tokens_after < result.tokens_before
    assert result.messages_after < result.messages_before
    assert reduction_percent(result) > 0
    assert result.usage.requests >= 1
    assert result.usage.input_tokens > 0


def test_compaction_truncates_when_only_the_call_still_matters() -> None:
    messages = transcript(14)
    result = compact_transcript(
        messages,
        FakeJudge(call=0.95, result=0.05),
        config=CompactionConfig(trigger_tokens=100, preserve_recent_messages=4),
    )
    assert result.compacted
    assert result.truncated > 0
    assert result.dropped == 0
    assert result.messages_after == result.messages_before


def test_compaction_keeps_everything_when_jev_says_keep() -> None:
    messages = transcript(14)
    result = compact_transcript(
        messages,
        FakeJudge(call=0.95, result=0.95),
        config=CompactionConfig(trigger_tokens=100, preserve_recent_messages=4),
    )
    assert not result.compacted
    assert result.reason == "reduction_too_small"
    assert result.messages == messages


def test_compaction_preserves_the_task_and_recent_messages() -> None:
    messages = transcript(14)
    result = compact_transcript(
        messages,
        FakeJudge(call=0.0, result=0.0),
        config=CompactionConfig(trigger_tokens=100, preserve_recent_messages=6),
    )
    assert result.messages[0] == messages[0]
    assert result.messages[-6:] == messages[-6:]


def test_compaction_fails_open_when_jev_fails() -> None:
    messages = transcript(14)
    result = compact_transcript(
        messages,
        BrokenJudge(),
        config=CompactionConfig(trigger_tokens=100, preserve_recent_messages=4),
    )
    assert not result.compacted
    assert result.reason == "jev_unavailable"
    assert result.messages == messages
    assert transcript_tokens(result.messages) == result.tokens_before


def test_compaction_fails_open_on_invalid_answers() -> None:
    class PartialJudge:
        def evaluate(
            self, state: Mapping[str, Any], questions: Mapping[str, Any]
        ) -> Evaluation:
            name = sorted(questions)[0]
            return Evaluation({name: 0.0}, "fake", 1, 1, None, 1.0)

    result = compact_transcript(
        transcript(14),
        PartialJudge(),
        config=CompactionConfig(trigger_tokens=100, preserve_recent_messages=4),
    )
    assert not result.compacted
    assert result.reason == "jev_unavailable"


def test_compaction_stops_when_nothing_is_eligible() -> None:
    messages = transcript(14)
    result = compact_transcript(
        messages,
        FakeJudge(),
        config=CompactionConfig(trigger_tokens=100, preserve_recent_messages=100),
    )
    assert result.reason == "nothing_eligible"


def test_compaction_batches_questions_under_the_request_limit() -> None:
    judge = FakeJudge(call=0.0, result=0.0)
    compact_transcript(
        transcript(30),
        judge,
        config=CompactionConfig(
            trigger_tokens=100,
            preserve_recent_messages=2,
            max_state_tokens=25_000,
            max_request_tokens=5800,
        ),
    )
    assert len(judge.batches) > 1
    assert all(judge.states[0] == state for state in judge.states)


def test_result_dictionary_reports_the_pass() -> None:
    result = compact_transcript(
        transcript(14),
        FakeJudge(call=0.0, result=0.0),
        config=CompactionConfig(trigger_tokens=100, preserve_recent_messages=4),
    )
    payload = result.to_dict()
    assert payload["compacted"] is True
    assert payload["tokens_removed"] > 0
    assert payload["jev_requests"] >= 1
    assert len(payload["decisions"]) == result.interactions


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trigger_tokens": -1},
        {"preserve_recent_messages": -1},
        {"keep_threshold": 1.5},
        {"truncate_head_chars": -1},
        {"minimum_reduction": 2.0},
        {"max_state_tokens": 0},
    ],
)
def test_config_validates_its_thresholds(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CompactionConfig(**kwargs)
