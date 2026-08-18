from __future__ import annotations

from pathlib import Path

from contextlens.ci import StaticCiPolicy, evaluate_static_ci
from contextlens.minimize import (
    build_minimization_candidate,
    minimization_is_safe,
)
from contextlens.regression import RegressionVerdict
from contextlens.repository import RepositoryDiff, scan_repository_files


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
