"""Stage a hidden grader only after an agent run, execute it, then remove it."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--working-directory", type=Path, default=Path("."))
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a grader command is required after --")

    workspace = Path.cwd().resolve()
    fixture = args.fixture.resolve()
    destination = (workspace / args.destination).resolve()
    working_directory = (workspace / args.working_directory).resolve()
    _require_inside(workspace, destination)
    _require_inside(workspace, working_directory)
    if not fixture.is_file():
        raise FileNotFoundError(f"hidden grader fixture does not exist: {fixture}")
    original = destination.read_bytes() if destination.exists() else None
    if original is not None and not args.replace_existing:
        raise FileExistsError(
            f"refusing to overwrite grader destination: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fixture, destination)
    grader_config = workspace / ".contextlens-grader-config"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(workspace),
            "USERPROFILE": str(workspace),
            "XDG_CONFIG_HOME": str(grader_config),
            "BROWSER_USE_CONFIG_DIR": str(grader_config / "browser-use"),
        }
    )
    try:
        completed = subprocess.run(
            _normalize_path_arguments(args.command, working_directory),
            cwd=working_directory,
            env=environment,
            check=False,
        )
        return completed.returncode
    finally:
        if original is None:
            destination.unlink(missing_ok=True)
        else:
            destination.write_bytes(original)
        shutil.rmtree(grader_config, ignore_errors=True)


def _normalize_path_arguments(command: list[str], working_directory: Path) -> list[str]:
    normalized = [shutil.which(command[0]) or command[0]]
    for argument in command[1:]:
        path = Path(argument)
        candidate = path if path.is_absolute() else working_directory / path
        normalized.append(str(path) if candidate.exists() else argument)
    return normalized


def _require_inside(root: Path, path: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"path escapes the graded workspace: {path}") from error


if __name__ == "__main__":
    raise SystemExit(main())
