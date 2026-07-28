from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments import (
    AgentOutcome,
    AgentSettings,
    DirectorySnapshot,
    Evaluation,
    ReplayCoordinator,
    ReplayRequest,
    ReplayResult,
    ReplayTask,
    ReplayWorker,
    ResourceLimits,
    ScoreObservation,
    SearchConfig,
    SearchReport,
)
from contextlens.experiments.search import AdaptiveAblationPlanner
from contextlens.optimization import (
    ContextOptimizer,
    ContextValuePredictor,
    OptimizationObjective,
    OptimizationPolicy,
    RecalibrationPolicy,
    TrainingExample,
    ValuePrediction,
)
from contextlens.profiler import (
    EvidenceLevel,
    SourceProfile,
    UsageLabel,
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
        content=f"Useful content from {source_id}.",
        token_count=tokens,
        token_count_method="fixture",
    )


def profile(
    source_id: str,
    kind: SourceKind,
    label: UsageLabel,
    tokens: int,
) -> SourceProfile:
    return SourceProfile(
        source_id=source_id,
        name=source_id,
        kind=kind.value,
        label=label,
        token_count=tokens,
        token_count_method="fixture",
        position=0.5,
        output_overlap=0.5 if label is UsageLabel.USED else 0,
        duplicated_by=("other",) if label is UsageLabel.DUPLICATED else (),
        age_seconds=None,
        retrieval_rank=None,
        matched_output_spans=(),
        signals=(),
        evidence_level=EvidenceLevel.OBSERVED,
    )


def search_report(
    context: tuple[ContextSource, ...],
) -> SearchReport:
    planner = AdaptiveAblationPlanner(
        context,
        config=SearchConfig(
            quality_tolerance=0.02,
            max_experiments=10,
            batch_size=3,
        ),
        groups={
            item.source_id: frozenset({item.source_id})
            for item in context
        },
    )
    planner.next_batch()
    planner.record("baseline", ScoreObservation(1.0))
    for variant in planner.next_batch():
        source_id = next(iter(variant.removed_source_ids))
        scores = {
            "critical": 0.7,
            "fluff": 1.0,
            "harmful": 1.1,
        }
        planner.record(variant.variant_id, ScoreObservation(scores[source_id]))
    while planner.next_batch():
        raise AssertionError("all singleton groups should resolve in one batch")
    return planner.report()


class PredictorTests(unittest.TestCase):
    def test_learns_serializes_and_restores_value_predictions(self) -> None:
        examples = (
            TrainingExample(
                profile("used-1", SourceKind.FILE, UsageLabel.USED, 20),
                0.4,
            ),
            TrainingExample(
                profile("used-2", SourceKind.FILE, UsageLabel.USED, 30),
                0.5,
            ),
            TrainingExample(
                profile(
                    "duplicate-1",
                    SourceKind.FILE,
                    UsageLabel.DUPLICATED,
                    20,
                ),
                -0.2,
            ),
            TrainingExample(
                profile(
                    "duplicate-2",
                    SourceKind.FILE,
                    UsageLabel.DUPLICATED,
                    30,
                ),
                -0.1,
            ),
        )
        predictor = ContextValuePredictor(regularization=0.1).fit(examples)

        used = predictor.predict(
            profile("used-new", SourceKind.FILE, UsageLabel.USED, 25)
        )
        duplicate = predictor.predict(
            profile(
                "duplicate-new",
                SourceKind.FILE,
                UsageLabel.DUPLICATED,
                25,
            )
        )
        restored = ContextValuePredictor.from_dict(predictor.to_dict())

        self.assertGreater(used.predicted_effect, duplicate.predicted_effect)
        self.assertAlmostEqual(
            restored.predict(
                profile("used-new", SourceKind.FILE, UsageLabel.USED, 25)
            ).predicted_effect,
            used.predicted_effect,
        )
        self.assertEqual(predictor.training_examples, 4)

    def test_recalibration_policy_requires_periodic_verification(self) -> None:
        policy = RecalibrationPolicy(
            verification_interval=5,
            max_prediction_uncertainty=0.2,
            minimum_training_examples=10,
        )
        self.assertTrue(
            policy.should_verify(
                predictions_since_verification=0,
                training_examples=4,
                prediction_uncertainty=0.1,
            )
        )
        self.assertTrue(
            policy.should_verify(
                predictions_since_verification=5,
                training_examples=20,
                prediction_uncertainty=0.1,
            )
        )
        self.assertFalse(
            policy.should_verify(
                predictions_since_verification=2,
                training_examples=20,
                prediction_uncertainty=0.1,
            )
        )


class OptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = (
            source("critical", SourceKind.FILE, 40),
            source("fluff", SourceKind.MESSAGE, 30),
            source("harmful", SourceKind.TOOL_SCHEMA, 50),
        )
        self.report = search_report(self.context)

    def test_combines_safe_removals_for_cost_optimization(self) -> None:
        candidate = ContextOptimizer(self.context).propose(
            self.report,
            OptimizationPolicy(OptimizationObjective.MIN_COST),
        )
        self.assertEqual(candidate.removed_source_ids, ("fluff", "harmful"))
        self.assertEqual(candidate.retained_source_ids, ("critical",))
        self.assertEqual(candidate.removed_tokens, 80)
        self.assertFalse(candidate.predicted_removals)

    def test_max_quality_only_keeps_removals_that_improved_score(self) -> None:
        candidate = ContextOptimizer(self.context).propose(
            self.report,
            OptimizationPolicy(OptimizationObjective.MAX_QUALITY),
        )
        self.assertEqual(candidate.removed_source_ids, ("harmful",))

    def test_token_budget_can_add_predicted_low_value_removals(self) -> None:
        class Predictor:
            def predict(self, item: SourceProfile) -> ValuePrediction:
                values = {"critical": 1.0, "fluff": -0.2, "harmful": -0.5}
                return ValuePrediction(item.source_id, values[item.source_id], 0)

        profiles = tuple(
            profile(
                item.source_id,
                item.kind,
                UsageLabel.UNCERTAIN,
                item.token_count or 0,
            )
            for item in self.context
        )
        empty_search = AdaptiveAblationPlanner(
            self.context,
            groups={
                item.source_id: frozenset({item.source_id})
                for item in self.context
            },
        )
        empty_search.next_batch()
        empty_search.record("baseline", ScoreObservation(1))
        for variant in empty_search.next_batch():
            empty_search.record(
                variant.variant_id,
                ScoreObservation(0.99, uncertainty=0.1),
            )
        while empty_search.next_batch():
            raise AssertionError("unexpected additional experiment")

        candidate = ContextOptimizer(
            self.context,
            profiles=profiles,
            predictor=Predictor(),
        ).propose(
            empty_search.report(),
            OptimizationPolicy(
                OptimizationObjective.TOKEN_BUDGET,
                token_budget=70,
            ),
        )

        self.assertEqual(candidate.predicted_removals, ("harmful",))
        self.assertEqual(candidate.retained_tokens, 70)

    def test_fixed_answer_screening_is_labeled_non_verified(self) -> None:
        class Scorer:
            scorer_id = "fixture-logprob"

            def score(
                self,
                context: tuple[ContextSource, ...],
                answer: str,
            ) -> float:
                return float(len(context))

        optimizer = ContextOptimizer(self.context)
        candidate = optimizer.propose(
            self.report,
            OptimizationPolicy(OptimizationObjective.MIN_COST),
        )
        screening = optimizer.screen(
            candidate,
            answer="fixed answer",
            scorer=Scorer(),
        )
        self.assertEqual(screening.full_context_score, 3)
        self.assertEqual(screening.candidate_score, 1)
        self.assertEqual(screening.evidence_scope, "screening")

    def test_combined_candidate_is_verified_on_isolated_worker(self) -> None:
        class Adapter:
            adapter_id = "optimization-fixture"

            def run(self, request: ReplayRequest) -> AgentOutcome:
                ids = {item.source_id for item in request.context}
                score = 1.0 if "critical" in ids else 0.0
                return AgentOutcome(
                    output_text=str(score),
                    input_tokens=sum(item.token_count or 0 for item in request.context),
                    output_tokens=1,
                    cost_usd=0.01,
                )

        class Evaluator:
            evaluator_id = "float-output"

            def evaluate(
                self,
                task: ReplayTask,
                result: ReplayResult,
            ) -> Evaluation:
                assert result.outcome is not None
                return Evaluation(
                    scores={"quality": float(result.outcome.output_text)}
                )

        optimizer = ContextOptimizer(self.context)
        candidate = optimizer.propose(
            self.report,
            OptimizationPolicy(OptimizationObjective.MIN_COST),
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.txt").write_text("task", encoding="utf-8")
            replay_worker = ReplayWorker(
                adapter=Adapter(),
                snapshot=DirectorySnapshot(root),
                task=ReplayTask("task", "Complete task."),
                context=self.context,
                settings=AgentSettings("fixture", "fixture-model"),
                timeout_seconds=5,
            )
            verified = optimizer.verify(
                candidate,
                coordinator=ReplayCoordinator(
                    replay_worker,
                    ResourceLimits(),
                ),
                evaluator=Evaluator(),
                score_name="quality",
                baseline_score=1.0,
                policy=OptimizationPolicy(
                    OptimizationObjective.MIN_COST,
                    quality_tolerance=0.01,
                    token_budget=40,
                    max_cost_usd=0.02,
                ),
                baseline_cost_usd=0.02,
            )

        self.assertTrue(verified.accepted)
        self.assertEqual(verified.quality_change, 0)
        self.assertGreater(verified.objective_improvement or 0, 0)
        self.assertEqual(verified.evidence_scope, "target_model")


if __name__ == "__main__":
    unittest.main()
