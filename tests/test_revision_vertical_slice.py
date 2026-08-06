from __future__ import annotations

import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.analysis import Measurement, PairedAnalyzer
from contextlens.evaluators import CodingTaskEvaluator
from contextlens.experiments import (
    AgentOutcome,
    AgentSettings,
    ContextMutation,
    ContextVariant,
    DeterministicExperimentCoordinator,
    DirectorySnapshot,
    ExperimentLifecycle,
    ExperimentStatus,
    MutationOperation,
    ReplayRequest,
    ReplayTask,
    ReplayWorker,
    SummaryResult,
    apply_mutations,
)
from contextlens.policy import (
    ContextPolicy,
    PolicyRule,
    PolicyStrategy,
    mutations_from_policy,
)
from contextlens.profiler import ContextProfiler, RunObservation
from contextlens.storage import ContextLensStore
from contextlens.trace import (
    AgentStatus,
    AgentTrace,
    ContextEvent,
    ContextSource,
    SecretRedactor,
    SourceKind,
    StepType,
    TokenUsage,
    TraceHeader,
    TraceReader,
    TraceStep,
    TraceWriter,
)

FIXTURE = Path(__file__).parent / "fixtures" / "coding-agent-repo"


def source(
    source_id: str,
    kind: SourceKind,
    name: str,
    content: str,
    *,
    tokens: int = 100,
    provenance: dict[str, object] | None = None,
    target_phase: str | None = None,
) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=kind,
        name=name,
        content=content,
        token_count=tokens,
        token_count_method="fixture",
        provenance=provenance or {},
        target_phase=target_phase,
    )


class FixtureSummarizer:
    summarizer_id = "fixture-summary-v1"

    def summarize(
        self,
        item: ContextSource,
        target_tokens: int,
    ) -> SummaryResult:
        return SummaryResult(
            content="Tests failed once; rerun the acceptance suite.",
            prompt=f"Summarize {item.name} to {target_tokens} tokens.",
            provider="fixture",
            model="deterministic-summary",
        )


class FixtureCodingAdapter:
    adapter_id = "fixture-coding-v1"

    def run(self, request: ReplayRequest) -> AgentOutcome:
        names = {item.name for item in request.context}
        workspace = Path(request.workspace)
        implementation = (
            "def add(left: int, right: int) -> int:\n"
            '    """Return the sum of two integers."""\n'
            "    return left + right\n"
            if "AGENTS.md" in names
            else "def add(left: int, right: int) -> int:\n    return left - right\n"
        )
        (workspace / "calculator.py").write_text(implementation, encoding="utf-8")
        if "distracting.md" in names:
            (workspace / "migration-notes.txt").write_text(
                "Unrelated migration note.\n",
                encoding="utf-8",
            )
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", "acceptance_calculator.py"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        result_line = (
            "acceptance tests passed"
            if completed.returncode == 0
            else "acceptance tests failed"
        )
        return AgentOutcome(
            output_text=result_line,
            commands=("python -m unittest -q",),
            test_results=(result_line,),
            input_tokens=sum(item.token_count or 0 for item in request.context),
            output_tokens=12,
            tool_calls=2,
            metadata={
                "task_completion": completed.returncode == 0,
                "build": True,
                "type_check": True,
                "lint": True,
            },
        )


