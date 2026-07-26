from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments import (
    AgentOutcome,
    AgentSettings,
    ContextVariant,
    DirectorySnapshot,
    MemoryReplayCache,
    ReplayCoordinator,
    ReplayRequest,
    ReplayStatus,
    ReplayTask,
    ReplayWorker,
    ResourceLimits,
    SubprocessAgentAdapter,
)
from contextlens.trace import ContextSource, SourceKind


def context() -> tuple[ContextSource, ...]:
    return (
        ContextSource(
            source_id="instructions",
            kind=SourceKind.AGENT_INSTRUCTION,
            name="AGENTS.md",
            content="Run tests.",
            token_count=2,
            token_count_method="fixture",
        ),
        ContextSource(
            source_id="history",
            kind=SourceKind.GIT_HISTORY,
            name="git history",
            content="Previous parser changes.",
            token_count=4,
            token_count_method="fixture",
        ),
    )


class RecordingAdapter:
    adapter_id = "recording-v1"

    def __init__(self) -> None:
        self.workspaces: list[str] = []
        self.contexts: list[tuple[str, ...]] = []
        self._lock = threading.Lock()

    def run(self, request: ReplayRequest) -> AgentOutcome:
        with self._lock:
            self.workspaces.append(request.workspace)
            self.contexts.append(
                tuple(source.source_id for source in request.context)
            )
        workspace = Path(request.workspace)
        seed = (workspace / "seed.txt").read_text(encoding="utf-8")
        (workspace / "result.txt").write_text(
            f"{seed}:{request.variant.variant_id}",
            encoding="utf-8",
        )
        return AgentOutcome(
            output_text=request.variant.variant_id,
            commands=("agent run",),
            input_tokens=6,
        )


def worker(root: Path, adapter: RecordingAdapter) -> ReplayWorker:
    return ReplayWorker(
        adapter=adapter,
        snapshot=DirectorySnapshot(root),
        task=ReplayTask("task-1", "Update the parser."),
        context=context(),
        settings=AgentSettings(
            provider="fixture",
            model="fixture-model",
            seed=42,
            temperature=0,
            tools=("shell",),
        ),
        timeout_seconds=10,
    )


