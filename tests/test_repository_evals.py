from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from contextlens.analysis import Measurement
from contextlens.trace import ContextSource, SourceKind
from evals.repository_cases import (
    acquire_repository,
    discover_context,
    load_manifest,
    smoke_manifests,
)
from evals.run import (
    FINAL_POLICIES,
    _deployment_rejection_reasons,
    _random_until,
)


def test_smoke_suite_has_two_tasks_for_three_real_repositories() -> None:
    manifests = tuple(load_manifest(path) for path in smoke_manifests())
    assert len(manifests) == 6
    counts: dict[str, int] = {}
    for manifest in manifests:
        counts[manifest.repo] = counts.get(manifest.repo, 0) + 1
        assert len(manifest.commit) == 40
        assert manifest.verification
    assert counts == {
        "aws-powertools/powertools-lambda-python": 2,
        "spotify/luigi": 2,
        "microsoft/tslib": 2,
    }


def test_primary_comparison_has_only_requested_groups() -> None:
    assert FINAL_POLICIES == ("full_context", "contextlens", "matched_random")


def test_matched_random_control_uses_closest_token_subset() -> None:
    context = tuple(
        ContextSource(
            kind=SourceKind.RETRIEVED_DOCUMENT,
            name=f"source-{index}",
            content="x",
            source_id=f"source-{index}",
            token_count=tokens,
            token_count_method="test",
        )
        for index, tokens in enumerate((31_000, 3_769, 2_469, 1_893, 121))
    )
    case = SimpleNamespace(case_id="token-match", context=context)

    selected = _random_until(case, 39_131)
    selected_tokens = sum(
        source.token_count or 0
        for source in context
        if source.source_id in selected
    )

    assert selected_tokens == 39_131
    assert len(selected) < len(context)

    different = _random_until(
        case,
        39_131,
        forbidden=(frozenset(selected),),
    )
    assert frozenset(different) != frozenset(selected)


def test_deployment_gate_rejects_any_observed_regression() -> None:
    measurements = (
        _measurement("full_context", 1, 0.875, True),
        _measurement("contextlens", 1, 0.325, False),
        _measurement("full_context", 2, 0.875, True),
        _measurement("contextlens", 2, 0.875, True),
        _measurement("full_context", 3, 0.875, True),
        _measurement("contextlens", 3, 0.875, True),
    )

    reasons = _deployment_rejection_reasons(
        measurements,
        trials=3,
        quality_tolerance=0.02,
    )

    assert reasons == ("final trial 1 regressed from success to failure",)


def _measurement(
    variant_id: str, trial: int, score: float, success: bool
) -> Measurement:
    return Measurement(
        task_id="case",
        trial_id=f"final:trial-{trial}",
        variant_id=variant_id,
        score=score,
        success=success,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_seconds=0.0,
    )


def test_manifest_public_value_does_not_expose_hidden_patch() -> None:
    manifest = load_manifest(smoke_manifests()[0])
    public = json.dumps(manifest.public_value(), sort_keys=True)
    assert "diff --git" not in public
    assert "assert " not in public


def test_context_discovery_uses_repository_conventions(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("instructions", encoding="utf-8")
    (tmp_path / "README.md").write_text("read me", encoding="utf-8")
    (tmp_path / "module.py").write_text("answer = 42", encoding="utf-8")
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "eval@example.com")
    _git(tmp_path, "config", "user.name", "Eval")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "fixture")
    context = discover_context(tmp_path)
    names = {source.name for source in context}
    assert {
        "AGENTS.md",
        "README.md",
        "repository-file-map.txt",
        "pinned-commit.txt",
    } <= names
    assert "module.py" not in names


def test_acquisition_fetches_only_pinned_commit(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "--quiet")
    _git(upstream, "config", "user.email", "eval@example.com")
    _git(upstream, "config", "user.name", "Eval")
    (upstream / "README.md").write_text("first", encoding="utf-8")
    _git(upstream, "add", ".")
    _git(upstream, "commit", "--quiet", "-m", "first")
    pinned = _git(upstream, "rev-parse", "HEAD").stdout.strip()
    (upstream / "README.md").write_text("solution", encoding="utf-8")
    _git(upstream, "commit", "--quiet", "-am", "withheld solution")
    manifest_path = tmp_path / "case.yaml"
    manifest_path.write_text(
        json.dumps(
            {
                "case_id": "local-case",
                "suite": "smoke",
                "repo": "owner/repository",
                "commit": pinned,
                "task": "Change behavior.",
                "setup": [],
                "verification": {"commands": [["python", "-V"]]},
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    proxy = _LocalManifest(manifest, str(upstream))
    checkout = acquire_repository(proxy, tmp_path / "checkout")  # type: ignore[arg-type]
    assert (checkout / "README.md").read_text(encoding="utf-8") == "first"
    assert _git(checkout, "rev-list", "--all", "--count").stdout.strip() == "1"
    assert _git(checkout, "remote").stdout.strip() == ""


class _LocalManifest:
    def __init__(self, manifest: object, clone_url: str) -> None:
        self.commit = manifest.commit
        self.clone_url = clone_url


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
