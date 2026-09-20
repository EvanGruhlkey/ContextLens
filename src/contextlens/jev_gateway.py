"""Typed Jev decisions through Vercel's documented evaluation endpoint."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

ENDPOINT = "https://ai-gateway.vercel.sh/v1/evaluate"
MODEL = "typesafe-ai/jev"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GatewayError(RuntimeError):
    """Public, sanitized provider failure; never contains raw response bodies."""


@dataclass(frozen=True)
class Evaluation:
    probabilities: dict[str, float]
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost: str | None
    latency_ms: float


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def gateway_options() -> dict[str, Any]:
    """Build provider routing options, with paid-plan ZDR as an explicit opt-in."""
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
            raise ValueError("Gateway timeout must be positive and finite")
        self.timeout = timeout

    def evaluate(self, state: dict[str, Any], questions: dict[str, Any]) -> Evaluation:
        key = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
        if not key:
            raise GatewayError("Set AI_GATEWAY_API_KEY to use Jev through Vercel.")
        payload = {
            "model": MODEL,
            "state": state,
            "questions": questions,
            "providerOptions": {"gateway": gateway_options()},
        }
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
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
                raise GatewayError("Jev returned an oversized response.")
            data = json.loads(raw)
        except urllib.error.HTTPError as error:
            raise GatewayError(
                f"Vercel evaluation failed (HTTP {error.code}); "
                "check Gateway credentials, credits and model availability."
            ) from None
        except (OSError, ValueError) as error:
            del error
            raise GatewayError("Vercel evaluation failed or timed out.") from None
        return parse_evaluation(
            data, set(questions), (time.monotonic() - started) * 1000
        )


def parse_evaluation(data: Any, expected: set[str], latency_ms: float) -> Evaluation:
    """Reject partial, unexpected and non-finite decisions before selecting code."""
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise GatewayError("Jev returned invalid decisions.")
    answers = data["answers"]
    if set(answers) != expected:
        raise GatewayError("Jev returned invalid decision identifiers.")
    probabilities: dict[str, float] = {}
    for name, answer in answers.items():
        if not isinstance(answer, dict) or answer.get("type") != "boolean":
            raise GatewayError("Jev returned an invalid decision type.")
        value = answer.get("probability")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise GatewayError("Jev returned an invalid probability.")
        probabilities[name] = float(value)
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    metadata = data.get("providerMetadata")
    gateway = metadata.get("gateway") if isinstance(metadata, dict) else None
    cost = gateway.get("cost") if isinstance(gateway, dict) else None
    model = data.get("model")
    return Evaluation(
        probabilities,
        model if isinstance(model, str) else MODEL,
        _tokens(usage.get("inputTokens")),
        _tokens(usage.get("outputTokens")),
        str(cost) if isinstance(cost, (str, int, float)) else None,
        latency_ms,
    )


def _tokens(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None