class ReplayWorkerTests(unittest.TestCase):
    def test_parallel_variants_use_distinct_isolated_workspaces(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed.txt").write_text("original", encoding="utf-8")
            adapter = RecordingAdapter()
            coordinator = ReplayCoordinator(
                worker(root, adapter),
                ResourceLimits(max_workers=2),
            )

            results = coordinator.run(
                (
                    ContextVariant("baseline"),
                    ContextVariant(
                        "without-history",
                        frozenset({"history"}),
                    ),
                )
            )

            self.assertEqual(
                [result.status for result in results],
                [ReplayStatus.COMPLETED, ReplayStatus.COMPLETED],
            )
            self.assertEqual(len(set(adapter.workspaces)), 2)
            self.assertFalse(any(Path(path).exists() for path in adapter.workspaces))
            self.assertCountEqual(
                adapter.contexts,
                [
                    ("instructions", "history"),
                    ("instructions",),
                ],
            )
            self.assertEqual(
                [result.context_tokens for result in results],
                [6, 2],
            )
            self.assertTrue(
                all(
                    result.file_changes[0].path == "result.txt"
                    for result in results
                )
            )
            self.assertEqual(
                (root / "seed.txt").read_text(encoding="utf-8"),
                "original",
            )
            self.assertFalse((root / "result.txt").exists())

    def test_cache_reuses_equivalent_context(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed.txt").write_text("original", encoding="utf-8")
            adapter = RecordingAdapter()
            cache = MemoryReplayCache()
            coordinator = ReplayCoordinator(
                worker(root, adapter),
                ResourceLimits(),
                cache=cache,
            )

            first = coordinator.run((ContextVariant("first"),))[0]
            second = coordinator.run((ContextVariant("second"),))[0]

            self.assertEqual(first.status, ReplayStatus.COMPLETED)
            self.assertEqual(second.status, ReplayStatus.CACHED)
            self.assertEqual(second.variant_id, "second")
            self.assertEqual(len(adapter.workspaces), 1)

    def test_preflight_enforces_run_token_and_cost_limits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed.txt").write_text("original", encoding="utf-8")
            replay_worker = worker(root, RecordingAdapter())

            with self.assertRaisesRegex(ValueError, "max_runs"):
                ReplayCoordinator(
                    replay_worker,
                    ResourceLimits(max_runs=1),
                ).run((ContextVariant("one"), ContextVariant("two")))

            with self.assertRaisesRegex(ValueError, "context tokens"):
                ReplayCoordinator(
                    replay_worker,
                    ResourceLimits(max_context_tokens=5),
                ).run((ContextVariant("baseline"),))

            with self.assertRaisesRegex(ValueError, "estimated cost"):
                ReplayCoordinator(
                    replay_worker,
                    ResourceLimits(max_estimated_cost_usd=0.01),
                ).run((ContextVariant("baseline"),))

    def test_unknown_removed_source_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed.txt").write_text("original", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown source"):
                worker(root, RecordingAdapter()).run(
                    ContextVariant("invalid", frozenset({"missing"}))
                )

    def test_timeout_and_failures_become_results(self) -> None:
        class TimeoutAdapter:
            adapter_id = "timeout"

            def run(self, request: ReplayRequest) -> AgentOutcome:
                raise TimeoutError("fixture timeout")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed.txt").write_text("original", encoding="utf-8")
            replay_worker = ReplayWorker(
                adapter=TimeoutAdapter(),
                snapshot=DirectorySnapshot(root),
                task=ReplayTask("task", "Run."),
                context=context(),
                settings=AgentSettings("fixture", "fixture"),
                timeout_seconds=1,
            )
            result = replay_worker.run(ContextVariant("timeout"))
            self.assertEqual(result.status, ReplayStatus.TIMED_OUT)
            self.assertIn("fixture timeout", result.error or "")

    def test_coordinator_retries_failed_workers(self) -> None:
        class FlakyAdapter:
            adapter_id = "flaky"

            def __init__(self) -> None:
                self.attempts = 0

            def run(self, request: ReplayRequest) -> AgentOutcome:
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("temporary failure")
                return AgentOutcome(output_text="recovered")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed.txt").write_text("original", encoding="utf-8")
            adapter = FlakyAdapter()
            replay_worker = ReplayWorker(
                adapter=adapter,
                snapshot=DirectorySnapshot(root),
                task=ReplayTask("task", "Run."),
                context=context(),
                settings=AgentSettings("fixture", "fixture"),
                timeout_seconds=1,
            )
            result = ReplayCoordinator(
                replay_worker,
                ResourceLimits(retries=1),
            ).run((ContextVariant("flaky"),))[0]

            self.assertEqual(result.status, ReplayStatus.COMPLETED)
            self.assertEqual(result.attempt, 2)
            self.assertEqual(adapter.attempts, 2)


class SubprocessAdapterTests(unittest.TestCase):
    def test_subprocess_receives_request_and_changes_workspace(self) -> None:
        script = (
            "import json,os,pathlib;"
            "p=pathlib.Path(os.environ['CONTEXTLENS_REQUEST']);"
            "d=json.loads(p.read_text());"
            "pathlib.Path('agent.txt').write_text(d['variant']['variant_id']);"
            "print(d['task']['instruction'])"
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed.txt").write_text("original", encoding="utf-8")
            replay_worker = ReplayWorker(
                adapter=SubprocessAgentAdapter((sys.executable, "-c", script)),
                snapshot=DirectorySnapshot(root),
                task=ReplayTask("task", "Run the external agent."),
                context=context(),
                settings=AgentSettings("fixture", "fixture"),
                timeout_seconds=10,
            )

            result = replay_worker.run(ContextVariant("subprocess"))

            self.assertEqual(result.status, ReplayStatus.COMPLETED)
            assert result.outcome is not None
            self.assertIn("Run the external agent.", result.outcome.output_text)
            self.assertEqual(result.file_changes[0].path, "agent.txt")


if __name__ == "__main__":
    unittest.main()
