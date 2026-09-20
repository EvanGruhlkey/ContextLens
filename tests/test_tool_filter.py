from contextlens.action_models import ActionKind, CandidateAction
from contextlens.jev_gateway import Evaluation, GatewayError
from contextlens.tool_filter import ToolFilter


class Judge:
    def __init__(self, probabilities=None, error=None):
        self.probabilities = probabilities or {}
        self.error = error

    def evaluate(self, state, questions):
        if self.error:
            raise self.error
        return Evaluation(self.probabilities, "typesafe-ai/jev", 9, 2, "0", 2.0)


def actions():
    return [
        CandidateAction(
            "search", ActionKind.SEARCH_REPOSITORY, "Search", "context_select"
        ),
        CandidateAction("read", ActionKind.READ_SOURCE, "Read", "context_read"),
        CandidateAction(
            "recover", ActionKind.READ_DEFERRED, "Recover", "context_expand"
        ),
        CandidateAction("test", ActionKind.RUN_TARGETED_TEST, "Run a test"),
    ]


def test_filter_keeps_relevant_and_recovery_capabilities():
    result = ToolFilter(
        judge=Judge({"search": 0.9, "read": 0.1, "recover": 0.1, "test": 0.2})
    ).filter(task="locate ttl", focus="search", candidates=actions())
    assert [item.action_id for item in result.candidates] == [
        "search",
        "read",
        "recover",
    ]
    assert result.model == "typesafe-ai/jev"
    assert result.cost == "0"


def test_filter_failure_returns_every_capability():
    result = ToolFilter(judge=Judge(error=GatewayError("down"))).filter(
        task="task", focus="focus", candidates=actions()
    )
    assert result.fallback_reason == "gateway_unavailable"
    assert result.candidates == tuple(actions())
