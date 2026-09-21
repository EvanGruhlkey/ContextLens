| Condition   | Verified Fixes | Coding Input Tokens | Uncached Input | Cached Input | Output Tokens | Total Coding Tokens | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Recoveries | Jev Input |
| ----------- | -------------: | ------------------: | -------------: | -----------: | ------------: | ------------------: | --------------: | -------------------: | -------------: | ----------: | ---------: | --------: |
| Baseline    | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Jev Filter  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ContextLens | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

| Metric                      | Jev Filter vs Baseline | ContextLens vs Baseline |
| --------------------------- | ---------------------: | ----------------------: |
| Coding-model input tokens   | +0 | +0 |
| Uncached coding-model input | +0 | +0 |
| Injected tool output        | +0 | +0 |
| Agent turns                 | +0 | +0 |
| Verified fixes              | +0 | +0 |

| Task      | Baseline Pass | ContextLens Pass | Baseline Input | ContextLens Input | Tokens Saved | Input Change |
| --------- | ------------- | ---------------- | -------------: | ----------------: | -----------: | -----------: |
| aws-powertools-eventbridge-replay | ❌ | ❌ | 0 | 0 | 0 | n/a |
| aws-powertools-query-merge | ❌ | ❌ | 0 | 0 | 0 | n/a |
| click-empty-default | ❌ | ❌ | 0 | 0 | 0 | n/a |
| click-short-help | ❌ | ❌ | 0 | 0 | 0 | n/a |
| pallets-flask-trusted-hosts | ❌ | ❌ | 0 | 0 | 0 | n/a |
| python-babel-parse-time | ❌ | ❌ | 0 | 0 | 0 | n/a |
| responses-blank-query | ❌ | ❌ | 0 | 0 | 0 | n/a |
| responses-query-mutation | ❌ | ❌ | 0 | 0 | 0 | n/a |
| spotify-luigi-bool-default | ❌ | ❌ | 0 | 0 | 0 | n/a |
| spotify-luigi-run-arguments | ❌ | ❌ | 0 | 0 | 0 | n/a |

This run did not execute a coding model. Every paired attempt ended with `agent_unavailable` (no OPENAI_API_KEY or AI_GATEWAY_API_KEY). The zeros below are that blocked run, not a measured ContextLens saving or quality result.

Verified fixes: Baseline 0/10, Jev Filter 0/10, ContextLens 0/10.

ContextLens saved 0 coding-model input tokens.
Uncached coding-model input changed by 0.
Tool-output tokens prevented from entering model context: 0 (injected 0 vs baseline 0).
Agent turns did not change.
Recoveries: 0 calls restoring 0 tokens.
Jev usage is separate from the coding model: 0 input / 0 output tokens, cost 0.000000.
Do not treat these zeros as evidence that ContextLens preserved fixes or reduced frontier-model context.
