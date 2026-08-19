# Command-line workflows

The default ContextLens workflow is Git-native and requires no proprietary
trace format:

```bash
contextlens init
contextlens scan
contextlens diff --base origin/main
contextlens verify .contextlens/evals.json --base origin/main
contextlens minimize AGENTS.md --config .contextlens/evals.json
```

`scan` and `diff` never call a model. `verify` and verified `minimize` invoke
the configured agent and should be budgeted accordingly.

## Initialize a verification suite

```bash
contextlens init [repository]
contextlens init --output .contextlens/evals.json
```

`init` detects Python, Node, Rust, and Go project markers; common test and
static-check commands; recognized context; and a locally installed Codex CLI
or `CONTEXTLENS_AGENT_COMMAND`. The generated JSON remains deliberately small.
If meaningful checks or an agent are missing, the file contains explicit TODO
commands and the terminal output says it is not runnable.

## Scan repository context

```bash
contextlens scan [repository]
contextlens scan --target packages/api/src/auth.ts --provider codex
contextlens scan --target src/a.py --target src/b.py --provider portable
contextlens scan --format json --output .contextlens/scan.json
contextlens scan --format markdown --output .contextlens/scan.md
```

Discovery recognizes root and nested `AGENTS.md`/`CLAUDE.md`, Copilot
instructions, Cursor rules, repository skills, MCP configs, and conventional
static tool-schema files. UTF-8 bytes divided by four is used as an explicitly
labeled estimate because `scan` has no provider/model dependency.

Without `--target`, the total is the **repository context footprint**: every
recognized configuration file in Git, not a claim about one prompt. With one
or more `--target` values, ContextLens reports the union of effective context
for those paths. Resolver choices are `portable`, `codex`, `claude`, `copilot`,
and `cursor`; every source records whether its scope rule is `documented` or
`approximated`. Deleted targets remain analyzable through lexical scope.

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
contextlens diff --base origin/main --target packages/api/src/auth.ts
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

Every base and candidate trial receives a fresh copy of the same workspace and
a fresh agent execution. The built-in Codex adapter launches ephemeral
`codex exec` with `--ignore-user-config` and `--ignore-rules`. Recognized native
context files are also omitted from experimental workspace copies, and only the
task-effective context variant is injected in the prompt. The generic
subprocess contract receives the exact context version in the request JSON.
Custom subprocesses must not independently load repository instructions,
because doing so would invalidate the intervention.

JSON reports include the execution manifest and raw paired experiment records:
repository/commit identity, task and grader hashes, fixed-dimension hash,
adapter/model settings, alternating order, task-effective source paths and
scope decisions, context content hashes, workspace and agent-execution IDs,
raw provider telemetry, changed files, classifications, and per-pair deltas.
Infrastructure-invalid trials remain in `raw_trials` but are excluded from
causal aggregates and force an inconclusive verdict.

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

Static signals generate prioritized deduplicate, stale-guidance removal, and
scoped-move experiments. Positive-footprint candidates are first compared
individually with current context. Only isolated PASS results enter the
combined candidate, which receives a separate repeated final verification to
catch interactions. A patch is written only after that final PASS. WARN,
INCONCLUSIVE, FAIL, or a combined regression writes no patch, and repository
source files are never edited.

Scope candidates are reported but remain review-only until target-effective
replay evidence is configured. Semantic summaries and lazy loading require an
explicit summarizer or retrieval contract and are not silently synthesized.

Without `--config`, candidates are printed as `candidate/static — NOT VERIFIED`
and no patch can be written.

## CI

```bash
contextlens ci --mode static --base origin/main \
  --target packages/api/src/auth.ts --provider codex \
  --max-context-increase 0.25 \
  --max-duplicate-increase 0 \
  --json-output .contextlens/ci-result.json

contextlens ci --mode verified --base origin/main \
  --config .contextlens/evals.json
```

When `GITHUB_STEP_SUMMARY` is set, Markdown is appended automatically. Use
`--summary path.md` elsewhere. Static thresholds are optional and gate only
deterministic footprint/configuration properties—not claimed task performance.
With `--target`, or task `target_paths` read from the eval config, static CI also
reports the effective-context delta. A context increase does not fail unless a
user explicitly configures a threshold.

Verified evaluation also uses `target_paths`: base and candidate context are
resolved independently for each task, so moved or newly scoped instruction
files are reflected correctly. Set `context_provider` on each task or once at
the config root. Codex adapters default to `codex`; targeted non-Codex adapters
must choose a provider explicitly. An empty `target_paths` list intentionally
preserves repository-wide context for compatibility and emits a scope warning
in every trial record.

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
