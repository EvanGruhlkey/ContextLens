from __future__ import annotations

import json
from pathlib import Path


def test_real_context_change_manifest_is_pinned_and_selection_neutral() -> None:
    path = Path(__file__).parents[1] / "case-studies" / "cases.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    cases = value["cases"]

    assert 5 <= len(cases) <= 10
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["repository"] for case in cases}) >= 5
    assert any("Remove" in case["change"] for case in cases)
    assert any("add" in case["change"].casefold() for case in cases)
    for case in cases:
        assert len(case["base_commit"]) == 40
        assert len(case["candidate_commit"]) == 40
        assert case["base_commit"] != case["candidate_commit"]
        assert case["context_paths"]
        assert case["study_status"] == "static_ready"
        assert case["commit_url"].startswith(
            f"https://github.com/{case['repository']}/commit/"
        )


def test_checked_in_real_report_is_explicitly_unverified() -> None:
    path = (
        Path(__file__).parents[1]
        / "case-studies"
        / "reports"
        / "vscode-add-agents.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))

    assert value["case"]["repository"] == "microsoft/vscode"
    assert value["evidence"] == "observed/static"
    assert value["verified"] is False
    assert value["report"]["summary"]["delta_estimated_tokens"] == 68
