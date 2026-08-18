from __future__ import annotations

from pathlib import Path

from contextlens.experiments import AgentOutcome, AgentSettings, ReplayRequest
from contextlens.regression import (
    RegressionVerdict,
    VerificationPolicy,
    VerificationTask,
    run_context_verification,
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


def _task(tmp_path: Path) -> VerificationTask:
    (tmp_path / "fixture.txt").write_text("fixed workspace\n", encoding="utf-8")
    return VerificationTask(
        "task",
        "Return ok.",
        tmp_path,
        expected_output="ok",
    )


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
