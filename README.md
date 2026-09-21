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

Checked-in 21 September 2026 Jev-enabled run: hidden graders calibrated
**10/10**. The host agent completed all **30** paired attempts on
`gpt-5.6-luna` (`reasoning.effort=low`, 20 turns, 300s). Jev scored through
the Vercel AI Gateway. Coding-model calls stayed on OpenAI. Tool-output tokens
removed are **21,356** (Jev Filter) and **19,877** (ContextLens). Jev tokens
are separate: **136,505** / **124,519** input.

| Condition   | Verified Fixes | Coding Input Tokens | Uncached Input | Cached Input | Output Tokens | Total Coding Tokens | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Recoveries | Jev Input |
| ----------- | -------------: | ------------------: | -------------: | -----------: | ------------: | ------------------: | --------------: | -------------------: | -------------: | ----------: | ---------: | --------: |
| Baseline    |              5 |             583,459 |         67,718 |      515,741 |        14,975 |             598,434 |          46,950 |               46,950 |              0 |         136 |          0 |         0 |
| Jev Filter  |              7 |             379,716 |         57,359 |      322,357 |        16,137 |             395,853 |          48,276 |               26,920 |         21,356 |         134 |          0 |   136,505 |
| ContextLens |              5 |           1,105,615 |        151,696 |      953,919 |        14,989 |           1,120,604 |         138,931 |              119,054 |         19,877 |         141 |          0 |   124,519 |

| Metric                      | Jev Filter vs Baseline | ContextLens vs Baseline |
| --------------------------- | ---------------------: | ----------------------: |
| Coding-model input tokens   |       -203,743 (-34.9%) |        +522,156 (+89.5%) |
| Uncached coding-model input |        -10,359 (-15.3%) |         +83,978 (+124.0%) |
| Injected tool output        |        -20,030 (-42.7%) |         +72,104 (+153.6%) |
| Agent turns                 |              -2 (-1.5%) |               +5 (+3.7%) |
| Verified fixes              |                      +2 |                       +0 |

| Task                                | Baseline | Jev Filter | ContextLens | Baseline Input | Jev Filter Input | ContextLens Input |
| ----------------------------------- | -------- | ---------- | ----------- | -------------: | ---------------: | ----------------: |
| aws-powertools-eventbridge-replay   | ✅        | ✅          | ✅           |         79,418 |           30,005 |            29,772 |
| aws-powertools-query-merge          | ❌        | ❌          | ❌           |        125,593 |           80,315 |            79,077 |
| click-empty-default                 | ❌        | ❌          | ❌           |         61,088 |           30,619 |            34,485 |
| click-short-help                    | ❌        | ❌          | ❌           |         27,291 |           18,250 |           490,067 |
| pallets-flask-trusted-hosts         | ❌        | ✅          | ❌           |         79,769 |           75,477 |            73,710 |
| python-babel-parse-time             | ✅        | ✅          | ✅           |         37,987 |           28,294 |            40,720 |
| responses-blank-query               | ✅        | ✅          | ✅           |         24,891 |           24,289 |            22,721 |
| responses-query-mutation            | ✅        | ✅          | ✅           |         24,342 |           23,998 |            24,796 |
| spotify-luigi-bool-default          | ✅        | ✅          | ✅           |         41,709 |           39,789 |            45,746 |
| spotify-luigi-run-arguments         | ❌        | ✅          | ❌           |         81,371 |           28,680 |           264,521 |

Verified fixes: Baseline **5/10**, Jev Filter **7/10**, ContextLens **5/10**.
Jev Filter used **203,743** fewer coding-model input tokens (-34.9%) and
**2** fewer agent turns (-1.5%). Injected tool output fell from **46,950** to
**26,920**. Jev Filter extra fixes were Flask trusted-hosts and Luigi run
arguments; both still failed under ContextLens. This is one trial of ten
tasks, not a statistical quality claim.

ContextLens used **522,156** more coding-model input tokens (+89.5%). Two
trajectories dominate that total: `click-short-help` ingested **65,113** raw
tool-output tokens unreduced (**490,067** coding-model input vs **27,291**
baseline), and `spotify-luigi-run-arguments` used **264,521** coding-model
input after still dropping **11,593** tool tokens. ContextLens filtering
removed **19,877** tokens from its own raw tool output (**138,931 → 119,054**),
but the agent fetched much more raw output than baseline, so coding-model
input rose. Recoveries: **0**. Jev cost recorded by the gateway: **0**.

The component filter fixture is unchanged: injected tool-output tokens fell
from **1,004** to **418** (58.37%), with Jev scored separately (**60** input /
**12** output). That is not this coding-agent result.

The live paired coding-agent gate remains:

```text
task success does not regress
AND
coding-model input decreases meaningfully
AND
agent turns do not materially increase
```

On this single trial, **Jev Filter met those three numbers** (5/10 → 7/10,
-34.9% coding-model input, -1.5% turns). **ContextLens did not**: success
held at 5/10, coding-model input rose 89.5%, turns rose 3.7%. Do not treat
the Jev Filter totals as a shipping gate without more trials, and do not
average ContextLens with Jev Filter: full ContextLens is the product path
with structural expansion, and it lost to the outliers above.

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
