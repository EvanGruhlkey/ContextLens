from __future__ import annotations

import unittest

from contextlens.analysis import (
    CostCalculator,
    EffectVerdict,
    EvidenceScope,
    Measurement,
    ModelPricing,
    PairedAnalyzer,
    SavingsAction,
    SavingsAnalyzer,
    Workload,
)
from contextlens.evaluators import (
    CallableEvaluator,
    ExactMatchEvaluator,
    RecordedEvaluator,
)
from contextlens.evaluators import (
    TestResultsEvaluator as ResultsEvaluator,
)
from contextlens.experiments import (
    AgentOutcome,
    Evaluation,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
)


def result(
    run_id: str,
    variant_id: str,
    *,
    output: str = "",
    tests: tuple[str, ...] = (),
    input_tokens: int | None = 100,
    output_tokens: int | None = 20,
    cost: float | None = 0.01,
    duration: float = 1.0,
) -> ReplayResult:
    return ReplayResult(
        run_id=run_id,
        task_id="task-1",
        variant_id=variant_id,
        removed_source_ids=(),
        status=ReplayStatus.COMPLETED,
        attempt=1,
        duration_seconds=duration,
        context_source_ids=("source",),
        context_tokens=120,
        outcome=AgentOutcome(
            output_text=output,
            test_results=tests,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        ),
    )


class BuiltinEvaluatorTests(unittest.TestCase):
    def test_exact_match_normalizes_case_and_whitespace(self) -> None:
        evaluator = ExactMatchEvaluator({"task-1": "Expected Answer"})
        evaluation = evaluator.evaluate(
            ReplayTask("task-1", "Answer."),
            result("run", "baseline", output=" expected   answer\n"),
        )
        self.assertEqual(evaluation.scores["success"], 1)
        self.assertTrue(evaluation.metadata["deterministic"])

    def test_test_results_preserve_failure_evidence(self) -> None:
        evaluator = ResultsEvaluator()
        evaluation = evaluator.evaluate(
            ReplayTask("task-1", "Fix tests."),
            result(
                "run",
                "baseline",
                tests=("10 passed", "1 failed: parser"),
            ),
        )
        self.assertEqual(evaluation.scores["success"], 0)
        self.assertEqual(evaluation.evidence, ("1 failed: parser",))

    def test_callable_and_recorded_evaluators(self) -> None:
        replay = result("run", "baseline")
        task = ReplayTask("task-1", "Score.")
        expected = Evaluation(scores={"quality": 0.75}, evidence=("reviewed",))
        callable_evaluator = CallableEvaluator(
            "model-grader-v1",
            lambda task, result: expected,
        )
        recorded = RecordedEvaluator({"run": expected})

        self.assertIs(callable_evaluator.evaluate(task, replay), expected)
        self.assertIs(recorded.evaluate(task, replay), expected)


class CostTests(unittest.TestCase):
    def test_calculates_input_and_output_cost_separately(self) -> None:
        calculator = CostCalculator(
            ModelPricing(
                provider="fixture",
                model="fixture-model",
                input_per_million_usd=2,
                output_per_million_usd=8,
            )
        )
        usage = calculator.calculate(1_000_000, 500_000)
        self.assertEqual(usage.input_cost_usd, 2)
        self.assertEqual(usage.output_cost_usd, 4)
        self.assertEqual(usage.total_cost_usd, 6)


def measurement(
    task_id: str,
    trial_id: str,
    variant_id: str,
    score: float,
    *,
    tokens: int,
    cost: float,
) -> Measurement:
    return Measurement(
        task_id=task_id,
        trial_id=trial_id,
        variant_id=variant_id,
        score=score,
        success=score >= 0.5,
        input_tokens=tokens,
        output_tokens=20,
        cost_usd=cost,
        latency_seconds=1,
    )