class MutationAndPlanningTests(unittest.TestCase):
    def test_all_four_mutations_are_explicit_and_reproducible(self) -> None:
        context = (
            source("keep", SourceKind.FILE, "keep.py", "keep me"),
            source("remove", SourceKind.FILE, "remove.py", "remove me"),
            source("terminal", SourceKind.TERMINAL_OUTPUT, "terminal", "very noisy"),
            source("tools", SourceKind.TOOL_SCHEMA, "weather", "unused schema"),
            source(
                "phase",
                SourceKind.REPO_INSTRUCTION,
                "deploy.md",
                "deploy only",
                target_phase="deploy",
            ),
        )
        application = apply_mutations(
            context,
            (
                ContextMutation(MutationOperation.REMOVE, "remove"),
                ContextMutation(
                    MutationOperation.SUMMARIZE,
                    "terminal",
                    target_tokens=32,
                ),
                ContextMutation(MutationOperation.LAZY_LOAD, "tools"),
                ContextMutation(
                    MutationOperation.SCOPE,
                    "phase",
                    target_phases=("deploy",),
                ),
            ),
            summarizer=FixtureSummarizer(),
            phase="deploy",
        )
        selected = {item.source_id for item in application.context}
        self.assertIn("keep", selected)
        self.assertIn("phase", selected)
        self.assertNotIn("remove", selected)
        self.assertEqual(application.lazy_context[0].source_id, "tools")
        summary = application.generated_context[0]
        self.assertEqual(summary.kind, SourceKind.GENERATED_SUMMARY)
        self.assertEqual(summary.provenance["source_item_id"], "terminal")
        self.assertIn(summary.source_id, selected)

    def test_coordinator_respects_budget_and_schedules_repeated_pairs(self) -> None:
        context = (
            source(
                "instructions",
                SourceKind.REPO_INSTRUCTION,
                "AGENTS.md",
                "run tests",
            ),
            source(
                "tools",
                SourceKind.TOOL_SCHEMA,
                "schemas",
                "unused tools",
                tokens=500,
            ),
        )
        events = tuple(
            ContextEvent("request", index, item)
            for index, item in enumerate(context)
        )
        profile = ContextProfiler().profile(
            events,
            RunObservation(task_text="fix calculator and run tests"),
        )
        plan = DeterministicExperimentCoordinator(paired_runs=2).plan(
            context,
            profile.profiles,
            experiment_budget=8,
        )
        self.assertEqual(plan.planned_runs, 8)
        self.assertEqual(len(plan.experiments), 2)
        for experiment in plan.experiments:
            self.assertEqual(len(experiment.runs), 4)
            self.assertEqual(
                [run.variant for run in experiment.runs],
                ["baseline", "modified", "baseline", "modified"],
            )

    def test_lifecycle_rejects_skipped_states(self) -> None:
        lifecycle = ExperimentLifecycle()
        with self.assertRaises(ValueError):
            lifecycle.transition(ExperimentStatus.COMPLETED)
        lifecycle.transition(ExperimentStatus.RUNNING)
        lifecycle.transition(ExperimentStatus.EVALUATING)
        lifecycle.transition(ExperimentStatus.COMPLETED)


