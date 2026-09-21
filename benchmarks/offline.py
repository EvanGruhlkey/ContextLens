"""Offline harness check for the paired benchmark.

No coding model and no Jev gateway: a scripted solver replays a fixed tool
sequence against a synthetic repository, and a local judge answers the same
relevance questions Jev would. This measures the ContextLens layers and the
report plumbing, not a coding model. It never makes a network request.

    python -m benchmarks.offline --output benchmarks/results/offline.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.agent import CONDITIONS, Answer, ToolCall, run_agent
from benchmarks.report import analyze, markdown
from contextlens.compaction import CompactionConfig
from contextlens.filtering import PruneConfig, protected_line
from contextlens.jev import Evaluation
from contextlens.models import Message

NOISE_LINES = 1200
SCRIPT = (
    ("grep", {"pattern": "def parse_amount"}),
    ("read_file", {"path": "library/amounts.py"}),
    ("shell", {"command": "python -m pytest tests -q"}),
    ("shell", {"command": "python -m pip install --dry-run ."}),
    ("read_file", {"path": "library/amounts.py"}),
    ("shell", {"command": "python -m pytest tests -q"}),
)


class LocalJudge:
    """Answer relevance questions with a deterministic local heuristic.

    Stands in for Jev so the harness can be measured offline. Chunks and stale
    interactions that look like progress noise score low; anything holding a
    diagnostic, a result, or the word the task mentions scores high.
    """

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword.lower()
        self.requests = 0

    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation:
        self.requests += 1
        texts = self._texts(state)
        probabilities = {
            name: self._score(name, question, texts)
            for name, question in questions.items()
        }
        return Evaluation(probabilities, "local-heuristic", 0, 0, None, 0.0)

    def _texts(self, state: Mapping[str, Any]) -> dict[str, str]:
        texts: dict[str, str] = {}
        for chunk in state.get("output") or []:
            if isinstance(chunk, dict):
                texts[str(chunk.get("id"))] = str(chunk.get("text"))
        for entry in state.get("history") or []:
            if not isinstance(entry, dict):
                continue
            for call in entry.get("tool_calls") or []:
                if isinstance(call, dict):
                    texts[str(call.get("id"))] = json.dumps(call)
        return texts

    def _score(
        self, name: str, question: Mapping[str, Any], texts: Mapping[str, str]
    ) -> float:
        identifier = name.split("_", 1)[1] if "_" in name else name
        text = texts.get(identifier, str(question.get("instructions", "")))
        if protected_line(text):
            return 0.95
        if self.keyword in text.lower():
            return 0.9
        if re.search(r"progress|downloading|collecting|cached|Requirement", text):
            return 0.02
        return 0.05


def build_repository(root: Path) -> None:
    """A tiny repository whose test suite prints a lot of noise."""

    (root / "library").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "library" / "__init__.py").write_text("", encoding="utf-8")
    body = ['"""Amount helpers."""', "", ""]
    for index in range(120):
        body += [
            f"def helper_{index}(value: int) -> int:",
            f'    """Unrelated helper {index}."""',
            f"    return value + {index}",
            "",
            "",
        ]
    body += [
        "def parse_amount(text: str) -> int:",
        '    """Parse a currency amount into whole cents."""',
        "    cleaned = text.replace('$', '').strip()",
        "    return int(float(cleaned) * 100)",
        "",
    ]
    (root / "library" / "amounts.py").write_text("\n".join(body), encoding="utf-8")
    noise = "\n".join(
        f'    print("progress step {index} collecting fixtures")'
        for index in range(NOISE_LINES)
    )
    (root / "tests" / "test_amounts.py").write_text(
        "from library.amounts import parse_amount\n\n\n"
        "def test_noise() -> None:\n"
        f"{noise}\n"
        "    assert True\n\n\n"
        "def test_parse_amount_rounds_cents() -> None:\n"
        '    assert parse_amount("$1.005") == 101\n',
        encoding="utf-8",
    )
    subprocess.run(("git", "init", "--quiet"), cwd=root, check=True)
    subprocess.run(("git", "add", "."), cwd=root, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.email=harness@contextlens.invalid",
            "-c",
            "user.name=harness",
            "commit",
            "--quiet",
            "-m",
            "initial",
        ),
        cwd=root,
        check=True,
    )


def scripted_solver() -> Any:
    """Replay a fixed tool sequence, then answer. Identical per condition."""

    state = {"step": 0}

    def solver(messages: Sequence[Message]) -> ToolCall | Answer:
        del messages
        step = state["step"]
        state["step"] = step + 1
        if step >= len(SCRIPT):
            return Answer("inspected the parser and the failing test")
        name, arguments = SCRIPT[step]
        return ToolCall(name, arguments, f"call-{step}")

    return solver


def run(output: Path) -> dict[str, Any]:
    workspace_root = output.parent / (output.stem + "-workspaces")
    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    base = workspace_root / "base"
    build_repository(base)
    rows: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        workspace = workspace_root / condition
        shutil.copytree(base, workspace)
        metrics, transcript = run_agent(
            workspace,
            "fix parse_amount so it rounds cents correctly",
            condition,
            state=workspace_root / f"{condition}-state",
            solver=scripted_solver(),
            judge=LocalJudge("parse_amount"),
            prune_config=PruneConfig(minimum_tokens=600, chunk_lines=20),
            # The trigger is deliberately low: after live pruning this small
            # scripted transcript would never reach a production threshold, and
            # the point of the offline check is to exercise both layers.
            compaction_config=CompactionConfig(
                trigger_tokens=500, preserve_recent_messages=4
            ),
            max_turns=len(SCRIPT) + 2,
            timeout=600,
        )
        row = metrics.to_dict()
        row.update(
            {
                "case": "offline-parse-amount",
                "trial": 0,
                "condition": condition,
                "verified_success": row["status"] == "completed",
                "transcript_messages": len(transcript),
            }
        )
        rows.append(row)
    report = {
        "benchmark": "offline_harness_check",
        "started_at": datetime.now(UTC).isoformat(),
        "model": "scripted-solver",
        "reasoning": "none",
        "trials": 1,
        "timeout": 600,
        "max_turns": len(SCRIPT) + 2,
        "conditions": list(CONDITIONS),
        "note": (
            "No coding model and no Jev gateway. A scripted solver replays one "
            "fixed tool sequence and a local heuristic answers the relevance "
            "questions. Coding-model token columns are zero because no coding "
            "model ran. This measures the ContextLens layers and the report, "
            "not task success or frontier-model savings."
        ),
        "tasks": [{"case_id": "offline-parse-amount"}],
        "rows": rows,
        "analysis": analyze(rows, 1),
        "status": "completed",
    }
    shutil.rmtree(workspace_root)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/results/offline.json")
    )
    arguments = parser.parse_args(argv)
    report = run(arguments.output.resolve())
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    arguments.output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    for row in report["rows"]:
        print(
            json.dumps(
                {
                    key: row[key]
                    for key in (
                        "condition",
                        "raw_tool_output_tokens",
                        "injected_tool_output_tokens",
                        "tool_output_tokens_removed",
                        "compaction_events",
                        "final_transcript_tokens",
                        "jev_requests",
                    )
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
