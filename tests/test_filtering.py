from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from contextlens.filtering import (
    MAX_CHUNKS,
    STATE_CONTEXT,
    OutputPruner,
    PruneConfig,
    PruneRequest,
    PruneSession,
    classify_output,
    looks_binary,
    protected_line,
    question_for,
    render,
    split_chunks,
)
from contextlens.jev import Evaluation, JevError
from contextlens.models import OutputCategory
from contextlens.receipts import ReceiptStore


class FakeJudge:
    def __init__(self, probability: float = 0.0) -> None:
        self.probability = probability
        self.states: list[Mapping[str, Any]] = []
        self.batches: list[list[str]] = []

    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation:
        self.states.append(state)
        self.batches.append(sorted(questions))
        return Evaluation(
            {name: self.probability for name in questions}, "fake", 60, 12, "0.002", 3.0
        )


class BrokenJudge:
    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation:
        raise JevError("gateway down")


def noise(lines: int = 900, prefix: str = "progress") -> str:
    return "\n".join(
        f"{prefix} step {index} downloading package" for index in range(lines)
    )


def pruner(tmp_path: Path, judge: object, **kwargs: Any) -> OutputPruner:
    return OutputPruner(
        ReceiptStore(tmp_path),
        judge=judge,  # type: ignore[arg-type]
        config=PruneConfig(minimum_tokens=100, **kwargs),
    )


def test_protected_lines_cover_diagnostics_and_results() -> None:
    assert protected_line("E   AssertionError: boom")
    assert protected_line("WARNING: deprecated call")
    assert protected_line("Traceback (most recent call last)")
    assert protected_line("=== 3 failed, 5 passed in 2.1s ===")
    assert protected_line("exit: 1")
    assert not protected_line("collecting tests")


def test_split_chunks_covers_every_line_once() -> None:
    output = "\n".join(str(index) for index in range(103))
    chunks = split_chunks(output, 20)
    assert len(chunks) == 6
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == 103
    assert "\n".join(chunk.text for chunk in chunks) == output


def test_split_chunks_caps_the_chunk_count() -> None:
    output = "\n".join("x" for _ in range(10_000))
    assert len(split_chunks(output, 1)) <= MAX_CHUNKS


def test_split_chunks_breaks_up_very_long_lines() -> None:
    chunks = split_chunks("y" * 9_000, 1)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 2_000 for chunk in chunks)


def test_binary_detection() -> None:
    assert looks_binary("abc\x00def")
    assert looks_binary("\x01\x02\x03\x04\x05")
    assert not looks_binary("plain text\nwith newlines\n")
    assert not looks_binary("")


@pytest.mark.parametrize(
    ("tool", "arguments", "output", "expected"),
    [
        ("grep", {}, "a.py:1:x", OutputCategory.SEARCH),
        ("read_file", {"path": "src/a.py"}, "def f(): pass", OutputCategory.SOURCE),
        ("read_file", {"path": "notes.txt"}, "hello", OutputCategory.UNKNOWN),
        ("shell", {"command": "pytest -q"}, "....", OutputCategory.BUILD),
        ("shell", {"command": "echo hi"}, "hi", OutputCategory.UNKNOWN),
        ("shell", {}, json.dumps({"a": [1, 2]}), OutputCategory.STRUCTURED),
        ("shell", {}, "diff --git a/x b/x\n--- a/x\n", OutputCategory.STRUCTURED),
        ("shell", {}, "=== 1 failed in 1s ===", OutputCategory.BUILD),
    ],
)
def test_classification(
    tool: str, arguments: dict[str, Any], output: str, expected: OutputCategory
) -> None:
    request = PruneRequest(task="t", output=output, tool=tool, arguments=arguments)
    assert classify_output(request) is expected


def test_question_is_a_single_relevance_boolean() -> None:
    chunk = split_chunks("a\nb\nc\nd", 2)[0]
    question = question_for(chunk)
    assert set(question) == {chunk.id}
    assert question[chunk.id]["type"] == "boolean"
    assert "criteria" in question[chunk.id]


def test_render_marks_each_omitted_run_once() -> None:
    chunks = split_chunks("\n".join(str(index) for index in range(60)), 10)
    text, omitted = render(chunks, {chunks[0].id, chunks[-1].id}, "cl_" + "a" * 24)
    assert text.count("contextlens omitted") == 1
    assert len(omitted) == 1
    assert omitted[0].start_line == 11
    assert omitted[0].end_line == 50
    assert "cl_" + "a" * 24 in text


def test_small_output_passes_through_without_touching_jev(tmp_path: Path) -> None:
    judge = FakeJudge()
    outcome = OutputPruner(
        ReceiptStore(tmp_path), judge=judge, config=PruneConfig(minimum_tokens=10_000)
    ).prune(PruneRequest(task="t", output="small output", tool="shell"))
    assert not outcome.pruned
    assert outcome.reason == "below_minimum_tokens"
    assert outcome.text == "small output"
    assert judge.states == []


