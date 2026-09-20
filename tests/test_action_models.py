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
