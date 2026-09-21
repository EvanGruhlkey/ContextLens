from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from benchmarks.agent import (
    CONDITIONS,
    TOOL_SCHEMAS,
    Answer,
    RunMetrics,
    ToolCall,
    coding_prompt,
    response_input,
    run_agent,
    workspace_tools,
)
from benchmarks.offline import LocalJudge, build_repository
from benchmarks.offline import run as run_offline
from benchmarks.report import analyze, comma, markdown
from benchmarks.tasks import load_task, load_tasks, verify
from contextlens.compaction import CompactionConfig
from contextlens.filtering import PruneConfig
from contextlens.jev import Evaluation
from contextlens.models import Message, ToolResult, ToolUse


class DropJudge:
    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation:
        return Evaluation({name: 0.0 for name in questions}, "fake", 7, 3, "0.01", 1.0)


def noisy_repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "log.txt").write_text(
        "\n".join(f"progress step {index} downloading" for index in range(1200)),
        encoding="utf-8",
    )
    return root


def solver_for(script: Sequence[ToolCall | Answer]) -> Any:
    state = {"step": 0}

    def solver(messages: Sequence[Message]) -> ToolCall | Answer:
        del messages
        step = state["step"]
        state["step"] = step + 1
        return script[step] if step < len(script) else Answer("done")

    return solver


def test_frozen_tasks_load_and_pin_a_commit() -> None:
    tasks = load_tasks()
    assert len(tasks) >= 10
    for task in tasks:
        assert len(task.commit) == 40
        assert task.verification
        assert task.gold_patch.is_file()
        public = task.public_value()
        assert "verification" not in public
        assert public["verification_commands"] == len(task.verification)


