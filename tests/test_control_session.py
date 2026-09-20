from contextlens.action_controller import ActionController
from contextlens.action_models import ActionKind, CandidateAction
from contextlens.control_session import ControlSession
from contextlens.jev_gateway import Evaluation
from contextlens.observations import ObservationStore
from contextlens.retention import RetentionController
from contextlens.tool_filter import ToolFilter


class Judge:
    def __init__(self, answers):
        self.answers = iter(answers)

    def evaluate(self, state, questions):
        probabilities = next(self.answers)
        assert set(probabilities) == set(questions)
        return Evaluation(probabilities, "typesafe-ai/jev", 10, 2, "0", 1.0)


def candidates():
    return [
        CandidateAction(
            "search", ActionKind.SEARCH_REPOSITORY, "Search", "context_select"
        ),
        CandidateAction("read", ActionKind.READ_SOURCE, "Read", "context_read"),
        CandidateAction(
            "recover", ActionKind.READ_DEFERRED, "Recover", "context_expand"
        ),
        CandidateAction("edit", ActionKind.READY_TO_EDIT, "Edit"),
    ]


def test_session_redecides_after_each_new_observation(tmp_path):
    store = ObservationStore(tmp_path)
    retention_judge = Judge([{"obs_placeholder": 1.0}])
    # Empty first working set avoids a retention call; the second is configured below.
    tool_judge = Judge(
        [
            {"search": 0.9, "read": 0.9, "recover": 0.1, "edit": 0.1},
            {"search": 0.1, "read": 0.9, "recover": 0.1, "edit": 0.9},
        ]
    )
    action_judge = Judge(
        [
            {"search": 0.9, "read": 0.2, "recover": 0.1},
            {"read": 0.2, "recover": 0.1, "edit": 0.9},
        ]
    )
    session = ControlSession(
        task="fix expiry",
        store=store,
        actions=ActionController(judge=action_judge),
        retention=RetentionController(judge=retention_judge),
        tools=ToolFilter(judge=tool_judge),
    )

    first = session.next(focus="locate TTL", candidates=candidates())
    assert first.action.selected.action_id == "search"

    observed = session.observe(
        kind="search_result",
        summary="TTL is in config.py",
        content="config.py:12 TOKEN_TTL",
    )
    retention_judge.answers = iter([{observed.handle: 0.9}])
    second = session.next(focus="prepare change", candidates=candidates())
    assert second.action.selected.action_id == "edit"
    assert second.step == 2


def test_session_does_not_execute_selected_action(tmp_path):
    choices = candidates()
    session = ControlSession(
        task="task",
        store=ObservationStore(tmp_path),
        actions=ActionController(
            judge=Judge([{"search": 1.0, "read": 0.0, "recover": 0.0}])
        ),
        retention=RetentionController(judge=Judge([])),
        tools=ToolFilter(
            judge=Judge([{"search": 1.0, "read": 0.0, "recover": 0.0, "edit": 0.0}])
        ),
    )
    result = session.next(focus="find code", candidates=choices)
    assert result.action.selected.tool == "context_select"
    assert session.store.active() == []