def test_the_original_is_recoverable_even_when_nothing_is_pruned(
    tmp_path: Path,
) -> None:
    receipts = ReceiptStore(tmp_path)
    outcome = OutputPruner(
        receipts, judge=FakeJudge(), config=PruneConfig(minimum_tokens=10_000)
    ).prune(PruneRequest(task="t", output="small", tool="shell"))
    assert receipts.read(outcome.receipt_id) == "small"


def test_binary_and_structured_output_pass_through(tmp_path: Path) -> None:
    binary = OutputPruner(
        ReceiptStore(tmp_path),
        judge=FakeJudge(),
        config=PruneConfig(minimum_tokens=1),
    ).prune(PruneRequest(task="t", output="\x00" + noise(), tool="shell"))
    assert binary.reason == "binary"
    document = pruner(tmp_path, FakeJudge()).prune(
        PruneRequest(
            task="t",
            output=json.dumps({"items": list(range(4000))}),
            tool="shell",
        )
    )
    assert document.reason == "structured"


def test_short_outputs_with_few_chunks_pass_through(tmp_path: Path) -> None:
    outcome = pruner(tmp_path, FakeJudge(), chunk_lines=1000).prune(
        PruneRequest(task="t", output=noise(200), tool="shell")
    )
    assert outcome.reason == "few_chunks"


def test_noise_is_dropped_and_stays_recoverable(tmp_path: Path) -> None:
    receipts = ReceiptStore(tmp_path)
    output = noise()
    outcome = OutputPruner(
        receipts, judge=FakeJudge(0.0), config=PruneConfig(minimum_tokens=100)
    ).prune(PruneRequest(task="find the failing test", output=output, tool="shell"))
    assert outcome.pruned
    assert outcome.reason == "pruned"
    assert outcome.kept_chunks < outcome.chunks
    assert outcome.retained_tokens < outcome.original_tokens
    assert outcome.removed_tokens > 0
    assert 0 < outcome.reduction < 1
    assert receipts.read(outcome.receipt_id) == output


def test_failures_survive_a_drop_everything_verdict(tmp_path: Path) -> None:
    lines = noise().splitlines()
    lines[500] = "E   AssertionError: expected 3 got 4"
    output = "\n".join(lines)
    outcome = pruner(tmp_path, FakeJudge(0.0)).prune(
        PruneRequest(task="fix the assertion", output=output, tool="shell")
    )
    assert outcome.pruned
    assert "AssertionError: expected 3 got 4" in outcome.text


def test_first_and_last_chunks_always_survive(tmp_path: Path) -> None:
    output = noise()
    outcome = pruner(tmp_path, FakeJudge(0.0)).prune(
        PruneRequest(task="t", output=output, tool="shell")
    )
    lines = output.splitlines()
    assert lines[0] in outcome.text
    assert lines[-1] in outcome.text


def test_uncertain_scores_keep_content(tmp_path: Path) -> None:
    outcome = pruner(tmp_path, FakeJudge(0.2)).prune(
        PruneRequest(task="t", output=noise(), tool="shell")
    )
    assert not outcome.pruned
    assert outcome.reason == "kept_all"


def test_confident_keeps_return_the_original(tmp_path: Path) -> None:
    outcome = pruner(tmp_path, FakeJudge(0.99)).prune(
        PruneRequest(task="t", output=noise(), tool="shell")
    )
    assert outcome.reason == "kept_all"
    assert outcome.text == noise()


def test_pruning_fails_open_when_jev_fails(tmp_path: Path) -> None:
    output = noise()
    outcome = pruner(tmp_path, BrokenJudge()).prune(
        PruneRequest(task="t", output=output, tool="shell")
    )
    assert not outcome.pruned
    assert outcome.reason == "jev_unavailable"
    assert outcome.text == output


def test_pruning_fails_open_on_missing_answers(tmp_path: Path) -> None:
    class PartialJudge:
        def evaluate(
            self, state: Mapping[str, Any], questions: Mapping[str, Any]
        ) -> Evaluation:
            name = sorted(questions)[0]
            return Evaluation({name: 0.0}, "fake", 1, 1, None, 1.0)

    outcome = pruner(tmp_path, PartialJudge()).prune(
        PruneRequest(task="t", output=noise(), tool="shell")
    )
    assert outcome.reason == "jev_unavailable"