def test_manifest_validation_rejects_bad_input(tmp_path: Path) -> None:
    path = tmp_path / "case.json"
    path.write_text(
        json.dumps(
            {
                "case_id": "x",
                "repo": "not a repo",
                "commit": "a" * 40,
                "task": "t",
                "verification": {"commands": [["true"]]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_task(path)
    path.write_text(
        json.dumps(
            {
                "case_id": "x",
                "repo": "a/b",
                "commit": "short",
                "task": "t",
                "verification": {"commands": [["true"]]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_task(path)


def test_verify_reports_failed_and_broken_commands(tmp_path: Path) -> None:
    assert verify(tmp_path, (("true",),))["success"] is True
    assert verify(tmp_path, (("false",),))["success"] is False
    broken = verify(tmp_path, (("definitely-not-a-command",),))
    assert broken["success"] is False
    assert broken["commands"][0]["returncode"] is None


def test_tool_schemas_match_the_tool_set() -> None:
    assert {schema["name"] for schema in TOOL_SCHEMAS} == {
        "read_file",
        "grep",
        "shell",
        "apply_patch",
        "recover",
    }


def test_prompt_never_mentions_contextlens() -> None:
    prompt = coding_prompt("fix the parser").lower()
    assert "contextlens" not in prompt
    assert "jev" not in prompt
    assert "prune" not in prompt


def test_tools_stay_inside_the_workspace(tmp_path: Path) -> None:
    workspace = noisy_repository(tmp_path / "repo")
    tools = workspace_tools(workspace)
    assert "progress step 0" in tools["read_file"]({"path": "log.txt"})
    assert tools["read_file"]({"path": "../escape.txt"}).startswith("Path is outside")
    assert tools["read_file"]({"path": "missing.txt"}).startswith("File not found")
    assert tools["read_file"]({}).startswith("read_file requires")
    assert tools["read_file"](
        {"path": "log.txt", "start_line": 2, "end_line": 3}
    ).count("\n") <= 2
    assert tools["grep"]({}).startswith("grep requires")
    assert "log.txt" in tools["grep"]({"pattern": "progress step 5 "})
    assert tools["grep"]({"pattern": "zzz-not-present"}) == "(no matches)"
    assert tools["shell"]({}).startswith("shell requires")
    assert "exit=0" in tools["shell"]({"command": "echo hi"})
    assert tools["apply_patch"]({}).startswith("apply_patch requires")


def test_response_input_carries_calls_and_outputs() -> None:
    messages = (
        Message("user", "task"),
        Message("assistant", "", (ToolUse("c1", "grep", {"pattern": "x"}),)),
        Message("user", "", (), (ToolResult("c1", "hit"),)),
        Message("assistant", "answer"),
    )
    items = response_input(messages)
    assert [item.get("type") or item["role"] for item in items] == [
        "user",
        "function_call",
        "function_call_output",
        "assistant",
    ]
    assert json.loads(items[1]["arguments"]) == {"pattern": "x"}
    assert items[2]["output"] == "hit"


def test_baseline_injects_raw_output(tmp_path: Path) -> None:
    workspace = noisy_repository(tmp_path / "repo")
    metrics, transcript = run_agent(
        workspace,
        "read the log",
        "baseline",
        state=tmp_path / "state",
        solver=solver_for([ToolCall("read_file", {"path": "log.txt"}, "c1")]),
        judge=DropJudge(),
        max_turns=4,
    )
    payload = metrics.to_dict()
    assert payload["status"] == "completed"
    assert payload["raw_tool_output_tokens"] == payload["injected_tool_output_tokens"]
    assert payload["tool_output_tokens_removed"] == 0
    assert payload["jev_requests"] == 0
    assert "progress step 900" in transcript[2].tool_results[0].text


def test_live_pruning_shrinks_tool_output(tmp_path: Path) -> None:
    workspace = noisy_repository(tmp_path / "repo")
    metrics, transcript = run_agent(
        workspace,
        "read the log",
        "live_pruning",
        state=tmp_path / "state",
        solver=solver_for([ToolCall("read_file", {"path": "log.txt"}, "c1")]),
        judge=DropJudge(),
        prune_config=PruneConfig(minimum_tokens=100),
        max_turns=4,
    )
    payload = metrics.to_dict()
    assert payload["tool_output_tokens_removed"] > 0
    assert payload["tool_output_reduction_percent"] > 0
    assert payload["jev_requests"] > 0
    assert payload["jev_input_tokens"] > 0
    assert "contextlens omitted" in transcript[2].tool_results[0].text


def test_the_agent_can_recover_omitted_output(tmp_path: Path) -> None:
    workspace = noisy_repository(tmp_path / "repo")
    original = (workspace / "log.txt").read_text(encoding="utf-8")

    def solver(messages: Sequence[Message]) -> ToolCall | Answer:
        for message in reversed(messages):
            for result in message.tool_results:
                if "receipt=" in result.text:
                    handle = result.text.split("receipt=")[1].split("]")[0]
                    return ToolCall("recover", {"receipt": handle}, "c2")
                if result.text == original:
                    return Answer("recovered")
        return ToolCall("read_file", {"path": "log.txt"}, "c1")

    metrics, transcript = run_agent(
        workspace,
        "read the log",
        "live_pruning",
        state=tmp_path / "state",
        solver=solver,
        judge=DropJudge(),
        prune_config=PruneConfig(minimum_tokens=100),
        max_turns=6,
    )
    payload = metrics.to_dict()
    assert payload["recovery_calls"] == 1
    assert payload["recovered_tokens"] > 0
    assert any(
        result.text == original
        for message in transcript
        for result in message.tool_results
    )


def test_bad_recovery_handles_do_not_break_the_run(tmp_path: Path) -> None:
    workspace = noisy_repository(tmp_path / "repo")
    metrics, transcript = run_agent(
        workspace,
        "read the log",
        "live_pruning",
        state=tmp_path / "state",
        solver=solver_for(
            [
                ToolCall("recover", {}, "c1"),
                ToolCall("recover", {"receipt": "cl_" + "0" * 24}, "c2"),
                ToolCall("nonexistent", {}, "c3"),
            ]
        ),
        judge=DropJudge(),
        max_turns=6,
    )
    assert metrics.to_dict()["status"] == "completed"
    texts = [
        result.text for message in transcript for result in message.tool_results
    ]
    assert texts[0].startswith("recover requires")
    assert texts[1].startswith("recover failed")
    assert texts[2].startswith("Tool unavailable")


def test_compaction_shrinks_the_transcript(tmp_path: Path) -> None:
    workspace = noisy_repository(tmp_path / "repo")
    script = [
        ToolCall("read_file", {"path": "log.txt"}, f"c{index}") for index in range(8)
    ]
    plain, plain_transcript = run_agent(
        workspace,
        "read the log",
        "live_pruning",
        state=tmp_path / "plain",
        solver=solver_for(script),
        judge=DropJudge(),
        prune_config=PruneConfig(minimum_tokens=10_000_000),
        max_turns=12,
    )
    compacted, compacted_transcript = run_agent(
        workspace,
        "read the log",
        "full_contextlens",
        state=tmp_path / "compacted",
        solver=solver_for(script),
        judge=DropJudge(),
        prune_config=PruneConfig(minimum_tokens=10_000_000),
        compaction_config=CompactionConfig(
            trigger_tokens=2_000, preserve_recent_messages=4
        ),
        max_turns=12,
    )
    first = compacted.to_dict()
    assert first["compaction_events"] > 0
    assert first["compaction_tokens_removed"] > 0
    assert first["final_transcript_tokens"] < plain.to_dict()[
        "final_transcript_tokens"
    ]
    assert first["agent_turns"] == plain.to_dict()["agent_turns"]
    assert compacted_transcript[0] == plain_transcript[0]


def test_unknown_conditions_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_agent(
            tmp_path,
            "task",
            "structural",
            state=tmp_path / "state",
            solver=solver_for([Answer("done")]),
        )


def test_missing_credentials_are_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    metrics, _transcript = run_agent(
        noisy_repository(tmp_path / "repo"),
        "task",
        "baseline",
        state=tmp_path / "state",
        max_turns=2,
    )
    assert metrics.to_dict()["status"] == "agent_unavailable"
    assert (tmp_path / "state" / "error.txt").is_file()


def test_turn_limit_is_reported(tmp_path: Path) -> None:
    metrics, _transcript = run_agent(
        noisy_repository(tmp_path / "repo"),
        "task",
        "baseline",
        state=tmp_path / "state",
        solver=solver_for([ToolCall("shell", {"command": "true"}, "c1")] * 5),
        max_turns=2,
    )
    assert metrics.to_dict()["status"] == "turn_limit"


def test_local_judge_protects_diagnostics_and_task_words() -> None:
    judge = LocalJudge("parse_amount")
    state = {
        "output": [
            {"id": "c1", "text": "E   AssertionError: boom"},
            {"id": "c2", "text": "progress step 3 downloading"},
            {"id": "c3", "text": "def parse_amount(text):"},
        ]
    }
    questions: dict[str, dict[str, Any]] = {"c1": {}, "c2": {}, "c3": {}}
    probabilities = judge.evaluate(state, questions).probabilities
    assert probabilities["c1"] > 0.5
    assert probabilities["c2"] < 0.1
    assert probabilities["c3"] > 0.5


def test_offline_harness_measures_all_four_conditions(tmp_path: Path) -> None:
    report = run_offline(tmp_path / "offline.json")
    rows = {row["condition"]: row for row in report["rows"]}
    assert set(rows) == set(CONDITIONS)
    baseline = rows["baseline"]
    pruned = rows["live_pruning"]
    compacted = rows["compaction_only"]
    both = rows["full_contextlens"]
    assert baseline["injected_tool_output_tokens"] == baseline[
        "raw_tool_output_tokens"
    ]
    assert pruned["injected_tool_output_tokens"] < baseline[
        "injected_tool_output_tokens"
    ]
    assert pruned["compaction_events"] == 0
    assert compacted["injected_tool_output_tokens"] == baseline[
        "injected_tool_output_tokens"
    ]
    assert compacted["compaction_events"] >= 1
    assert compacted["final_transcript_tokens"] < baseline[
        "final_transcript_tokens"
    ]
    assert both["compaction_events"] >= 1
    assert both["final_transcript_tokens"] < pruned["final_transcript_tokens"]
    assert report["analysis"]["complete"] is True
    assert report["analysis"]["conditions"]["compaction_only"][
        "tasks_with_compaction"
    ] == 1
    assert "no coding model" in report["note"].lower()


def test_build_repository_produces_a_committed_checkout(tmp_path: Path) -> None:
    build_repository(tmp_path / "repo")
    assert (tmp_path / "repo" / "library" / "amounts.py").is_file()
    assert (tmp_path / "repo" / ".git").is_dir()


def test_analysis_flags_an_incomplete_run() -> None:
    rows = [
        {
            "case": "a",
            "trial": 0,
            "condition": "baseline",
            "status": "completed",
            "verified_success": True,
            "agent_seconds": 1.0,
            "jev_cost": "0",
            **{field: 1 for field in ("input_tokens", "agent_turns")},
        }
    ]
    analysis = analyze(rows, 1)
    assert analysis["complete"] is False
    assert analysis["complete_pairs"] == 0
    assert analysis["conditions"]["live_pruning"]["attempts"] == 0


def test_analysis_rejects_duplicate_attempts() -> None:
    row = {
        "case": "a",
        "trial": 0,
        "condition": "baseline",
        "status": "completed",
        "verified_success": True,
        "agent_seconds": 1.0,
        "jev_cost": "0",
    }
    with pytest.raises(ValueError):
        analyze([row, dict(row)], 1)


def test_analysis_records_quality_regressions() -> None:
    def row(condition: str, passed: bool) -> dict[str, Any]:
        return {
            "case": "a",
            "trial": 0,
            "condition": condition,
            "status": "completed",
            "verified_success": passed,
            "agent_seconds": 1.0,
            "jev_cost": "0",
            "input_tokens": 100,
        }

    analysis = analyze(
        [
            row("baseline", True),
            row("live_pruning", False),
            row("compaction_only", True),
            row("full_contextlens", True),
        ],
        1,
    )
    assert analysis["quality_regressions"]["live_pruning"] == ["a"]
    assert analysis["quality_regressions"]["compaction_only"] == []
    assert analysis["quality_regressions"]["full_contextlens"] == []
    text = markdown(
        {
            "started_at": "now",
            "model": "m",
            "reasoning": "low",
            "trials": 1,
            "timeout": 300,
            "max_turns": 20,
            "tasks": [{"case_id": "a"}],
            "analysis": analysis,
        }
    )
    assert "1 regression(s) — a" in text
    assert "nothing a ContextLens condition could regress" not in text


def test_markdown_does_not_claim_preserved_fixes_when_baseline_failed() -> None:
    rows = [
        {
            "case": "a",
            "trial": 0,
            "condition": condition,
            "status": "agent_unavailable",
            "verified_success": False,
            "agent_seconds": 0.1,
            "jev_cost": "0",
            "input_tokens": 0,
        }
        for condition in CONDITIONS
    ]
    text = markdown(
        {
            "started_at": "now",
            "model": "m",
            "reasoning": "low",
            "trials": 1,
            "timeout": 300,
            "max_turns": 20,
            "tasks": [{"case_id": "a"}],
            "analysis": analyze(rows, 1),
        }
    )
    assert "nothing a ContextLens condition could regress" in text
    assert "No ContextLens condition lost" not in text


def test_markdown_reports_missing_numbers_as_not_available() -> None:
    rows = [
        {
            "case": "a",
            "trial": 0,
            "condition": condition,
            "status": "agent_unavailable",
            "verified_success": False,
            "agent_seconds": 0.1,
            "jev_cost": "0",
            "input_tokens": None,
        }
        for condition in CONDITIONS
    ]
    report = {
        "started_at": "now",
        "model": "m",
        "reasoning": "low",
        "trials": 1,
        "timeout": 300,
        "max_turns": 20,
        "tasks": [{"case_id": "a"}],
        "rows": rows,
        "analysis": analyze(rows, 1),
    }
    text = markdown(report)
    assert "n/a" in text
    assert "executed no coding model" in text
    assert "Jev never scored" not in text


def test_comma_formats_values() -> None:
    assert comma(None) == "n/a"
    assert comma(1234567) == "1,234,567"
    assert comma(1.25) == "1.2"
    assert comma(True) == "✅"
    assert comma("0.01") == "0.01"


def test_metrics_default_to_zero_not_none() -> None:
    payload = RunMetrics().to_dict()
    assert payload["input_tokens"] == 0
    assert payload["tool_output_reduction_percent"] == 0.0
    assert payload["jev_cost"] == "0"
