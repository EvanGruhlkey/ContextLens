"""Jev relevance decisions through Vercel's documented evaluation endpoint.

Jev answers bounded relevance questions and nothing else. It never chooses the
coding agent's next action, writes code, runs tools, plans, or summarizes.
Every failure path raises :class:`JevError` so callers can fail open.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from contextlens.models import estimate_state_tokens

ENDPOINT = "https://ai-gateway.vercel.sh/v1/evaluate"
MODEL = "typesafe-ai/jev"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

DEFAULT_MAX_STATE_TOKENS = 25_000
DEFAULT_MAX_REQUEST_TOKENS = 30_000
REQUEST_OVERHEAD_TOKENS = 20

T = TypeVar("T")


class JevError(RuntimeError):
    """Sanitized provider failure; it never carries a raw response body."""


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Validated probabilities for exactly the questions that were asked."""

    probabilities: Mapping[str, float]
    model: str
    input_tokens: int
    output_tokens: int
    cost: str | None
    latency_ms: float

    def probability(self, name: str) -> float:
        try:
            return self.probabilities[name]
        except KeyError as error:
            raise JevError(f"Jev did not answer {name}") from error


@dataclass(slots=True)
class JevUsage:
    """Jev spend, kept strictly separate from coding-model usage."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: str | None = None

    def add(self, evaluation: Evaluation) -> None:
        self.requests += 1
        self.input_tokens += evaluation.input_tokens
        self.output_tokens += evaluation.output_tokens
        if evaluation.cost is None:
            return
        try:
            total = float(self.cost or 0) + float(evaluation.cost)
        except ValueError:
            return
        self.cost = format(total, "f")

    def to_dict(self) -> dict[str, Any]:
        return {
            "jev_requests": self.requests,
            "jev_input_tokens": self.input_tokens,
            "jev_output_tokens": self.output_tokens,
            "jev_cost": self.cost or "0",
        }


class Judge(Protocol):
    """Anything that can answer Jev questions: the gateway, or a host adapter."""

    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation: ...


def boolean_question(
    instructions: str,
    *,
    keep: str | None = None,
    drop: str | None = None,
) -> dict[str, Any]:
    """Build one relevance question. ``keep``/``drop`` describe the two sides."""

    question: dict[str, Any] = {"type": "boolean", "instructions": instructions}
    criteria = {}
    if keep:
        criteria["true"] = keep
    if drop:
        criteria["false"] = drop
    if criteria:
        question["criteria"] = criteria
    return question


def batch_questions(
    items: Sequence[T],
    questions_for: Callable[[T], Mapping[str, Any]],
    *,
    state_tokens: int,
    max_request_tokens: int = DEFAULT_MAX_REQUEST_TOKENS,
) -> list[list[T]]:
    """Group items so state plus one batch of questions stays within the limit.

    The same state is resent with every batch, so a large state means more,
    smaller batches. An item whose questions cannot fit beside the state at all
    is left out; callers must treat unscored items as kept.
    """

    budget = max_request_tokens - state_tokens - REQUEST_OVERHEAD_TOKENS
    batches: list[list[T]] = []
    current: list[T] = []
    current_tokens = 0
    for item in items:
        tokens = estimate_state_tokens(_dumps(questions_for(item)))
        if tokens > budget:
            continue
        if current and current_tokens + tokens > budget:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def gateway_options() -> dict[str, Any]:
    """Provider routing, with paid-plan zero data retention as an opt-in."""

    options: dict[str, Any] = {"only": ["typesafe-ai"]}
    if os.environ.get("CONTEXTLENS_VERCEL_ZDR", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        options["zeroDataRetention"] = True
    return options


class JevGateway:
    """One bounded evaluation request; credentials come only from the host."""

    def __init__(self, *, timeout: float = 20.0) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("gateway timeout must be positive and finite")
        self.timeout = timeout

    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation:
        key = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
        if not key:
            raise JevError("Set AI_GATEWAY_API_KEY to use Jev through Vercel.")
        if not questions:
            raise JevError("Jev requires at least one question.")
        payload = {
            "model": MODEL,
            "state": dict(state),
            "questions": dict(questions),
            "providerOptions": {"gateway": gateway_options()},
        }
        request = urllib.request.Request(
            ENDPOINT,
            data=_dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=self.timeout
            ) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise JevError("Jev returned an oversized response.")
            data = json.loads(raw)
        except urllib.error.HTTPError as error:
            raise JevError(
                f"Jev evaluation failed (HTTP {error.code}); check gateway "
                "credentials, credits, and model availability."
            ) from None
        except (OSError, ValueError):
            raise JevError("Jev evaluation failed or timed out.") from None
        return parse_evaluation(
            data, set(questions), (time.monotonic() - started) * 1000
        )


def parse_evaluation(
    data: Any, expected: Iterable[str], latency_ms: float
) -> Evaluation:
    """Validate a response strictly before any deletion decision is made."""

    names = set(expected)
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise JevError("Jev returned invalid decisions.")
    answers = data["answers"]
    if set(answers) != names:
        raise JevError("Jev returned invalid decision identifiers.")
    probabilities: dict[str, float] = {}
    for name, answer in answers.items():
        if not isinstance(answer, dict) or answer.get("type") != "boolean":
            raise JevError("Jev returned an invalid decision type.")
        value = answer.get("probability")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise JevError("Jev returned an invalid probability.")
        probabilities[name] = float(value)
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    metadata = data.get("providerMetadata")
    gateway = metadata.get("gateway") if isinstance(metadata, dict) else None
    cost = gateway.get("cost") if isinstance(gateway, dict) else None
    model = data.get("model")
    return Evaluation(
        probabilities,
        model if isinstance(model, str) and model else MODEL,
        _tokens(usage.get("inputTokens")),
        _tokens(usage.get("outputTokens")),
        str(cost) if isinstance(cost, (str, int, float)) else None,
        latency_ms,
    )


def _tokens(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
