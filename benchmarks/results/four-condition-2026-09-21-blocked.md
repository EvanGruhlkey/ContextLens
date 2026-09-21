# Paired coding-agent benchmark — 2026-09-21T05:15:30.275317+00:00

> **This run produced no measurement.** It is saved as evidence that the
> four-condition harness runs end to end, and to record exactly what blocked
> it. Every token count, fix count, and delta below is zero because no coding
> model was reachable. Do not cite any number in this file.

Model `gpt-5.6-luna`, reasoning `low`, 20 turns, 300s timeout, 1 trial(s) over 10 frozen task(s). Implementation `c93f37de2ff8`.

All four conditions share the coding model, reasoning effort, issue prompt, repository commit, tool set, timeout, turn limit, and hidden grader. The agent is never told that ContextLens exists. Jev tokens are counted separately and never enter coding-model input.

## Aggregate

| Condition | Verified Fixes | Coding Input | Uncached Input | Cached Input | Output | Total Coding | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Tool Calls | Compaction Events | Compaction Tokens Removed | Tasks Compacted | Recovery Calls | Recovered Tokens | Jev Input | Jev Output | Jev Cost | Wall Clock (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 1.9 |
| Live Pruning | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 2.0 |
| Compaction Only | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 1.8 |
| Full ContextLens | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 1.9 |

## Versus baseline

| Metric | Live Pruning | Compaction Only | Full ContextLens |
| --- | ---: | ---: | ---: |
| Verified fixes | +0 | +0 | +0 |
| Coding-model input tokens | +0 | +0 | +0 |
| Uncached coding-model input | +0 | +0 | +0 |
| Cached coding-model input | +0 | +0 | +0 |
| Output tokens | +0 | +0 | +0 |
| Total coding-model tokens | +0 | +0 | +0 |
| Raw tool output | +0 | +0 | +0 |
| Injected tool output | +0 | +0 | +0 |
| Agent turns | +0 | +0 | +0 |
| Tool calls | +0 | +0 | +0 |
| Wall clock | +0.1 (+5.3%) | -0.1 (-5.3%) | +0.0 (+0.0%) |

## Per task

| Task | Baseline Fix | Live Pruning Fix | Compaction Only Fix | Full ContextLens Fix | Baseline Input | Live Pruning Input | Compaction Only Input | Full ContextLens Input | Compacted |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| aws-powertools-eventbridge-replay | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| aws-powertools-query-merge | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| click-empty-default | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| click-short-help | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| pallets-flask-trusted-hosts | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| python-babel-parse-time | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| responses-blank-query | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| responses-query-mutation | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| spotify-luigi-bool-default | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |
| spotify-luigi-run-arguments | ❌ | ❌ | ❌ | ❌ | 0 | 0 | 0 | 0 | none |

## Regressions

- **Live Pruning**: no task that baseline fixed regressed.
- **Compaction Only**: no task that baseline fixed regressed.
- **Full ContextLens**: no task that baseline fixed regressed.

The baseline fixed no task, so there was nothing a ContextLens condition could regress. This is not evidence that ContextLens preserves fixes.

## Notes

Verified fixes: Baseline 0/10, Live Pruning 0/10, Compaction Only 0/10, Full ContextLens 0/10.

**This run executed no coding model.** Every attempt ended in `agent_unavailable` because neither `OPENAI_API_KEY` nor `AI_GATEWAY_API_KEY` was set. Every number below is a blocked run, not a measured result. Do not read any token figure, fix count, or delta here as evidence about ContextLens.

**Live Pruning**: coding-model input unchanged; injected tool output unchanged; agent turns unchanged; tool calls unchanged. Jev used 0 input / 0 output tokens over 0 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens.

**Compaction Only**: coding-model input unchanged; injected tool output unchanged; agent turns unchanged; tool calls unchanged. Jev used 0 input / 0 output tokens over 0 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens. Compaction fired on 0 of 10 attempt(s) across 0 event(s), removing 0 transcript tokens.

**Full ContextLens**: coding-model input unchanged; injected tool output unchanged; agent turns unchanged; tool calls unchanged. Jev used 0 input / 0 output tokens over 0 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens. Compaction fired on 0 of 10 attempt(s) across 0 event(s), removing 0 transcript tokens.

The harness itself completed: all ten repositories were fetched at their pinned commits, all ten hidden graders calibrated (each fails before the fix and passes with the gold patch), all forty attempts ran, and all forty patches were graded. Only the coding-model call failed, because no API key was available in the environment this was run in. Re-run with OPENAI_API_KEY and AI_GATEWAY_API_KEY set to obtain measured numbers.

1 trial(s) over 10 task(s) is not a statistical quality claim.
