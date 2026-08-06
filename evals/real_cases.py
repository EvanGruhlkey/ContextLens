"""Real-repository SWE-bench Verified pilot cases."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from contextlens.trace import ContextSource, SourceKind
from evals.cases import (
    CommandCheck,
    EvalCase,
    EvalCategory,
    EvalSuite,
    VerificationSpec,
)

_ROOT = Path(__file__).resolve().parent
_DATASET_URL = "https://datasets-server.huggingface.co/rows?" + urllib.parse.urlencode(
    {
        "dataset": "SWE-bench/SWE-bench_Verified",
        "config": "default",
        "split": "test",
        "offset": 0,
        "length": 100,
    }
)


def _row(instance_id: str) -> dict[str, object]:
    for offset in range(0, 500, 100):
        url = _DATASET_URL.replace("offset=0", f"offset={offset}")
        with urllib.request.urlopen(url, timeout=30) as response:
            rows = json.load(response)["rows"]
        for wrapper in rows:
            row = wrapper["row"]
            if row["instance_id"] == instance_id:
                return row
    raise RuntimeError(f"SWE-bench case not found: {instance_id}")


def _text(path: Path, limit: int = 12_000) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def _source(case_id: str, repo: Path, relative: str, position: int) -> ContextSource:
    return ContextSource(
        source_id=f"{case_id}:file:{relative}",
        kind=SourceKind.RETRIEVED_DOCUMENT,
        name=relative,
        content=_text(repo / relative),
        tags=("real-repository", "retrieved-file"),
        insertion_position=position,
    )


def requests_case() -> EvalCase:
    instance_id = "psf__requests-5414"
    row = _row(instance_id)
    repo = _ROOT / "real-repos" / "requests"
    files = (
        "README.md",
        "HISTORY.md",
        "requests/models.py",
        "requests/sessions.py",
        "requests/adapters.py",
        "requests/utils.py",
        "requests/api.py",
        "requests/exceptions.py",
        "tests/test_requests.py",
    )
    context = tuple(
        _source(instance_id, repo, relative, position)
        for position, relative in enumerate(files)
    )
    return EvalCase(
        case_id=instance_id,
        suite=EvalSuite.REAL,
        category=EvalCategory.BUG_FIX,
        instruction=(
            str(row["problem_statement"]).strip()
            + "\n\nFix the issue in this checkout. Keep the change focused and "
            "run relevant tests."
        ),
        workspace_files={".contextlens-real-case": str(row["base_commit"])},
        context=context,
        allowed_files=tuple(files),
        oracle_source_ids=(f"{instance_id}:file:requests/models.py",),
        verification=VerificationSpec(
            patch=str(row["test_patch"]),
            commands=(
                CommandCheck(
                    (
                        "uv",
                        "run",
                        "--no-project",
                        "--isolated",
                        "--with",
                        "pytest<9",
                        "--with",
                        "pytest-mock",
                        "--with",
                        "pytest-httpbin==0.0.7",
                        "--with",
                        "urllib3<1.27",
                        "--with",
                        "charset-normalizer<3",
                        "--with",
                        "certifi",
                        "--with",
                        "idna",
                        "--with",
                        "pysocks",
                        "python",
                        "-c",
                        (
                            "import requests; from requests.exceptions import "
                            "InvalidURL; "
                            "\ntry: requests.get('http://.example.com')"
                            "\nexcept InvalidURL: pass"
                            "\nelse: raise AssertionError('InvalidURL was not raised')"
                        ),
                    )
                ),
            ),
        ),
        source_directory=repo,
    )


def pytest_case() -> EvalCase:
    instance_id = "pytest-dev__pytest-10051"
    row = _row(instance_id)
    short_checkout = Path("C:/contextlens-real/pytest")
    repo = (
        short_checkout
        if short_checkout.is_dir()
        else _ROOT / "real-repos" / "pytest"
    )
    files = (
        "README.rst",
        "doc/en/how-to/logging.rst",
        "src/_pytest/logging.py",
        "src/_pytest/fixtures.py",
        "src/_pytest/stash.py",
        "src/_pytest/hookspec.py",
        "testing/logging/test_fixture.py",
    )
    context = tuple(
        _source(instance_id, repo, relative, position)
        for position, relative in enumerate(files)
    )
    return EvalCase(
        case_id=instance_id,
        suite=EvalSuite.REAL,
        category=EvalCategory.BUG_FIX,
        instruction=(
            str(row["problem_statement"]).strip()
            + "\n\nFix the issue in this checkout. Keep the change focused and "
            "run relevant tests."
        ),
        workspace_files={".contextlens-real-case": str(row["base_commit"])},
        context=context,
        allowed_files=tuple(files),
        oracle_source_ids=(f"{instance_id}:file:src/_pytest/logging.py",),
        verification=VerificationSpec(
            patch=str(row["test_patch"]),
            commands=(
                CommandCheck(
                    (
                        "uv",
                        "run",
                        "--isolated",
                        "--with",
                        "setuptools<81",
                        "--with",
                        "pytest<9",
                        "pytest",
                        "-q",
                        "testing/logging/test_fixture.py::test_clear_for_call_stage",
                    )
                ),
            ),
        ),
        source_directory=repo,
    )


def sphinx_case() -> EvalCase:
    instance_id = "sphinx-doc__sphinx-10323"
    row = _row(instance_id)
    repo = _ROOT / "real-repos" / "sphinx"
    files = (
        "README.rst",
        "CHANGES",
        "sphinx/directives/code.py",
        "sphinx/util/docutils.py",
        "sphinx/config.py",
        "tests/test_directive_code.py",
        "doc/usage/restructuredtext/directives.rst",
    )
    context = tuple(
        _source(instance_id, repo, relative, position)
        for position, relative in enumerate(files)
    )
    dependencies = (
        "pytest<9",
        "docutils<0.18",
        "Jinja2<4",
        "Pygments",
        "snowballstemmer",
        "babel",
        "alabaster<0.8",
        "imagesize",
        "requests",
        "packaging",
        "sphinxcontrib-applehelp",
        "sphinxcontrib-devhelp",
        "sphinxcontrib-jsmath",
        "sphinxcontrib-htmlhelp",
        "sphinxcontrib-serializinghtml",
        "sphinxcontrib-qthelp",
    )
    command = ["uv", "run", "--no-project", "--isolated"]
    for dependency in dependencies:
        command.extend(("--with", dependency))
    command.extend(
        (
            "pytest",
            "--runxfail",
            "-q",
            (
                "tests/test_directive_code.py::"
                "test_LiteralIncludeReader_dedent_and_append_and_prepend"
            ),
        )
    )
    return EvalCase(
        case_id=instance_id,
        suite=EvalSuite.REAL,
        category=EvalCategory.BUG_FIX,
        instruction=(
            str(row["problem_statement"]).strip()
            + "\n\nFix the issue in this checkout. Keep the change focused and "
            "run relevant tests."
        ),
        workspace_files={".contextlens-real-case": str(row["base_commit"])},
        context=context,
        allowed_files=tuple(files),
        oracle_source_ids=(f"{instance_id}:file:sphinx/directives/code.py",),
        verification=VerificationSpec(
            patch=str(row["test_patch"]),
            commands=(CommandCheck(tuple(command)),),
        ),
        source_directory=repo,
    )


def get_real_cases() -> tuple[EvalCase, ...]:
    """Return real cases only when explicitly selecting the real suite."""

    return (requests_case(), pytest_case(), sphinx_case())
