from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments import AgentOutcome
from evals.cases import (
    DEVELOPMENT_CASES,
    HELDOUT_CASES,
    SMOKE_CASES,
    EvalCategory,
    EvalSuite,
    all_cases,
    get_case,
    get_suite,
)
from evals.graders import HiddenCaseVerifier


class EvalCaseCorpusTests(unittest.TestCase):
    def test_partitions_are_stable_rich_and_disjoint(self) -> None:
        self.assertGreaterEqual(len(HELDOUT_CASES), 20)
        self.assertGreaterEqual(len({case.category for case in HELDOUT_CASES}), 5)
        self.assertEqual(get_suite("smoke"), SMOKE_CASES)
        self.assertEqual(get_suite(EvalSuite.DEVELOPMENT), DEVELOPMENT_CASES)
        values = all_cases()
        self.assertEqual(len({case.case_id for case in values}), len(values))
        for case in values:
            self.assertGreaterEqual(len(case.context), 8)
            kinds = {source.kind.value for source in case.context}
            self.assertIn("repo_instruction", kinds)
            self.assertIn("architecture_decision", kinds)
            self.assertIn("tool_schema", kinds)
            self.assertIn("terminal_output", kinds)
            self.assertIn("git_history", kinds)
            tags = {tag for source in case.context for tag in source.tags}
            self.assertIn("stale", tags)
            self.assertIn("duplicate", tags)
            self.assertIn("irrelevant", tags)
            self.assertTrue(
                all(
                    source.token_count_method == "whitespace_estimate"
                    for source in case.context
                )
            )

    def test_public_task_does_not_leak_hidden_or_oracle_evidence(self) -> None:
        case = get_case("heldout-retry-delay")
        task = case.replay_task()
        serialized = json.dumps(dict(task.metadata), sort_keys=True)
        self.assertNotIn("verification", serialized)
        self.assertNotIn("oracle", serialized)
        self.assertNotIn("expected", serialized)
        self.assertEqual(task.metadata["allowed_files"], ["module.py"])

    def test_workspaces_materialize_independently(self) -> None:
        case = SMOKE_CASES[0]
        with TemporaryDirectory() as directory:
            base = Path(directory)
            first = case.materialize_workspace(base / "first")
            second = case.materialize_workspace(base / "second")
            (first / "module.py").write_text("changed\n", encoding="utf-8")
            self.assertNotEqual(
                (first / "module.py").read_text(encoding="utf-8"),
                (second / "module.py").read_text(encoding="utf-8"),
            )
            with self.assertRaises(FileExistsError):
                case.materialize_workspace(first)

    def test_hidden_python_verifier_fails_then_accepts_real_behavior(self) -> None:
        case = get_case("smoke-true-division")
        with TemporaryDirectory() as directory:
            workspace = case.materialize_workspace(Path(directory) / "case")
            verifier = HiddenCaseVerifier(case)
            failed = verifier.verify(
                workspace,
                case.replay_task(),
                AgentOutcome(),
            )
            self.assertFalse(failed.passed)
            (workspace / "module.py").write_text(
                "def divide(left: int, right: int) -> float:\n"
                "    return left / right\n",
                encoding="utf-8",
            )
            passed = verifier.verify(
                workspace,
                case.replay_task(),
                AgentOutcome(),
            )
            self.assertTrue(passed.passed, passed.stderr)

    def test_hidden_json_verifier_preserves_unrelated_contract_fields(self) -> None:
        case = get_case("smoke-service-config")
        self.assertEqual(case.category, EvalCategory.CONFIGURATION)
        with TemporaryDirectory() as directory:
            workspace = case.materialize_workspace(Path(directory) / "case")
            path = workspace / "service.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value.update({"mode": "strict", "retries": 3})
            path.write_text(json.dumps(value), encoding="utf-8")
            result = HiddenCaseVerifier(case).verify(
                workspace,
                case.replay_task(),
                AgentOutcome(),
            )
            self.assertTrue(result.passed, result.stderr)


if __name__ == "__main__":
    unittest.main()
