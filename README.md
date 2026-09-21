# ContextLens

ContextLens reduces the context coding agents have to read.

It filters large tool outputs and source reads before they enter the coding
model. Jev cheaply scores semantic relevance; local structural analysis
preserves code dependencies; omitted evidence remains recoverable.

```text
Coding agent
    ↓
tool call
    ↓
raw tool result
    ↓
ContextLens
    ↓
smaller exact/recoverable result
    ↓
coding agent
```

Jev performs relevance decisions, not reasoning for the coding agent. It does
not choose the next action, run tools, write commands, or generate code.
Exact original source is preserved. Anything omitted stays behind a stable
handle. Structural support (imports, class headers, referenced constants) is
added deterministically from the AST. The goal is lower coding-model context
without lower task success.

ContextLens is not an agent controller. The coding agent still decides what to
do, which tools to call, what commands to run, and what edits to make.
Filtering happens on the tool-response path so it does not add extra
coding-model turns.

## Default tools

The default MCP profile exposes a small interface:

- `context_filter` — reduce a raw tool observation (source, search, tests, logs)
- `context_read` — exact current source; large files are filtered when a task is known
- `context_recover` — restore omitted or deferred exact text
- `context_pin` — keep an observation out of automatic garbage collection
- `context_list` — list active, pinned, and deferred observations

Pinned items, including explicit user requirements and the current task, are
never dropped automatically. Deferred observations keep a handle and can be
recovered exactly. ContextLens does not summarize multiple observations into a
new synthesized observation.

Small files, explicit narrow line-range reads, and known-symbol reads pass
through. Thresholds are configurable (`CONTEXTLENS_MIN_TOKENS`,
`CONTEXTLENS_KEEP_THRESHOLD`, and related `CONTEXTLENS_*` variables).

## Run it

Requires Git and Python 3.12+. Local discovery and exact reads need no model:

```bash
python -m pip install -e .
contextlens find --root . --query "refresh-token timeout" --encoding o200k_base
contextlens read --root . --path src/auth.py --start-line 20 --end-line 60
contextlens recover cl_RECEIPT_ID --receipts .contextlens/source
```

Jev filtering needs a Vercel AI Gateway key:

```bash
export AI_GATEWAY_API_KEY="your-vercel-ai-gateway-key"
contextlens filter --task "fix the refresh-token timeout" --kind code --input src/auth.py
contextlens mcp --root . --state .contextlens --encoding o200k_base
```

`--profile filter` is the default. Direct reads of small or ranged source do
not need a gateway key.

Vercel Pro and Enterprise users can set `CONTEXTLENS_VERCEL_ZDR=1` for
zero-data-retention routing. Vercel rejects that option on Hobby plans.

## Real Coding-Agent Benchmark

The main evaluation is a paired coding-agent run on ten frozen real Python
GitHub issues (Click, responses, Luigi, Powertools, Flask, Babel). Two of the
tasks are SWE-bench-Live lite instances (`python-babel__babel-1141`,
`pallets__flask-5637`). The agent sees original issue text only. Gold patches,
hidden tests, and relevant symbols stay on the host.

Three conditions share the same model, reasoning level, prompt, commit, tools,
timeout, environment, and max turns. ContextLens is not mentioned to the agent.
It transforms tool results on the host before they enter the coding model:

1. **Baseline** — raw tool outputs
2. **Jev Filter** — KEEP/DROP relevance filtering, no AST expansion
3. **ContextLens** — Jev filtering, structural source expansion, recovery handles

```bash
export OPENAI_API_KEY="..."          # or AI_GATEWAY_API_KEY
export AI_GATEWAY_API_KEY="..."      # required for Jev
python -m benchmarks.goal --output evals/artifacts/coding-agent --trials 1 --timeout 300
```

Jev tokens and cost are reported separately and are never added to coding-model
input. `benchmarks.filter_eval` remains a component regression for the filter
pipeline; it is not this coding-agent result.

Checked-in 21 September 2026 run: hidden graders calibrated **10/10** (buggy
checkout fails, gold patch passes). Every one of the **30** paired attempts
then ended `agent_unavailable` because this environment had no coding-model
API key. The tables record that blocked run in full. They are **not** a
measurement of ContextLens savings or bug-fix quality.

