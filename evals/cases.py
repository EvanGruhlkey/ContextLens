"""Executable task corpus for real ContextLens model evaluations."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from contextlens.experiments import ReplayTask
from contextlens.trace import ContextSource, SourceKind


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe eval fixture path: {value!r}")


class EvalSuite(StrEnum):
    """Dataset partitions with held-out results reserved for final reporting."""

    SMOKE = "smoke"
    DEVELOPMENT = "development"
    HELDOUT = "heldout"
    REAL = "real"


class EvalCategory(StrEnum):
    """Executable task categories represented in the corpus."""

    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    CONFIGURATION = "configuration"
    INCIDENT_RESPONSE = "incident_response"
    CODEBASE_QUESTION = "codebase_question"


@dataclass(frozen=True, slots=True)
class CommandCheck:
    """A hidden command executed independently after an agent finishes."""

    command: tuple[str, ...]
    expected_exit_code: int = 0

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("hidden verification command cannot be empty")
        object.__setattr__(self, "command", tuple(self.command))


@dataclass(frozen=True, slots=True)
class JsonExpectation:
    """One hidden assertion against a JSON document."""

    path: str
    key_path: tuple[str | int, ...]
    expected: Any

    def __post_init__(self) -> None:
        _validate_relative_path(self.path)
        if not self.key_path:
            raise ValueError("JSON expectation key_path cannot be empty")
        object.__setattr__(self, "key_path", tuple(self.key_path))


@dataclass(frozen=True, slots=True)
class VerificationSpec:
    """Hidden mechanical expectations that are never placed in a replay task."""

    required_files: tuple[str, ...] = ()
    forbidden_files: tuple[str, ...] = ()
    exact_files: Mapping[str, str] = field(default_factory=dict)
    contains: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    json_expectations: tuple[JsonExpectation, ...] = ()
    commands: tuple[CommandCheck, ...] = ()
    patch: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        paths = (
            *self.required_files,
            *self.forbidden_files,
            *self.exact_files,
            *self.contains,
        )
        for path in paths:
            _validate_relative_path(path)
        normalized_contains = {
            path: tuple(values) for path, values in self.contains.items()
        }
        object.__setattr__(self, "required_files", tuple(self.required_files))
        object.__setattr__(self, "forbidden_files", tuple(self.forbidden_files))
        object.__setattr__(
            self,
            "exact_files",
            MappingProxyType(dict(self.exact_files)),
        )
        object.__setattr__(
            self,
            "contains",
            MappingProxyType(normalized_contains),
        )
        object.__setattr__(
            self,
            "json_expectations",
            tuple(self.json_expectations),
        )
        object.__setattr__(self, "commands", tuple(self.commands))


@dataclass(frozen=True, slots=True)
class EvalCase:
    """A public task fixture plus a separate hidden verification contract."""

    case_id: str
    suite: EvalSuite
    category: EvalCategory
    instruction: str
    workspace_files: Mapping[str, str]
    context: tuple[ContextSource, ...]
    allowed_files: tuple[str, ...]
    oracle_source_ids: tuple[str, ...]
    verification: VerificationSpec = field(repr=False)
    source_directory: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.case_id or not self.instruction.strip():
            raise ValueError("case ID and instruction cannot be empty")
        if not self.workspace_files or not self.context:
            raise ValueError("eval cases require workspace files and context")
        for path in (*self.workspace_files, *self.allowed_files):
            _validate_relative_path(path)
        source_ids = tuple(source.source_id for source in self.context)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("context source IDs must be unique within a case")
        unknown_oracle = set(self.oracle_source_ids) - set(source_ids)
        if unknown_oracle:
            raise ValueError(f"oracle references unknown sources: {unknown_oracle}")
        object.__setattr__(
            self,
            "workspace_files",
            MappingProxyType(dict(self.workspace_files)),
        )
        object.__setattr__(self, "context", tuple(self.context))
        object.__setattr__(self, "allowed_files", tuple(self.allowed_files))
        object.__setattr__(
            self,
            "oracle_source_ids",
            tuple(self.oracle_source_ids),
        )
        if self.source_directory is not None:
            source = self.source_directory.resolve()
            if not source.is_dir():
                raise ValueError(f"real eval source directory is missing: {source}")
            object.__setattr__(self, "source_directory", source)

    def replay_task(self) -> ReplayTask:
        """Build the public task without leaking hidden expectations or oracle IDs."""

        return ReplayTask(
            task_id=self.case_id,
            instruction=self.instruction,
            metadata={
                "suite": self.suite.value,
                "category": self.category.value,
                "allowed_files": list(self.allowed_files),
            },
        )

    def materialize_workspace(self, root: Path) -> Path:
        """Create one new fixture directory suitable for `DirectorySnapshot`."""

        root = root.resolve()
        if self.source_directory is not None:
            shutil.copytree(
                self.source_directory,
                root,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
            )
            return root
        root.mkdir(parents=True, exist_ok=False)
        for relative, content in self.workspace_files.items():
            destination = root.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        return root


@dataclass(frozen=True, slots=True)
class _CaseDefinition:
    case_id: str
    suite: EvalSuite
    category: EvalCategory
    instruction: str
    workspace_files: Mapping[str, str]
    allowed_files: tuple[str, ...]
    current_decision: str
    stale_decision: str
    history: str
    verification: VerificationSpec
    relevant_name: str


def _case(definition: _CaseDefinition) -> EvalCase:
    context = _rich_context(definition)
    prefix = definition.case_id
    return EvalCase(
        case_id=prefix,
        suite=definition.suite,
        category=definition.category,
        instruction=definition.instruction,
        workspace_files=definition.workspace_files,
        context=context,
        allowed_files=definition.allowed_files,
        oracle_source_ids=(
            f"{prefix}:repo",
            f"{prefix}:source",
            f"{prefix}:current",
            f"{prefix}:history",
        ),
        verification=definition.verification,
    )


def _rich_context(definition: _CaseDefinition) -> tuple[ContextSource, ...]:
    prefix = definition.case_id
    source_text = definition.workspace_files[definition.relevant_name]
    values: tuple[
        tuple[
            str,
            SourceKind,
            str,
            str,
            tuple[str, ...],
            Mapping[str, Any],
        ],
        ...,
    ] = (
        (
            "repo",
            SourceKind.REPO_INSTRUCTION,
            "AGENTS.md",
            "Modify only the allowed task files. Preserve public APIs and run "
            "available checks. Accepted architecture decisions outrank old notes.",
            ("authoritative", "instructions"),
            {"authority": "repository"},
        ),
        (
            "source",
            SourceKind.FILE,
            definition.relevant_name,
            source_text,
            ("workspace_snapshot", "relevant"),
            {"path": definition.relevant_name},
        ),
        (
            "current",
            SourceKind.ARCHITECTURE_DECISION,
            f"ADR-{prefix}-current.md",
            "Status: Accepted\n" + definition.current_decision,
            ("authoritative", "current", "relevant"),
            {"status": "accepted", "revision": 3},
        ),
        (
            "stale",
            SourceKind.RETRIEVED_DOCUMENT,
            f"ADR-{prefix}-superseded.md",
            "Status: Superseded\n" + definition.stale_decision,
            ("stale", "superseded", "conflicting"),
            {"status": "superseded", "superseded_by": f"{prefix}:current"},
        ),
        (
            "tool",
            SourceKind.TOOL_SCHEMA,
            "unused-weather-tool.schema.json",
            json.dumps(
                {
                    "name": "get_historical_weather",
                    "arguments": {"city": "string", "year": "integer"},
                },
                sort_keys=True,
            ),
            ("irrelevant", "tool_schema"),
            {"used_in_task": False},
        ),
        (
            "terminal",
            SourceKind.TERMINAL_OUTPUT,
            "terminal-previous-run.txt",
            "Archived run from two releases ago. A developer tried the "
            f"superseded approach: {definition.stale_decision} The run failed.",
            ("stale", "noisy", "failed_run"),
            {"exit_code": 1, "age_days": 120},
        ),
        (
            "history",
            SourceKind.GIT_HISTORY,
            "git-history.txt",
            definition.history,
            ("relevant", "history"),
            {"commits": 3},
        ),
        (
            "duplicate",
            SourceKind.MESSAGE,
            "architecture-summary-copy.md",
            "Copied summary, not authoritative: " + definition.current_decision,
            ("duplicate", "secondary"),
            {"duplicates": f"{prefix}:current"},
        ),
    )
    return tuple(
        _context_source(
            source_id=f"{prefix}:{suffix}",
            kind=kind,
            name=name,
            content=content,
            tags=tags,
            provenance=provenance,
            position=index,
        )
        for index, (suffix, kind, name, content, tags, provenance) in enumerate(values)
    )


def _context_source(
    *,
    source_id: str,
    kind: SourceKind,
    name: str,
    content: str,
    tags: tuple[str, ...],
    provenance: Mapping[str, Any],
    position: int,
) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=kind,
        name=name,
        content=content,
        token_count=max(1, len(content.split())),
        token_count_method="whitespace_estimate",
        provenance=provenance,
        tags=tags,
        insertion_position=position,
    )


def _python_definition(
    *,
    case_id: str,
    suite: EvalSuite,
    category: EvalCategory,
    instruction: str,
    source: str,
    current: str,
    stale: str,
    hidden_assertions: str,
    public_assertion: str,
) -> _CaseDefinition:
    public_test = (
        "import unittest\n"
        "import module\n\n"
        "class PublicContract(unittest.TestCase):\n"
        f"    def test_public_contract(self):\n        {public_assertion}\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    hidden_script = "import module\n" + hidden_assertions
    return _CaseDefinition(
        case_id=case_id,
        suite=suite,
        category=category,
        instruction=instruction,
        workspace_files={
            "module.py": source,
            "test_public.py": public_test,
            "README.md": "Implement the requested behavior and preserve the API.\n",
        },
        allowed_files=("module.py",),
        current_decision=current,
        stale_decision=stale,
        history="The latest compatibility commit adopted the accepted ADR after "
        "the older behavior caused production edge-case failures.",
        verification=VerificationSpec(
            required_files=("module.py",),
            commands=(CommandCheck((sys.executable, "-c", hidden_script)),),
        ),
        relevant_name="module.py",
    )


def _json_definition(
    *,
    case_id: str,
    suite: EvalSuite,
    category: EvalCategory,
    instruction: str,
    filename: str,
    initial: Mapping[str, Any],
    current: str,
    stale: str,
    expected: tuple[tuple[tuple[str | int, ...], Any], ...],
) -> _CaseDefinition:
    return _CaseDefinition(
        case_id=case_id,
        suite=suite,
        category=category,
        instruction=instruction,
        workspace_files={
            filename: json.dumps(initial, indent=2, sort_keys=True) + "\n",
            "README.md": "Update the JSON document using the accepted decision.\n",
        },
        allowed_files=(filename,),
        current_decision=current,
        stale_decision=stale,
        history="The current rollout replaced the superseded values after a "
        "production review; retain unrelated keys for compatibility.",
        verification=VerificationSpec(
            required_files=(filename,),
            json_expectations=tuple(
                JsonExpectation(filename, key_path, value)
                for key_path, value in expected
            ),
        ),
        relevant_name=filename,
    )


def _answer_definition(
    *,
    case_id: str,
    suite: EvalSuite,
    instruction: str,
    source_name: str,
    source: str,
    current: str,
    stale: str,
    answer: str,
) -> _CaseDefinition:
    return _CaseDefinition(
        case_id=case_id,
        suite=suite,
        category=EvalCategory.CODEBASE_QUESTION,
        instruction=instruction + " Write only the answer to answer.txt.",
        workspace_files={
            source_name: source,
            # Windows elevated-sandbox ACLs can make newly created files
            # unreadable to the host. The task still has to produce the answer,
            # but it does so by modifying this empty, host-owned output file.
            "answer.txt": "",
            "README.md": "Use current architecture records when answering.\n",
        },
        allowed_files=("answer.txt",),
        current_decision=current,
        stale_decision=stale,
        history="The ownership map was updated in the latest release and the old "
        "location is retained only for historical reference.",
        verification=VerificationSpec(
            required_files=("answer.txt",),
            exact_files={"answer.txt": answer},
        ),
        relevant_name=source_name,
    )


SMOKE_CASES = (
    _case(
        _python_definition(
            case_id="smoke-true-division",
            suite=EvalSuite.SMOKE,
            category=EvalCategory.BUG_FIX,
            instruction="Fix divide() according to the current numeric contract.",
            source=(
                "def divide(left: int, right: int) -> float:\n"
                "    return left // right\n"
            ),
            current="divide() must use true division and retain Python's zero error.",
            stale="Use floor division so all results remain integers.",
            hidden_assertions="assert module.divide(7, 2) == 3.5\n",
            public_assertion="self.assertEqual(module.divide(8, 2), 4)",
        )
    ),
    _case(
        _json_definition(
            case_id="smoke-service-config",
            suite=EvalSuite.SMOKE,
            category=EvalCategory.CONFIGURATION,
            instruction="Update service.json for the current production rollout.",
            filename="service.json",
            initial={"mode": "legacy", "retries": 1, "owner": "platform"},
            current="Production mode is strict and retries must be 3.",
            stale="Keep legacy mode and disable retries.",
            expected=((("mode",), "strict"), (("retries",), 3)),
        )
    ),
)


DEVELOPMENT_CASES = (
    _case(
        _python_definition(
            case_id="dev-trim-label",
            suite=EvalSuite.DEVELOPMENT,
            category=EvalCategory.BUG_FIX,
            instruction="Correct normalize_label() using the accepted contract.",
            source="def normalize_label(value: str) -> str:\n    return value\n",
            current="Trim surrounding whitespace and lowercase labels.",
            stale="Preserve whitespace and case exactly.",
            hidden_assertions="assert module.normalize_label('  Ready ') == 'ready'\n",
            public_assertion="self.assertEqual(module.normalize_label('ok'), 'ok')",
        )
    ),
    _case(
        _python_definition(
            case_id="dev-clamp",
            suite=EvalSuite.DEVELOPMENT,
            category=EvalCategory.FEATURE,
            instruction="Implement clamp() under the current utility contract.",
            source=(
                "def clamp(value: int, lower: int, upper: int) -> int:\n"
                "    raise NotImplementedError\n"
            ),
            current="Clamp inclusively and raise ValueError when lower exceeds upper.",
            stale="Swap reversed bounds silently.",
            hidden_assertions=(
                "assert module.clamp(-1, 0, 5) == 0\n"
                "assert module.clamp(8, 0, 5) == 5\n"
                "try:\n module.clamp(1, 2, 1)\nexcept ValueError:\n pass\n"
                "else:\n raise AssertionError('expected ValueError')\n"
            ),
            public_assertion="self.assertEqual(module.clamp(3, 0, 5), 3)",
        )
    ),
    _case(
        _json_definition(
            case_id="dev-queue-incident",
            suite=EvalSuite.DEVELOPMENT,
            category=EvalCategory.INCIDENT_RESPONSE,
            instruction="Record the current queue mitigation in resolution.json.",
            filename="resolution.json",
            initial={"action": "pending", "queue": "emails", "ticket": 441},
            current=(
                "Set action to drain_dead_letter and leave queue and ticket intact."
            ),
            stale="Purge the primary queue immediately.",
            expected=((("action",), "drain_dead_letter"), (("ticket",), 441)),
        )
    ),
    _case(
        _answer_definition(
            case_id="dev-owner-answer",
            suite=EvalSuite.DEVELOPMENT,
            instruction="Identify the current owner of request serialization.",
            source_name="ownership.txt",
            source="legacy_serializer=core.http\ncurrent_registry=transport.codec\n",
            current="transport.codec owns request serialization.",
            stale="core.http remains the serializer owner.",
            answer="transport.codec",
        )
    ),
)


HELDOUT_CASES = (
    _case(
        _python_definition(
            case_id="heldout-normalize-identifier",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.BUG_FIX,
            instruction="Fix normalize_identifier() to match the accepted API.",
            source=(
                "def normalize_identifier(value: str) -> str:\n"
                "    return value.replace(' ', '_')\n"
            ),
            current=(
                "Trim, lowercase, and collapse runs of spaces or hyphens to one "
                "underscore."
            ),
            stale="Preserve case and convert spaces only.",
            hidden_assertions=(
                "assert module.normalize_identifier('  API--Client Name ') == "
                "'api_client_name'\n"
            ),
            public_assertion=(
                "self.assertEqual(module.normalize_identifier('one two'), 'one_two')"
            ),
        )
    ),
    _case(
        _python_definition(
            case_id="heldout-retry-delay",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.BUG_FIX,
            instruction="Repair retry_delay() under the current resilience ADR.",
            source=(
                "def retry_delay(attempt: int, base: int = 2, cap: int = 30) -> int:\n"
                "    return min(cap, base * attempt)\n"
            ),
            current=(
                "Attempts are one-based exponential backoff: base * "
                "2**(attempt-1), capped; reject attempts below 1."
            ),
            stale="Use linear backoff and treat attempt zero as the first retry.",
            hidden_assertions=(
                "assert module.retry_delay(1) == 2\n"
                "assert module.retry_delay(4) == 16\n"
                "assert module.retry_delay(8) == 30\n"
                "try:\n module.retry_delay(0)\nexcept ValueError:\n pass\n"
                "else:\n raise AssertionError('expected ValueError')\n"
            ),
            public_assertion="self.assertEqual(module.retry_delay(2), 4)",
        )
    ),
    _case(
        _python_definition(
            case_id="heldout-feature-flag-parser",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.BUG_FIX,
            instruction="Correct parse_feature_flag() for the current config format.",
            source=(
                "def parse_feature_flag(value: str) -> bool:\n    return bool(value)\n"
            ),
            current=(
                "Accept case-insensitive true/1/on and false/0/off; reject every "
                "other value."
            ),
            stale="Any nonempty string enables a feature.",
            hidden_assertions=(
                "assert module.parse_feature_flag('ON') is True\n"
                "assert module.parse_feature_flag('0') is False\n"
                "try:\n module.parse_feature_flag('maybe')\nexcept ValueError:\n pass\n"
                "else:\n raise AssertionError('expected ValueError')\n"
            ),
            public_assertion="self.assertTrue(module.parse_feature_flag('true'))",
        )
    ),
    _case(
        _python_definition(
            case_id="heldout-header-merge",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.BUG_FIX,
            instruction="Fix merge_headers() without breaking its public signature.",
            source=(
                "def merge_headers(base: dict[str, str], override: dict[str, str]) "
                "-> dict[str, str]:\n"
                "    return {**base, **override}\n"
            ),
            current=(
                "Header names compare case-insensitively; overrides win and retain "
                "override casing."
            ),
            stale="Header keys are case-sensitive and duplicate casing is allowed.",
            hidden_assertions=(
                "result = module.merge_headers({'Accept': 'text', 'X-A': '1'}, "
                "{'accept': 'json'})\n"
                "assert result == {'X-A': '1', 'accept': 'json'}\n"
            ),
            public_assertion=(
                "self.assertEqual(module.merge_headers({'A': '1'}, {'B': '2'}), "
                "{'A': '1', 'B': '2'})"
            ),
        )
    ),
    _case(
        _python_definition(
            case_id="heldout-slugify",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.FEATURE,
            instruction="Implement slugify() according to the current URL policy.",
            source="def slugify(value: str) -> str:\n    raise NotImplementedError\n",
            current=(
                "Lowercase ASCII text, replace non-alphanumeric runs with one "
                "hyphen, and trim hyphens."
            ),
            stale="Use underscores and preserve uppercase letters.",
            hidden_assertions=(
                "assert module.slugify('  Hello, API World!! ') == 'hello-api-world'\n"
            ),
            public_assertion="self.assertEqual(module.slugify('one'), 'one')",
        )
    ),
    _case(
        _python_definition(
            case_id="heldout-bounded-page",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.FEATURE,
            instruction="Implement bounded_page() for the current pagination contract.",
            source=(
                "def bounded_page(requested: int, total_pages: int) -> int:\n"
                "    raise NotImplementedError\n"
            ),
            current=(
                "Return 0 when total_pages is 0; otherwise clamp requested "
                "inclusively to 1..total_pages."
            ),
            stale="Pages are zero-based and negative requests wrap from the end.",
            hidden_assertions=(
                "assert module.bounded_page(-2, 5) == 1\n"
                "assert module.bounded_page(9, 5) == 5\n"
                "assert module.bounded_page(3, 0) == 0\n"
            ),
            public_assertion="self.assertEqual(module.bounded_page(3, 5), 3)",
        )
    ),
    _case(
        _python_definition(
            case_id="heldout-redact-token",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.FEATURE,
            instruction="Implement redact_token() under the current logging policy.",
            source=(
                "def redact_token(value: str) -> str:\n    raise NotImplementedError\n"
            ),
            current=(
                "For tokens longer than 4 characters return stars plus the final "
                "4; fully star shorter tokens."
            ),
            stale="Expose the first 4 token characters for debugging.",
            hidden_assertions=(
                "assert module.redact_token('abcdefgh') == '****efgh'\n"
                "assert module.redact_token('abc') == '***'\n"
            ),
            public_assertion="self.assertEqual(module.redact_token(''), '')",
        )
    ),
    _case(
        _python_definition(
            case_id="heldout-select-timeout",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.FEATURE,
            instruction="Implement select_timeout() using the accepted client policy.",
            source=(
                "def select_timeout("
                "default: float, requested: float | None) -> float:\n"
                "    raise NotImplementedError\n"
            ),
            current=(
                "Use default when requested is None; otherwise require positive "
                "values and choose the smaller timeout."
            ),
            stale="Requested timeouts always replace defaults, including zero.",
            hidden_assertions=(
                "assert module.select_timeout(10, None) == 10\n"
                "assert module.select_timeout(10, 4) == 4\n"
                "assert module.select_timeout(10, 20) == 10\n"
                "try:\n module.select_timeout(10, 0)\nexcept ValueError:\n pass\n"
                "else:\n raise AssertionError('expected ValueError')\n"
            ),
            public_assertion="self.assertEqual(module.select_timeout(5, 3), 3)",
        )
    ),
    _case(
        _json_definition(
            case_id="heldout-api-config",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.CONFIGURATION,
            instruction="Update api.json for the accepted production deployment.",
            filename="api.json",
            initial={"mode": "compat", "logging": "plain", "retries": 1, "port": 8080},
            current="Set mode=strict, logging=json, retries=4; port remains unchanged.",
            stale="Keep compatibility mode with plain logs and one retry.",
            expected=(
                (("mode",), "strict"),
                (("logging",), "json"),
                (("retries",), 4),
                (("port",), 8080),
            ),
        )
    ),
    _case(
        _json_definition(
            case_id="heldout-worker-config",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.CONFIGURATION,
            instruction="Apply the current worker capacity decision.",
            filename="worker.json",
            initial={"concurrency": 2, "prefetch": 10, "queue": "critical"},
            current="Use concurrency 6 and prefetch 3; preserve the critical queue.",
            stale="Increase prefetch to 50 and leave concurrency at 2.",
            expected=(
                (("concurrency",), 6),
                (("prefetch",), 3),
                (("queue",), "critical"),
            ),
        )
    ),
    _case(
        _json_definition(
            case_id="heldout-database-config",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.CONFIGURATION,
            instruction="Update database.json to the accepted connection policy.",
            filename="database.json",
            initial={"ssl": "prefer", "pool": {"min": 1, "max": 30}, "driver": "pg"},
            current="Require SSL and set pool min=3 max=12; retain the pg driver.",
            stale="Disable SSL and use a maximum pool of 30.",
            expected=(
                (("ssl",), "require"),
                (("pool", "min"), 3),
                (("pool", "max"), 12),
                (("driver",), "pg"),
            ),
        )
    ),
    _case(
        _json_definition(
            case_id="heldout-rollout-config",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.CONFIGURATION,
            instruction="Apply the approved feature rollout to rollout.json.",
            filename="rollout.json",
            initial={"feature": "new_search", "percentage": 5, "regions": ["us-east"]},
            current=(
                "Roll out to 25 percent in us-east and us-west, keeping the "
                "feature name."
            ),
            stale="Roll out globally at 100 percent immediately.",
            expected=(
                (("feature",), "new_search"),
                (("percentage",), 25),
                (("regions",), ["us-east", "us-west"]),
            ),
        )
    ),
    _case(
        _json_definition(
            case_id="heldout-payments-incident",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.INCIDENT_RESPONSE,
            instruction="Record the approved payments mitigation in resolution.json.",
            filename="resolution.json",
            initial={"service": "payments", "action": "pending", "region": "us-east"},
            current=(
                "Fail payments over to us-central and set action=regional_failover."
            ),
            stale="Restart every payments instance in us-east.",
            expected=(
                (("service",), "payments"),
                (("action",), "regional_failover"),
                (("region",), "us-central"),
            ),
        )
    ),
    _case(
        _json_definition(
            case_id="heldout-queue-incident",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.INCIDENT_RESPONSE,
            instruction="Record the accepted queue recovery action.",
            filename="resolution.json",
            initial={
                "queue": "notifications",
                "action": "pending",
                "preserve_messages": True,
            },
            current="Set action=replay_dead_letter while preserving messages.",
            stale="Purge both the primary and dead-letter queues.",
            expected=(
                (("queue",), "notifications"),
                (("action",), "replay_dead_letter"),
                (("preserve_messages",), True),
            ),
        )
    ),
    _case(
        _json_definition(
            case_id="heldout-cache-incident",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.INCIDENT_RESPONSE,
            instruction=(
                "Record the scoped cache mitigation selected by incident command."
            ),
            filename="resolution.json",
            initial={"namespace": "catalog:v3", "action": "pending", "scope": "global"},
            current="Evict only catalog:v3 and set scope=namespace, action=evict.",
            stale="Flush the entire shared cache cluster.",
            expected=(
                (("namespace",), "catalog:v3"),
                (("action",), "evict"),
                (("scope",), "namespace"),
            ),
        )
    ),
    _case(
        _json_definition(
            case_id="heldout-tls-incident",
            suite=EvalSuite.HELDOUT,
            category=EvalCategory.INCIDENT_RESPONSE,
            instruction="Record the current TLS remediation in resolution.json.",
            filename="resolution.json",
            initial={"certificate": "edge-2025", "action": "pending", "reload": False},
            current=(
                "Rotate to edge-2026, set action=rotate_certificate, and enable reload."
            ),
            stale="Disable certificate verification until the next release.",
            expected=(
                (("certificate",), "edge-2026"),
                (("action",), "rotate_certificate"),
                (("reload",), True),
            ),
        )
    ),
    _case(
        _answer_definition(
            case_id="heldout-serialization-owner",
            suite=EvalSuite.HELDOUT,
            instruction="Which module currently owns response serialization?",
            source_name="module-map.txt",
            source="legacy=web.response\ncodec=transport.serializer\n",
            current="transport.serializer owns response serialization.",
            stale="web.response owns all serialization.",
            answer="transport.serializer",
        )
    ),
    _case(
        _answer_definition(
            case_id="heldout-timeout-key",
            suite=EvalSuite.HELDOUT,
            instruction="What is the canonical configuration key for request timeout?",
            source_name="settings-map.txt",
            source="deprecated=request_timeout_ms\ncanonical=http.client.timeout_seconds\n",
            current="The canonical key is http.client.timeout_seconds.",
            stale="Use request_timeout_ms for every client.",
            answer="http.client.timeout_seconds",
        )
    ),
    _case(
        _answer_definition(
            case_id="heldout-compatibility-version",
            suite=EvalSuite.HELDOUT,
            instruction=(
                "Which wire-protocol version is the current compatibility floor?"
            ),
            source_name="compatibility.txt",
            source="v1=removed\nv2=maintenance\nv3=current\n",
            current="Wire protocol v2 is the compatibility floor; v3 is preferred.",
            stale="All clients must use v1 indefinitely.",
            answer="v2",
        )
    ),
    _case(
        _answer_definition(
            case_id="heldout-release-command",
            suite=EvalSuite.HELDOUT,
            instruction="Which command is required for the current release validation?",
            source_name="commands.txt",
            source=(
                "legacy=make smoke\ncurrent=python -m tools.release_check --strict\n"
            ),
            current="Run python -m tools.release_check --strict before release.",
            stale="Only make smoke is required.",
            answer="python -m tools.release_check --strict",
        )
    ),
)


def get_suite(suite: EvalSuite | str) -> tuple[EvalCase, ...]:
    """Return a stable dataset partition without inspecting evaluation results."""

    selected = EvalSuite(suite)
    if selected is EvalSuite.REAL:
        from evals.real_cases import get_real_cases

        return get_real_cases()
    return {
        EvalSuite.SMOKE: SMOKE_CASES,
        EvalSuite.DEVELOPMENT: DEVELOPMENT_CASES,
        EvalSuite.HELDOUT: HELDOUT_CASES,
    }[selected]


def all_cases() -> tuple[EvalCase, ...]:
    """Return every case in suite order."""

    return (*SMOKE_CASES, *DEVELOPMENT_CASES, *HELDOUT_CASES)


def get_case(case_id: str) -> EvalCase:
    """Resolve one unique case by its stable identifier."""

    matches = tuple(case for case in all_cases() if case.case_id == case_id)
    if len(matches) != 1:
        raise KeyError(f"unknown or duplicate eval case: {case_id}")
    return matches[0]
