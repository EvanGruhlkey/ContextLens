from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments import (
    AdaptiveAblationPlanner,
    AgentOutcome,
    AgentSettings,
    DirectorySnapshot,
    Evaluation,
    GroupDecision,
    MemoryReplayCache,
    ReplayCoordinator,
    ReplayRequest,
    ReplayResult,
    ReplayTask,
    ReplayWorker,
    ResourceLimits,
    SearchConfig,
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
        return Evaluation(
            scores={"quality": score, "success": float(score >= 0.5)}
        )


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
        decisions = {
            node.group.group_id: node.decision for node in run.report.nodes
        }
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


if __name__ == "__main__":
    unittest.main()
