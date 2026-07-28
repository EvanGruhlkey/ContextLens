"""Small dependency-free predictor trained on verified context effects."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Protocol

from contextlens.profiler.model import SourceProfile, UsageLabel
from contextlens.trace.model import SourceKind

_KINDS = tuple(kind.value for kind in SourceKind)
_LABELS = tuple(label.value for label in UsageLabel)


@dataclass(frozen=True, slots=True)
class TrainingExample:
    """Profiler features paired with a verified context effect."""

    profile: SourceProfile
    verified_effect: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.verified_effect):
            raise ValueError("verified_effect must be finite")


@dataclass(frozen=True, slots=True)
class ValuePrediction:
    """Estimated context effect; positive means likely helpful."""

    source_id: str
    predicted_effect: float
    uncertainty: float
    model_version: str = "ridge-v1"
    evidence_scope: str = "predicted"


class ValuePredictor(Protocol):
    """Predict context value from one-run profile features."""

    def predict(self, profile: SourceProfile) -> ValuePrediction:
        """Return a predicted context effect."""


@dataclass(frozen=True, slots=True)
class RecalibrationPolicy:
    """Decide when predicted removals need another verified experiment."""

    verification_interval: int = 10
    max_prediction_uncertainty: float = 0.1
    minimum_training_examples: int = 20

    def __post_init__(self) -> None:
        if self.verification_interval < 1 or self.minimum_training_examples < 2:
            raise ValueError("recalibration intervals must be positive")
        if self.max_prediction_uncertainty < 0:
            raise ValueError("max_prediction_uncertainty cannot be negative")

    def should_verify(
        self,
        *,
        predictions_since_verification: int,
        training_examples: int,
        prediction_uncertainty: float,
    ) -> bool:
        return (
            training_examples < self.minimum_training_examples
            or predictions_since_verification >= self.verification_interval
            or prediction_uncertainty > self.max_prediction_uncertainty
        )


class ContextValuePredictor:
    """Ridge regression over stable one-run profiler features."""

    def __init__(self, *, regularization: float = 1.0) -> None:
        if regularization <= 0:
            raise ValueError("regularization must be positive")
        self.regularization = regularization
        self._weights: tuple[float, ...] | None = None
        self._residual_sd = 0.0
        self.training_examples = 0

    def fit(
        self,
        examples: tuple[TrainingExample, ...],
    ) -> ContextValuePredictor:
        if len(examples) < 2:
            raise ValueError("at least two verified examples are required")
        rows = [_features(example.profile) for example in examples]
        targets = [example.verified_effect for example in examples]
        width = len(rows[0])
        matrix = [[0.0] * width for _ in range(width)]
        vector = [0.0] * width
        for row, target in zip(rows, targets, strict=True):
            for left in range(width):
                vector[left] += row[left] * target
                for right in range(width):
                    matrix[left][right] += row[left] * row[right]
        for index in range(1, width):
            matrix[index][index] += self.regularization
        matrix[0][0] += self.regularization * 0.01
        weights = _solve(matrix, vector)
        residuals = [
            target
            - sum(
                weight * value
                for weight, value in zip(weights, row, strict=True)
            )
            for row, target in zip(rows, targets, strict=True)
        ]
        self._weights = tuple(weights)
        self._residual_sd = (
            statistics.stdev(residuals) if len(residuals) > 1 else 0.0
        )
        self.training_examples = len(examples)
        return self

    def predict(self, profile: SourceProfile) -> ValuePrediction:
        if self._weights is None:
            raise RuntimeError("fit the predictor before making predictions")
        values = _features(profile)
        prediction = sum(
            weight * value
            for weight, value in zip(self._weights, values, strict=True)
        )
        return ValuePrediction(
            source_id=profile.source_id,
            predicted_effect=prediction,
            uncertainty=self._residual_sd,
        )

    def to_dict(self) -> dict[str, object]:
        if self._weights is None:
            raise RuntimeError("fit the predictor before serialization")
        return {
            "model_version": "ridge-v1",
            "regularization": self.regularization,
            "weights": list(self._weights),
            "residual_sd": self._residual_sd,
            "training_examples": self.training_examples,
            "feature_kinds": list(_KINDS),
            "feature_labels": list(_LABELS),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ContextValuePredictor:
        if value.get("model_version") != "ridge-v1":
            raise ValueError("unsupported predictor model version")
        feature_kinds = value.get("feature_kinds")
        feature_labels = value.get("feature_labels")
        weights = value.get("weights")
        regularization = value.get("regularization")
        residual_sd = value.get("residual_sd")
        training_examples = value.get("training_examples")
        if not isinstance(feature_kinds, list):
            raise ValueError("predictor feature_kinds must be a list")
        if not isinstance(feature_labels, list):
            raise ValueError("predictor feature_labels must be a list")
        if not isinstance(weights, list):
            raise ValueError("predictor weights must be a list")
        if not isinstance(regularization, int | float):
            raise ValueError("predictor regularization must be numeric")
        if not isinstance(residual_sd, int | float):
            raise ValueError("predictor residual_sd must be numeric")
        if not isinstance(training_examples, int):
            raise ValueError("predictor training_examples must be an integer")
        if tuple(str(item) for item in feature_kinds) != _KINDS:
            raise ValueError("predictor source-kind schema does not match")
        if tuple(str(item) for item in feature_labels) != _LABELS:
            raise ValueError("predictor usage-label schema does not match")
        if not all(isinstance(item, int | float) for item in weights):
            raise ValueError("predictor weights must be numeric")
        predictor = cls(regularization=float(regularization))
        predictor._weights = tuple(float(item) for item in weights)
        predictor._residual_sd = float(residual_sd)
        predictor.training_examples = training_examples
        return predictor


def _features(profile: SourceProfile) -> tuple[float, ...]:
    kind_values = tuple(float(profile.kind == kind) for kind in _KINDS)
    label_values = tuple(float(profile.label.value == label) for label in _LABELS)
    return (
        1.0,
        math.log1p(profile.token_count) / 10,
        profile.position,
        profile.output_overlap or 0.0,
        math.log1p(len(profile.duplicated_by)),
        math.log1p(profile.age_seconds or 0.0) / 20,
        math.log1p(profile.retrieval_rank or 0) / 10,
        *kind_values,
        *label_values,
    )


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a nonsingular linear system with pivoted Gaussian elimination."""

    size = len(vector)
    augmented = [
        [*row, vector[index]]
        for index, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = max(
            range(column, size),
            key=lambda row: abs(augmented[row][column]),
        )
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("training matrix is singular")
        augmented[column], augmented[pivot] = (
            augmented[pivot],
            augmented[column],
        )
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            augmented[row] = [
                left - factor * right
                for left, right in zip(
                    augmented[row],
                    augmented[column],
                    strict=True,
                )
            ]
    return [augmented[index][-1] for index in range(size)]
