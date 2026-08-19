from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments import (
    AdaptiveAblationPlanner,
    AgentOutcome,
    AgentSettings,
    CommandWorkspacePreparer,
    ContextExperimentRunner,
    DirectorySnapshot,
    Evaluation,
    ExperimentContext,
    GroupDecision,
    MemoryReplayCache,
    PairedAgentExperiment,
    ReplayCoordinator,
    ReplayRequest,
    ReplayResult,
    ReplayTask,
    ReplayWorker,
    ResourceLimits,
    SearchConfig,
    TrialClassification,
    WorkspaceSetupCommand,
    WorkspaceVerification,
)
from contextlens.experiments.paired_runner import PairedAdaptiveSearchRunner
from contextlens.trace import ContextSource, SourceKind


def _source(source_id: str, tokens: int = 10) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=SourceKind.FILE,
        name=source_id,
        content=f"Context for {source_id}.",
        token_count=tokens,
        token_count_method="fixture",
    )


class RecordingAdapter:
    adapter_id = "paired-recording-v1"

    def __init__(self, *, noisy: bool = False) -> None:
        self.noisy = noisy
        self.workspaces: list[str] = []
        self.calls_by_context: dict[tuple[str, ...], int] = {}
        self._lock = threading.Lock()

    def run(self, request: ReplayRequest) -> AgentOutcome:
        source_ids = tuple(source.source_id for source in request.context)
        with self._lock:
            self.workspaces.append(request.workspace)
            count = self.calls_by_context.get(source_ids, 0)
            self.calls_by_context[source_ids] = count + 1
        if "critical" not in source_ids:
            score = 0.8 if self.noisy and count % 2 == 0 else 1.0 if self.noisy else 0.5
        else:
            score = 1.0
        return AgentOutcome(
            output_text=str(score),
            input_tokens=sum(source.token_count or 0 for source in request.context),
            output_tokens=1,
        )


class FloatEvaluator:
    evaluator_id = "float-output-v1"

    def evaluate(self, task: ReplayTask, result: ReplayResult) -> Evaluation:
        assert result.outcome is not None
        score = float(result.outcome.output_text)
        return Evaluation(scores={"quality": score, "success": float(score >= 0.5)})


def _coordinator(
    root: Path,
    context: tuple[ContextSource, ...],
    adapter: RecordingAdapter,
    *,
    cached: bool = False,
) -> ReplayCoordinator:
    worker = ReplayWorker(
        adapter=adapter,
        snapshot=DirectorySnapshot(root),
        task=ReplayTask("task", "Complete the task."),
        context=context,
        settings=AgentSettings("fixture", "fixture-model"),
        timeout_seconds=5,
    )
    return ReplayCoordinator(
        worker,
        ResourceLimits(max_runs=10),
        cache=MemoryReplayCache() if cached else None,
    )


