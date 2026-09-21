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
checkout fails, gold patch passes). The host agent then completed all **30**
paired attempts on `gpt-5.6-luna` (`reasoning.effort=low`, 20 turns, 300s).
There was no `AI_GATEWAY_API_KEY`, so Jev never scored. Jev Filter and
ContextLens **failed open** (passthrough). Tool-output tokens removed are
**0** on every attempt. The tables are a real coding-agent result. They are
**not** a measurement of ContextLens context reduction.

| Condition   | Verified Fixes | Coding Input Tokens | Uncached Input | Cached Input | Output Tokens | Total Coding Tokens | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Recoveries | Jev Input |
| ----------- | -------------: | ------------------: | -------------: | -----------: | ------------: | ------------------: | --------------: | -------------------: | -------------: | ----------: | ---------: | --------: |
| Baseline    |              6 |             468,766 |         64,773 |      403,993 |        14,631 |             483,397 |          40,751 |               40,751 |              0 |         125 |          0 |         0 |
| Jev Filter  |              5 |             452,790 |         62,653 |      390,137 |        14,967 |             467,757 |          38,036 |               38,573 |              0 |         119 |          0 |         0 |
| ContextLens |              5 |             489,836 |         63,405 |      426,431 |        16,234 |             506,070 |          39,555 |               39,980 |              0 |         128 |          0 |         0 |

| Metric                      | Jev Filter vs Baseline | ContextLens vs Baseline |
| --------------------------- | ---------------------: | ----------------------: |
| Coding-model input tokens   |         -15,976 (-3.4%) |          +21,070 (+4.5%) |
| Uncached coding-model input |          -2,120 (-3.3%) |           -1,368 (-2.1%) |
| Injected tool output        |          -2,178 (-5.3%) |             -771 (-1.9%) |
| Agent turns                 |              -6 (-4.8%) |               +3 (+2.4%) |
| Verified fixes              |                      -1 |                       -1 |

| Task                                | Baseline Pass | ContextLens Pass | Baseline Input | ContextLens Input | Tokens Saved | Input Change |
| ----------------------------------- | ------------- | ---------------- | -------------: | ----------------: | -----------: | -----------: |
| aws-powertools-eventbridge-replay   | ✅             | ✅                |         58,529 |            80,518 |      -21,989 |       -37.6% |
| aws-powertools-query-merge          | ❌             | ❌                |         89,528 |            78,941 |       10,587 |       +11.8% |
| click-empty-default                 | ❌             | ❌                |         53,261 |            48,392 |        4,869 |        +9.1% |
| click-short-help                    | ❌             | ❌                |         25,816 |            36,642 |      -10,826 |       -41.9% |
| pallets-flask-trusted-hosts         | ✅             | ❌                |         84,166 |            65,356 |       18,810 |       +22.3% |
| python-babel-parse-time             | ✅             | ✅                |         28,745 |            33,419 |       -4,674 |       -16.3% |
| responses-blank-query               | ✅             | ✅                |         24,635 |            28,882 |       -4,247 |       -17.2% |
| responses-query-mutation            | ✅             | ✅                |         22,915 |            19,320 |        3,595 |       +15.7% |
| spotify-luigi-bool-default          | ✅             | ✅                |         26,044 |            41,615 |      -15,571 |       -59.8% |
| spotify-luigi-run-arguments         | ❌             | ❌                |         55,127 |            56,751 |       -1,624 |        -2.9% |

Verified fixes: Baseline **6/10**, Jev Filter **5/10**, ContextLens **5/10**.
ContextLens used **21,070** more coding-model input tokens (+4.5%). Uncached
coding-model input changed by **-1,368** (-2.1%). Injected tool-output tokens
differed by **-771** (39,980 vs baseline 40,751); filtering removed **0**.
Agent turns increased by **3** (+2.4%). Recoveries: **0** calls restoring **0**
tokens. Jev usage (separate): **0** input / **0** output tokens, cost **0**.
The Flask trusted-hosts miss on the filter-named conditions is another
independent trajectory, not dropped evidence: Jev never ran. Do not treat
these deltas as ContextLens savings or as a measured quality regression from
filtering.

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

This run does not pass that gate. Filter conditions were passthrough, so the
input and turn deltas are coding-agent variance. Re-run with a Vercel AI
Gateway key before treating Jev Filter or ContextLens as a filter result.

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
