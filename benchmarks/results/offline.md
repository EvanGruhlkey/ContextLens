# Paired coding-agent benchmark — 2026-09-21T05:17:14.342047+00:00

Model `scripted-solver`, reasoning `none`, 8 turns, 600s timeout, 1 trial(s) over 1 frozen task(s). Implementation `n/a`.

All four conditions share the coding model, reasoning effort, issue prompt, repository commit, tool set, timeout, turn limit, and hidden grader. The agent is never told that ContextLens exists. Jev tokens are counted separately and never enter coding-model input.

## Aggregate

| Condition | Verified Fixes | Coding Input | Uncached Input | Cached Input | Output | Total Coding | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Tool Calls | Compaction Events | Compaction Tokens Removed | Tasks Compacted | Recovery Calls | Recovered Tokens | Jev Input | Jev Output | Jev Cost | Wall Clock (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 1 | 0 | 0 | 0 | 0 | 0 | 6,587 | 6,587 | 0 | 7 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0.0 |
| Live Pruning | 1 | 0 | 0 | 0 | 0 | 0 | 6,587 | 477 | 6,110 | 7 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0.0 |
| Compaction Only | 1 | 0 | 0 | 0 | 0 | 0 | 6,587 | 6,587 | 0 | 7 | 6 | 1 | 3,275 | 1 | 0 | 0 | 0 | 0 | 0.000000 | 0.0 |
| Full ContextLens | 1 | 0 | 0 | 0 | 0 | 0 | 6,587 | 477 | 6,110 | 7 | 6 | 1 | 250 | 1 | 0 | 0 | 0 | 0 | 0.000000 | 0.0 |

## Versus baseline

| Metric | Live Pruning | Compaction Only | Full ContextLens |
| --- | ---: | ---: | ---: |
| Verified fixes | +0 | +0 | +0 |
| Coding-model input tokens | +0 | +0 | +0 |
| Uncached coding-model input | +0 | +0 | +0 |
| Cached coding-model input | +0 | +0 | +0 |
| Output tokens | +0 | +0 | +0 |
| Total coding-model tokens | +0 | +0 | +0 |
| Raw tool output | +0 (+0.0%) | +0 (+0.0%) | +0 (+0.0%) |
| Injected tool output | -6,110 (-92.8%) | +0 (+0.0%) | -6,110 (-92.8%) |
| Agent turns | +0 (+0.0%) | +0 (+0.0%) | +0 (+0.0%) |
| Tool calls | +0 (+0.0%) | +0 (+0.0%) | +0 (+0.0%) |
| Wall clock | +0.0 | +0.0 | +0.0 |

## Per task

| Task | Baseline Fix | Live Pruning Fix | Compaction Only Fix | Full ContextLens Fix | Baseline Input | Live Pruning Input | Compaction Only Input | Full ContextLens Input | Compacted |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| offline-parse-amount | ✅ | ✅ | ✅ | ✅ | 0 | 0 | 0 | 0 | Compaction Only, Full ContextLens |

## Regressions

- **Live Pruning**: no task that baseline fixed regressed.
- **Compaction Only**: no task that baseline fixed regressed.
- **Full ContextLens**: no task that baseline fixed regressed.

No ContextLens condition lost any of the 1 fix(es) the baseline achieved.

## Notes

Verified fixes: Baseline 1/1, Live Pruning 1/1, Compaction Only 1/1, Full ContextLens 1/1.

**Live Pruning**: coding-model input unchanged; injected tool output down 6,110 (-92.8%); agent turns unchanged; tool calls unchanged. Jev used 0 input / 0 output tokens over 2 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens.

**Compaction Only**: coding-model input unchanged; injected tool output unchanged; agent turns unchanged; tool calls unchanged. Jev used 0 input / 0 output tokens over 4 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens. Compaction fired on 1 of 1 attempt(s) across 1 event(s), removing 3,275 transcript tokens.

**Full ContextLens**: coding-model input unchanged; injected tool output down 6,110 (-92.8%); agent turns unchanged; tool calls unchanged. Jev used 0 input / 0 output tokens over 3 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens. Compaction fired on 1 of 1 attempt(s) across 1 event(s), removing 250 transcript tokens.

No coding model and no Jev gateway. A scripted solver replays one fixed tool sequence and a local heuristic answers the relevance questions. Coding-model token columns are zero because no coding model ran. This measures the ContextLens layers and the report, not task success or frontier-model savings.

1 trial(s) over 1 task(s) is not a statistical quality claim.
