# Paired coding-agent benchmark — 2026-09-21T04:29:34.985539+00:00

Model `scripted-solver`, reasoning `none`, 8 turns, 600s timeout, 1 trial(s) over 1 frozen task(s). Implementation `n/a`.

Jev tokens are counted separately and are never part of coding-model input. The agent is not told that ContextLens exists.

| Condition | Verified Fixes | Coding Input | Uncached Input | Cached Input | Output | Total Coding | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Compactions | Recoveries | Recovered Tokens | Jev Input | Jev Output | Jev Cost | Median Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 1 | 0 | 0 | 0 | 0 | 0 | 6,587 | 6,587 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0.0 |
| Live Pruning | 1 | 0 | 0 | 0 | 0 | 0 | 6,587 | 477 | 6,110 | 7 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 0.0 |
| Live + Compaction | 1 | 0 | 0 | 0 | 0 | 0 | 6,587 | 477 | 6,110 | 7 | 1 | 0 | 0 | 0 | 0 | 0.000000 | 0.0 |

| Metric vs Baseline | Live Pruning | Live + Compaction |
| --- | ---: | ---: |
| Coding-model input tokens | +0 | +0 |
| Uncached coding-model input | +0 | +0 |
| Injected tool output | -6,110 (-92.8%) | -6,110 (-92.8%) |
| Total coding tokens | +0 | +0 |
| Agent turns | +0 (+0.0%) | +0 (+0.0%) |
| Tool calls | +0 (+0.0%) | +0 (+0.0%) |
| Verified fixes | +0 | +0 |

| Task | Baseline Fix | Live Pruning Fix | Live + Compaction Fix | Baseline Input | Live Pruning Input | Live + Compaction Input |
| --- | --- | --- | --- | ---: | ---: | ---: |
| offline-parse-amount | ✅ | ✅ | ✅ | 0 | 0 | 0 |

Verified fixes: Baseline 1/1, Live Pruning 1/1, Live + Compaction 1/1.

**Live Pruning**: coding-model input unchanged; injected tool output down 6,110 (-92.8%); agent turns unchanged. Jev used 0 input / 0 output tokens over 2 requests, cost 0.000000. 0 recovery calls restored 0 tokens. No task that baseline fixed regressed.

**Live + Compaction**: coding-model input unchanged; injected tool output down 6,110 (-92.8%); agent turns unchanged. Jev used 0 input / 0 output tokens over 3 requests, cost 0.000000. 0 recovery calls restored 0 tokens. No task that baseline fixed regressed.

No coding model and no Jev gateway. A scripted solver replays one fixed tool sequence and a local heuristic answers the relevance questions. Coding-model token columns are zero because no coding model ran. This measures the ContextLens layers and the report, not task success or frontier-model savings.

1 trial(s) over 1 task(s) is not a statistical quality claim.
