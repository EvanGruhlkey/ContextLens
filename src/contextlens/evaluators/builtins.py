"""Deterministic evaluators and extension adapters."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from contextlens.experiments.evaluation import Evaluation
from contextlens.experiments.model import ReplayResult, ReplayTask


class ExactMatchEvaluator:
    """Score normalized final output against a task-specific expected answer."""

    def __init__(
        self,
        expected: Mapping[str, str],
        *,
        case_sensitive: bool = False,
        collapse_whitespace: bool = True,
        evaluator_id: str = "exact-match-v1",
    ) -> None:
        self.expected = dict(expected)
        self.case_sensitive = case_sensitive
        self.collapse_whitespace = collapse_whitespace
        self._evaluator_id = evaluator_id

    @property
    def evaluator_id(self) -> str:
        return self._evaluator_id

    def evaluate(self, task: ReplayTask, result: ReplayResult) -> Evaluation:
        if task.task_id not in self.expected:
            raise KeyError(f"no expected answer for task {task.task_id!r}")
        actual = result.outcome.output_text if result.outcome is not None else ""
        expected = self.expected[task.task_id]
        matched = self._normalize(actual) == self._normalize(expected)
        return Evaluation(
            scores={"success": float(matched), "exact_match": float(matched)},
            evidence=(
                "normalized output matched expected answer"
                if matched
                else "normalized output did not match expected answer",
            ),
            metadata={"deterministic": True},
        )

    def _normalize(self, value: str) -> str:
        value = value.strip()
        if self.collapse_whitespace:
            value = re.sub(r"\s+", " ", value)
        return value if self.case_sensitive else value.casefold()


class TestResultsEvaluator:
    """Convert adapter-recorded test result lines into a success score."""

    def __init__(
        self,
        *,
        failure_markers: tuple[str, ...] = ("fail", "error", "timeout"),
        evaluator_id: str = "test-results-v1",
    ) -> None:
        self.failure_markers = tuple(marker.casefold() for marker in failure_markers)
        self._evaluator_id = evaluator_id

    @property
    def evaluator_id(self) -> str:
        return self._evaluator_id

    def evaluate(self, task: ReplayTask, result: ReplayResult) -> Evaluation:
        lines = result.outcome.test_results if result.outcome is not None else ()
        if not lines:
            return Evaluation(
                scores={"success": 0.0},
                evidence=("no test results were recorded",),
                metadata={"deterministic": True, "missing_evidence": True},
            )
        failures = tuple(
            line
            for line in lines
            if any(marker in line.casefold() for marker in self.failure_markers)
        )
        success = not failures
        return Evaluation(
            scores={"success": float(success)},
            evidence=failures or ("all recorded test results passed",),
            metadata={"deterministic": True, "test_result_count": len(lines)},
        )


EvaluationFunction = Callable[[ReplayTask, ReplayResult], Evaluation]


class CallableEvaluator:
    """Wrap a programmatic or model-graded evaluation function."""

    def __init__(
        self,
        evaluator_id: str,
        function: EvaluationFunction,
    ) -> None:
        if not evaluator_id:
            raise ValueError("evaluator_id cannot be empty")
        self._evaluator_id = evaluator_id
        self.function = function

    @property
    def evaluator_id(self) -> str:
        return self._evaluator_id

    def evaluate(self, task: ReplayTask, result: ReplayResult) -> Evaluation:
        return self.function(task, result)


class RecordedEvaluator:
    """Attach previously collected human or external evaluations by run ID."""

    def __init__(
        self,
        evaluations: Mapping[str, Evaluation],
        *,
        evaluator_id: str = "recorded-human-v1",
    ) -> None:
        self.evaluations = dict(evaluations)
        self._evaluator_id = evaluator_id

    @property
    def evaluator_id(self) -> str:
        return self._evaluator_id

    def evaluate(self, task: ReplayTask, result: ReplayResult) -> Evaluation:
        try:
            return self.evaluations[result.run_id]
        except KeyError as error:
            message = f"no recorded evaluation for run {result.run_id!r}"
            raise KeyError(message) from error