def test_state_holds_the_task_chunks_and_diagnostics(tmp_path: Path) -> None:
    judge = FakeJudge(0.0)
    lines = noise().splitlines()
    lines[10] = "ERROR: cannot resolve dependency"
    pruner(tmp_path, judge).prune(
        PruneRequest(
            task="resolve the dependency",
            output="\n".join(lines),
            tool="shell",
            arguments={"command": "npm ci"},
            focus="which dependency failed",
        )
    )
    state = judge.states[0]
    assert state["context"] == STATE_CONTEXT
    assert state["task"] == "resolve the dependency"
    assert state["focus"] == "which dependency failed"
    assert state["category"] == "build"
    assert "ERROR: cannot resolve dependency" in state["diagnostics"]
    assert "npm ci" in state["tool_call"]
    assert state["output"]


def test_large_output_is_split_into_several_bounded_states(tmp_path: Path) -> None:
    judge = FakeJudge(0.0)
    output = noise(4000)
    outcome = pruner(tmp_path, judge, max_state_tokens=2000).prune(
        PruneRequest(task="t", output=output, tool="shell")
    )
    assert len(judge.states) > 1
    assert all(len(state["output"]) < outcome.chunks for state in judge.states)
    scored = {name for batch in judge.batches for name in batch}
    assert len(scored) == outcome.chunks
    assert outcome.pruned


def test_chunks_that_cannot_fit_beside_the_task_are_left_unscored(
    tmp_path: Path,
) -> None:
    lines = [f"progress step {index}" for index in range(400)]
    lines[200] = "z" * 1_900
    request = PruneRequest(task="t", output="\n".join(lines), tool="shell")
    chunks = split_chunks(request.output, 1)
    groups = pruner(tmp_path, FakeJudge(), max_state_tokens=400)._state_groups(
        request, chunks, OutputCategory.UNKNOWN
    )
    scorable = {chunk.id for _state, group in groups for chunk in group}
    oversized = next(chunk for chunk in chunks if "z" * 1_900 in chunk.text)
    assert oversized.id not in scorable
    assert scorable


def test_output_that_cannot_be_scored_at_all_passes_through(tmp_path: Path) -> None:
    judge = FakeJudge(0.0)
    outcome = pruner(
        tmp_path, judge, chunk_lines=200, max_state_tokens=260
    ).prune(PruneRequest(task="t", output=noise(2000), tool="shell"))
    assert judge.states == []
    assert outcome.reason == "no_scoring_capacity"
    assert outcome.text == noise(2000)


def test_one_state_is_reused_across_several_batched_requests(
    tmp_path: Path,
) -> None:
    judge = FakeJudge(0.0)
    pruner(
        tmp_path, judge, max_state_tokens=25_000, max_request_tokens=4_100
    ).prune(PruneRequest(task="t", output=noise(300), tool="shell"))
    assert len(judge.batches) > 1
    assert all(judge.states[0] == state for state in judge.states)
    assert max(len(batch) for batch in judge.batches) < 15


def test_outcome_dictionary_reports_jev_usage_separately(tmp_path: Path) -> None:
    outcome = pruner(tmp_path, FakeJudge(0.0)).prune(
        PruneRequest(task="t", output=noise(), tool="shell")
    )
    payload = outcome.to_dict()
    assert payload["pruned"] is True
    assert payload["removed_tokens"] > 0
    assert payload["jev_input_tokens"] > 0
    assert payload["jev_requests"] >= 1


def test_session_totals_and_recovery(tmp_path: Path) -> None:
    receipts = ReceiptStore(tmp_path)
    session = PruneSession(
        receipts,
        task="  fix   the parser  ",
        judge=FakeJudge(0.0),
        config=PruneConfig(minimum_tokens=100),
    )
    assert session.task == "fix the parser"
    first = session.observe(noise(), tool="shell", arguments={"command": "npm ci"})
    session.observe("tiny", tool="grep")
    metrics = session.metrics()
    assert metrics["observations"] == 2
    assert metrics["prune_calls"] == 1
    assert metrics["tool_output_tokens_removed"] > 0
    assert metrics["tool_output_reduction_percent"] > 0
    assert metrics["jev_input_tokens"] > 0
    assert metrics["recovery_calls"] == 0
    assert session.recover(first.receipt_id) == noise()
    assert session.metrics()["recovery_calls"] == 1
    assert session.metrics()["recovered_tokens"] > 0


def test_session_requires_a_task(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PruneSession(ReceiptStore(tmp_path), task="  ")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum_tokens": -1},
        {"chunk_lines": 0},
        {"keep_threshold": 2.0},
        {"uncertain_keep_probability": -0.5},
        {"max_state_tokens": 0},
    ],
)
def test_config_validates_its_thresholds(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PruneConfig(**kwargs)


def test_config_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXTLENS_MIN_TOKENS", "42")
    monkeypatch.setenv("CONTEXTLENS_KEEP_THRESHOLD", "0.75")
    config = PruneConfig.from_env()
    assert config.minimum_tokens == 42
    assert config.keep_threshold == 0.75
    monkeypatch.setenv("CONTEXTLENS_MIN_TOKENS", "-1")
    with pytest.raises(ValueError):
        PruneConfig.from_env()
