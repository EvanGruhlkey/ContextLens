# Benchmarks

One question: **can cheap Jev relevance decisions reduce the context the coding
model processes, without lowering verified task success or changing how the
agent behaves?**

Three conditions share the same frozen tasks, model, reasoning effort, tools,
prompt, repository revision, timeout, and turn limit. The agent is never told
that ContextLens exists.

| Condition | What ContextLens does |
| --- | --- |
| `baseline` | nothing; raw tool output, transcript grows untouched |
| `live_pruning` | large tool results are pruned before the coding model reads them |
| `live_and_compaction` | live pruning, plus stale tool calls and results compacted out of the transcript once it grows past a threshold |

## Paired run (needs credentials)

```bash
export OPENAI_API_KEY="..."        # the coding model
export AI_GATEWAY_API_KEY="..."    # Jev, through the Vercel AI Gateway
python -m benchmarks.run --output benchmarks/artifacts/paired --trials 1 --timeout 300
```

Each attempt writes its own directory with the prompt, transcript shape, patch,
verification output, and row. The run writes `report.json` after every attempt
and `README.md` with the measured tables at the end. Jev tokens are recorded
separately from coding-model tokens and are never added to coding-model input.

Add `--case click-short-help` (repeatable) to run a subset, `--model` to change
the coding model, and `--max-turns` to change the turn budget.

Before any attempt, every task is calibrated on a fresh checkout: the hidden
checks must fail without a fix and pass with the gold patch. A run aborts if
that calibration does not hold.

## Offline harness check (no credentials, no network)

```bash
python -m benchmarks.offline --output benchmarks/results/offline.json
```

A scripted solver replays one fixed tool sequence against a synthetic
repository and a local heuristic answers the relevance questions in Jev's
place. This measures the two ContextLens layers and the report plumbing. It
runs no coding model, so its coding-model token columns are zero, and it is not
evidence about task success or frontier-model savings.

## Tasks

`fixtures/coding/*.json` pins a public GitHub repository at one commit with the
original issue text. The matching `*.patch` is the gold fix, used only for
calibration. Hidden assertions live in the manifest's `verification` block and
never enter the agent's workspace. Two tasks come from SWE-bench-Live.

## Results

`results/` holds measured reports. Reports for architectures that no longer
exist are under [`docs/history/`](../docs/history/).
