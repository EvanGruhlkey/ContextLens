import pytest

from contextlens.action_models import ActionKind, CandidateAction


def test_candidate_action_has_bounded_typed_state():
    action = CandidateAction(
        action_id="read_auth",
        kind=ActionKind.READ_SOURCE,
        description="Read the refresh token implementation",
        tool="context_read",
        arguments={"handle": "h_123"},
    )

    assert action.to_state() == {
        "id": "read_auth",
        "kind": "read_source",
        "description": "Read the refresh token implementation",
        "tool": "context_read",
        "arguments": {"handle": "h_123"},
    }


def test_capabilities_can_leave_arguments_to_the_host():
    read = CandidateAction("read", ActionKind.READ_SOURCE, "Read the implementation")
    test = CandidateAction(
        "test", ActionKind.RUN_TARGETED_TEST, "Run the focused test", "exec_command"
    )
    assert read.tool is None
    assert test.arguments is None


def test_open_ended_capability_cannot_include_a_command():
    with pytest.raises(ValueError, match="unsupported arguments"):
        CandidateAction(
            "test",
            ActionKind.RUN_TARGETED_TEST,
            "Run the focused test",
            "exec_command",
            {"command": "pytest"},
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"action_id": "bad id"},
        {"description": ""},
        {"description": "x" * 501},
        {"tool": "shell"},
        {"arguments": {"command": "rm -rf /"}},
    ],
)
def test_candidate_action_rejects_unbounded_or_unknown_values(changes):
    values = {
        "action_id": "search_token",
        "kind": ActionKind.SEARCH_REPOSITORY,
        "description": "Search for token configuration",
        "tool": "context_select",
        "arguments": {"focus": "TOKEN_TTL"},
    }
    values.update(changes)
    with pytest.raises(ValueError):
        CandidateAction(**values)
