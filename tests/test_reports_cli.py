from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.cli import main
from contextlens.policy import ContextPolicy, PolicyRule, PolicyStrategy
from contextlens.reports import (
    Finding,
    Report,
    render_csv,
    render_html,
    render_json,
    render_terminal,
)
from contextlens.trace import ContextSource, SourceKind, TraceWriter


def write_trace(path: Path) -> None:
    with TraceWriter(path) as writer:
        writer.add(
            "request-1",
            ContextSource(
                source_id="agents",
                kind=SourceKind.AGENT_INSTRUCTION,
                name="AGENTS.md",
                content="Always run parser tests.",
                token_count=4,
                token_count_method="fixture",
            ),
        )
        writer.add(
            "request-1",
            ContextSource(
                source_id="history",
                kind=SourceKind.GIT_HISTORY,
                name="history",
                content="Old release history and unrelated notes.",
                token_count=6,
                token_count_method="fixture",
            ),
        )


class ReportRendererTests(unittest.TestCase):
    def test_round_trip_and_all_renderers(self) -> None:
        report = Report(
            title="Example <report>",
            generated_at="2026-07-27T00:00:00+00:00",
            findings=(
                Finding(
                    source_id="agents",
                    name="AGENTS.md",
                    kind="agent_instruction",
                    evidence_level="target_model",
                    verdict="helpful",
                    tokens=100,
                    effect=0.2,
                    confidence_low=0.1,
                    confidence_high=0.3,
                ),
            ),
            warnings=("small sample",),
            summary={"experiments": 5},
        )
        restored = Report.from_dict(json.loads(render_json(report)))

        self.assertEqual(restored, report)
        self.assertIn("AGENTS.md", render_terminal(report))
        self.assertIn("source_id,name,kind", render_csv(report))
        rendered_html = render_html(report)
        self.assertIn("<!doctype html>", rendered_html)
        self.assertIn("Example &lt;report&gt;", rendered_html)
        self.assertNotIn("<report>", rendered_html)


