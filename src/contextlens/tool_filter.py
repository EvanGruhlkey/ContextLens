"""Experimental capability filter for the controller profile.

This is not observation filtering. The default product scores tool *results*,
not which action the coding agent should take next.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol

from contextlens.action_models import ActionKind, CandidateAction
from contextlens.jev_gateway import Evaluation, GatewayError, JevGateway

_RECOVERY_KINDS = frozenset({ActionKind.READ_SOURCE, ActionKind.READ_DEFERRED})


class ToolJudge(Protocol):
    def evaluate(
        self, state: dict[str, Any], questions: dict[str, Any]
    ) -> Evaluation: ...


@dataclass(frozen=True, slots=True)
class ToolFilterDecision:
    candidates: tuple[CandidateAction, ...]
    probabilities: dict[str, float]
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost: str | None
    latency_ms: float | None
    fallback_reason: str | None = None


class ToolFilter:
    def __init__(
        self, *, judge: ToolJudge | None = None, include_threshold: float = 0.5
    ) -> None:
        if not math.isfinite(include_threshold) or not 0 <= include_threshold <= 1:
            raise ValueError("include threshold must be between zero and one")
        self.judge = judge if judge is not None else JevGateway()
        self.include_threshold = include_threshold

    def filter(
        self,
        *,
        task: str,
        focus: str,
        candidates: list[CandidateAction],
    ) -> ToolFilterDecision:
        if not 2 <= len(candidates) <= 12:
            raise ValueError("two to twelve candidate actions are required")
        identifiers = [candidate.action_id for candidate in candidates]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("candidate action ids must be unique")
        state = {
            "task": task,
            "focus": focus,
            "candidate_capabilities": {
                item.action_id: {
                    "kind": item.kind.value,
                    "description": item.description,
                    "tool": item.tool,
                }
                for item in candidates
            },
        }
        questions = {
            item.action_id: {
                "type": "boolean",
                "instructions": (
                    f"Is candidate_capabilities.{item.action_id} useful now?"
                ),
            }
            for item in candidates
        }
        try:
            evaluation = self.judge.evaluate(state, questions)
        except GatewayError:
            return ToolFilterDecision(
                tuple(candidates),
                {},
                None,
                None,
                None,
                None,
                None,
                "gateway_unavailable",
            )
        probabilities = evaluation.probabilities
        if set(probabilities) != set(questions) or any(
            not math.isfinite(value) or not 0 <= value <= 1
            for value in probabilities.values()
        ):
            raise ValueError("invalid capability decisions")
        selected = tuple(
            item
            for item in candidates
            if item.kind in _RECOVERY_KINDS
            or probabilities[item.action_id] >= self.include_threshold
        )
        return ToolFilterDecision(
            selected,
            dict(probabilities),
            evaluation.model,
            evaluation.input_tokens,
            evaluation.output_tokens,
            evaluation.cost,
            evaluation.latency_ms,
        )
