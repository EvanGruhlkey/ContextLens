"""Immutable directory snapshots and isolated working copies."""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments.model import FileChange

_IGNORED_NAMES = frozenset(
    {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
)


def _files(root: Path) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _IGNORED_NAMES for part in relative.parts):
            continue
        values[relative.as_posix()] = path.read_bytes()
    return values


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class DirectorySnapshot:
    """A source directory copied fresh for every replay worker."""

    source: Path

    def __post_init__(self) -> None:
        source = self.source.resolve()
        if not source.is_dir():
            raise ValueError(f"workspace source is not a directory: {source}")
        object.__setattr__(self, "source", source)

    @property
    def digest(self) -> str:
        hasher = hashlib.sha256()
        for path, content in _files(self.source).items():
            hasher.update(path.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(content)
            hasher.update(b"\0")
        return hasher.hexdigest()

    @contextmanager
    def isolated(self) -> Iterator[tuple[Path, dict[str, bytes]]]:
        with TemporaryDirectory(prefix="contextlens-") as directory:
            workspace = Path(directory) / "workspace"
            shutil.copytree(
                self.source,
                workspace,
                ignore=shutil.ignore_patterns(*_IGNORED_NAMES),
            )
            _prepare_windows_python_cache_directories(workspace)
            before = _files(workspace)
            yield workspace, before


def _prepare_windows_python_cache_directories(workspace: Path) -> None:
    """Avoid sandbox-owned Python cache directories that Windows cannot clean.

    The native elevated sandbox can assign a restrictive ACL when a test runner
    creates ``__pycache__``. Pre-creating each cache directory in the host
    process preserves normal ownership; bytecode remains excluded from snapshots.
    """

    if os.name != "nt":
        return
    parents = {path.parent for path in workspace.rglob("*.py")}
    for parent in parents:
        (parent / "__pycache__").mkdir(exist_ok=True)
    for name in (".pytest_cache", ".mypy_cache", ".ruff_cache"):
        (workspace / name).mkdir(exist_ok=True)


def compare_workspace(
    workspace: Path,
    before: dict[str, bytes],
) -> tuple[FileChange, ...]:
    """Return created, modified, and deleted files with small text patches."""

    after = _files(workspace)
    changes: list[FileChange] = []
    for path in sorted(before.keys() | after.keys()):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        if old is None:
            change = "created"
        elif new is None:
            change = "deleted"
        else:
            change = "modified"
        changes.append(
            FileChange(
                path=path,
                change=change,
                before_digest=_digest(old) if old is not None else None,
                after_digest=_digest(new) if new is not None else None,
                patch=_text_patch(path, old, new),
            )
        )
    return tuple(changes)


def _text_patch(path: str, old: bytes | None, new: bytes | None) -> str | None:
    try:
        old_text = old.decode("utf-8").splitlines(keepends=True) if old else []
        new_text = new.decode("utf-8").splitlines(keepends=True) if new else []
    except UnicodeDecodeError:
        return None
    patch = "".join(
        difflib.unified_diff(
            old_text,
            new_text,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return patch if len(patch.encode("utf-8")) <= 64 * 1024 else None