class CliTests(unittest.TestCase):
    def test_trim_emits_prompt_lazy_store_and_token_audit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sources = (
                ContextSource(
                    source_id="keep",
                    kind=SourceKind.AGENT_INSTRUCTION,
                    name="AGENTS.md",
                    content="keep",
                    token_count=10,
                    token_count_method="fixture",
                ),
                ContextSource(
                    source_id="drop",
                    kind=SourceKind.GIT_HISTORY,
                    name="history.txt",
                    content="drop",
                    token_count=20,
                    token_count_method="fixture",
                ),
                ContextSource(
                    source_id="lazy",
                    kind=SourceKind.TOOL_SCHEMA,
                    name="tools.json",
                    content="lazy",
                    token_count=30,
                    token_count_method="fixture",
                ),
            )
            context = root / "context.json"
            policy_path = root / "policy.json"
            output = root / "trimmed.json"
            audit = root / "audit.json"
            context.write_text(
                json.dumps({"context": [item.to_dict() for item in sources]}),
                encoding="utf-8",
            )
            policy_path.write_text(
                ContextPolicy(
                    context={
                        "keep": PolicyRule(
                            sources=("AGENTS.md",),
                            strategy=PolicyStrategy.ALWAYS_INCLUDE,
                        ),
                        "drop": PolicyRule(
                            sources=("history.txt",),
                            strategy=PolicyStrategy.EXCLUDE,
                        ),
                        "lazy": PolicyRule(
                            sources=("tools.json",),
                            strategy=PolicyStrategy.LAZY_LOAD,
                        ),
                    }
                ).to_json(),
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()) as stdout:
                main(
                    [
                        "trim",
                        str(context),
                        "--policy",
                        str(policy_path),
                        "--output",
                        str(output),
                        "--audit-output",
                        str(audit),
                        "--min-reduction",
                        "0.8",
                    ]
                )

            trimmed = json.loads(output.read_text(encoding="utf-8"))
            lazy = json.loads((root / "trimmed.lazy.json").read_text(encoding="utf-8"))
            audit_value = json.loads(audit.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["source_id"] for item in trimmed["context"]],
                ["keep"],
            )
            self.assertEqual(lazy["context"][0]["source_id"], "lazy")
            self.assertEqual(trimmed["savings"]["before_tokens"], 60)
            self.assertEqual(trimmed["savings"]["after_tokens"], 10)
            self.assertEqual(audit_value["savings"]["saved_tokens"], 50)
            self.assertIn("83.3% reduction", stdout.getvalue())

    def test_scan_and_report_commands(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "trace.jsonl"
            report_path = root / "report.json"
            html_path = root / "report.html"
            write_trace(trace)
            observation = root / "observation.json"
            observation.write_text(
                json.dumps(
                    {
                        "output_text": "I followed AGENTS.md and ran parser tests.",
                        "accessed_source_ids": ["agents"],
                    }
                ),
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "scan",
                        str(trace),
                        "--observation",
                        str(observation),
                        "--format",
                        "json",
                        "--output",
                        str(report_path),
                    ]
                )
                main(
                    [
                        "report",
                        str(report_path),
                        "--format",
                        "html",
                        "--output",
                        str(html_path),
                    ]
                )

            value = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(value["summary"]["profiled_sources"], 2)
            self.assertIn("<!doctype html>", html_path.read_text(encoding="utf-8"))

    def test_analyze_command_writes_verified_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            measurements = root / "measurements.json"
            output = root / "analysis.json"
            values: list[dict[str, object]] = []
            for index in range(5):
                common = {
                    "task_id": f"task-{index}",
                    "trial_id": "trial",
                    "success": True,
                    "output_tokens": 10,
                    "latency_seconds": 1,
                }
                values.extend(
                    (
                        {
                            **common,
                            "variant_id": "baseline",
                            "score": 1,
                            "input_tokens": 200,
                            "cost_usd": 0.02,
                        },
                        {
                            **common,
                            "variant_id": "ablated",
                            "score": 0.8,
                            "input_tokens": 100,
                            "cost_usd": 0.01,
                        },
                    )
                )
            measurements.write_text(json.dumps(values), encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "analyze",
                        str(measurements),
                        "--baseline",
                        "baseline",
                        "--ablated",
                        "ablated",
                        "--bootstrap-samples",
                        "100",
                        "--runs-per-day",
                        "100",
                        "--projection-days",
                        "30",
                        "--experiment-cost-usd",
                        "5",
                        "--format",
                        "json",
                        "--output",
                        str(output),
                    ]
                )

            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["findings"][0]["verdict"], "helpful")
            self.assertEqual(
                report["findings"][0]["evidence_level"],
                "verified",
            )
            self.assertEqual(report["findings"][0]["action"], "keep")
            self.assertEqual(
                report["summary"]["projected_input_tokens_saved"],
                0,
            )

    def test_record_runs_an_instrumented_agent(self) -> None:
        script = (
            "import os,pathlib;"
            "from contextlens.trace import TraceWriter,ContextSource,SourceKind;"
            "p=pathlib.Path(os.environ['CONTEXTLENS_TRACE']);"
            "w=TraceWriter(p);w.__enter__();"
            "w.add('request',ContextSource(kind=SourceKind.MESSAGE,"
            "name='user',content='hello'));w.__exit__()"
        )
        with TemporaryDirectory() as directory:
            output = Path(directory) / "recorded.jsonl"
            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "record",
                        "--output",
                        str(output),
                        "--",
                        sys.executable,
                        "-c",
                        script,
                    ]
                )
            self.assertTrue(output.exists())

    def test_optimize_runs_end_to_end_subprocess_workflow(self) -> None:
        agent_script = (
            "import json,os,pathlib;"
            "request=json.loads(pathlib.Path("
            "os.environ['CONTEXTLENS_REQUEST']).read_text());"
            "tokens=sum(x.get('token_count') or 0 for x in request['context']);"
            "result={'output_text':'ok','input_tokens':tokens,"
            "'output_tokens':1,'cost_usd':tokens/1000,"
            "'test_results':['1 passed']};"
            "pathlib.Path(os.environ['CONTEXTLENS_RESULT']).write_text("
            "json.dumps(result))"
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "trace.jsonl"
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "fixture.txt").write_text("fixture", encoding="utf-8")
            write_trace(trace)
            config = root / "experiment.json"
            output = root / "optimization.json"
            config.write_text(
                json.dumps(
                    {
                        "trace": "trace.jsonl",
                        "task": {
                            "task_id": "task",
                            "instruction": "Complete the task.",
                            "workspace": "workspace",
                        },
                        "agent": {
                            "command": [sys.executable, "-c", agent_script],
                            "provider": "fixture",
                            "model": "fixture-model",
                        },
                        "evaluator": {
                            "type": "exact_match",
                            "expected": "ok",
                        },
                        "search": {
                            "score_name": "success",
                            "max_experiments": 5,
                            "batch_size": 2,
                        },
                        "optimization": {
                            "objective": "min_cost",
                            "quality_tolerance": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "optimize",
                        str(config),
                        "--format",
                        "json",
                        "--output",
                        str(output),
                    ]
                )

            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["summary"]["candidate_accepted"])
            self.assertEqual(
                set(report["summary"]["candidate_removed_sources"]),
                {"agents", "history"},
            )


if __name__ == "__main__":
    unittest.main()
