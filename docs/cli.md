# Command-line workflows

Install the package in a Python 3.11+ environment:

```bash
python -m pip install -e .
contextlens --help
```

## Record

```bash
contextlens record --output traces/task-001.jsonl -- your-agent-command
```

`record` sets `CONTEXTLENS_TRACE` to the requested absolute path and launches
the command without a shell. The agent must use the ContextLens recording API
to write that file. The command fails if it exits unsuccessfully, does not
produce a trace, or produces an invalid trace.

This wrapper does not claim that it can intercept an uninstrumented provider
SDK automatically.

## Scan one run

```bash
contextlens scan traces/task-001.jsonl \
  --observation observations/task-001.json \
  --format terminal
```

Observation JSON may contain:

```json
{
  "output_text": "The final response",
  "accessed_source_ids": ["agents-md"],
  "commands": ["pytest"],
  "tool_inputs": ["src/parser.py"],
  "changed_files": ["src/parser.py"]
}
```

Use `--request-id` for a multi-request trace and `--artifacts` when content was
externalized.

## Analyze paired measurements

```bash
contextlens analyze measurements.json \
  --baseline baseline \
  --ablated without-tool-schemas \
  --label "Unused MCP schemas" \
  --runs-per-day 10000 \
  --projection-days 30 \
  --experiment-cost-usd 25 \
  --format html \
  --output reports/tool-schemas.html
```

The input is a JSON list of `Measurement` fields. Supported report formats are
`terminal`, `json`, `csv`, and `html`.

When production workload arguments are supplied, the report recommends
`keep`, `remove`, or `investigate` and projects tokens, dollars, latency, net
savings, and break-even runs. Project a combined candidate rather than summing
individual source projections.

## Optimize

```bash
contextlens optimize experiment.json \
  --format json \
  --output runs/latest.json
```

The command performs:

1. Trace loading and one-run profiling.
2. Baseline replay.
3. Adaptive group ablation.
4. Evaluation of every completed worker.
5. Objective-specific candidate construction.
6. Combined target-model verification.
7. Report assembly with the experiment tree and worker evidence.

An optimization configuration uses JSON:

```json
{
  "trace": "traces/task-001.jsonl",
  "artifacts": "traces/artifacts",
  "task": {
    "task_id": "parser-1",
    "instruction": "Fix the parser.",
    "workspace": "fixtures/parser"
  },
  "agent": {
    "command": ["python", "agent_wrapper.py"],
    "adapter_id": "my-agent-v1",
    "provider": "provider",
    "model": "model",
    "seed": 42,
    "temperature": 0,
    "tools": ["shell"]
  },
  "evaluator": {
    "type": "test_results"
  },
  "limits": {
    "max_workers": 4,
    "max_runs": 25,
    "timeout_seconds": 300,
    "retries": 1
  },
  "search": {
    "score_name": "success",
    "quality_tolerance": 0,
    "max_experiments": 20,
    "batch_size": 4
  },
  "optimization": {
    "objective": "min_cost",
    "quality_tolerance": 0.01,
    "max_cost_usd": 0.05
  }
}
```

Paths are resolved relative to the configuration file.

Built-in CLI evaluators are `exact_match` and `test_results`. Library users can
use custom, human, or model-graded evaluators directly.

### Subprocess result contract

The worker sets:

- `CONTEXTLENS_REQUEST` to the complete replay request JSON.
- `CONTEXTLENS_RESULT` to the path where the agent may write result JSON.

Result JSON can contain:

```json
{
  "output_text": "Completed",
  "commands": ["pytest"],
  "test_results": ["42 passed"],
  "input_tokens": 12000,
  "output_tokens": 800,
  "cost_usd": 0.08,
  "metadata": {}
}
```

If no result file is written, standard output becomes `output_text`. Nonzero
exit status and timeouts become failed worker results.

## Rerender

```bash
contextlens report runs/latest.json --format html --output runs/latest.html
```

JSON reports are stable, rerenderable artifacts. HTML reports are
self-contained. Reports include aggregate findings, adaptive decisions,
stopping reasons, warnings, and compact per-worker drill-down without embedding
raw model output.
