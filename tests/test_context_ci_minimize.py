from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import contextlens.minimize as minimize_module
import contextlens.regression as regression_module
from contextlens.ci import StaticCiPolicy, build_ci_arguments, evaluate_static_ci
from contextlens.experiments import AgentOutcome, ReplayRequest
from contextlens.minimize import (
    build_minimization_candidate,
    generate_minimization_edits,
    minimization_is_safe,
    minimize_repository,
)
from contextlens.regression import RegressionVerdict
from contextlens.repository import (
    RepositoryDiff,
    diff_effective_context,
    scan_repository_files,
)


class _PassingUsageAdapter:
    adapter_id = "passing-usage-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        tokens = sum(source.token_count or 0 for source in request.context)
        return AgentOutcome(
            output_text="ok",
            input_tokens=100 + tokens,
            output_tokens=5,
        )


def test_static_ci_has_stable_failure_exit_code(tmp_path: Path) -> None:
    base = scan_repository_files(
        tmp_path,
        {"AGENTS.md": "Run tests.\n"},
        revision="base",
    )
    candidate = scan_repository_files(
        tmp_path,
        {"AGENTS.md": "Run tests.\n" + ("Explain every decision.\n" * 20)},
    )
    report = RepositoryDiff(
        root=str(tmp_path),
        base_ref="base",
        base=base,
        candidate=candidate,
        sources=(),
    )

    result = evaluate_static_ci(
        report,
        StaticCiPolicy(max_context_increase_fraction=0.10),
    )

    assert not result.passed
    assert result.exit_code == 4
    assert result.mode == "static"


def test_static_ci_limits_new_context_when_base_is_empty(tmp_path: Path) -> None:
    base = scan_repository_files(tmp_path, {}, revision="base")
    candidate = scan_repository_files(
        tmp_path,
        {"AGENTS.md": "Always run tests before committing changes.\n"},
    )
    report = RepositoryDiff(
        root=str(tmp_path),
        base_ref="base",
        base=base,
        candidate=candidate,
        sources=(),
    )

    result = evaluate_static_ci(
        report,
        StaticCiPolicy(max_context_increase_fraction=0.25),
    )

    assert not result.passed
    assert "from zero" in result.reasons[0]


def test_static_ci_reports_effective_context_without_implicitly_gating_it(
    tmp_path: Path,
) -> None:
    base = scan_repository_files(
        tmp_path,
        {
            "AGENTS.md": "Root.\n",
            "backend/AGENTS.md": "Backend.\n",
        },
        revision="base",
    )
    candidate = scan_repository_files(
        tmp_path,
        {"AGENTS.md": "Root.\n"},
    )
    report = RepositoryDiff(str(tmp_path), "base", base, candidate, ())
    effective = diff_effective_context(report, ["backend/api.py"], provider="codex")

    result = evaluate_static_ci(
        report,
        StaticCiPolicy(),
        effective_context=effective,
    )

    assert result.passed
    assert result.report["effective_context"]["delta_estimated_tokens"] < 0


def test_minimizer_generates_candidate_but_rejects_unsafe_verdict(
    tmp_path: Path,
) -> None:
    repeated = "Always run the focused tests before changing shared code."
    scan = scan_repository_files(
        tmp_path,
        {
            "AGENTS.md": f"- {repeated}\n",
            "packages/api/AGENTS.md": f"- {repeated}\n",
        },
    )

    candidate = build_minimization_candidate(scan)

    assert candidate.edits
    assert candidate.saved_tokens > 0
    assert not minimization_is_safe(
        RegressionVerdict.FAIL,
        saved_tokens=candidate.saved_tokens,
    )
    assert not minimization_is_safe(
        RegressionVerdict.WARN,
        saved_tokens=candidate.saved_tokens,
    )
    assert minimization_is_safe(
        RegressionVerdict.PASS,
        saved_tokens=candidate.saved_tokens,
    )


def test_minimizer_generates_remove_and_scope_candidates(tmp_path: Path) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "app.ts").write_text("export {};\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "- Read `src/deleted.py` before changing the parser.\n"
        "- In frontend/ always run the component tests before editing UI code.\n",
        encoding="utf-8",
    )

    edits = generate_minimization_edits(
        scan_repository_files(
            tmp_path,
            {"AGENTS.md": (tmp_path / "AGENTS.md").read_text(encoding="utf-8")},
            known_paths=frozenset({"AGENTS.md", "frontend/app.ts"}),
        )
    )

    assert {edit.operation for edit in edits} == {"remove", "scope"}
    scope = next(edit for edit in edits if edit.operation == "scope")
    assert scope.replacement_path == "frontend/AGENTS.md"


