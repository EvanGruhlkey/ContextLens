"""Reproduce static ContextLens reports for pinned public context changes."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from contextlens import __version__
from contextlens.repository import (
    compare_repository_scans,
    render_diff_terminal,
    scan_git_ref,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", dest="case_id", required=True)
    parser.add_argument(
        "--manifest", type=Path, default=Path(__file__).with_name("cases.json")
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    case = load_case(arguments.manifest, arguments.case_id)
    with tempfile.TemporaryDirectory(prefix="contextlens-case-") as directory:
        root = Path(directory)
        _git(root, "init", "--quiet")
        _git(root, "remote", "add", "origin", str(case["clone_url"]))
        for commit in (case["base_commit"], case["candidate_commit"]):
            _git(
                root,
                "-c",
                "protocol.version=2",
                "fetch",
                "--quiet",
                "--filter=blob:none",
                "--depth=1",
                "origin",
                str(commit),
            )
        base = scan_git_ref(root, str(case["base_commit"]))
        candidate = scan_git_ref(root, str(case["candidate_commit"]))
        report = compare_repository_scans(
            root,
            base_ref=str(case["base_commit"]),
            base=base,
            candidate=candidate,
        )
        report_value = report.to_dict()
        report_value["root"] = str(case["clone_url"])
        value = {
            "case": case,
            "evidence": "observed/static",
            "verified": False,
            "reproduction": {
                "contextlens_version": __version__,
                "acquisition": "git fetch --filter=blob:none --depth=1",
                "checkout_performed": False,
                "token_count_method": "estimated_utf8_bytes_div_4",
            },
            "report": report_value,
        }
        print(render_diff_terminal(report))
        if arguments.output is not None:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(
                json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )


def load_case(path: Path, case_id: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    cases = value.get("cases", [])
    selected = [item for item in cases if item.get("id") == case_id]
    if len(selected) != 1:
        raise ValueError(f"unknown or duplicate case id: {case_id}")
    case = dict(selected[0])
    for field in ("repository", "clone_url", "base_commit", "candidate_commit"):
        if not case.get(field):
            raise ValueError(f"case {case_id!r} is missing {field}")
    return case


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(("git", *arguments), cwd=root, check=True)


if __name__ == "__main__":
    main()
