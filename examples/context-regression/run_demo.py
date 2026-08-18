"""Create a temporary Git repo and exercise scan, diff, and verify locally."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> int:
    with TemporaryDirectory(prefix="contextlens-demo-") as directory:
        root = Path(directory)
        _git(root, "init", "--quiet")
        _git(root, "config", "user.email", "contextlens@example.com")
        _git(root, "config", "user.name", "ContextLens demo")
        nested = root / "packages" / "api" / "AGENTS.md"
        nested.parent.mkdir(parents=True)
        instruction = "Always run focused tests before changing shared code."
        (root / "AGENTS.md").write_text(f"- {instruction}\n", encoding="utf-8")
        nested.write_text(f"- {instruction}\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "--quiet", "-m", "base context")
        base = _git(root, "rev-parse", "HEAD").stdout.strip()

        # Candidate change: remove one exact nested duplicate.
        nested.unlink()
        agent = root / "fixture_agent.py"
        agent.write_text(_AGENT, encoding="utf-8")
        config = root / "evals.json"
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
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        commands = (
            ("scan", str(root)),
            ("diff", str(root), "--base", base),
            (
                "verify",
                str(config),
                "--repository",
                str(root),
                "--base",
                base,
            ),
        )
        for command in commands:
            print(f"\n$ contextlens {' '.join(command)}\n")
            completed = subprocess.run(
                (sys.executable, "-m", "contextlens.cli", *command),
                check=False,
            )
            if completed.returncode != 0:
                return completed.returncode
    return 0


def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


_AGENT = """import json
import os

request = json.load(open(os.environ["CONTEXTLENS_REQUEST"], encoding="utf-8"))
tokens = sum(item["token_count"] for item in request["context"])
result = {
    "output_text": "ok",
    "input_tokens": 1000 + tokens,
    "cached_input_tokens": 800,
    "output_tokens": 10,
    "tool_calls": 1,
    "metadata": {"turns": 2, "files_read": ["fixture.py"]},
}
open(os.environ["CONTEXTLENS_RESULT"], "w", encoding="utf-8").write(
    json.dumps(result)
)
"""


if __name__ == "__main__":
    raise SystemExit(main())
