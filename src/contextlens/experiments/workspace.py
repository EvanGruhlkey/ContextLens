"""Immutable directory snapshots and isolated working copies."""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from contextlens.experiments.model import FileChange

_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".cache",
        ".mypy_cache",
        ".npm",
        ".pnpm-store",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
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
    excluded_paths: tuple[str, ...] = ()
    identity: str | None = None

    def __post_init__(self) -> None:
        source = self.source.resolve()
        if not source.is_dir():
            raise ValueError(f"workspace source is not a directory: {source}")
        object.__setattr__(self, "source", source)
        normalized = tuple(
            sorted({_normalize_relative_path(path) for path in self.excluded_paths})
        )
        object.__setattr__(self, "excluded_paths", normalized)
        if self.identity is not None and not self.identity.strip():
            raise ValueError("snapshot identity cannot be empty")

    @property
    def digest(self) -> str:
        hasher = hashlib.sha256()
        if self.identity is not None:
            hasher.update(b"pinned-identity\0")
            hasher.update(self.identity.encode("utf-8"))
            hasher.update(b"\0")
            for path in self.excluded_paths:
                hasher.update(path.encode("utf-8"))
                hasher.update(b"\0")
            return hasher.hexdigest()
        for path, content in _files(self.source).items():
            if path in self.excluded_paths:
                continue
            hasher.update(path.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(content)
            hasher.update(b"\0")
        return hasher.hexdigest()

    @contextmanager
    def isolated(self) -> Iterator[tuple[Path, dict[str, bytes]]]:
        directory = Path(mkdtemp(prefix="contextlens-"))
        try:
            workspace = directory / "workspace"
            _copy_workspace(self.source, workspace)
            for relative_path in self.excluded_paths:
                hidden = workspace / Path(relative_path)
                if hidden.is_dir():
                    shutil.rmtree(hidden)
                else:
                    hidden.unlink(missing_ok=True)
            _prepare_windows_python_cache_directories(workspace)
            before = _files(workspace)
            yield workspace, before
        finally:
            _cleanup_isolated_directory(directory)

    def capture(self, workspace: Path) -> dict[str, bytes]:
        """Capture a post-setup baseline for later agent-change comparison."""

        resolved = workspace.resolve()
        if not resolved.is_dir():
            raise ValueError(f"workspace is not a directory: {resolved}")
        return _files(resolved)


def _normalize_relative_path(value: str) -> str:
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"excluded path must stay within the snapshot: {value!r}")
    normalized = path.as_posix().removeprefix("./")
    if not normalized:
        raise ValueError("excluded path cannot be empty")
    return normalized


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
    for name in (
        ".cache",
        ".mypy_cache",
        ".npm",
        ".pnpm-store",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
    ):
        (workspace / name).mkdir(exist_ok=True)


def _copy_workspace(source: Path, destination: Path) -> None:
    """Materialize an independent copy, using Windows' parallel copier."""

    if os.name != "nt":
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(*_IGNORED_NAMES),
        )
        return
    destination.mkdir(parents=True)
    completed = subprocess.run(
        (
            "robocopy",
            str(source),
            str(destination),
            "/E",
            "/COPY:DAT",
            "/DCOPY:DAT",
            "/R:1",
            "/W:1",
            "/MT:16",
            "/XJ",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/NP",
            "/XD",
            *sorted(_IGNORED_NAMES),
            "/XF",
            ".git",
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode > 7:
        raise RuntimeError(
            "robocopy could not materialize the isolated workspace: "
            f"{completed.stderr or completed.stdout}"
        )


def _cleanup_isolated_directory(directory: Path) -> None:
    """Best-effort cleanup, including ACLs created by the Windows sandbox."""

    shutil.rmtree(directory, ignore_errors=True)
    if os.name != "nt" or not directory.exists():
        return
    subprocess.run(
        ("icacls", str(directory), "/reset", "/T", "/C", "/Q"),
        capture_output=True,
        check=False,
    )
    username = os.environ.get("USERNAME")
    domain = os.environ.get("USERDOMAIN")
    if username:
        identity = f"{domain}\\{username}" if domain else username
        subprocess.run(
            (
                "icacls",
                str(directory),
                "/grant:r",
                f"{identity}:(OI)(CI)F",
                "/T",
                "/C",
                "/Q",
            ),
            capture_output=True,
            check=False,
        )
    shutil.rmtree(directory, ignore_errors=True)


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
