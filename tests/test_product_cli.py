from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

from contextlens.cli import main


def test_scan_diff_and_verify_work_without_contextlens_instrumentation(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "contextlens@example.com")
    _git(tmp_path, "config", "user.name", "ContextLens")
    (tmp_path / "packages" / "api").mkdir(parents=True)
    repeated = "Always run the focused tests before changing shared code."
    (tmp_path / "AGENTS.md").write_text(f"- {repeated}\n", encoding="utf-8")
    nested = tmp_path / "packages" / "api" / "AGENTS.md"
    nested.write_text(f"- {repeated}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "base context")
    base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    nested.unlink()

    agent = tmp_path / "agent.py"
    agent.write_text(
        """import json
import os

request = json.load(open(os.environ["CONTEXTLENS_REQUEST"], encoding="utf-8"))
tokens = sum(item["token_count"] for item in request["context"])
result = {
    "output_text": "ok",
    "input_tokens": 1000 + tokens,
    "cached_input_tokens": 800,
    "output_tokens": 10,
    "tool_calls": 1,
    "metadata": {"turns": 2, "files_read": ["fixture.txt"]},
}
open(os.environ["CONTEXTLENS_RESULT"], "w", encoding="utf-8").write(json.dumps(result))
""",
        encoding="utf-8",
    )
    config = tmp_path / "evals.json"
    config.write_text(
        json.dumps(
            {
                "trials": 2,
                "agent": {
                    "type": "subprocess",
                    "command": [sys.executable, str(agent)],
                    "provider": "fixture",
                    "model": "deterministic",
                },
                "tasks": [
                    {
                        "id": "smoke",
                        "instruction": "Return ok.",
                        "workspace": ".",
                        "expected_output": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    scan_output = tmp_path / "scan.json"
    diff_output = tmp_path / "diff.json"
    verify_output = tmp_path / "verify.json"

    with redirect_stdout(io.StringIO()):
        main(["scan", str(tmp_path), "--format", "json", "--output", str(scan_output)])
        main(
            [
                "diff",
                str(tmp_path),
                "--base",
                base,
                "--format",
                "json",
                "--output",
                str(diff_output),
            ]
        )
        main(
            [
                "verify",
                str(config),
                "--repository",
                str(tmp_path),
                "--base",
                base,
                "--format",
                "json",
                "--output",
                str(verify_output),
            ]
        )

    scan = json.loads(scan_output.read_text(encoding="utf-8"))
    context_diff = json.loads(diff_output.read_text(encoding="utf-8"))
    verification = json.loads(verify_output.read_text(encoding="utf-8"))
    assert scan["report_type"] == "repository_context_scan"
    assert scan["verified"] is False
    assert context_diff["summary"]["delta_estimated_tokens"] < 0
    assert verification["verdict"] == "PASS"
    assert verification["base"]["quality"]["successes"] == 2
    assert (
        verification["candidate"]["economics"]["provider_input_tokens"]
        < (verification["base"]["economics"]["provider_input_tokens"])
    )


def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
