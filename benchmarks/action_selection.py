"""Fixed bounded next-action benchmark using live Jev decisions."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextlens.action_controller import ActionController
from contextlens.action_models import ActionKind, CandidateAction


@dataclass(frozen=True)
class Case:
    case_id: str
    task: str
    focus: str
    observations: list[dict[str, Any]]
    gold: str


def candidate_actions() -> list[CandidateAction]:
    return [
        CandidateAction(
            "search",
            ActionKind.SEARCH_REPOSITORY,
            "Search the repository when the relevant location is unknown",
            "context_select",
            {"focus": "current focus"},
        ),
        CandidateAction(
            "read",
            ActionKind.READ_SOURCE,
            "Read current source from an already known evidence handle",
            "context_read",
            {"handle": "h_known"},
        ),
        CandidateAction(
            "test",
            ActionKind.RUN_TARGETED_TEST,
            "Ask the coding model to propose and run a targeted test",
        ),
        CandidateAction(
            "failure",
            ActionKind.INSPECT_FAILURE,
            "Inspect the latest failing test or traceback",
        ),
        CandidateAction(
            "edit",
            ActionKind.READY_TO_EDIT,
            "Return control because enough evidence exists to attempt the edit",
        ),
        CandidateAction(
            "stop",
            ActionKind.STOP,
            "Stop because the task is complete or no further action is useful",
        ),
    ]


def cases() -> list[Case]:
    return [
        Case(
            "unknown-location",
            "fix refresh tokens expiring early",
            "locate where TOKEN_TTL is applied; no source location is known",
            [],
            "search",
        ),
        Case(
            "known-handle",
            "fix refresh tokens expiring early",
            "inspect the known refresh implementation",
            [_obs("source", "Discovery returned handle h_known for refresh_token")],
            "read",
        ),
        Case(
            "need-reproduction",
            "fix an intermittent refresh expiry regression",
            "confirm the reported behavior before changing code",
            [_obs("user_constraint", "No failing test or traceback is available")],
            "test",
        ),
        Case(
            "failure-after-edit",
            "fix refresh tokens expiring early",
            "understand why the attempted patch still fails",
            [
                _obs(
                    "test_output",
                    "Targeted test failed after the edit: expected 3600, got 900",
                )
            ],
            "failure",
        ),
        Case(
            "enough-evidence",
            "fix refresh tokens expiring early",
            "decide whether investigation is complete",
            [
                _obs("source", "refresh_token passes TOKEN_TTL to encode"),
                _obs("test_output", "Expected 3600 but configuration loads 900"),
                _obs("configuration", "TOKEN_TTL default is incorrectly set to 900"),
            ],
            "edit",
        ),
        Case(
            "completed-task",
            "fix refresh tokens expiring early",
            "all requested work is complete",
            [_obs("test_output", "Targeted and broader authentication tests pass")],
            "stop",
        ),
    ]


def _obs(kind: str, summary: str) -> dict[str, Any]:
    return {"id": f"obs_{kind}", "type": kind, "summary": summary, "age_steps": 0}


def run(root: Path) -> dict[str, Any]:
    controller = ActionController()
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    rows = []
    for case in cases():
        decision = controller.choose_next_action(
            task=case.task,
            focus=case.focus,
            observations=case.observations,
            candidates=candidate_actions(),
            repository_revision=revision,
        )
        ranking = sorted(
            decision.probabilities,
            key=lambda action_id: -decision.probabilities[action_id],
        )
        rows.append(
            {
                "case": case.case_id,
                "gold": case.gold,
                "selected": (
                    decision.selected.action_id if decision.selected else None
                ),
                "ranking": ranking,
                "probabilities": decision.probabilities,
                "input_tokens": decision.input_tokens,
                "output_tokens": decision.output_tokens,
                "latency_ms": decision.latency_ms,
                "cost": decision.cost,
                "fallback_reason": decision.fallback_reason,
            }
        )
    return {
        "benchmark": "bounded_next_action_selection",
        "repository_revision": revision,
        "python": platform.python_version(),
        "summary": summarize(rows),
        "rows": rows,
        "limits": (
            "Fixed development cases with hand-authored gold capabilities; no tools "
            "are executed and no coding-task success is measured."
        ),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    valid = [row for row in rows if row["ranking"]]
    valid_count = len(valid)
    top_one = sum(row["ranking"][:1] == [row["gold"]] for row in valid)
    top_three = sum(row["gold"] in row["ranking"][:3] for row in valid)
    return {
        "cases": count,
        "valid_cases": valid_count,
        "fallbacks": count - valid_count,
        "top_1_correct": top_one,
        "top_1_accuracy": top_one / valid_count if valid_count else None,
        "planned_top_1_accuracy": top_one / count if count else 0.0,
        "top_3_correct": top_three,
        "top_3_recall": top_three / valid_count if valid_count else None,
        "planned_top_3_recall": top_three / count if count else 0.0,
        "input_tokens": sum(row["input_tokens"] or 0 for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/results/action-selection.json"),
    )
    args = parser.parse_args()
    report = run(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
