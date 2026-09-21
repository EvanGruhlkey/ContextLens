# Paired coding-agent benchmark — 2026-09-21T05:31:00.762951+00:00

Model `gpt-5.6-luna`, reasoning `low`, 20 turns, 300s timeout, 1 trial(s) over 10 frozen task(s). Implementation `c70cf0498aa1`.

All four conditions share the coding model, reasoning effort, issue prompt, repository commit, tool set, timeout, turn limit, and hidden grader. The agent is never told that ContextLens exists. Jev tokens are counted separately and never enter coding-model input.

## Aggregate

| Condition | Verified Fixes | Coding Input | Uncached Input | Cached Input | Output | Total Coding | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Tool Calls | Compaction Events | Compaction Tokens Removed | Tasks Compacted | Recovery Calls | Recovered Tokens | Jev Input | Jev Output | Jev Cost | Wall Clock (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 5 | 463,023 | 55,593 | 407,430 | 17,974 | 480,997 | 44,533 | 44,533 | 0 | 135 | 126 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 403.3 |
| Live Pruning | 5 | 447,362 | 61,704 | 385,658 | 16,283 | 463,645 | 55,279 | 53,798 | 1,481 | 120 | 110 | 0 | 0 | 0 | 0 | 0 | 22,079 | 799 | 0.000000 | 327.6 |
| Compaction Only | 7 | 476,918 | 53,818 | 423,100 | 17,177 | 494,095 | 56,801 | 56,801 | 0 | 117 | 107 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 390.1 |
| Full ContextLens | 5 | 479,354 | 61,744 | 417,610 | 19,207 | 498,561 | 53,426 | 52,743 | 683 | 127 | 117 | 0 | 0 | 0 | 0 | 0 | 19,105 | 512 | 0.000000 | 353.1 |

## Versus baseline

| Metric | Live Pruning | Compaction Only | Full ContextLens |
| --- | ---: | ---: | ---: |
| Verified fixes | +0 | +2 | +0 |
| Coding-model input tokens | -15,661 (-3.4%) | +13,895 (+3.0%) | +16,331 (+3.5%) |
| Uncached coding-model input | +6,111 (+11.0%) | -1,775 (-3.2%) | +6,151 (+11.1%) |
| Cached coding-model input | -21,772 (-5.3%) | +15,670 (+3.8%) | +10,180 (+2.5%) |
| Output tokens | -1,691 (-9.4%) | -797 (-4.4%) | +1,233 (+6.9%) |
| Total coding-model tokens | -17,352 (-3.6%) | +13,098 (+2.7%) | +17,564 (+3.7%) |
| Raw tool output | +10,746 (+24.1%) | +12,268 (+27.5%) | +8,893 (+20.0%) |
| Injected tool output | +9,265 (+20.8%) | +12,268 (+27.5%) | +8,210 (+18.4%) |
| Agent turns | -15 (-11.1%) | -18 (-13.3%) | -8 (-5.9%) |
| Tool calls | -16 (-12.7%) | -19 (-15.1%) | -9 (-7.1%) |
| Wall clock | -75.7 (-18.8%) | -13.2 (-3.3%) | -50.2 (-12.4%) |

## Per task

| Task | Baseline Fix | Live Pruning Fix | Compaction Only Fix | Full ContextLens Fix | Baseline Input | Live Pruning Input | Compaction Only Input | Full ContextLens Input | Compacted |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| aws-powertools-eventbridge-replay | ✅ | ❌ | ✅ | ❌ | 26,084 | 57,670 | 77,805 | 52,262 | none |
| aws-powertools-query-merge | ❌ | ❌ | ❌ | ❌ | 82,705 | 58,357 | 41,511 | 98,385 | none |
| click-empty-default | ❌ | ❌ | ❌ | ❌ | 40,411 | 34,185 | 40,033 | 34,293 | none |
| click-short-help | ❌ | ❌ | ❌ | ❌ | 19,518 | 31,285 | 24,670 | 35,513 | none |
| pallets-flask-trusted-hosts | ❌ | ✅ | ✅ | ✅ | 80,186 | 88,309 | 96,772 | 69,388 | none |
| python-babel-parse-time | ✅ | ✅ | ✅ | ✅ | 42,228 | 28,888 | 27,450 | 23,725 | none |
| responses-blank-query | ✅ | ✅ | ✅ | ✅ | 22,653 | 29,837 | 16,977 | 21,893 | none |
| responses-query-mutation | ✅ | ❌ | ✅ | ✅ | 19,953 | 17,913 | 28,659 | 32,162 | none |
| spotify-luigi-bool-default | ✅ | ✅ | ✅ | ✅ | 47,921 | 42,532 | 42,302 | 32,301 | none |
| spotify-luigi-run-arguments | ❌ | ✅ | ✅ | ❌ | 81,364 | 58,386 | 80,739 | 79,432 | none |

## Regressions

- **Live Pruning**: 2 regression(s) — aws-powertools-eventbridge-replay, responses-query-mutation. Newly fixed: pallets-flask-trusted-hosts, spotify-luigi-run-arguments.
- **Compaction Only**: no task that baseline fixed regressed. Newly fixed: pallets-flask-trusted-hosts, spotify-luigi-run-arguments.
- **Full ContextLens**: 1 regression(s) — aws-powertools-eventbridge-replay. Newly fixed: pallets-flask-trusted-hosts.


## Notes

Verified fixes: Baseline 5/10, Live Pruning 5/10, Compaction Only 7/10, Full ContextLens 5/10.

**Live Pruning**: coding-model input down 15,661 (-3.4%); injected tool output up 9,265 (+20.8%); agent turns down 15 (-11.1%); tool calls down 16 (-12.7%). Jev used 22,079 input / 799 output tokens over 4 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens.

**Compaction Only**: coding-model input up 13,895 (+3.0%); injected tool output up 12,268 (+27.5%); agent turns down 18 (-13.3%); tool calls down 19 (-15.1%). Jev used 0 input / 0 output tokens over 0 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens. Compaction fired on 0 of 10 attempt(s) across 0 event(s), removing 0 transcript tokens.

**Full ContextLens**: coding-model input up 16,331 (+3.5%); injected tool output up 8,210 (+18.4%); agent turns down 8 (-5.9%); tool calls down 9 (-7.1%). Jev used 19,105 input / 512 output tokens over 3 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens. Compaction fired on 0 of 10 attempt(s) across 0 event(s), removing 0 transcript tokens.

**Neither layer materially engaged, so this run does not answer the main question.** Transcript compaction never fired: the largest transcript across all forty attempts reached 14,022 estimated tokens against the 20,000-token production trigger (median about 5,600), so `compaction_only` was mechanically identical to `baseline` and its two extra fixes are trajectory noise, not compaction. Live pruning fired on three of ten tasks (four Jev requests) and removed 1,481 of 55,279 raw tool-output tokens, 2.7%, because the average tool result was about 500 tokens against a 1,500-token minimum-size gate. The threshold was not lowered to force either layer to trigger. Injected tool output rose in every candidate condition because those agents ran different, longer tool sequences, not because ContextLens added text. One baseline attempt (pallets-flask-trusted-hosts) hit the 20-turn limit, so 39 of 40 attempts completed.

1 trial(s) over 10 task(s) is not a statistical quality claim.
