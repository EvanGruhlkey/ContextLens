import json
import shutil
import subprocess
import sys

import pytest

from benchmarks.goal import analyze, command_for, grade, patch, prompt, usage
from evals.repository_cases import load_manifest


def row(policy, passed=True, tokens=100, status="completed", reads=1):
    return {
        "case": "task",
        "trial": 0,
        "policy": policy,
        "status": status,
        "verified_success": passed,
        "input_tokens": tokens,
        "cached_input_tokens": 20,
        "uncached_input_tokens": tokens - 20,
        "output_tokens": 10,
        "total_tokens": tokens + 10,
        "agent_seconds": 1,
        "contextlens_calls": reads,
        "contextlens_read_calls": reads,
    }


def test_usage_sums_terminal_turns_without_double_counting_cache():
    event = {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 80,
            "output_tokens": 10,
        },
    }
    result = usage(
        [event, {"type": "item.completed", "usage": {"input_tokens": 999}}, event], True
    )
    assert result == {
        "input_tokens": 200,
        "cached_input_tokens": 160,
        "output_tokens": 20,
        "uncached_input_tokens": 40,
        "total_tokens": 220,
    }
    assert all(value is None for value in usage([event], False).values())
    with pytest.raises(ValueError, match="cached input exceeds"):
        usage(
            [
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 1, "cached_input_tokens": 2},
                }
            ],
            True,
        )


def test_goal_requires_lower_input_correct_patch_and_actual_tool_use():
    result = analyze([row("normal"), row("compact", tokens=50)], 1)
    assert result["paired_gross_input_reduction_percent"] == 50
    assert result["observed_sample_meets_goal"] is True
    assert result["statistical_quality_preservation_claim"] is None
    failed = analyze([row("normal"), row("compact", passed=False, tokens=50)], 1)
    assert failed["observed_quality_regressions"] == ["task"]
    assert failed["observed_sample_meets_goal"] is False
    assert failed["conditions"]["compact"]["input_tokens"] == 50
    unused = analyze([row("normal"), row("compact", reads=0)], 1)
    assert unused["observed_sample_meets_goal"] is None
    incomplete = analyze([row("normal"), row("compact", status="timeout")], 1)
    assert incomplete["complete_pairs"] == 0
    assert incomplete["observed_sample_meets_goal"] is None


def test_control_analysis_counts_controller_tokens_and_uptake():
    baseline = row("normal", tokens=100)
    control = row("control", tokens=60, reads=0)
    control.update(
        {
            "controller_calls": 2,
            "jev_input_tokens": 20,
            "jev_output_tokens": 5,
        }
    )
    result = analyze([baseline, control], 1, candidate_policy="control")
    assert result["paired_complete_input_reduction_percent"] == 20
    assert result["all_candidate_runs_used_controller"] is True
    assert result["observed_sample_meets_goal"] is True


def test_duplicate_attempt_is_rejected():
    with pytest.raises(ValueError, match="duplicate attempt"):
        analyze([row("normal"), row("normal")], 1)


def test_compact_configuration_is_on_demand_without_eager_evidence(tmp_path):
    normal = command_for(tmp_path, tmp_path / "state", "normal", "model", "codex")
    compact = command_for(tmp_path, tmp_path / "state", "compact", "model", "codex")
    assert not any("mcp_servers" in arg for arg in normal)
    assert "mcp_servers.contextlens.required=true" in compact
    assert any('"compact"' in arg and '"mcp"' in arg for arg in compact)
    text = prompt("Repair the bug", "compact")
    assert "Initial repository evidence" not in text
    assert "evidence_verify" not in text
    assert "Do not skip ContextLens entirely" in text
    assert "plugins" in normal and "skip_host_skill_discovery" in normal


def test_control_configuration_uses_the_jev_profile(tmp_path):
    control = command_for(tmp_path, tmp_path / "state", "control", "model", "codex")
    assert any('"jev"' in arg and '"mcp"' in arg for arg in control)
    text = prompt("Repair the bug", "control")
    assert "context_next" in text
    assert "context_observe" in text


def test_grading_replays_new_files_without_changing_base(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "module.py").write_text("value = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=base, check=True)
    subprocess.run(["git", "add", "."], cwd=base, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Benchmark",
            "-c",
            "user.email=benchmark@example.invalid",
            "commit",
            "-qm",
            "base",
        ],
        cwd=base,
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=base, text=True
    ).strip()
    manifest_path = tmp_path / "case.json"
    manifest_path.write_text(
        json.dumps(
            {
                "case_id": "test",
                "suite": "smoke",
                "repo": "owner/repo",
                "commit": revision,
                "task": "Fix value",
                "verification": {
                    "commands": [
                        [
                            sys.executable,
                            "-c",
                            "import module,helper;assert module.value == 2;"
                            "assert helper.ready",
                        ]
                    ]
                },
            }
        )
    )
    workspace = tmp_path / "workspace"
    shutil.copytree(base, workspace)
    (workspace / "module.py").write_text("value = 2\n")
    (workspace / "helper.py").write_text("ready = True\n")
    diff = patch(workspace)
    assert b"helper.py" in diff
    result = grade(base, tmp_path / "grading", diff, load_manifest(manifest_path))
    assert result["success"]
    assert (base / "module.py").read_text() == "value = 1\n"
    assert not (base / "helper.py").exists()
