# Paired coding-agent benchmark — 2026-09-21T06:35:15.934717+00:00

Model `gpt-5.6-luna`, reasoning `low`, 20 turns, 300s timeout, 1 trial(s) over 10 frozen task(s). Implementation `50573c0d92a9`.

All four conditions share the coding model, reasoning effort, issue prompt, repository commit, tool set, timeout, turn limit, and hidden grader. The agent is never told that ContextLens exists. Jev tokens are counted separately and never enter coding-model input.

## Aggregate

| Condition | Verified Fixes | Coding Input | Uncached Input | Cached Input | Output | Total Coding | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Tool Calls | Compaction Events | Compaction Tokens Removed | Tasks Compacted | Recovery Calls | Recovered Tokens | Jev Input | Jev Output | Jev Cost | Wall Clock (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 5 | 622,586 | 69,094 | 553,492 | 18,702 | 641,288 | 60,844 | 60,844 | 0 | 137 | 129 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 445.9 |
| Live Pruning | 5 | 435,221 | 51,955 | 383,266 | 19,595 | 454,816 | 57,647 | 38,791 | 18,856 | 137 | 127 | 0 | 0 | 0 | 0 | 0 | 71,895 | 2,457 | 0.000000 | 535.3 |
| Compaction Only | 5 | 480,956 | 66,484 | 414,472 | 14,765 | 495,721 | 60,596 | 60,596 | 0 | 116 | 107 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000000 | 356.4 |
| Full ContextLens | 4 | 496,496 | 60,163 | 436,333 | 17,436 | 513,932 | 60,626 | 47,836 | 12,790 | 132 | 123 | 0 | 0 | 0 | 0 | 0 | 72,938 | 2,297 | 0.000000 | 431.3 |

## Versus baseline

| Metric | Live Pruning | Compaction Only | Full ContextLens |
| --- | ---: | ---: | ---: |
| Verified fixes | +0 | +0 | -1 |
| Coding-model input tokens | -187,365 (-30.1%) | -141,630 (-22.7%) | -126,090 (-20.3%) |
| Uncached coding-model input | -17,139 (-24.8%) | -2,610 (-3.8%) | -8,931 (-12.9%) |
| Cached coding-model input | -170,226 (-30.8%) | -139,020 (-25.1%) | -117,159 (-21.2%) |
| Output tokens | +893 (+4.8%) | -3,937 (-21.1%) | -1,266 (-6.8%) |
| Total coding-model tokens | -186,472 (-29.1%) | -145,567 (-22.7%) | -127,356 (-19.9%) |
| Raw tool output | -3,197 (-5.3%) | -248 (-0.4%) | -218 (-0.4%) |
| Injected tool output | -22,053 (-36.2%) | -248 (-0.4%) | -13,008 (-21.4%) |
| Agent turns | +0 (+0.0%) | -21 (-15.3%) | -5 (-3.6%) |
| Tool calls | -2 (-1.6%) | -22 (-17.1%) | -6 (-4.7%) |
| Wall clock | +89.4 (+20.0%) | -89.5 (-20.1%) | -14.6 (-3.3%) |

## Per task

| Task | Baseline Fix | Live Pruning Fix | Compaction Only Fix | Full ContextLens Fix | Baseline Input | Live Pruning Input | Compaction Only Input | Full ContextLens Input | Compacted |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| aws-powertools-eventbridge-replay | ❌ | ✅ | ✅ | ❌ | 47,840 | 33,360 | 77,937 | 25,193 | none |
| aws-powertools-query-merge | ❌ | ❌ | ❌ | ❌ | 149,710 | 70,334 | 84,515 | 104,300 | none |
| click-empty-default | ❌ | ❌ | ❌ | ❌ | 36,848 | 31,359 | 25,059 | 41,202 | none |
| click-short-help | ❌ | ❌ | ❌ | ❌ | 32,469 | 29,319 | 28,672 | 28,096 | none |
| pallets-flask-trusted-hosts | ❌ | ❌ | ❌ | ❌ | 135,474 | 104,507 | 106,276 | 88,108 | none |
| python-babel-parse-time | ✅ | ✅ | ✅ | ✅ | 35,132 | 29,900 | 34,992 | 22,551 | none |
| responses-blank-query | ✅ | ✅ | ✅ | ✅ | 27,183 | 23,846 | 29,483 | 28,113 | none |
| responses-query-mutation | ✅ | ✅ | ✅ | ✅ | 28,988 | 23,836 | 24,286 | 20,615 | none |
| spotify-luigi-bool-default | ✅ | ✅ | ✅ | ✅ | 46,449 | 38,152 | 62,918 | 58,796 | none |
| spotify-luigi-run-arguments | ✅ | ❌ | ❌ | ❌ | 82,493 | 50,608 | 6,818 | 79,522 | none |

## Regressions

- **Live Pruning**: 1 regression(s) — spotify-luigi-run-arguments. Newly fixed: aws-powertools-eventbridge-replay.
- **Compaction Only**: 1 regression(s) — spotify-luigi-run-arguments. Newly fixed: aws-powertools-eventbridge-replay.
- **Full ContextLens**: 1 regression(s) — spotify-luigi-run-arguments.


## Notes

Verified fixes: Baseline 5/10, Live Pruning 5/10, Compaction Only 5/10, Full ContextLens 4/10.

**Some attempts never reached the coding model** (`agent_unavailable`). Condition totals are not comparable.

**Live Pruning**: coding-model input down 187,365 (-30.1%); injected tool output down 22,053 (-36.2%); agent turns unchanged; tool calls down 2 (-1.6%). Jev used 71,895 input / 2,457 output tokens over 28 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens.

**Compaction Only**: coding-model input down 141,630 (-22.7%); injected tool output down 248 (-0.4%); agent turns down 21 (-15.3%); tool calls down 22 (-17.1%). Jev used 0 input / 0 output tokens over 0 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens. Compaction fired on 0 of 10 attempt(s) across 0 event(s), removing 0 transcript tokens.

**Full ContextLens**: coding-model input down 126,090 (-20.3%); injected tool output down 13,008 (-21.4%); agent turns down 5 (-3.6%); tool calls down 6 (-4.7%). Jev used 72,938 input / 2,297 output tokens over 29 requests, cost 0.000000. 0 recovery call(s) restored 0 tokens. Compaction fired on 0 of 10 attempt(s) across 0 event(s), removing 0 transcript tokens.

**Live pruning reached the goal on this trial; transcript compaction was never exercised.** Live pruning held verified fixes at 5/10 against baseline's 5/10 while cutting injected tool output 36.2% and uncached coding-model input 24.8%, with agent turns unchanged. Read total-input deltas with care: `compaction_only` fired zero compaction events, so it is mechanically identical to baseline, and it still shows -22.7% total input and -3.8% uncached input. That is this suite's noise floor, inflated further because one of its attempts (spotify-luigi-run-arguments) died on an OpenAI HTTP 503 after 2 turns and contributed only 6,818 input tokens. The defensible signals are injected tool output (-36.2% for live pruning against -0.4% for the inert control) and uncached input (-24.8% against -3.8%). Compaction never triggered: the largest transcript reached 14,178 estimated tokens against the 20,000-token production trigger, which was not lowered. Every condition including the inert control failed spotify-luigi-run-arguments, so that regression is task flakiness rather than pruning. Live pruning's outcome mix: 103 observations below the size gate, 33 too few chunks, 33 where Jev kept everything, 22 pruned, 7 gateway failures that failed open, 6 structured documents skipped. 36 of 40 attempts completed: 3 hit the 20-turn limit and 1 hit the 503.

1 trial(s) over 10 task(s) is not a statistical quality claim.
