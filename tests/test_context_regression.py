from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import contextlens.regression as regression_module
from contextlens.experiments import AgentOutcome, AgentSettings, ReplayRequest
from contextlens.regression import (
    RegressionVerdict,
    VerificationPolicy,
    VerificationTask,
    run_context_verification,
    verify_repository,
)
from contextlens.trace import ContextSource, SourceKind


def _source(source_id: str, tokens: int) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=SourceKind.REPO_INSTRUCTION,
        name=f"{source_id}.md",
        content=f"Instructions from {source_id}.",
        token_count=tokens,
        token_count_method="fixture",
    )


class _EconomicalAdapter:
    adapter_id = "economical-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        context_tokens = sum(source.token_count or 0 for source in request.context)
        input_tokens = 500 + context_tokens
        cached = (
            300 if "cache-prefix" in {item.source_id for item in request.context} else 0
        )
        return AgentOutcome(
            output_text="ok",
            input_tokens=input_tokens,
            cached_input_tokens=cached,
            output_tokens=20,
            tool_calls=2,
            metadata={
                "turns": 3,
                "files_read": ["a.py", "b.py"],
                "searches": ["symbol"],
                "reasoning_tokens": 5,
            },
        )


class _CriticalAdapter:
    adapter_id = "critical-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        ids = {source.source_id for source in request.context}
        return AgentOutcome(
            output_text="ok" if "critical" in ids else "wrong",
            input_tokens=1_000,
            cached_input_tokens=900 if "critical" in ids else 0,
            output_tokens=10,
        )


class _CacheRegressionAdapter:
    adapter_id = "cache-regression-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        ids = {source.source_id for source in request.context}
        cached = 900 if "cache-prefix" in ids else 100
        return AgentOutcome(
            output_text="ok",
            input_tokens=1_000,
            cached_input_tokens=cached,
            output_tokens=10,
        )


class _MissingUsageAdapter:
    adapter_id = "missing-usage-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        del request
        return AgentOutcome(output_text="ok")


class _NondeterministicCandidateAdapter:
    adapter_id = "nondeterministic-candidate-fixture"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, request: ReplayRequest) -> AgentOutcome:
        self.calls += 1
        is_candidate = "candidate" in {item.source_id for item in request.context}
        output = "wrong" if is_candidate and self.calls == 2 else "ok"
        return AgentOutcome(output_text=output, input_tokens=100, output_tokens=5)


class _WorkspaceIsolationAdapter:
    adapter_id = "workspace-isolation-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        marker = Path(request.workspace) / ".contextlens-run-marker"
        clean = not marker.exists()
        marker.write_text("mutated", encoding="utf-8")
        return AgentOutcome(
            output_text="ok" if clean else "contaminated",
            input_tokens=100,
            output_tokens=5,
        )


class _AlwaysOkAdapter:
    adapter_id = "always-ok-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        context_tokens = sum(source.token_count or 0 for source in request.context)
        return AgentOutcome(
            output_text="ok",
            input_tokens=100 + context_tokens,
            output_tokens=5,
        )


class _CandidateInfrastructureFailureAdapter:
    adapter_id = "candidate-infrastructure-failure-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        if "candidate" in {source.source_id for source in request.context}:
            raise RuntimeError("provider unavailable")
        return AgentOutcome(output_text="ok", input_tokens=100, output_tokens=5)


class _NoNativeContextLeakAdapter:
    adapter_id = "no-native-context-leak-fixture"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        workspace = Path(request.workspace)
        assert not (workspace / "evals.json").exists()
        assert not tuple(workspace.rglob("AGENTS.md"))
        return AgentOutcome(output_text="ok", input_tokens=100, output_tokens=5)


