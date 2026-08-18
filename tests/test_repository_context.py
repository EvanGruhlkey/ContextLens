from __future__ import annotations

import subprocess
from pathlib import Path

from contextlens.repository import (
    STATIC_EVIDENCE,
    diff_repository,
    scan_repository,
)


def test_scan_discovers_nested_context_and_conservative_findings(
    tmp_path: Path,
) -> None:
    (tmp_path / "packages" / "web").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "actual.py").write_text("pass\n", encoding="utf-8")
    repeated = "Always run the focused unit tests before changing shared code."
    (tmp_path / "AGENTS.md").write_text(
        f"# Rules\n\n- {repeated}\n- Read `src/missing.py` before editing.\n",
        encoding="utf-8",
    )
    (tmp_path / "packages" / "web" / "AGENTS.md").write_text(
        f"# Web\n\n- {repeated}\n",
        encoding="utf-8",
    )
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text(
        "Use packages/web/components for all user interface changes.\n",
        encoding="utf-8",
    )
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    (tmp_path / ".cursor" / "rules" / "python.mdc").write_text(
        "Always use Python type annotations for public functions.\n",
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"repo":{"command":"python"}}}',
        encoding="utf-8",
    )

    report = scan_repository(tmp_path)

    paths = {source.path for source in report.sources}
    assert paths == {
        ".cursor/rules/python.mdc",
        ".github/copilot-instructions.md",
        ".mcp.json",
        "AGENTS.md",
        "packages/web/AGENTS.md",
    }
    nested = next(
        source for source in report.sources if source.path.endswith("web/AGENTS.md")
    )
    assert nested.scope == "packages/web"
    categories = {finding.category for finding in report.findings}
    assert "nested_scope_duplicate" in categories
    assert "stale_reference" in categories
    assert "tool_schema_footprint" in categories
    assert all(finding.evidence == STATIC_EVIDENCE for finding in report.findings)
    assert not any(finding.verified for finding in report.findings)
    assert report.to_dict()["verified"] is False


def test_git_diff_compares_worktree_with_immutable_base(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "contextlens@example.com")
    _git(tmp_path, "config", "user.name", "ContextLens")
    (tmp_path / "AGENTS.md").write_text("Run tests.\n", encoding="utf-8")
    _git(tmp_path, "add", "AGENTS.md")
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    (tmp_path / "AGENTS.md").write_text(
        "Run tests.\nExplain every public API change in detail.\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("Use mechanical checks.\n", encoding="utf-8")

    report = diff_repository(tmp_path, base_ref=base)

    assert report.base_ref == base
    assert report.delta_tokens > 0
    assert {item.path for item in report.sources} == {"AGENTS.md", "CLAUDE.md"}
    assert report.to_dict()["evidence"] == STATIC_EVIDENCE
    assert report.to_dict()["verified"] is False


def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