class PairedAnalyzerTests(unittest.TestCase):
    def test_identifies_helpful_context_and_resource_savings(self) -> None:
        values: list[Measurement] = []
        for index in range(6):
            task_id = f"task-{index}"
            values.extend(
                (
                    measurement(
                        task_id,
                        "trial-1",
                        "baseline",
                        1.0,
                        tokens=200,
                        cost=0.02,
                    ),
                    measurement(
                        task_id,
                        "trial-1",
                        "without-source",
                        0.8,
                        tokens=100,
                        cost=0.01,
                    ),
                )
            )

        effect = PairedAnalyzer(
            bootstrap_samples=500,
            random_seed=42,
        ).analyze(
            tuple(values),
            baseline_variant_id="baseline",
            ablated_variant_id="without-source",
        )

        self.assertEqual(effect.verdict, EffectVerdict.HELPFUL)
        self.assertAlmostEqual(effect.effect, 0.2)
        self.assertAlmostEqual(effect.relative_effect or 0, 0.25)
        self.assertEqual(effect.pair_count, 6)
        self.assertEqual(effect.task_count, 6)
        self.assertEqual(effect.input_tokens_saved_by_ablation, 100)
        self.assertAlmostEqual(effect.cost_saved_by_ablation_usd, 0.01)
        self.assertAlmostEqual(effect.quality_per_1k_tokens or 0, 2)
        self.assertFalse(effect.warnings)

    def test_identifies_harmful_context(self) -> None:
        values: list[Measurement] = []
        for index in range(5):
            values.extend(
                (
                    measurement(
                        f"task-{index}",
                        "trial",
                        "baseline",
                        0.7,
                        tokens=200,
                        cost=0.02,
                    ),
                    measurement(
                        f"task-{index}",
                        "trial",
                        "ablated",
                        0.9,
                        tokens=100,
                        cost=0.01,
                    ),
                )
            )
        effect = PairedAnalyzer(bootstrap_samples=200).analyze(
            tuple(values),
            baseline_variant_id="baseline",
            ablated_variant_id="ablated",
        )
        self.assertEqual(effect.verdict, EffectVerdict.HARMFUL)
        self.assertLess(effect.confidence_high, 0)

        savings = SavingsAnalyzer().recommend(
            effect,
            Workload(
                runs_per_day=100,
                projection_days=30,
                experiment_cost_usd=5,
            ),
            source_id="unused-schemas",
            name="Unused MCP schemas",
        )
        self.assertEqual(savings.action, SavingsAction.REMOVE)
        self.assertEqual(savings.projected_runs, 3_000)
        self.assertAlmostEqual(savings.projected_gross_cost_saved_usd, 30)
        self.assertAlmostEqual(savings.projected_net_cost_saved_usd, 25)
        self.assertEqual(savings.break_even_runs, 500)
        self.assertGreater(savings.removal_quality_change, 0)

    def test_single_pair_stays_uncertain_and_warns(self) -> None:
        values = (
            measurement(
                "task",
                "trial",
                "baseline",
                1,
                tokens=100,
                cost=0.01,
            ),
            measurement(
                "task",
                "trial",
                "ablated",
                0,
                tokens=50,
                cost=0.005,
            ),
        )
        effect = PairedAnalyzer(bootstrap_samples=100).analyze(
            values,
            baseline_variant_id="baseline",
            ablated_variant_id="ablated",
        )
        self.assertEqual(effect.verdict, EffectVerdict.UNCERTAIN)
        self.assertTrue(effect.warnings)

    def test_equivalence_interval_can_be_labeled_neutral(self) -> None:
        values: list[Measurement] = []
        for index in range(5):
            values.extend(
                (
                    measurement(
                        f"task-{index}",
                        "trial",
                        "baseline",
                        1,
                        tokens=100,
                        cost=0.01,
                    ),
                    measurement(
                        f"task-{index}",
                        "trial",
                        "ablated",
                        1,
                        tokens=50,
                        cost=0.005,
                    ),
                )
            )
        effect = PairedAnalyzer(
            bootstrap_samples=100,
            equivalence_tolerance=0.01,
        ).analyze(
            tuple(values),
            baseline_variant_id="baseline",
            ablated_variant_id="ablated",
        )
        self.assertEqual(effect.verdict, EffectVerdict.NEUTRAL)
        recommendation = SavingsAnalyzer().recommend(
            effect,
            Workload(runs_per_day=10),
        )
        self.assertEqual(recommendation.action, SavingsAction.REMOVE)

    def test_helpful_context_is_kept_without_claiming_savings(self) -> None:
        values: list[Measurement] = []
        for index in range(5):
            values.extend(
                (
                    measurement(
                        f"task-{index}",
                        "trial",
                        "baseline",
                        1,
                        tokens=100,
                        cost=0.01,
                    ),
                    measurement(
                        f"task-{index}",
                        "trial",
                        "ablated",
                        0.5,
                        tokens=50,
                        cost=0.005,
                    ),
                )
            )
        effect = PairedAnalyzer(bootstrap_samples=100).analyze(
            tuple(values),
            baseline_variant_id="baseline",
            ablated_variant_id="ablated",
        )
        recommendation = SavingsAnalyzer().recommend(
            effect,
            Workload(runs_per_day=100),
        )
        self.assertEqual(recommendation.action, SavingsAction.KEEP)
        self.assertEqual(recommendation.projected_input_tokens_saved, 0)
        self.assertEqual(recommendation.projected_net_cost_saved_usd, 0)

    def test_measurement_uses_recorded_usage_and_context_fallback(self) -> None:
        replay = result(
            "run",
            "baseline",
            input_tokens=None,
            output_tokens=None,
            cost=None,
            duration=2.5,
        )
        item = Measurement.from_result(
            replay,
            Evaluation(scores={"quality": 0.8, "success": 1}),
            trial_id="trial",
            score_name="quality",
        )
        self.assertEqual(item.input_tokens, replay.context_tokens)
        self.assertEqual(item.output_tokens, 0)
        self.assertEqual(item.cost_usd, 0)
        self.assertEqual(item.latency_seconds, 2.5)

    def test_rejects_mixed_screening_and_target_model_evidence(self) -> None:
        baseline = measurement(
            "task",
            "trial",
            "baseline",
            1,
            tokens=100,
            cost=0.01,
        )
        ablated = Measurement(
            task_id="task",
            trial_id="trial",
            variant_id="ablated",
            score=0.8,
            success=True,
            input_tokens=50,
            output_tokens=10,
            cost_usd=0.005,
            latency_seconds=1,
            evidence_scope=EvidenceScope.SCREENING,
        )
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            PairedAnalyzer(bootstrap_samples=100).analyze(
                (baseline, ablated),
                baseline_variant_id="baseline",
                ablated_variant_id="ablated",
            )


if __name__ == "__main__":
    unittest.main()