class StorageAndSecurityTests(unittest.TestCase):
    def test_sql_migration_matches_runtime_store(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "migrated.db"
            migration = (
                Path(__file__).parents[1]
                / "migrations"
                / "0001_normalized_contextlens.sql"
            )
            connection = sqlite3.connect(path)
            try:
                connection.executescript(migration.read_text(encoding="utf-8"))
            finally:
                connection.close()
            store = ContextLensStore(path)
            store.create_project("migrated", "Migrated project")

    def test_jsonl_round_trips_run_context_and_steps(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            header = TraceHeader(trace_id="trace-complete")
            run = AgentTrace(
                trace_id=header.trace_id,
                task="Fix calculator.",
                agent_type="fixture",
                model_provider="local",
                model_name="deterministic",
            )
            item = source(
                "agents",
                SourceKind.REPO_INSTRUCTION,
                "AGENTS.md",
                "Run tests.",
            )
            with TraceWriter(path, header=header) as writer:
                writer.set_trace(run)
                writer.add("request", item)
                writer.add_step(
                    TraceStep(
                        trace_id=run.trace_id,
                        sequence=0,
                        step_type=StepType.MODEL_REQUEST,
                        input_context_item_ids=(item.source_id,),
                    )
                )
            reader = TraceReader(path)
            self.assertEqual(reader.read_trace(), run)
            self.assertEqual(tuple(reader.events())[0].source, item)
            self.assertEqual(tuple(reader.steps())[0].step_type, StepType.MODEL_REQUEST)

    def test_normalized_store_scopes_trace_reads_to_project(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContextLensStore(Path(directory) / "contextlens.db")
            store.create_project("project-a", "A")
            store.create_project("project-b", "B")
            trace = AgentTrace(
                trace_id="trace-1",
                project_id="project-a",
                task="Fix calculator.",
                agent_type="fixture",
                model_provider="local",
                model_name="deterministic",
                status=AgentStatus.COMPLETED,
            )
            item = source(
                "agents",
                SourceKind.REPO_INSTRUCTION,
                "AGENTS.md",
                "Run tests.",
            )
            step = TraceStep(
                trace_id=trace.trace_id,
                sequence=0,
                step_type=StepType.MODEL_REQUEST,
                input_context_item_ids=(item.source_id,),
                token_usage=TokenUsage(input=100),
            )
            store.save_trace(
                trace,
                steps=(step,),
                context_events=(ContextEvent("request", 0, item),),
            )
            self.assertEqual(
                store.get_trace("project-a", "trace-1")["task"],
                trace.task,
            )
            self.assertEqual(
                store.context_items("project-a", "trace-1")[0]["id"],
                "agents",
            )
            with self.assertRaises(KeyError):
                store.get_trace("project-b", "trace-1")
            store.delete_trace("project-a", "trace-1")
            with self.assertRaises(KeyError):
                store.get_trace("project-a", "trace-1")

    def test_content_hash_and_built_in_secret_redaction(self) -> None:
        item = source(
            "secret",
            SourceKind.FILE,
            ".env",
            "API_KEY=super-secret-token-value",
        )
        redacted = SecretRedactor().redact(item)
        self.assertNotEqual(item.content_hash, redacted.content_hash)
        self.assertNotIn("super-secret", redacted.content or "")
        self.assertIn("redacted", redacted.tags)


class PolicyAndEvaluationTests(unittest.TestCase):
    def test_policy_serializes_to_valid_yaml_and_json_shapes(self) -> None:
        policy = ContextPolicy(
            context={
                "repo_instructions": PolicyRule(
                    sources=("AGENTS.md",),
                    strategy=PolicyStrategy.ALWAYS_INCLUDE,
                ),
                "tool_schemas": PolicyRule(
                    sources=("MCP tools",),
                    strategy=PolicyStrategy.LAZY_LOAD,
                    max_tokens=500,
                ),
            }
        )
        self.assertIn("strategy: lazy_load", policy.to_yaml())
        self.assertEqual(policy.to_dict()["version"], 1)
        self.assertIn('"objective": "balanced"', policy.to_json())
        compiled = mutations_from_policy(
            (
                source("agents", SourceKind.REPO_INSTRUCTION, "AGENTS.md", "rules"),
                source("tools", SourceKind.TOOL_SCHEMA, "MCP tools", "schemas"),
            ),
            policy,
        )
        self.assertEqual(len(compiled), 1)
        self.assertEqual(compiled[0].operation, MutationOperation.LAZY_LOAD)

    def test_coding_evaluator_preserves_dimensions_and_costs(self) -> None:
        result = _worker().run(ContextVariant("baseline"))
        evaluation = CodingTaskEvaluator().evaluate(
            ReplayTask(
                "fix-add",
                "Fix add.",
                metadata={"allowed_files": ["calculator.py"]},
            ),
            result,
        )
        self.assertTrue(evaluation.success)
        self.assertIn("tests", evaluation.dimensions)
        self.assertLess(evaluation.dimensions["patchScope"], 1)
        self.assertEqual(evaluation.tool_calls, 2)


class VerticalSliceTests(unittest.TestCase):
    def test_fixture_derives_helpful_harmful_and_lazy_load_findings(self) -> None:
        worker = _worker()
        evaluator = CodingTaskEvaluator(objective="quality")
        variants = {
            "baseline": ContextVariant("baseline"),
            "without-helpful": ContextVariant(
                "without-helpful",
                mutations=(
                    ContextMutation(MutationOperation.REMOVE, "agents"),
                ),
            ),
            "without-distractor": ContextVariant(
                "without-distractor",
                mutations=(
                    ContextMutation(MutationOperation.REMOVE, "distractor"),
                ),
            ),
            "lazy-tools": ContextVariant(
                "lazy-tools",
                mutations=(
                    ContextMutation(MutationOperation.LAZY_LOAD, "tools"),
                ),
            ),
        }
        results = {
            name: tuple(worker.run(variant) for _ in range(2))
            for name, variant in variants.items()
        }
        task = worker.task

        def effect(variant: str):
            measurements: list[Measurement] = []
            for index in range(2):
                for name in ("baseline", variant):
                    result = results[name][index]
                    evaluation = evaluator.evaluate(task, result)
                    measurements.append(
                        Measurement.from_result(
                            result,
                            evaluation,
                            trial_id=str(index),
                            score_name="quality",
                        )
                    )
            return PairedAnalyzer(
                bootstrap_samples=100,
                equivalence_tolerance=0.001,
            ).analyze(
                tuple(measurements),
                baseline_variant_id="baseline",
                ablated_variant_id=variant,
            )

        helpful = effect("without-helpful")
        distracting = effect("without-distractor")
        lazy = effect("lazy-tools")
        self.assertEqual(helpful.verdict.value, "helpful")
        self.assertEqual(helpful.recommendation, "retain")
        self.assertEqual(distracting.verdict.value, "harmful")
        self.assertEqual(distracting.recommendation, "remove")
        self.assertEqual(lazy.verdict.value, "neutral")
        self.assertGreater(lazy.input_tokens_saved_by_ablation, 0)
        self.assertEqual(lazy.recommendation, "remove")


def _context() -> tuple[ContextSource, ...]:
    return (
        source(
            "agents",
            SourceKind.REPO_INSTRUCTION,
            "AGENTS.md",
            (FIXTURE / "AGENTS.md").read_text(encoding="utf-8"),
            tokens=80,
        ),
        source(
            "architecture",
            SourceKind.FILE,
            "docs/architecture.md",
            (FIXTURE / "docs" / "architecture.md").read_text(encoding="utf-8"),
            tokens=90,
        ),
        source(
            "tools",
            SourceKind.TOOL_SCHEMA,
            "tool-schemas.json",
            (FIXTURE / "tool-schemas.json").read_text(encoding="utf-8"),
            tokens=600,
        ),
        source(
            "terminal",
            SourceKind.TERMINAL_OUTPUT,
            "terminal-output.txt",
            (FIXTURE / "terminal-output.txt").read_text(encoding="utf-8"),
            tokens=400,
        ),
        source(
            "history",
            SourceKind.GIT_HISTORY,
            "git-history.txt",
            (FIXTURE / "git-history.txt").read_text(encoding="utf-8"),
            tokens=60,
        ),
        source(
            "distractor",
            SourceKind.FILE,
            "distracting.md",
            (FIXTURE / "distracting.md").read_text(encoding="utf-8"),
            tokens=100,
            provenance={"contradicts": "agents"},
        ),
    )


def _worker() -> ReplayWorker:
    return ReplayWorker(
        adapter=FixtureCodingAdapter(),
        snapshot=DirectorySnapshot(FIXTURE),
        task=ReplayTask(
            "fix-add",
            "Fix add() and run the acceptance test.",
            metadata={"allowed_files": ["calculator.py"]},
        ),
        context=_context(),
        settings=AgentSettings("fixture", "deterministic"),
        timeout_seconds=10,
    )


if __name__ == "__main__":
    unittest.main()