@pytest.mark.parametrize(
    "verdict",
    [
        RegressionVerdict.WARN,
        RegressionVerdict.INCONCLUSIVE,
        RegressionVerdict.FAIL,
    ],
)
def test_minimize_never_writes_patch_for_nonpass_and_never_mutates_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verdict: RegressionVerdict,
) -> None:
    repeated = "Always run focused tests before changing shared code."
    (tmp_path / "packages" / "api").mkdir(parents=True)
    root_context = tmp_path / "AGENTS.md"
    nested_context = tmp_path / "packages" / "api" / "AGENTS.md"
    root_context.write_text(f"- {repeated}\n", encoding="utf-8")
    nested_context.write_text(f"- {repeated}\n", encoding="utf-8")
    before = nested_context.read_text(encoding="utf-8")
    monkeypatch.setattr(
        minimize_module,
        "verify_context_candidate",
        lambda *args, **kwargs: SimpleNamespace(verdict=verdict),
    )

    report = minimize_repository(tmp_path, config_path=tmp_path / "evals.json")

    assert not report.recommended
    assert report.patch is None
    assert nested_context.read_text(encoding="utf-8") == before


def test_minimize_emits_patch_only_after_isolated_and_final_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = "Always run focused tests before changing shared code."
    (tmp_path / "nested").mkdir()
    (tmp_path / "AGENTS.md").write_text(f"- {repeated}\n", encoding="utf-8")
    nested = tmp_path / "nested" / "AGENTS.md"
    nested.write_text(f"- {repeated}\n", encoding="utf-8")
    calls: list[str] = []

    def passing(*args: object, **kwargs: object) -> object:
        calls.append(str(kwargs["base_label"]))
        return SimpleNamespace(verdict=RegressionVerdict.PASS)

    monkeypatch.setattr(minimize_module, "verify_context_candidate", passing)

    report = minimize_repository(tmp_path, config_path=tmp_path / "evals.json")

    assert report.recommended
    assert report.patch is not None
    assert len(calls) == 2
    assert calls[-1] == "current-context-final-combination"
    assert nested.read_text(encoding="utf-8") == f"- {repeated}\n"


def test_combined_candidate_regression_rejects_individually_safe_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = "Always run focused tests before changing shared code."
    for directory in ("one", "two"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "AGENTS.md").write_text(
            f"- {repeated}\n", encoding="utf-8"
        )
    (tmp_path / "AGENTS.md").write_text(f"- {repeated}\n", encoding="utf-8")
    verdicts = iter(
        [RegressionVerdict.PASS, RegressionVerdict.PASS, RegressionVerdict.FAIL]
    )
    monkeypatch.setattr(
        minimize_module,
        "verify_context_candidate",
        lambda *args, **kwargs: SimpleNamespace(verdict=next(verdicts)),
    )

    report = minimize_repository(tmp_path, config_path=tmp_path / "evals.json")

    assert len(report.experiments) == 2
    assert all(experiment.accepted for experiment in report.experiments)
    assert not report.recommended
    assert report.patch is None


def test_minimization_verification_uses_task_effective_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = "Always run focused tests before changing shared code."
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend" / "api.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "frontend" / "app.ts").write_text("export {};\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(f"- {repeated}\n", encoding="utf-8")
    (tmp_path / "backend" / "AGENTS.md").write_text(f"- {repeated}\n", encoding="utf-8")
    (tmp_path / "frontend" / "AGENTS.md").write_text(
        "Frontend-only rules that must not enter a backend task.\n",
        encoding="utf-8",
    )
    config = tmp_path / "evals.json"
    config.write_text(
        json.dumps(
            {
                "trials": 2,
                "context_provider": "codex",
                "agent": {
                    "type": "subprocess",
                    "command": ["fixture"],
                    "provider": "fixture",
                    "model": "deterministic",
                },
                "tasks": [
                    {
                        "id": "backend-task",
                        "instruction": "Return ok.",
                        "expected_output": "ok",
                        "target_paths": ["backend/api.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        regression_module,
        "_agent_factory",
        lambda value: _PassingUsageAdapter,
    )

    report = minimize_repository(tmp_path, config_path=config)

    assert report.recommended
    assert report.verification is not None
    assert report.verification.experiments
    assert all(
        experiment.verification is not None and experiment.verification.experiments
        for experiment in report.experiments
        if experiment.accepted
    )
    for trial in report.verification.trials:
        assert "frontend/AGENTS.md" not in trial.context_source_paths
        assert trial.context_provider == "codex"
    assert report.verification.candidate.initial_context_tokens < (
        report.verification.base.initial_context_tokens
    )


def test_ci_argument_generation_is_shell_and_platform_independent() -> None:
    arguments = build_ci_arguments(
        mode="verified",
        base="origin/main",
        provider="codex",
        targets=("src/api.py", "src/worker.py"),
        max_context_increase="0.25",
    )

    assert arguments[:3] == ("ci", "--mode", "verified")
    assert arguments.count("--target") == 2
    assert "src/api.py" in arguments
    assert "--config" in arguments
