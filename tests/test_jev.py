from __future__ import annotations

from typing import Any

import pytest

from contextlens.jev import (
    Evaluation,
    JevError,
    JevGateway,
    JevUsage,
    batch_questions,
    boolean_question,
    gateway_options,
    parse_evaluation,
)


def answer(probability: float) -> dict[str, Any]:
    return {"type": "boolean", "probability": probability}


def test_boolean_question_includes_optional_criteria() -> None:
    assert boolean_question("keep?") == {
        "type": "boolean",
        "instructions": "keep?",
    }
    question = boolean_question("keep?", keep="needed", drop="noise")
    assert question["criteria"] == {"true": "needed", "false": "noise"}


def test_parse_evaluation_returns_validated_probabilities() -> None:
    evaluation = parse_evaluation(
        {
            "model": "typesafe-ai/jev",
            "answers": {"c1": answer(0.9), "c2": answer(0.1)},
            "usage": {"inputTokens": 40, "outputTokens": 4},
            "providerMetadata": {"gateway": {"cost": "0.0001"}},
        },
        {"c1", "c2"},
        12.5,
    )
    assert evaluation.probabilities == {"c1": 0.9, "c2": 0.1}
    assert evaluation.input_tokens == 40
    assert evaluation.output_tokens == 4
    assert evaluation.cost == "0.0001"
    assert evaluation.probability("c1") == 0.9


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"answers": []},
        {"answers": {"c1": answer(0.5), "c2": answer(0.5)}},
        {"answers": {"c1": {"type": "score", "probability": 0.5}}},
        {"answers": {"c1": {"type": "boolean", "probability": True}}},
        {"answers": {"c1": {"type": "boolean", "probability": 1.5}}},
        {"answers": {"c1": {"type": "boolean", "probability": float("nan")}}},
        {"answers": {"c1": {"type": "boolean"}}},
    ],
)
def test_parse_evaluation_rejects_invalid_responses(data: Any) -> None:
    with pytest.raises(JevError):
        parse_evaluation(data, {"c1"}, 1.0)


def test_missing_answer_lookup_raises() -> None:
    evaluation = parse_evaluation({"answers": {"c1": answer(0.5)}}, {"c1"}, 1.0)
    with pytest.raises(JevError):
        evaluation.probability("c2")


def test_usage_accumulates_tokens_and_cost() -> None:
    usage = JevUsage()
    usage.add(Evaluation({}, "m", 10, 2, "0.5", 1.0))
    usage.add(Evaluation({}, "m", 5, 1, "0.25", 1.0))
    assert usage.to_dict() == {
        "jev_requests": 2,
        "jev_input_tokens": 15,
        "jev_output_tokens": 3,
        "jev_cost": "0.750000",
    }


def test_usage_reports_zero_cost_when_provider_omits_it() -> None:
    usage = JevUsage()
    usage.add(Evaluation({}, "m", 1, 1, None, 1.0))
    assert usage.to_dict()["jev_cost"] == "0"


def test_batch_questions_splits_on_the_request_budget() -> None:
    items = [f"chunk-{index}" for index in range(20)]
    batches = batch_questions(
        items,
        lambda item: {item: boolean_question(f"keep {item}?")},
        state_tokens=0,
        max_request_tokens=120,
    )
    assert sum(len(batch) for batch in batches) == len(items)
    assert len(batches) > 1


def test_batch_questions_drops_items_that_cannot_fit() -> None:
    batches = batch_questions(
        ["a"],
        lambda item: {item: boolean_question("x" * 4000)},
        state_tokens=0,
        max_request_tokens=100,
    )
    assert batches == []


def test_gateway_options_opt_into_zero_data_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTEXTLENS_VERCEL_ZDR", raising=False)
    assert gateway_options() == {"only": ["typesafe-ai"]}
    monkeypatch.setenv("CONTEXTLENS_VERCEL_ZDR", "1")
    assert gateway_options()["zeroDataRetention"] is True


def test_gateway_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(JevError):
        JevGateway().evaluate({}, {"c1": boolean_question("keep?")})


def test_gateway_requires_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test")
    with pytest.raises(JevError):
        JevGateway().evaluate({}, {})


def test_gateway_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError):
        JevGateway(timeout=0)
