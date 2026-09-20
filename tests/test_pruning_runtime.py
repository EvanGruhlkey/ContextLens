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
    assert scorer.requests[0].query == (
        "For the coding task 'Fix refresh timeout', what code in src/client.py "
        "is needed to answer: Trace request options?"
    )


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
    ) == (ObservationKind.LOG, None)


def test_explicit_kind_overrides_inference() -> None:
    observation = ToolObservation(
        "traceback",
        "shell",
        kind=ObservationKind.LOG,
        language="python",
    )

    assert classify_observation(observation) == (ObservationKind.LOG, "python")


def test_session_summarizes_the_complete_task(tmp_path: Path) -> None:
    scorer = _RecordingScorer()
    session = PruningSession(
        ContextPruner(scorer, ReceiptStore(tmp_path)),
        "Inspect output",
        minimum_tokens=0,
        context_radius=0,
    )
    session.observe(
        ToolObservation(_source(), "read_file", {"path": "src/first.py"})
    )
    session.observe(ToolObservation("short output", "shell"))

    summary = session.summary()
    payload = summary.to_dict()

    assert summary.observations == 2
    assert summary.pruned_observations == 1
    assert summary.saved_tokens > 0
    assert summary.reduction_fraction > 0
    assert summary.backend_counts == {"fixture-v1": 1, "passthrough": 1}
    assert summary.bypass_counts == {"unsupported_kind": 1}
    assert summary.reason_counts["semantic"] == 1
    assert payload["bypassed_observations"] == 1


def test_empty_session_has_zero_reduction(tmp_path: Path) -> None:
    session = PruningSession(
        ContextPruner(_RecordingScorer(), ReceiptStore(tmp_path)),
        "Inspect output",
    )

    summary = session.summary()

    assert summary.observations == 0
    assert summary.saved_tokens == 0
    assert summary.reduction_fraction == 0.0
