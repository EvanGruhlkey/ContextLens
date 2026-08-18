from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from contextlens.bootstrap import detect_repository_checks, initialize_repository


def test_init_detects_python_checks_and_codex(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "AGENTS.md").write_text("Run tests.\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\ndependencies=[]\n"
        "[project.optional-dependencies]\ndev=['pytest','ruff','mypy']\n"
        "[tool.ruff]\nline-length=88\n[tool.mypy]\nstrict=true\n",
        encoding="utf-8",
    )

    result = initialize_repository(
        tmp_path,
        executable_lookup=lambda name: (
            "/usr/local/bin/codex" if name == "codex" else None
        ),
    )
    value = json.loads(Path(result.output).read_text(encoding="utf-8"))

    assert result.languages == ("Python",)
    assert [check.name for check in result.checks] == ["pytest", "ruff", "mypy"]
    assert result.runnable is True
    assert value["agent"]["type"] == "codex"
    assert value["agent"]["model"] == "gpt-5.6-terra"
    assert value["tasks"][0]["checks"][0] == [
        sys.executable,
        "-m",
        "pytest",
        "-q",
    ]
    assert result.context_paths == ("AGENTS.md",)


def test_init_detects_node_package_manager_and_scripts(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "vitest run",
                    "typecheck": "tsc --noEmit",
                    "lint": "eslint .",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")

    languages, checks = detect_repository_checks(tmp_path)

    assert languages == ("Node.js",)
    assert [check.command for check in checks] == [
        ("pnpm", "test"),
        ("pnpm", "typecheck"),
        ("pnpm", "lint"),
    ]


def test_init_incomplete_repository_writes_explicit_nonrunnable_todos(
    tmp_path: Path,
) -> None:
    result = initialize_repository(tmp_path, executable_lookup=lambda name: None)
    value = json.loads(Path(result.output).read_text(encoding="utf-8"))

    assert result.runnable is False
    assert value["agent"]["command"] == ["TODO_AGENT_COMMAND"]
    assert value["tasks"][0]["checks"] == [["TODO_TEST_COMMAND"]]
    assert all(
        "TODO" in note or "Review" in note or "historical" in note
        for note in value["notes"]
    )
    with pytest.raises(ValueError, match="refusing to overwrite"):
        initialize_repository(tmp_path, executable_lookup=lambda name: None)


def test_explicit_subprocess_agent_overrides_auto_detected_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[project.optional-dependencies]\ndev=['pytest']\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    monkeypatch.setenv("CONTEXTLENS_AGENT_COMMAND", "python fixture_agent.py")

    result = initialize_repository(
        tmp_path,
        executable_lookup=lambda name: "/usr/local/bin/codex",
    )

    assert result.agent.startswith("generic subprocess")
    assert result.config["agent"]["type"] == "subprocess"
