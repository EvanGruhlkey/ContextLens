# Command-line workflows

The default ContextLens workflow is Git-native and requires no proprietary
trace format:

```bash
contextlens scan
contextlens diff --base origin/main
contextlens verify .contextlens/evals.json --base origin/main
contextlens minimize AGENTS.md --config .contextlens/evals.json
```

`scan` and `diff` never call a model. `verify` and verified `minimize` invoke
the configured agent and should be budgeted accordingly.

## Scan repository context

```bash
contextlens scan [repository]
contextlens scan --format json --output .contextlens/scan.json
contextlens scan --format markdown --output .contextlens/scan.md
```

Discovery recognizes root and nested `AGENTS.md`/`CLAUDE.md`, Copilot
instructions, Cursor rules, repository skills, MCP configs, and conventional
static tool-schema files. UTF-8 bytes divided by four is used as an explicitly
labeled estimate because `scan` has no provider/model dependency.

Findings include duplicate and nested-scope duplicate instructions, explicit
missing path references, potential modal conflicts, narrowly targeted root
guidance, and tool/schema footprint. They are always `observed / static` and
`NOT VERIFIED`.

Backwards compatibility: `contextlens scan trace.jsonl ...` still profiles a
recorded request. New integrations should use the explicit spelling:

```bash
contextlens profile trace.jsonl --observation observation.json
```

## Diff context versions

```bash
contextlens diff [repository] --base origin/main
```

If `--base` is omitted, ContextLens tries the merge-base with `origin/main`,
`main`, `origin/master`, or `master`, then `HEAD^`. Base files are read with
`git ls-tree` and `git show`; the worktree is never changed.

Formats are `terminal`, `json`, and `markdown`.

## Verify a context change

```bash
contextlens verify .contextlens/evals.json \
  --repository . \
  --base origin/main \
  --format json \
  --output .contextlens/verify.json
```

The config is intentionally small:

The portable JSON Schema is
[`schemas/contextlens-evals.schema.json`](../schemas/contextlens-evals.schema.json).

```json
{
  "trials": 3,
  "quality_tolerance": 0,
  "economics_tolerance": 0.02,
  "require_provider_usage": true,
  "max_runs": 30,
  "agent": {
    "type": "subprocess",
    "command": ["python", "tools/contextlens_agent.py"],
    "adapter_id": "our-agent-v1",
    "provider": "provider",
    "model": "model",
    "seed": 42,
    "temperature": 0,
    "tools": ["shell"],
    "parameters": {}
  },
  "tasks": [
    {
      "id": "parser-regression",
      "instruction": "Fix the parser regression.",
      "workspace": ".",
      "checks": [
        ["python", "-m", "pytest", "tests/test_parser.py", "-q"],
        ["python", "-m", "mypy", "src/parser.py"]
      ],
      "allowed_files": ["src/parser.py"],
      "timeout_seconds": 300
    }
  ]
}
```

For deterministic question/answer tasks, replace `checks` with
`"expected_output": "..."`. Mechanical checks are preferred; ContextLens does
not insert an LLM judge.

Every base and candidate trial receives a fresh copy of the same workspace.
The built-in Codex adapter uses `--ignore-rules` and injects only the selected
discovered context. The generic subprocess contract receives the exact context
version in the request JSON. Custom subprocesses must not independently load
repository instructions, because doing so would invalidate the intervention.

### Subprocess contract

ContextLens sets:

- `CONTEXTLENS_REQUEST`: complete JSON request path;
- `CONTEXTLENS_RESULT`: result JSON path.

The result may contain:

```json
{
  "output_text": "Completed",
  "commands": ["pytest tests/test_parser.py -q"],
  "test_results": ["12 passed"],
  "input_tokens": 12000,
  "cached_input_tokens": 9000,
  "output_tokens": 800,
  "cost_usd": 0.08,
  "tool_calls": 7,
  "retries": 0,
  "metadata": {
    "reasoning_tokens": 220,
    "turns": 5,
    "files_read": ["src/parser.py", "tests/test_parser.py"],
    "searches": ["parse_header"],
    "model_latency_ms": 18000,
    "tool_latency_ms": 3200
  }
}
```

Missing categories remain unavailable; ContextLens does not replace them with
context-footprint estimates.

### Pricing

Optional pricing is a dated, explicit snapshot:

```json
{
  "pricing": {
    "provider": "provider",
    "model": "model",
    "effective_date": "2026-08-18",
    "uncached_input_per_million_usd": 2.0,
    "cached_input_per_million_usd": 0.2,
    "output_per_million_usd": 8.0,
    "reasoning_per_million_usd": 8.0
  }
}
```

ContextLens does not bundle live prices. If an observed token category lacks a
price, calculated dollar cost stays unavailable rather than using a false
equivalence.

### Verdict and exit codes

- `PASS` — quality passed and measured economics did not regress beyond policy;
- `WARN` — quality passed but optional provider economics were unavailable;
- `CONTEXT REGRESSION` — candidate quality or measured economics regressed;
- `INCONCLUSIVE` — required evidence was missing or only one pair was run.

Exit codes: `0` for PASS/WARN, `4` for regression, `5` for inconclusive, and
`2` for invalid input/configuration.

## Minimize context

```bash
contextlens minimize AGENTS.md packages/api/AGENTS.md \
  --config .contextlens/evals.json \
  --max-candidates 8 \
  --patch-output .contextlens/patches/context-minimized.diff \
  --report-output .contextlens/minimize.json \
  --format json
```

Static analysis generates candidates. The combined candidate is compared with
the current context using the verification suite. A patch is written only for
a PASS verdict and positive footprint reduction. The repository source files
are never edited.

Without `--config`, candidates are printed as `candidate/static — NOT VERIFIED`
and no patch can be written.

## CI

```bash
contextlens ci --mode static --base origin/main \
  --max-context-increase 0.25 \
  --max-duplicate-increase 0 \
  --json-output .contextlens/ci-result.json

contextlens ci --mode verified --base origin/main \
  --config .contextlens/evals.json
```

When `GITHUB_STEP_SUMMARY` is set, Markdown is appended automatically. Use
`--summary path.md` elsewhere. Static thresholds are optional and gate only
deterministic footprint/configuration properties—not claimed task performance.

## Existing advanced commands

The original experimental interfaces remain supported:

- `record`: launch an instrumented agent that writes a ContextLens JSONL trace;
- `profile`: one-run utilization analysis from a trace;
- `analyze`: paired bootstrap analysis from normalized measurements;
- `optimize`: adaptive ablation and combined target-model verification;
- `policy`: export a validated policy from a saved report;
- `trim`: apply a previously verified policy before an agent request;
- `report`: render a saved report as terminal, JSON, CSV, or HTML.

These commands are useful for custom research and deeply integrated systems.
Teams do not need them to obtain value from `scan` or `diff`.

See [migration notes](migration.md) for exact compatibility behavior.
