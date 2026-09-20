import json

import pytest

from contextlens.action_controller import ActionController
from contextlens.action_models import ActionKind, CandidateAction
from contextlens.jev_gateway import Evaluation, GatewayError


class Judge:
    def __init__(self, probabilities=None, error=None):
        self.probabilities = probabilities or {"search": 0.2, "read": 0.8}
        self.error = error
        self.requests = []

    def evaluate(self, state, questions):
        self.requests.append((state, questions))
        if self.error:
            raise self.error
        return Evaluation(
            self.probabilities,
            "typesafe-ai/jev",
            120,
            12,
            "0",
            8.5,
        )


def actions():
    return [
        CandidateAction(
            "search",
            ActionKind.SEARCH_REPOSITORY,
            "Search for TOKEN_TTL",
            "context_select",
            {"focus": "TOKEN_TTL"},
        ),
        CandidateAction(
            "read",
            ActionKind.READ_SOURCE,
            "Read the known refresh implementation",
            "context_read",
            {"handle": "h_123"},
        ),
    ]


def test_controller_selects_only_an_offered_action_and_records_telemetry(tmp_path):
    judge = Judge()
    controller = ActionController(
        judge=judge, telemetry_path=tmp_path / "actions.jsonl"
    )

    decision = controller.choose_next_action(
        task="fix refresh expiry",
        focus="find why TTL is 900",
        observations=[
            {
                "id": "obs_1",
                "type": "test_output",
                "summary": "expected 3600, got 900",
                "age_steps": 0,
            }
        ],
        candidates=actions(),
        repository_revision="abc123",
    )

    assert decision.selected is not None
    assert decision.selected.action_id == "read"
    assert decision.probabilities == {"search": 0.2, "read": 0.8}
    assert decision.input_tokens == 120
    state, questions = judge.requests[0]
    assert set(state["candidate_actions"]) == {"search", "read"}
    assert set(questions) == {"search", "read"}
    row = json.loads((tmp_path / "actions.jsonl").read_text())
    assert row["selected"] == "read"
    assert row["repository_revision"] == "abc123"
    assert "h_123" not in json.dumps(row)


def test_controller_fails_open_when_gateway_is_unavailable():
    controller = ActionController(judge=Judge(error=GatewayError("unavailable")))
    decision = controller.choose_next_action(
        task="fix refresh expiry",
        focus="",
        observations=[],
        candidates=actions(),
    )
    assert decision.selected is None
    assert decision.fallback_reason == "gateway_unavailable"
    assert decision.available_action_ids == ("search", "read")


def test_controller_rejects_duplicate_or_unmatched_decisions():
    with pytest.raises(ValueError, match="unique"):
        ActionController(judge=Judge()).choose_next_action(
            task="task", focus="", observations=[], candidates=[actions()[0]] * 2
        )
    with pytest.raises(ValueError, match="invalid"):
        ActionController(judge=Judge({"invented": 1.0})).choose_next_action(
            task="task", focus="", observations=[], candidates=actions()
        )
