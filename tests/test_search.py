from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments import (
    AgentOutcome,
    AgentSettings,
    AdaptiveAblationPlanner,
    AdaptiveSearchRunner,
    DirectorySnapshot,
    Evaluation,
    GroupDecision,
    ReplayCoordinator,
    ReplayRequest,
    ReplayResult,
    ReplayTask,
    ReplayWorker,
    ResourceLimits,
    ScoreObservation,
    SearchConfig,
)
from contextlens.trace import ContextSource, SourceKind


def source(
    source_id: str,
    kind: SourceKind,
    tokens: int,
) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=kind,
        name=source_id,
        content=f"Content for {source_id} with enough words.",
        token_count=tokens,
        token_count_method="fixture",
    )


def context() -> tuple[ContextSource, ...]:
    return (
        source("critical", SourceKind.FILE, 20),
        source("fluff", SourceKind.FILE, 15),
        source("tool-a", SourceKind.TOOL_SCHEMA, 50),
        source("tool-b", SourceKind.TOOL_SCHEMA, 40),
        source("memory", SourceKind.MEMORY, 10),
    )


class AdaptiveAblationPlannerTests(unittest.TestCase):
    def test_adaptively_splits_harmful_group_and_accepts_safe_removals(
        self,
    ) -> None:
        planner = AdaptiveAblationPlanner(
            context(),
            config=SearchConfig(
                quality_tolerance=0.02,
                max_experiments=10,
                batch_size=3,
            ),
        )

        baseline = planner.next_batch()
        self.assertEqual([variant.variant_id for variant in baseline], ["baseline"])
        planner.record("baseline", ScoreObservation(1.0))

        planned_removed: dict[str, frozenset[str]] = {}
        while batch := planner.next_batch():
            for variant in batch:
                planned_removed[variant.variant_id] = variant.removed_source_ids
                removed = variant.removed_source_ids
                if "critical" in removed:
                    score = 0.7
                elif {"tool-a", "tool-b"} <= removed:
                    score = 1.1
                else:
                    score = 1.0
                planner.record(variant.variant_id, ScoreObservation(score))

        report = planner.report()
        by_sources = {
            node.group.source_ids: node
            for node in report.nodes
        }

        self.assertEqual(
            by_sources[("critical", "fluff")].decision,
            GroupDecision.SPLIT,
        )
        self.assertEqual(
            by_sources[("critical",)].decision,
            GroupDecision.KEEP,
        )
        self.assertEqual(
            by_sources[("fluff",)].decision,
            GroupDecision.REMOVE,
        )
        self.assertEqual(
            by_sources[("tool-a", "tool-b")].decision,
            GroupDecision.REMOVE,
        )
        self.assertEqual(report.stopping_reason, "search_complete")
        self.assertEqual(report.experiments_planned, 6)
        self.assertEqual(
            set(report.recommended_removals),
            {"fluff", "memory", "tool-a", "tool-b"},
        )
        self.assertEqual(
            planned_removed["ablate:kind:file/1"],
            frozenset({"critical"}),
        )

    def test_uncertainty_produces_an_inconclusive_result(self) -> None:
        planner = AdaptiveAblationPlanner(
            (source("only", SourceKind.FILE, 10),),
            config=SearchConfig(quality_tolerance=0.01),
        )
        planner.next_batch()
        planner.record("baseline", ScoreObservation(1.0, uncertainty=0.02))
        variant = planner.next_batch()[0]
        planner.record(
            variant.variant_id,
            ScoreObservation(0.99, uncertainty=0.02),
        )

        node = planner.report().nodes[0]
        self.assertEqual(node.decision, GroupDecision.INCONCLUSIVE)
        self.assertFalse(planner.report().recommended_removals)

    def test_experiment_budget_stops_before_excess_work(self) -> None:
        planner = AdaptiveAblationPlanner(
            context(),
            config=SearchConfig(max_experiments=2, batch_size=4),
        )
        planner.next_batch()
        planner.record("baseline", ScoreObservation(1.0))
        batch = planner.next_batch()

        self.assertEqual(len(batch), 1)
        self.assertEqual(
            planner.report().stopping_reason,
            "experiment_budget_exhausted",
        )
        self.assertEqual(planner.report().experiments_planned, 2)

    def test_token_and_cost_budgets_are_checked_during_planning(self) -> None:
        total_tokens = sum(item.token_count or 0 for item in context())
        planner = AdaptiveAblationPlanner(
            context(),
            config=SearchConfig(
                max_planned_context_tokens=total_tokens,
                max_estimated_cost_usd=1,
                estimated_cost_per_1k_tokens=1,
            ),
        )
        baseline = planner.next_batch()
        self.assertEqual(len(baseline), 1)
        planner.record("baseline", ScoreObservation(1.0))

        self.assertFalse(planner.next_batch())
        self.assertEqual(
            planner.report().stopping_reason,
            "token_budget_exhausted",
        )

    def test_custom_groups_must_partition_all_sources(self) -> None:
        with self.assertRaisesRegex(ValueError, "omit sources"):
            AdaptiveAblationPlanner(
                context(),
                groups={"partial": frozenset({"critical"})},
            )


class AdaptiveSearchRunnerTests(unittest.TestCase):
    def test_runs_replays_and_evaluations_until_search_completes(self) -> None:
        class Adapter:
            adapter_id = "search-fixture"

            def run(self, request: ReplayRequest) -> AgentOutcome:
                return AgentOutcome(output_text="completed")

        class Evaluator:
            evaluator_id = "quality-fixture"

            def evaluate(
                self,
                task: ReplayTask,
                result: ReplayResult,
            ) -> Evaluation:
                source_ids = set(result.context_source_ids)
                score = 1.0 if "critical" in source_ids else 0.5
                return Evaluation(scores={"quality": score})

        search_context = (
            source("critical", SourceKind.FILE, 20),
            source("fluff", SourceKind.FILE, 15),
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.txt").write_text("fixture", encoding="utf-8")
            task = ReplayTask("task", "Complete the fixture.")
            planner = AdaptiveAblationPlanner(
                search_context,
                config=SearchConfig(max_experiments=5, batch_size=2),
            )
            replay_worker = ReplayWorker(
                adapter=Adapter(),
                snapshot=DirectorySnapshot(root),
                task=task,
                context=search_context,
                settings=AgentSettings("fixture", "fixture-model"),
                timeout_seconds=5,
            )
            run = AdaptiveSearchRunner(
                planner,
                ReplayCoordinator(replay_worker, ResourceLimits()),
                Evaluator(),
                score_name="quality",
            ).run()

        self.assertEqual(run.report.stopping_reason, "search_complete")
        self.assertEqual(run.report.recommended_removals, ("fluff",))
        self.assertEqual(len(run.replay_results), 4)
        self.assertEqual(len(run.evaluations), 4)


if __name__ == "__main__":
    unittest.main()
