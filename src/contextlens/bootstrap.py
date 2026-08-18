"""Conservative repository detection for ``contextlens init``."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextlens.repository import scan_repository


@dataclass(frozen=True, slots=True)
class DetectedCheck:
    """One mechanically detected repository command."""

    name: str
    command: tuple[str, ...]
    category: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "category": self.category,
        }


@dataclass(frozen=True, slots=True)
class InitResult:
    """Generated starter configuration plus transparent detection evidence."""

    root: str
    output: str
    languages: tuple[str, ...]
    checks: tuple[DetectedCheck, ...]
    context_paths: tuple[str, ...]
    agent: str
    runnable: bool
    todos: tuple[str, ...]
    config: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "output": self.output,
            "languages": list(self.languages),
            "checks": [check.to_dict() for check in self.checks],
            "context_paths": list(self.context_paths),
            "agent": self.agent,
            "runnable": self.runnable,
            "todos": list(self.todos),
            "config": dict(self.config),
        }


def initialize_repository(
    root: Path = Path("."),
    *,
    output: Path | None = None,
    force: bool = False,
    executable_lookup: Any = shutil.which,
) -> InitResult:
    """Inspect a repository and write a minimal, editable eval configuration."""

    resolved = root.resolve()
    if not resolved.is_dir():
        raise ValueError(f"repository path is not a directory: {resolved}")
    destination = (
        output.resolve()
        if output is not None and output.is_absolute()
        else (resolved / (output or Path(".contextlens/evals.json"))).resolve()
    )
    if destination.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file: {destination}")
    languages, checks = detect_repository_checks(resolved)
    scan = scan_repository(resolved)
    agent_config, agent_name, agent_runnable, agent_todos = _detect_agent(
        executable_lookup
    )
    todos = list(agent_todos)
    selected_checks = [list(check.command) for check in checks]
    if not selected_checks:
        selected_checks = [["TODO_TEST_COMMAND"]]
        todos.append(
            "Replace TODO_TEST_COMMAND with a deterministic command that "
            "grades the task."
        )
    instruction = (
        "Run the configured repository checks and fix any failures without changing "
        "the checks or their intent."
        if checks
        else "TODO: replace this with a small historical bug or coding task."
    )
    config: dict[str, Any] = {
        "generated_by": "contextlens init",
        "notes": [
            "Review this starter task before using results as evidence.",
            "Use a historical failing revision or explicit task for meaningful "
            "A/B tests.",
            *todos,
        ],
        "trials": 3,
        "quality_tolerance": 0,
        "economics_tolerance": 0.02,
        "require_provider_usage": False,
        "agent": agent_config,
        "tasks": [
            {
                "id": "repository-checks",
                "instruction": instruction,
                "workspace": ".",
                "checks": selected_checks,
                "language": languages[0] if len(languages) == 1 else "mixed",
                "category": "repository_validation",
                "target_paths": [],
            }
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    runnable = agent_runnable and bool(checks)
    return InitResult(
        root=str(resolved),
        output=str(destination),
        languages=languages,
        checks=checks,
        context_paths=tuple(source.path for source in scan.sources),
        agent=agent_name,
        runnable=runnable,
        todos=tuple(todos),
        config=config,
    )


def detect_repository_checks(
    root: Path,
) -> tuple[tuple[str, ...], tuple[DetectedCheck, ...]]:
    """Detect ecosystems and commands without executing project code."""

    languages: list[str] = []
    checks: list[DetectedCheck] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        languages.append("Python")
        raw = pyproject.read_bytes()
        try:
            value = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            value = {}
        flattened = json.dumps(value, sort_keys=True).casefold()
        if "pytest" in flattened or (root / "tests").is_dir():
            checks.append(
                DetectedCheck("pytest", (sys.executable, "-m", "pytest", "-q"), "test")
            )
        if "ruff" in flattened or _has_tool(value, "ruff"):
            checks.append(DetectedCheck("ruff", ("ruff", "check", "."), "static"))
        if "mypy" in flattened or _has_tool(value, "mypy"):
            checks.append(DetectedCheck("mypy", ("mypy",), "static"))
    package_json = root / "package.json"
    if package_json.is_file():
        languages.append("Node.js")
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            package = {}
        scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
        scripts = scripts if isinstance(scripts, dict) else {}
        manager = _node_package_manager(root)
        for script_name, category in (
            ("test", "test"),
            ("typecheck", "static"),
            ("lint", "static"),
        ):
            if script_name in scripts:
                checks.append(
                    DetectedCheck(
                        f"{manager} {script_name}",
                        _node_script_command(manager, script_name),
                        category,
                    )
                )
    if (root / "Cargo.toml").is_file():
        languages.append("Rust")
        checks.extend(
            (
                DetectedCheck("cargo test", ("cargo", "test"), "test"),
                DetectedCheck("cargo check", ("cargo", "check"), "static"),
            )
        )
    if (root / "go.mod").is_file():
        languages.append("Go")
        checks.append(DetectedCheck("go test", ("go", "test", "./..."), "test"))
    return tuple(languages), tuple(_deduplicate_checks(checks))


def render_init_terminal(result: InitResult) -> str:
    """Render setup results for a new external user."""

    lines = ["ContextLens Setup", "", "Repository"]
    if result.languages:
        lines.extend(f"  {language} project" for language in result.languages)
    else:
        lines.append("  Ecosystem not confidently detected")
    lines.extend(f"  {check.name} detected" for check in result.checks)
    lines.extend(("", "Agent context"))
    if result.context_paths:
        lines.extend(f"  {path}" for path in result.context_paths)
    else:
        lines.append("  No recognized repository context yet")
    lines.extend(("", "Agent", f"  {result.agent}"))
    lines.extend(("", "Created:", f"  {result.output}"))
    if not result.runnable:
        lines.extend(("", "Before verify:"))
        lines.extend(f"  TODO: {todo}" for todo in result.todos)
    lines.extend(("", "Next:", "  contextlens scan", "  contextlens verify"))
    return "\n".join(lines) + "\n"


def _detect_agent(
    executable_lookup: Any,
) -> tuple[dict[str, Any], str, bool, tuple[str, ...]]:
    configured = os.environ.get("CONTEXTLENS_AGENT_COMMAND", "").strip()
    if configured:
        command = shlex.split(configured, posix=os.name != "nt")
        return (
            {
                "type": "subprocess",
                "command": command,
                "adapter_id": "configured-subprocess-v1",
                "provider": "configured",
                "model": "configured",
            },
            "generic subprocess agent from CONTEXTLENS_AGENT_COMMAND",
            True,
            (),
        )
    codex = executable_lookup("codex")
    if codex:
        return (
            {
                "type": "codex",
                "command": [str(codex)],
                "provider": "openai",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "low",
                "sandbox": "workspace-write",
            },
            "Codex CLI detected",
            True,
            (),
        )
    return (
        {
            "type": "subprocess",
            "command": ["TODO_AGENT_COMMAND"],
            "adapter_id": "replace-me",
            "provider": "replace-me",
            "model": "replace-me",
        },
        "No supported agent executable detected",
        False,
        ("Install Codex CLI or replace TODO_AGENT_COMMAND with a subprocess adapter.",),
    )


def _has_tool(value: Mapping[str, Any], name: str) -> bool:
    tool = value.get("tool", {})
    return isinstance(tool, Mapping) and name in tool


def _node_package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _node_script_command(manager: str, script: str) -> tuple[str, ...]:
    if manager in {"pnpm", "yarn"} or script == "test":
        return (manager, script)
    return manager, "run", script


def _deduplicate_checks(checks: list[DetectedCheck]) -> list[DetectedCheck]:
    seen: set[tuple[str, ...]] = set()
    result: list[DetectedCheck] = []
    for check in checks:
        if check.command in seen:
            continue
        seen.add(check.command)
        result.append(check)
    return result
