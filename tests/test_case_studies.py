from __future__ import annotations

import json
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def test_real_context_change_manifest_is_pinned_and_selection_neutral() -> None:
    path = Path(__file__).parents[1] / "case-studies" / "cases.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    cases = value["cases"]

    assert 5 <= len(cases) <= 10
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["repository"] for case in cases}) >= 5
    assert any("Remove" in case["change"] for case in cases)
    assert any("add" in case["change"].casefold() for case in cases)
    for case in cases:
        assert len(case["base_commit"]) == 40
        assert len(case["candidate_commit"]) == 40
        assert case["base_commit"] != case["candidate_commit"]
        assert case["context_paths"]
        assert case["study_status"] == "static_ready"
        assert case["commit_url"].startswith(
            f"https://github.com/{case['repository']}/commit/"
        )


def test_checked_in_real_report_is_explicitly_unverified() -> None:
    path = (
        Path(__file__).parents[1]
        / "case-studies"
        / "reports"
        / "vscode-add-agents.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))

    assert value["case"]["repository"] == "microsoft/vscode"
    assert value["evidence"] == "observed/static"
    assert value["verified"] is False
    assert value["report"]["summary"]["delta_estimated_tokens"] == 68


def test_agent_study_tasks_are_pinned_with_auditable_validation_status() -> None:
    path = Path(__file__).parents[1] / "case-studies" / "cases.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    studies = value["agent_studies"]

    assert [study["id"] for study in studies] == [
        "browser-use",
        "infisical",
        "langfuse",
    ]
    assert sum(len(study["tasks"]) for study in studies) == 6
    assert value["agent_study_protocol"]["maximum_agent_runs"] == 36
    for study in studies:
        assert study["company_reference"].startswith("https://www.ycombinator.com/")
        for task in study["tasks"]:
            assert len(task["pre_fix_commit"]) == 40
            assert len(task["fixed_commit"]) == 40
            assert task["pre_fix_commit"] != task["fixed_commit"]
            assert task["target_paths"]
            assert task["status"] in {
                "ready_for_agent_run",
                "infrastructure_blocked_windows_vitest_filter",
            }


def test_candidate_transforms_are_deterministic_and_preserve_rules() -> None:
    module = _case_study_module("run.py")
    source = (
        "Keep this.\n<browser_use_docs>\nlarge docs\n</browser_use_docs>\nKeep that.\n"
    )

    bounded = module.remove_bounded_block(
        source,
        start="<browser_use_docs>",
        end="</browser_use_docs>",
    )
    scoped = module.remove_markdown_sections(
        "# Root\nA\n### Frontend\nremove\n### Backend\nkeep\n",
        ("### Frontend",),
    )

    assert bounded == "Keep this.\nKeep that.\n"
    assert scoped == "# Root\nA\n### Backend\nkeep\n"


def test_hidden_grader_is_removed_after_host_side_execution(tmp_path: Path) -> None:
    grade = Path(__file__).parents[1] / "case-studies" / "grade.py"
    fixture = tmp_path.parent / f"{tmp_path.name}-external-grader.py"
    fixture.write_text("print('hidden grader pass')\n", encoding="utf-8")
    destination = Path("tests") / "hidden.py"

    completed = subprocess.run(
        (
            sys.executable,
            str(grade),
            "--fixture",
            str(fixture),
            "--destination",
            str(destination),
            "--",
            sys.executable,
            str(destination),
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "hidden grader pass" in completed.stdout
    assert not (tmp_path / destination).exists()


def test_hidden_grader_can_replace_and_restore_an_existing_test(
    tmp_path: Path,
) -> None:
    grade = Path(__file__).parents[1] / "case-studies" / "grade.py"
    fixture = tmp_path.parent / f"{tmp_path.name}-replacement-grader.py"
    fixture.write_text("print('replacement pass')\n", encoding="utf-8")
    destination = Path("tests") / "existing.py"
    existing = tmp_path / destination
    existing.parent.mkdir(parents=True)
    existing.write_text("print('original')\n", encoding="utf-8")

    completed = subprocess.run(
        (
            sys.executable,
            str(grade),
            "--fixture",
            str(fixture),
            "--destination",
            str(destination),
            "--replace-existing",
            "--",
            sys.executable,
            str(destination),
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "replacement pass" in completed.stdout
    assert existing.read_text(encoding="utf-8") == "print('original')\n"


def _case_study_module(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "case-studies" / name
    spec = spec_from_file_location(f"contextlens_case_study_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
