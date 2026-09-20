# ContextLens

**ContextLens helps coding agents find the repository code they need for a task.**
Its goal is to reduce input tokens and irrelevant context while keeping enough
information for the agent to produce a correct result.

For example, a task like “fix the refresh-token timeout” may only need a few
functions and their dependencies. ContextLens finds that code, records where it
came from, and lets the agent recover more of the original source when needed.
It provides context tools for an agent; it does not generate the fix itself.

## Status

That goal is still unmet. Smaller returned source is not enough if discovery,
decision calls, extra turns, or recoveries make the whole run larger.

- Compact find-and-read tools can return much less text than a full file, and
  they keep required evidence in the local cases we measure.
- A nine-pair coding-agent pilot of that flow produced **7/9 correct fixes in
  both conditions**, used **24.12% more input**, and had one paired quality
  regression.
- A later Jev selector still passed 5/5 evidence checks and returned 68.7% less
  selected source, but Jev’s own decision tokens exceeded the full-file
  baseline.
- The completed context-and-action controller kept **2/3 verified fixes**,
  timed out once, and used **156.21% more complete input** across the two
  finished pairs.

The shipping path therefore stays optional and sparse. ContextLens should be
used when location is unknown or a decision is actually needed, not before
every tool call.

## How it works

1. **Find or select.** Local discovery returns locations and short handles.
   Optional Jev ranking can choose exact source for a task and keep the rest
   behind deferred handles.
2. **Read exact code.** A handle or known path returns current source. Freshness
   is checked so changed files are not treated as current.
3. **Keep a working set.** Useful observations can be pinned, kept, or
   deferred. Pinned items and user constraints stay available when other
   descriptors are dropped.
4. **Recover.** Deferred source and observations remain readable through stable
   handles. Historical snapshots are marked historical; they are not edit
   targets.
5. **Optionally choose a next capability.** `context_next` can retain
   observations, filter offered actions, and pick one next step. It never
   executes the action. The coding model still owns reasoning, edits, and
   commands.

Local code owns source identity, ranges, budgets, freshness, and recovery.
Whole files stay out of the response unless they are the smallest useful unit.

## Results

Five fixed evidence cases retained every required source anchor and none of the
fixture noise anchors.

| Case | Evidence | Full file | Selected source | Reduction |
| --- | --- | ---: | ---: | ---: |
| Function with constant support | Passed | 3,041 | 220 | 92.8% |
| Method with class support | Passed | 1,250 | 209 | 83.3% |
| Function with transitive support | Passed | 3,047 | 218 | 92.8% |
| Gateway response validation | Passed | 1,140 | 1,945 | -70.6% |
| Scope declaration analysis | Passed | 3,726 | 1,226 | 67.1% |
| **Total** | **5/5 passed** | **12,204** | **3,818** | **68.7%** |

These numbers measure returned evidence, not end-to-end savings. Descriptor-first
ranking later cut Jev decision input by 28.6% on the same cases. The bounded
action and controller-loop checks selected the labeled next step in 6/6 and 3/3
fixed states. The paired coding pilots above are the results that matter for
the goal, and they do not pass it.

[Jev evidence](docs/jev-benchmark-2026-09-20.md) ·
[descriptor comparison](docs/jev-descriptor-benchmark-2026-09-20.md) ·
[action selection](docs/action-selection-benchmark-2026-09-20.md) ·
[controller loop](docs/controller-loop-benchmark-2026-09-20.md) ·
[controller trajectory](docs/controller-trajectory-benchmark-2026-09-20.md) ·
[older whole-agent pilot](benchmarks/results/goal-e2e-2026-09-18/README.md)

## Run it

Requires Git and Python 3.12+. Finding and reading known source is local:

```bash
python -m pip install -e .
contextlens find --root . --query "refresh-token timeout" --encoding o200k_base
contextlens read --root . --handle h_REPLACE_WITH_RETURNED_HANDLE
contextlens read --root . --path src/auth.py --start-line 20 --end-line 60
```

Jev selection, retention, and next-action choice need a Vercel AI Gateway key:

```bash
export AI_GATEWAY_API_KEY="your-vercel-ai-gateway-key"
contextlens select --root . --task "fix the refresh-token timeout"
contextlens mcp --root . --state .contextlens --encoding o200k_base
```

The default MCP profile exposes `context_select`, `context_read`,
`context_expand`, `context_observe`, `context_working_set`, `context_recall`,
and `context_next`. Direct reads do not need a gateway key. Host-supplied tool
names on `context_next` are advisory and are never executed.

Vercel Pro and Enterprise users can set `CONTEXTLENS_VERCEL_ZDR=1` for
zero-data-retention routing. Vercel rejects that option on Hobby plans.

Install `.[neural]` only for older neural experiments. Legacy retrieval remains
available through `retrieve` and `mcp --profile legacy`.

## Develop it

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check src tests
mypy
```

| Folder | What's in it |
| --- | --- |
| [`src/contextlens/`](src/contextlens/) | Discovery, exact-source tools, working sets, optional Jev decisions, and MCP |
| [`tests/`](tests/) | Selection, recovery, freshness, MCP, and provider-validation coverage |
| [`benchmarks/`](benchmarks/) | Evidence, controller, and paired coding-agent evaluations |
| [`docs/`](docs/) | Architecture, research notes, and measured reports |

Released under the [MIT License](LICENSE).
