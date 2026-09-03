from __future__ import annotations

from pathlib import Path

from contextlens.pruning import (
    ContextPruner,
    ObservationKind,
    PruneRequest,
    PruningSession,
    ReceiptStore,
    SemanticScores,
    TaskGoal,
    ToolObservation,
    classify_observation,
)


class _RecordingScorer:
    backend_id = "fixture-v1"

    def __init__(self) -> None:
        self.requests: list[PruneRequest] = []

    def score(self, request: PruneRequest) -> SemanticScores:
        self.requests.append(request)
        return SemanticScores(self.backend_id, {2: 1.0})


def _source() -> str:
    lines = ["VALUE = 1", "print(VALUE)"]
    lines.extend(f"unused_{index} = {index}" for index in range(40))
    return "\n".join(lines) + "\n"


def test_goal_identity_survives_focus_changes() -> None:
    goal = TaskGoal("  Fix   refresh timeout  ")
    narrowed = goal.narrowed(" Trace retry configuration ")

    assert goal.objective == "Fix refresh timeout"
    assert narrowed.goal_id == goal.goal_id
    assert narrowed.focus == "Trace retry configuration"


def test_session_applies_task_and_current_focus(tmp_path: Path) -> None:
    scorer = _RecordingScorer()
    session = PruningSession(
        ContextPruner(scorer, ReceiptStore(tmp_path)),
        "Fix refresh timeout",
        minimum_tokens=0,
        context_radius=0,
    )
    session.set_focus("Trace request options")

    observation = session.observe(
        ToolObservation(
            content=_source(),
            tool="read_file",
            arguments={"path": "src/client.py"},
        )
    )

    assert observation.goal_id == session.goal.goal_id
    assert observation.result.retained_tokens < observation.result.original_tokens
    assert scorer.requests[0].task == "Fix refresh timeout"
    assert scorer.requests[0].focus == "Trace request options"
    assert scorer.requests[0].query.startswith("Trace request options")


def test_classifier_is_conservative() -> None:
    assert classify_observation(
        ToolObservation("x = 1", "read_file", {"path": "src/a.py"})
    ) == (ObservationKind.CODE, "python")
    assert classify_observation(
        ToolObservation("a.py:2:x", "rg", {"pattern": "x"})
    ) == (ObservationKind.SEARCH, None)
    assert classify_observation(
        ToolObservation("hello", "read_file", {"path": "README.md"})
    ) == (ObservationKind.TEXT, None)
    assert classify_observation(
        ToolObservation("ok", "shell", {"command": "test"})
    ) == (ObservationKind.TEXT, None)


def test_explicit_kind_overrides_inference() -> None:
    observation = ToolObservation(
        "traceback",
        "shell",
        kind=ObservationKind.LOG,
        language="python",
    )

    assert classify_observation(observation) == (ObservationKind.LOG, "python")
