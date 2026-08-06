"""Pinned real-repository case manifests and reproducible acquisition."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextlens.trace import ContextSource, SourceKind
from evals.cases import (
    CommandCheck,
    EvalCase,
    EvalCategory,
    EvalSuite,
    VerificationSpec,
)

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_TEXT_LIMIT = 24_000
_TOTAL_LIMIT = 240_000
_DISCOVERY_NAMES = (
    "AGENTS.md",
    "README*",
    "CONTRIBUTING*",
    "SECURITY*",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "package.json",
    "tsconfig.json",
)


@dataclass(frozen=True, slots=True)
class RepositoryCaseManifest:
    """Public task metadata plus hidden, mechanical verification."""

    path: Path
    case_id: str
    suite: str
    repo: str
    commit: str
    task: str
    setup: tuple[tuple[str, ...], ...]
    verification: tuple[tuple[str, ...], ...]
    verification_patch: str | None
    category: EvalCategory = EvalCategory.BUG_FIX

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.repo}.git"

    def public_value(self) -> dict[str, Any]:
        """Return the frozen manifest without hidden assertions or patches."""

        return {
            "case_id": self.case_id,
            "suite": self.suite,
            "repo": self.repo,
            "commit": self.commit,
            "task": self.task,
            "setup": [list(command) for command in self.setup],
            "verification_commands": len(self.verification),
            "category": self.category.value,
            "manifest_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
        }


def load_manifest(path: Path) -> RepositoryCaseManifest:
    """Load a JSON-form YAML 1.2 manifest using only the standard library."""

    path = path.resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{path} must use the JSON-compatible subset of YAML 1.2: {error}"
        ) from error
    if not isinstance(value, dict):
        raise ValueError("case manifest must be a mapping")
    case_id = _required_string(value, "case_id")
    suite = _required_string(value, "suite")
    repo = _required_string(value, "repo")
    commit = _required_string(value, "commit").lower()
    task = _required_string(value, "task")
    if not _REPO.fullmatch(repo):
        raise ValueError(f"invalid public GitHub repository: {repo!r}")
    if not _SHA.fullmatch(commit):
        raise ValueError("commit must be a full 40-character SHA")
    if suite != "smoke":
        raise ValueError("the initial repository corpus only supports suite='smoke'")
    setup = _commands(value.get("setup", []), "setup")
    verification = value.get("verification")
    if not isinstance(verification, dict):
        raise ValueError("verification must be a mapping")
    commands = _commands(verification.get("commands"), "verification.commands")
    patch_value: str | None = None
    patch_name = verification.get("patch_file")
    if patch_name is not None:
        if not isinstance(patch_name, str) or not patch_name:
            raise ValueError("verification.patch_file must be a nonempty string")
        patch_path = (path.parent / patch_name).resolve()
        if path.parent not in patch_path.parents:
            raise ValueError("verification patch must stay below the case directory")
        patch_value = patch_path.read_text(encoding="utf-8")
    category = EvalCategory(str(value.get("category", "bug_fix")))
    return RepositoryCaseManifest(
        path=path,
        case_id=case_id,
        suite=suite,
        repo=repo,
        commit=commit,
        task=task,
        setup=setup,
        verification=commands,
        verification_patch=patch_value,
        category=category,
    )


def acquire_repository(manifest: RepositoryCaseManifest, destination: Path) -> Path:
    """Fetch only the pinned commit and remove access to upstream history."""

    destination = destination.resolve()
    if destination.exists():
        raise ValueError(f"checkout destination already exists: {destination}")
    destination.mkdir(parents=True)
    _git(("init", "--quiet"), destination)
    _git(("remote", "add", "origin", manifest.clone_url), destination)
    try:
        _git(("fetch", "--quiet", "--depth=1", "origin", manifest.commit), destination)
        _git(("checkout", "--quiet", "--detach", "FETCH_HEAD"), destination)
    finally:
        _git(("remote", "remove", "origin"), destination, check=False)
    actual = _git(("rev-parse", "HEAD"), destination).stdout.strip().lower()
    if actual != manifest.commit:
        raise RuntimeError(f"checkout resolved to {actual}, expected {manifest.commit}")
    commits = _git(("rev-list", "--all", "--count"), destination).stdout.strip()
    if commits != "1":
        raise RuntimeError("isolated checkout unexpectedly contains repository history")
    return destination


def prepare_eval_case(
    manifest: RepositoryCaseManifest,
    checkout: Path,
) -> EvalCase:
    """Run setup, discover generic context, and build a production EvalCase."""

    setup_output: list[str] = []
    for command in manifest.setup:
        completed = subprocess.run(
            command,
            cwd=checkout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
        setup_output.append(
            f"$ {' '.join(command)}\nexit={completed.returncode}\n"
            f"{completed.stdout}\n{completed.stderr}".strip()
        )
        if completed.returncode != 0:
            raise RuntimeError(f"setup failed for {manifest.case_id}: {command!r}")
    context = discover_context(checkout, setup_output=tuple(setup_output))
    if not context:
        raise RuntimeError(f"no context discovered in {manifest.repo}")
    return EvalCase(
        case_id=manifest.case_id,
        suite=EvalSuite.REAL,
        category=manifest.category,
        instruction=manifest.task,
        workspace_files={".contextlens-pinned-commit": manifest.commit},
        context=context,
        allowed_files=(),
        oracle_source_ids=(),
        verification=VerificationSpec(
            patch=manifest.verification_patch,
            commands=tuple(CommandCheck(command) for command in manifest.verification),
        ),
        source_directory=checkout,
    )


def discover_context(
    checkout: Path,
    *,
    setup_output: tuple[str, ...] = (),
) -> tuple[ContextSource, ...]:
    """Discover context by repository conventions, never task-specific paths."""

    checkout = checkout.resolve()
    paths: set[Path] = set()
    for pattern in _DISCOVERY_NAMES:
        paths.update(path for path in checkout.glob(pattern) if path.is_file())
    paths.update(path for path in checkout.rglob("AGENTS.md") if path.is_file())
    docs = checkout / "docs"
    if docs.is_dir():
        for pattern in ("README*", "architecture*", "contributing*"):
            paths.update(path for path in docs.rglob(pattern) if path.is_file())
    selected: list[tuple[Path, str]] = []
    used = 0
    for path in sorted(paths, key=lambda item: item.relative_to(checkout).as_posix()):
        content = path.read_text(encoding="utf-8", errors="replace")[:_TEXT_LIMIT]
        if not content.strip() or used + len(content) > _TOTAL_LIMIT:
            continue
        selected.append((path, content))
        used += len(content)
    tracked = _git(("ls-files",), checkout).stdout.splitlines()
    repository_map = "\n".join(tracked[:5000])
    history = _git(("show", "-s", "--format=fuller", "HEAD"), checkout).stdout
    sources: list[ContextSource] = []
    for position, (path, content) in enumerate(selected):
        relative = path.relative_to(checkout).as_posix()
        kind = (
            SourceKind.REPO_INSTRUCTION
            if path.name == "AGENTS.md"
            else SourceKind.RETRIEVED_DOCUMENT
        )
        sources.append(_source(relative, kind, content, position))
    sources.append(
        _source(
            "repository-file-map.txt",
            SourceKind.RETRIEVED_DOCUMENT,
            repository_map,
            len(sources),
        )
    )
    sources.append(
        _source("pinned-commit.txt", SourceKind.GIT_HISTORY, history, len(sources))
    )
    if setup_output:
        sources.append(
            _source(
                "setup-output.txt",
                SourceKind.TERMINAL_OUTPUT,
                "\n\n".join(setup_output),
                len(sources),
            )
        )
    return tuple(sources)


def smoke_manifests(root: Path | None = None) -> tuple[Path, ...]:
    base = (root or Path(__file__).resolve().parent / "cases" / "smoke").resolve()
    return tuple(sorted(base.glob("*.yaml")))


def _source(name: str, kind: SourceKind, content: str, position: int) -> ContextSource:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return ContextSource(
        source_id=f"repo:{digest}",
        kind=kind,
        name=name,
        content=content,
        token_count=max(1, (len(content.encode("utf-8")) + 3) // 4),
        token_count_method="utf8_bytes_div_4",
        provenance={"discovery": "repository-conventions-v1", "path": name},
        tags=("real-repository", "automatically-discovered"),
        insertion_position=position,
    )


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return item.strip()


def _commands(value: Any, name: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list of argument lists")
    commands: list[tuple[str, ...]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or not item
            or not all(isinstance(part, str) and part for part in item)
        ):
            raise ValueError(f"{name} entries must be nonempty string lists")
        commands.append(tuple(item))
    return tuple(commands)


def _git(
    arguments: tuple[str, ...],
    cwd: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed


def remove_checkout(path: Path) -> None:
    """Remove a harness-owned acquisition directory after a case completes."""

    if path.exists():
        shutil.rmtree(path)