| Condition   | Verified Fixes | Coding Input Tokens | Uncached Input | Cached Input | Output Tokens | Total Coding Tokens | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Recoveries | Jev Input |
| ----------- | -------------: | ------------------: | -------------: | -----------: | ------------: | ------------------: | --------------: | -------------------: | -------------: | ----------: | ---------: | --------: |
| Baseline    |              0 |                   0 |              0 |            0 |             0 |                   0 |               0 |                    0 |              0 |           0 |          0 |         0 |
| Jev Filter  |              0 |                   0 |              0 |            0 |             0 |                   0 |               0 |                    0 |              0 |           0 |          0 |         0 |
| ContextLens |              0 |                   0 |              0 |            0 |             0 |                   0 |               0 |                    0 |              0 |           0 |          0 |         0 |

| Metric                      | Jev Filter vs Baseline | ContextLens vs Baseline |
| --------------------------- | ---------------------: | ----------------------: |
| Coding-model input tokens   |                     +0 |                      +0 |
| Uncached coding-model input |                     +0 |                      +0 |
| Injected tool output        |                     +0 |                      +0 |
| Agent turns                 |                     +0 |                      +0 |
| Verified fixes              |                     +0 |                      +0 |

| Task                                | Baseline Pass | ContextLens Pass | Baseline Input | ContextLens Input | Tokens Saved | Input Change |
| ----------------------------------- | ------------- | ---------------- | -------------: | ----------------: | -----------: | -----------: |
| aws-powertools-eventbridge-replay   | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| aws-powertools-query-merge          | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| click-empty-default                 | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| click-short-help                    | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| pallets-flask-trusted-hosts         | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| python-babel-parse-time             | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| responses-blank-query               | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| responses-query-mutation            | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| spotify-luigi-bool-default          | ❌             | ❌                |              0 |                 0 |            0 |          n/a |
| spotify-luigi-run-arguments         | ❌             | ❌                |              0 |                 0 |            0 |          n/a |

Verified fixes: Baseline **0/10**, Jev Filter **0/10**, ContextLens **0/10**.
ContextLens saved **0** coding-model input tokens (0%). Uncached coding-model
input saved **0**. Tool-output tokens prevented from entering model context:
**0**. Agent turns did not change. Recoveries: **0** calls restoring **0**
tokens. Jev usage (separate): **0** input / **0** output tokens, cost **0**.
Do not treat these zeros as evidence that ContextLens preserved fixes or
reduced frontier-model context.

The component filter fixture is unchanged: injected tool-output tokens fell
from **1,004** to **418** (58.37%), with Jev scored separately (**60** input /
**12** output). That is not a coding-agent result.

The live paired coding-agent gate remains:

```text
task success does not regress
AND
coding-model input decreases meaningfully
AND
agent turns do not materially increase
```

Do not treat fixture token reduction, or a blocked `agent_unavailable` run, as
that gate.

## Experimental / research

Mandatory controller routing was tested and removed from the default
architecture. A `context_next` loop that chose the agent's next capability
before ordinary tool use kept **2/3 verified fixes**, timed out once, and used
**156.21% more complete input** across the two finished pairs. That extra
decision point increased agent input and turns, so it is not the shipping
path.

Keep those experiments with:

```bash
contextlens mcp --profile controller
# aliases: --profile jev
```

Historical measurements stay in place:

- [controller trajectory](docs/controller-trajectory-benchmark-2026-09-20.md)
- [controller loop](docs/controller-loop-benchmark-2026-09-20.md)
- [action selection](docs/action-selection-benchmark-2026-09-20.md)
- [Jev evidence](docs/jev-benchmark-2026-09-20.md)
- [older whole-agent pilot](benchmarks/results/goal-e2e-2026-09-18/README.md)

A compact find-and-read profile (`--profile compact`) and the neural SWE-Pruner
path (`--profile legacy` / `contextlens prune`) remain available for research.
Install `.[neural]` only for that older scorer.

## Develop it

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check src tests
mypy
```

| Folder | What's in it |
| --- | --- |
| [`src/contextlens/`](src/contextlens/) | Observation filtering, exact-source recovery, optional Jev scoring, MCP |
| [`tests/`](tests/) | Filter, recovery, MCP, and provider-validation coverage |
| [`benchmarks/`](benchmarks/) | Fixture filter eval plus historical controller and evidence reports |
| [`docs/`](docs/) | Architecture, research notes, and measured reports |

Released under the [MIT License](LICENSE).
