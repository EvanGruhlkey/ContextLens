# Production savings

ContextLens turns verified context effects into operational decisions for a real
agent workload.

```text
Context source       Tokens   Effect   Action        30-day token saving
AGENTS.md              4,820    +14%    Keep                           0
Git history            7,430     +2%    Investigate                    0
Unused MCP schemas    19,410     -8%    Remove              1,941,000,000
Terminal output       11,220     -3%    Remove              1,122,000,000
Architecture notes     2,180    +21%    Keep                           0
```

The example assumes 100,000 production runs during the projection window.
Actual reports use the workload and paired measurements supplied by the user.

## Decision rules

- `keep`: removing the context caused a verified quality loss.
- `remove`: removal improved quality or remained inside the configured
  equivalence tolerance.
- `investigate`: evidence is uncertain or came from a screening model.

Only `remove` recommendations produce projected savings. ContextLens does not
claim hypothetical savings for context that should remain deployed.

## Projection

```bash
contextlens analyze measurements.json \
  --baseline baseline \
  --ablated without-unused-mcp \
  --label "Unused MCP schemas" \
  --runs-per-day 10000 \
  --projection-days 30 \
  --experiment-cost-usd 25
```

The result includes:

- Input and output tokens saved per production run.
- Gross and net projected dollar savings.
- Latency saved.
- Experiment cost.
- Break-even production runs.
- Expected quality change from removal and its interval.

Net savings are:

```text
(verified cost saved per run × projected production runs)
− experiment cost
```

## Real-agent requirement

A production projection is credible only when:

1. Tasks used in the evaluation represent the real workload.
2. Baseline and ablated trials use the target model and settings.
3. Token and cost measurements come from the agent/provider integration.
4. The evaluator measures the outcome the team actually values.
5. The combined optimized configuration is tested, not just each removal
   separately.

Individual source projections must not be added together unless their combined
configuration has been verified. To project deployable portfolio savings, run
paired analysis with the full baseline and the verified combined candidate as
the ablated variant.

