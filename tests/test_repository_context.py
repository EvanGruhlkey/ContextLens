from __future__ import annotations

import subprocess
from pathlib import Path

from contextlens.repository import (
    STATIC_EVIDENCE,
    diff_repository,
    resolve_effective_context,
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


def test_git_diff_reports_added_deleted_and_renamed_context_in_dirty_worktree(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "contextlens@example.com")
    _git(tmp_path, "config", "user.name", "ContextLens")
    (tmp_path / "nested").mkdir()
    (tmp_path / "AGENTS.md").write_text("root rules\n", encoding="utf-8")
    (tmp_path / "nested" / "AGENTS.md").write_text("nested rules\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    (tmp_path / "AGENTS.md").rename(tmp_path / "CLAUDE.md")
    (tmp_path / "nested" / "AGENTS.md").unlink()
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    (tmp_path / ".cursor" / "rules" / "python.mdc").write_text(
        "---\nglobs: '*.py'\n---\nUse typing.\n", encoding="utf-8"
    )
    (tmp_path / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    report = diff_repository(tmp_path, base_ref=base)
    by_path = {item.path: item for item in report.sources}

    assert by_path["AGENTS.md"].candidate_tokens == 0
    assert by_path["nested/AGENTS.md"].candidate_tokens == 0
    assert by_path["CLAUDE.md"].base_tokens == 0
    assert by_path[".cursor/rules/python.mdc"].base_tokens == 0
    assert {source.path for source in report.base.sources} == {
        "AGENTS.md",
        "nested/AGENTS.md",
    }


def test_effective_context_inherits_only_target_ancestor_agents_files(
    tmp_path: Path,
) -> None:
    for directory in ("backend/api", "frontend", "mobile"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("root", encoding="utf-8")
    (tmp_path / "backend" / "AGENTS.md").write_text("backend", encoding="utf-8")
    (tmp_path / "frontend" / "AGENTS.md").write_text("frontend", encoding="utf-8")
    (tmp_path / "mobile" / "AGENTS.md").write_text("mobile", encoding="utf-8")
    target = tmp_path / "backend" / "api" / "user.py"
    target.write_text("pass\n", encoding="utf-8")

    effective = resolve_effective_context(
        scan_repository(tmp_path),
        ["backend/api/user.py"],
        provider="codex",
    )
    copilot = resolve_effective_context(
        scan_repository(tmp_path),
        ["backend/api/user.py"],
        provider="copilot",
    )

    assert [item.source.path for item in effective.sources] == [
        "AGENTS.md",
        "backend/AGENTS.md",
    ]
    assert not effective.missing_targets
    assert all(item.scope_accuracy == "approximated" for item in effective.sources)
    assert any("working directory" in warning for warning in effective.warnings)
    assert [item.source.path for item in copilot.sources] == ["backend/AGENTS.md"]


def test_provider_scoping_resolves_claude_copilot_and_cursor_metadata(
    tmp_path: Path,
) -> None:
    (tmp_path / "apps" / "api").mkdir(parents=True)
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("root claude", encoding="utf-8")
    (tmp_path / "apps" / "api" / "CLAUDE.md").write_text("api claude", encoding="utf-8")
    (tmp_path / ".github" / "instructions").mkdir(parents=True)
    (tmp_path / ".github" / "instructions" / "python.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\n---\nUse Python typing.\n",
        encoding="utf-8",
    )
    (tmp_path / ".github" / "instructions" / "web.instructions.md").write_text(
        "---\napplyTo: '**/*.tsx'\n---\nUse React.\n",
        encoding="utf-8",
    )
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    (tmp_path / ".cursor" / "rules" / "api.mdc").write_text(
        "---\nglobs: ['apps/api/*.py']\n---\nAPI rules.\n",
        encoding="utf-8",
    )

    scan = scan_repository(tmp_path)
    claude = resolve_effective_context(scan, ["apps/api/service.py"], provider="claude")
    copilot = resolve_effective_context(
        scan, ["apps/api/service.py"], provider="copilot"
    )
    cursor = resolve_effective_context(scan, ["apps/api/service.py"], provider="cursor")

    assert [item.source.path for item in claude.sources] == [
        "CLAUDE.md",
        "apps/api/CLAUDE.md",
    ]
    assert all(item.scope_accuracy == "approximated" for item in claude.sources)
    assert [item.source.path for item in copilot.sources] == [
        ".github/instructions/python.instructions.md"
    ]
    assert [item.source.path for item in cursor.sources] == [".cursor/rules/api.mdc"]


def test_effective_context_handles_multiple_missing_and_root_targets(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "AGENTS.md").write_text("root", encoding="utf-8")
    (tmp_path / "backend" / "AGENTS.md").write_text("backend", encoding="utf-8")
    (tmp_path / "frontend" / "AGENTS.md").write_text("frontend", encoding="utf-8")
    scan = scan_repository(tmp_path)

    multiple = resolve_effective_context(
        scan,
        ["backend/deleted.py", "frontend/moved.py"],
        provider="codex",
    )
    root = resolve_effective_context(scan, ["."], provider="codex")

    assert {item.source.path for item in multiple.sources} == {
        "AGENTS.md",
        "backend/AGENTS.md",
        "frontend/AGENTS.md",
    }
    assert multiple.missing_targets == (
        "backend/deleted.py",
        "frontend/moved.py",
    )
    assert [item.source.path for item in root.sources] == ["AGENTS.md"]


def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