class PairedAdaptiveSearchRunnerTests(unittest.TestCase):
    def test_runs_fresh_paired_trials_and_returns_complete_evidence(self) -> None:
        context = (_source("critical"), _source("fluff"))
        planner = AdaptiveAblationPlanner(
            context,
            config=SearchConfig(max_experiments=3, batch_size=2),
            groups={
                "critical": frozenset({"critical"}),
                "fluff": frozenset({"fluff"}),
            },
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.txt").write_text("fixture", encoding="utf-8")
            adapter = RecordingAdapter()
            persisted: list[str] = []
            run = PairedAdaptiveSearchRunner(
                planner,
                _coordinator(root, context, adapter),
                FloatEvaluator(),
                score_name="quality",
                trials=2,
                on_invocation=lambda invocation: persisted.append(
                    invocation.result.run_id
                ),
            ).run()

        self.assertEqual(len(run.replay_results), 10)
        self.assertEqual(len(run.evaluations), 10)
        self.assertEqual(len(run.measurements), 10)
        self.assertEqual(len(run.effects), 2)
        self.assertEqual(
            persisted,
            [invocation.result.run_id for invocation in run.invocations],
        )
        self.assertFalse(run.errors)
        self.assertEqual(len({result.run_id for result in run.replay_results}), 10)
        self.assertEqual(len(set(adapter.workspaces)), 10)
        self.assertFalse(any(Path(path).exists() for path in adapter.workspaces))
        decisions = {node.group.group_id: node.decision for node in run.report.nodes}
        self.assertEqual(decisions["critical"], GroupDecision.KEEP)
        self.assertEqual(decisions["fluff"], GroupDecision.REMOVE)
        self.assertEqual(run.report.recommended_removals, ("fluff",))
        self.assertTrue(all(effect.pair_count == 2 for effect in run.effects))

    def test_bootstrap_interval_drives_planner_uncertainty(self) -> None:
        context = (_source("critical"),)
        planner = AdaptiveAblationPlanner(
            context,
            config=SearchConfig(max_experiments=2),
            groups={"critical": frozenset({"critical"})},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.txt").write_text("fixture", encoding="utf-8")
            run = PairedAdaptiveSearchRunner(
                planner,
                _coordinator(root, context, RecordingAdapter(noisy=True)),
                FloatEvaluator(),
                score_name="quality",
                trials=2,
            ).run()

        node = run.report.nodes[0]
        self.assertEqual(node.decision, GroupDecision.INCONCLUSIVE)
        assert node.observation is not None
        self.assertGreater(node.observation.uncertainty, 0)
        effect = run.effects[0]
        self.assertGreater(effect.confidence_high, effect.confidence_low)

    def test_rejects_a_cache_enabled_coordinator(self) -> None:
        context = (_source("critical"),)
        planner = AdaptiveAblationPlanner(context)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.txt").write_text("fixture", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cache-disabled"):
                PairedAdaptiveSearchRunner(
                    planner,
                    _coordinator(
                        root,
                        context,
                        RecordingAdapter(),
                        cached=True,
                    ),
                    FloatEvaluator(),
                    score_name="quality",
                )


class _HiddenAfterAgentVerifier:
    verifier_id = "hidden-after-agent-v1"

    def verify(
        self,
        workspace: Path,
        task: ReplayTask,
        outcome: AgentOutcome,
    ) -> WorkspaceVerification:
        del task, outcome
        hidden = workspace / "hidden_test.py"
        assert not hidden.exists()
        hidden.write_text("hidden", encoding="utf-8")
        return WorkspaceVerification(
            command=("hidden-grader",),
            exit_code=0,
            stdout="pass",
            stderr="",
            duration_seconds=0.01,
        )


class _IsolatedAgentAdapter:
    adapter_id = "isolated-agent-v1"

    def __init__(self, requests: list[ReplayRequest]) -> None:
        self.requests = requests

    def run(self, request: ReplayRequest) -> AgentOutcome:
        workspace = Path(request.workspace)
        assert not (workspace / "AGENTS.md").exists()
        assert not (workspace / "hidden_test.py").exists()
        self.requests.append(request)
        (workspace / "agent-change.txt").write_text(
            request.variant.variant_id,
            encoding="utf-8",
        )
        tokens = 100 if request.variant.variant_id == "base" else 60
        return AgentOutcome(
            output_text="1.0",
            input_tokens=tokens,
            cached_input_tokens=10,
            output_tokens=5,
            tool_calls=2,
            metadata={"turns": 1, "files_read": 1, "searches": 0},
        )


class ContextExperimentRunnerTests(unittest.TestCase):
    def test_isolates_pairs_persists_manifest_and_hides_native_context(self) -> None:
        requests: list[ReplayRequest] = []
        adapter_constructions = 0

        def agent_factory() -> _IsolatedAgentAdapter:
            nonlocal adapter_constructions
            adapter_constructions += 1
            return _IsolatedAgentAdapter(requests)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code.py").write_text("value = 1\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("native rules", encoding="utf-8")
            run = ContextExperimentRunner(
                PairedAgentExperiment(
                    experiment_id="repo:task",
                    repository="owner/repo",
                    commit="abc123",
                    snapshot=DirectorySnapshot(
                        root,
                        excluded_paths=("AGENTS.md",),
                    ),
                    task=ReplayTask("task", "Complete the task."),
                    base_context=ExperimentContext(
                        (_source("base", 100),),
                        provider="codex",
                        target_paths=("code.py",),
                        source_paths=("AGENTS.md",),
                    ),
                    candidate_context=ExperimentContext(
                        (_source("candidate", 60),),
                        provider="codex",
                        target_paths=("code.py",),
                        source_paths=("AGENTS.md",),
                    ),
                    agent_factory=agent_factory,
                    settings=AgentSettings(
                        "openai",
                        "fixture-model",
                        parameters={"reasoning_effort": "low"},
                    ),
                    evaluator=FloatEvaluator(),
                    verifier=_HiddenAfterAgentVerifier(),
                    grader_definition={"fixture": "hidden_test.py"},
                    trials=3,
                    timeout_seconds=5,
                )
            ).run()

        self.assertEqual(
            [pair.order for pair in run.pairs],
            [
                ("base", "candidate"),
                ("candidate", "base"),
                ("base", "candidate"),
            ],
        )
        self.assertEqual(len(requests), 6)
        self.assertEqual(adapter_constructions, 6)
        self.assertEqual(len({request.workspace for request in requests}), 6)
        self.assertTrue(
            all(not Path(request.workspace).exists() for request in requests)
        )
        self.assertEqual(len({item.agent_instance_id for item in run.invocations}), 6)
        self.assertEqual(len({item.result.run_id for item in run.invocations}), 6)
        self.assertEqual(
            len({item.fixed_dimensions_hash for item in run.invocations}), 1
        )
        self.assertEqual(
            len({item.context_hash for item in run.invocations}),
            2,
        )
        self.assertTrue(all(pair.infrastructure_valid for pair in run.pairs))
        raw = run.to_dict()
        self.assertEqual(raw["execution_status"], "completed")
        self.assertEqual(raw["manifest"]["task_id"], "task")
        self.assertEqual(
            raw["manifest"]["policy"]["order"][1],
            ["candidate", "base"],
        )
        self.assertEqual(
            raw["pairs"][0]["delta"]["provider_input_tokens"],
            -40,
        )
        self.assertEqual(len(raw["raw_trials"]), 6)

    def test_infrastructure_failures_are_retained_but_not_aggregated(self) -> None:
        class FailingAdapter:
            adapter_id = "failing-v1"
            constructions = 0

            def __init__(self) -> None:
                type(self).constructions += 1
                self.instance_number = type(self).constructions

            def run(self, request: ReplayRequest) -> AgentOutcome:
                if self.instance_number == 3:
                    raise RuntimeError("provider unavailable")
                return AgentOutcome(output_text="1.0", input_tokens=10)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code.py").write_text("value = 1\n", encoding="utf-8")
            run = ContextExperimentRunner(
                PairedAgentExperiment(
                    experiment_id="repo:failure",
                    repository="owner/repo",
                    commit="abc123",
                    snapshot=DirectorySnapshot(root),
                    task=ReplayTask("task", "Complete the task."),
                    base_context=ExperimentContext(
                        (_source("base"),), provider="codex"
                    ),
                    candidate_context=ExperimentContext(
                        (_source("candidate"),), provider="codex"
                    ),
                    agent_factory=FailingAdapter,
                    settings=AgentSettings("openai", "fixture-model"),
                    evaluator=FloatEvaluator(),
                    trials=2,
                    timeout_seconds=5,
                )
            ).run()

        failed = [
            item
            for item in run.invocations
            if item.classification is TrialClassification.INFRASTRUCTURE_ERROR
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].error_stage, "agent_execution")
        raw = run.to_dict()
        self.assertEqual(raw["execution_status"], "infrastructure_invalid")
        self.assertEqual(raw["aggregate"]["candidate"]["planned_runs"], 2)
        self.assertEqual(raw["aggregate"]["candidate"]["causal_runs"], 1)

    def test_workspace_setup_runs_before_agent_and_is_not_an_agent_change(self) -> None:
        requests: list[ReplayRequest] = []

        class SetupAwareAdapter:
            adapter_id = "setup-aware-v1"

            def run(self, request: ReplayRequest) -> AgentOutcome:
                assert (Path(request.workspace) / ".setup-marker").is_file()
                requests.append(request)
                return AgentOutcome(output_text="1.0", input_tokens=10)

        setup = CommandWorkspacePreparer(
            (
                WorkspaceSetupCommand(
                    (
                        sys.executable,
                        "-c",
                        (
                            "from pathlib import Path; "
                            "Path('.setup-marker').write_text('ok')"
                        ),
                    )
                ),
            ),
            timeout_seconds=5,
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code.py").write_text("value = 1\n", encoding="utf-8")
            run = ContextExperimentRunner(
                PairedAgentExperiment(
                    experiment_id="repo:setup",
                    repository="owner/repo",
                    commit="abc123",
                    snapshot=DirectorySnapshot(root),
                    task=ReplayTask("task", "Complete the task."),
                    base_context=ExperimentContext(
                        (_source("base"),), provider="codex"
                    ),
                    candidate_context=ExperimentContext(
                        (_source("candidate"),), provider="codex"
                    ),
                    agent_factory=SetupAwareAdapter,
                    settings=AgentSettings("openai", "fixture-model"),
                    evaluator=FloatEvaluator(),
                    preparer=setup,
                    trials=1,
                    timeout_seconds=5,
                )
            ).run()

        self.assertEqual(len(requests), 2)
        self.assertTrue(all(not item.result.file_changes for item in run.invocations))
        self.assertIsNotNone(run.to_dict()["manifest"]["workspace_setup"])

    def test_setup_failure_is_retained_without_launching_agent(self) -> None:
        calls = 0

        class MustNotRunAdapter:
            adapter_id = "must-not-run-v1"

            def run(self, request: ReplayRequest) -> AgentOutcome:
                del request
                nonlocal calls
                calls += 1
                return AgentOutcome(output_text="1.0")

        setup = CommandWorkspacePreparer(
            (WorkspaceSetupCommand((sys.executable, "-c", "raise SystemExit(2)")),),
            timeout_seconds=5,
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code.py").write_text("value = 1\n", encoding="utf-8")
            run = ContextExperimentRunner(
                PairedAgentExperiment(
                    experiment_id="repo:setup-failure",
                    repository="owner/repo",
                    commit="abc123",
                    snapshot=DirectorySnapshot(root),
                    task=ReplayTask("task", "Complete the task."),
                    base_context=ExperimentContext(
                        (_source("base"),), provider="codex"
                    ),
                    candidate_context=ExperimentContext(
                        (_source("candidate"),), provider="codex"
                    ),
                    agent_factory=MustNotRunAdapter,
                    settings=AgentSettings("openai", "fixture-model"),
                    evaluator=FloatEvaluator(),
                    preparer=setup,
                    trials=1,
                    timeout_seconds=5,
                )
            ).run()

        self.assertEqual(calls, 0)
        self.assertTrue(
            all(
                item.classification is TrialClassification.INFRASTRUCTURE_ERROR
                and item.error_stage == "setup"
                for item in run.invocations
            )
        )


if __name__ == "__main__":
    unittest.main()