def _task(tmp_path: Path) -> VerificationTask:
    (tmp_path / "fixture.txt").write_text("fixed workspace\n", encoding="utf-8")
    return VerificationTask(
        "task",
        "Return ok.",
        tmp_path,
        expected_output="ok",
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _verification_config(
    root: Path,
    *,
    target_paths: list[str] | None,
    context_provider: str | None = "codex",
) -> Path:
    task: dict[str, object] = {
        "id": "scoped-task",
        "instruction": "Return ok.",
        "workspace": ".",
        "expected_output": "ok",
    }
    if target_paths is not None:
        task["target_paths"] = target_paths
    if context_provider is not None:
        task["context_provider"] = context_provider
    config = {
        "trials": 1,
        "agent": {
            "type": "subprocess",
            "command": ["fixture"],
            "provider": "fixture",
            "model": "deterministic",
        },
        "tasks": [task],
    }
    path = root / "evals.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _initialize_context_repository(root: Path) -> None:
    (root / "backend").mkdir()
    (root / "frontend").mkdir()
    (root / "backend" / "api.py").write_text("pass\n", encoding="utf-8")
    (root / "frontend" / "app.ts").write_text("export {};\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("Root rules.\n", encoding="utf-8")
    (root / "backend" / "AGENTS.md").write_text("Backend rules.\n", encoding="utf-8")
    (root / "frontend" / "AGENTS.md").write_text("Frontend rules.\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "contextlens@example.invalid")
    _git(root, "config", "user.name", "ContextLens Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")


def test_paired_verification_passes_quality_and_provider_savings(
    tmp_path: Path,
) -> None:
    base = (_source("critical", 200), _source("fluff", 400))
    candidate = (_source("critical", 200),)

    report = run_context_verification(
        base_context=base,
        candidate_context=candidate,
        tasks=(_task(tmp_path),),
        agent_factory=_EconomicalAdapter,
        settings=AgentSettings("fixture", "deterministic"),
        policy=VerificationPolicy(trials=2),
    )

    assert report.verdict is RegressionVerdict.PASS
    assert report.paired_runs == 2
    assert report.base.successes == report.candidate.successes == 2
    assert report.candidate.initial_context_tokens == 200
    assert report.candidate.provider_input_tokens == 700
    assert report.candidate.files_read == 2
    assert report.to_dict()["delta"]["provider_input_tokens"]["fraction"] < 0


def test_verification_fails_closed_on_quality_regression(tmp_path: Path) -> None:
    report = run_context_verification(
        base_context=(_source("critical", 100),),
        candidate_context=(),
        tasks=(_task(tmp_path),),
        agent_factory=_CriticalAdapter,
        settings=AgentSettings("fixture", "deterministic"),
        policy=VerificationPolicy(trials=2),
    )

    assert report.verdict is RegressionVerdict.FAIL
    assert report.catastrophic_regressions == 2
    assert report.exit_code == 4


def test_cache_loss_is_visible_and_can_fail_economics_gate(tmp_path: Path) -> None:
    report = run_context_verification(
        base_context=(_source("cache-prefix", 900), _source("other", 100)),
        candidate_context=(_source("other", 100),),
        tasks=(_task(tmp_path),),
        agent_factory=_CacheRegressionAdapter,
        settings=AgentSettings("fixture", "deterministic"),
        policy=VerificationPolicy(trials=2),
    )

    assert report.base.uncached_input_tokens == 100
    assert report.candidate.uncached_input_tokens == 900
    assert report.verdict is RegressionVerdict.FAIL
    assert "uncached" in report.rationale.casefold()


def test_required_missing_provider_usage_is_inconclusive(tmp_path: Path) -> None:
    report = run_context_verification(
        base_context=(_source("base", 100),),
        candidate_context=(_source("candidate", 50),),
        tasks=(_task(tmp_path),),
        agent_factory=_MissingUsageAdapter,
        settings=AgentSettings("fixture", "deterministic"),
        policy=VerificationPolicy(trials=2, require_provider_usage=True),
    )

    assert report.verdict is RegressionVerdict.INCONCLUSIVE
    assert report.exit_code == 5
    assert report.base.provider_input_tokens is None


def test_nondeterministic_candidate_failure_is_never_dropped(tmp_path: Path) -> None:
    report = run_context_verification(
        base_context=(_source("base", 100),),
        candidate_context=(_source("candidate", 50),),
        tasks=(_task(tmp_path),),
        agent_factory=_NondeterministicCandidateAdapter,
        settings=AgentSettings("fixture", "deterministic"),
        policy=VerificationPolicy(trials=3),
    )

    assert report.verdict is RegressionVerdict.FAIL
    assert report.catastrophic_regressions == 1
    assert len(report.trials) == 6


def test_base_and_candidate_runs_use_fresh_isolated_workspaces(
    tmp_path: Path,
) -> None:
    report = run_context_verification(
        base_context=(_source("base", 100),),
        candidate_context=(_source("candidate", 50),),
        tasks=(_task(tmp_path),),
        agent_factory=_WorkspaceIsolationAdapter,
        settings=AgentSettings("fixture", "deterministic"),
        policy=VerificationPolicy(trials=2),
    )

    assert report.base.successes == report.candidate.successes == 2
    assert not (tmp_path / ".contextlens-run-marker").exists()


def test_infrastructure_errors_are_retained_and_excluded_from_causal_results(
    tmp_path: Path,
) -> None:
    report = run_context_verification(
        base_context=(_source("base", 100),),
        candidate_context=(_source("candidate", 50),),
        tasks=(_task(tmp_path),),
        agent_factory=_CandidateInfrastructureFailureAdapter,
        settings=AgentSettings("fixture", "deterministic"),
        policy=VerificationPolicy(trials=2),
    )

    assert report.verdict is RegressionVerdict.INCONCLUSIVE
    assert report.infrastructure_invalid_runs == 2
    assert report.base.runs == 2
    assert report.candidate.runs == 0
    assert report.candidate.infrastructure_errors == 2
    assert len(report.trials) == 4
    assert sum(not trial.infrastructure_valid for trial in report.trials) == 2
    assert report.paired_runs == 0
    raw = report.to_dict()
    assert raw["experiments"][0]["execution_status"] == "infrastructure_invalid"


def test_verify_uses_backend_effective_context_and_persists_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize_context_repository(tmp_path)
    config = _verification_config(tmp_path, target_paths=["backend/api.py"])
    monkeypatch.setattr(
        regression_module, "_agent_factory", lambda value: _AlwaysOkAdapter
    )

    report = verify_repository(config, root=tmp_path, base_ref="HEAD")

    for trial in report.trials:
        assert trial.context_provider == "codex"
        assert trial.context_source_paths == ("AGENTS.md", "backend/AGENTS.md")
        assert "frontend/AGENTS.md" not in trial.context_source_paths
        assert trial.initial_context_tokens > 0
        raw = trial.to_dict()["context"]
        assert raw["source_paths"] == ["AGENTS.md", "backend/AGENTS.md"]
        assert raw["effective_initial_tokens"] == trial.initial_context_tokens
        assert raw["content_hash"]
        assert len(raw["content_hashes"]) == 2
        assert all(raw["content_hashes"].values())
        assert all(item["scope_accuracy"] for item in raw["resolution"])
    manifest = report.to_dict()["experiments"][0]["manifest"]
    assert manifest["base_context"]["sources"] == [
        "AGENTS.md",
        "backend/AGENTS.md",
    ]
    assert manifest["candidate_context"]["content_hash"]


def test_verify_hides_native_context_and_eval_definition_from_agent_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize_context_repository(tmp_path)
    config = _verification_config(tmp_path, target_paths=["backend/api.py"])
    monkeypatch.setattr(
        regression_module,
        "_agent_factory",
        lambda value: _NoNativeContextLeakAdapter,
    )

    report = verify_repository(config, root=tmp_path, base_ref="HEAD")

    assert report.trials[0].success
    assert report.experiments[0].manifest["fixed_dimensions_hash"]


def test_verify_unions_multiple_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize_context_repository(tmp_path)
    config = _verification_config(
        tmp_path,
        target_paths=["backend/api.py", "frontend/app.ts"],
    )
    monkeypatch.setattr(
        regression_module, "_agent_factory", lambda value: _AlwaysOkAdapter
    )

    report = verify_repository(config, root=tmp_path, base_ref="HEAD")

    assert set(report.trials[0].context_source_paths) == {
        "AGENTS.md",
        "backend/AGENTS.md",
        "frontend/AGENTS.md",
    }


def test_base_and_candidate_resolve_their_own_context_trees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize_context_repository(tmp_path)
    config = _verification_config(tmp_path, target_paths=["backend/api.py"])
    (tmp_path / "backend" / "service").mkdir()
    (tmp_path / "backend" / "AGENTS.md").rename(
        tmp_path / "backend" / "service" / "AGENTS.md"
    )
    monkeypatch.setattr(
        regression_module, "_agent_factory", lambda value: _AlwaysOkAdapter
    )

    report = verify_repository(config, root=tmp_path, base_ref="HEAD")

    base = next(item for item in report.trials if item.variant == "base")
    candidate = next(item for item in report.trials if item.variant == "candidate")
    assert base.context_source_paths == ("AGENTS.md", "backend/AGENTS.md")
    assert candidate.context_source_paths == ("AGENTS.md",)
    assert "backend/service/AGENTS.md" not in candidate.context_source_paths


def test_no_targets_preserves_repository_wide_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize_context_repository(tmp_path)
    config = _verification_config(
        tmp_path,
        target_paths=None,
        context_provider=None,
    )
    monkeypatch.setattr(
        regression_module, "_agent_factory", lambda value: _AlwaysOkAdapter
    )

    report = verify_repository(config, root=tmp_path, base_ref="HEAD")

    assert set(report.trials[0].context_source_paths) == {
        "AGENTS.md",
        "backend/AGENTS.md",
        "frontend/AGENTS.md",
    }
    assert "repository-wide context" in report.trials[0].context_warnings[0]


def test_target_paths_require_explicit_provider_for_non_codex_adapter(
    tmp_path: Path,
) -> None:
    _initialize_context_repository(tmp_path)
    config = _verification_config(
        tmp_path,
        target_paths=["backend/api.py"],
        context_provider=None,
    )

    with pytest.raises(ValueError, match="no context_provider"):
        verify_repository(config, root=tmp_path, base_ref="HEAD")
