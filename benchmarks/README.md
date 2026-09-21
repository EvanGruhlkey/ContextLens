# Benchmarks

One question: **can cheap Jev relevance decisions reduce the context the coding
model processes, without lowering verified task success or changing how the
agent behaves?**

Three conditions share the same frozen tasks, model, reasoning effort, tools,
prompt, repository revision, timeout, and turn limit. The agent is never told
that ContextLens exists.

| Condition | Live pruning | Transcript compaction |
| --- | --- | --- |
| `baseline` | no | no |
| `live_pruning` | yes | no |
| `compaction_only` | no | yes |
| `full_contextlens` | yes | yes |

Everything else is held identical: coding model, reasoning effort, issue
prompt, repository commit, tool set, timeout, turn limit, and hidden grader.

## Paired run (needs credentials)

```bash
export OPENAI_API_KEY="..."        # the coding model
export AI_GATEWAY_API_KEY="..."    # Jev, through the Vercel AI Gateway
python -m pip install uv           # nine of the ten hidden graders shell out to uv
python -m benchmarks.run --output benchmarks/artifacts/paired --trials 1 --timeout 300
```

The compaction trigger is the production default (20,000 transcript tokens) and
is never lowered to force it to fire. `Tasks Compacted` reports how many
attempts actually reached it; zero there means the compaction layer was not
exercised, which is a result, not a bug.

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

`results/` holds measured reports:

- `four-condition-2026-09-21-blocked.{json,md}` — the four-condition run was
  executed and produced **no measurement**. The harness completed end to end
  (ten repositories fetched, ten graders calibrated, forty attempts run and
  graded) but every attempt ended `agent_unavailable` for lack of a coding-model
  key. Saved so the failure is on the record; cite no number from it.
- `offline.{json,md}` — the offline harness check.

Reports for architectures that no longer exist are under
[`docs/history/`](../docs/history/).
