"""Deterministic evaluators and extension adapters."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from contextlens.experiments.evaluation import Evaluation
from contextlens.experiments.model import ReplayResult, ReplayStatus, ReplayTask
from contextlens.trace.model import TokenUsage


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


class CodingTaskEvaluator:
    """Mechanical coding-task dimensions with visible objective weights."""

    _balanced_weights: Mapping[str, float] = {
        "taskCompletion": 0.30,
        "tests": 0.25,
        "build": 0.10,
        "typeCheck": 0.10,
        "lint": 0.05,
        "patchQuality": 0.10,
        "patchScope": 0.10,
    }

    def __init__(
        self,
        *,
        objective: str = "balanced",
        weights: Mapping[str, float] | None = None,
    ) -> None:
        if objective not in {
            "quality",
            "cost_without_regression",
            "latency_without_regression",
            "balanced",
        }:
            raise ValueError(f"unsupported optimization objective: {objective}")
        selected = dict(weights or self._balanced_weights)
        if not selected or any(value < 0 for value in selected.values()):
            raise ValueError("evaluation weights must be nonnegative")
        total = sum(selected.values())
        if total <= 0:
            raise ValueError("evaluation weights must have a positive sum")
        self.objective = objective
        self.weights = {name: value / total for name, value in selected.items()}

    @property
    def evaluator_id(self) -> str:
        return f"coding-task-v1:{self.objective}"

    def evaluate(self, task: ReplayTask, result: ReplayResult) -> Evaluation:
        if result.status not in {ReplayStatus.COMPLETED, ReplayStatus.CACHED}:
            return Evaluation(
                scores={"success": 0.0},
                dimensions={"taskCompletion": 0.0},
                utility_score=0.0,
                success=False,
                evidence=(result.error or f"replay status: {result.status.value}",),
                runtime_ms=round(result.duration_seconds * 1_000),
                metadata={
                    "deterministic": True,
                    "objective": self.objective,
                    "weights": self.weights,
                },
            )
        outcome = result.outcome
        if outcome is None:
            raise ValueError("completed replay is missing its outcome")
        metadata = outcome.metadata
        tests = _test_score(outcome.test_results)
        build = _mechanical_score(metadata.get("build"))
        type_check = _mechanical_score(metadata.get("type_check"))
        lint = _mechanical_score(metadata.get("lint"))
        task_completion = _mechanical_score(metadata.get("task_completion"))
        if task_completion is None:
            task_completion = tests if tests is not None else 0.5
        dimensions = {
            "taskCompletion": task_completion,
            "tests": tests if tests is not None else 0.5,
            "build": build if build is not None else 0.5,
            "typeCheck": type_check if type_check is not None else 0.5,
            "lint": lint if lint is not None else 0.5,
            "patchQuality": _patch_quality(result),
            "patchScope": _patch_scope(task, result),
        }
        quality = sum(
            dimensions.get(name, 0.5) * weight
            for name, weight in self.weights.items()
        )
        # Resource objectives apply a bounded penalty only after quality is scored.
        resource_penalty = 0.0
        if self.objective in {"balanced", "cost_without_regression"}:
            resource_penalty += min(0.1, result.context_tokens / 1_000_000)
        if self.objective in {"balanced", "latency_without_regression"}:
            resource_penalty += min(0.1, result.duration_seconds / 10_000)
        utility = max(0.0, min(1.0, quality - resource_penalty))
        success = task_completion >= 0.5 and (tests is None or tests >= 0.5)
        evidence = tuple(outcome.test_results) + tuple(
            f"{name}={value:.3f}" for name, value in dimensions.items()
        )
        return Evaluation(
            scores={**dimensions, "quality": quality, "success": float(success)},
            dimensions=dimensions,
            utility_score=utility,
            success=success,
            evidence=evidence,
            tokens=TokenUsage(
                input=outcome.input_tokens or result.context_tokens,
                output=outcome.output_tokens or 0,
                cached=outcome.cached_input_tokens or 0,
            ),
            runtime_ms=round(result.duration_seconds * 1_000),
            tool_calls=outcome.tool_calls,
            retries=max(outcome.retries, result.attempt - 1),
            metadata={
                "deterministic": True,
                "objective": self.objective,
                "weights": self.weights,
                "resource_penalty": resource_penalty,
            },
        )


def _mechanical_score(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    normalized = str(value).casefold()
    if any(word in normalized for word in ("fail", "error", "invalid")):
        return 0.0
    if any(word in normalized for word in ("pass", "success", "clean")):
        return 1.0
    return None


def _test_score(results: tuple[str, ...]) -> float | None:
    if not results:
        return None
    return 0.0 if any("fail" in item.casefold() for item in results) else 1.0


def _patch_quality(result: ReplayResult) -> float:
    if not result.file_changes:
        return 0.5
    patchable = sum(change.patch is not None for change in result.file_changes)
    return patchable / len(result.file_changes)


def _patch_scope(task: ReplayTask, result: ReplayResult) -> float:
    allowed = task.metadata.get("allowed_files")
    if not isinstance(allowed, list | tuple | set | frozenset) or not allowed:
        return 1.0 if len(result.file_changes) <= 10 else 10 / len(result.file_changes)
    allowed_paths = {str(path) for path in allowed}
    if not result.file_changes:
        return 1.0
    related = sum(change.path in allowed_paths for change in result.file_changes)
    return related / len(result.file_changes)
