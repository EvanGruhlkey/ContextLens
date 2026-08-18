# Agent economics and savings claims

ContextLens distinguishes an initial-context footprint reduction from an
end-to-end agent savings result.

An agent with less preloaded context can search more files, take more turns,
lose prompt-cache hits, produce more output or reasoning tokens, or trigger
compaction differently. Therefore:

```text
injected-context reduction != total agent efficiency
```

## Categories

Where available, verification reports:

- initial injected context;
- total provider input;
- cached input;
- uncached input;
- cache-write input;
- visible output;
- reasoning tokens;
- turns, tool calls, searches, and files read;
- total, model, and tool latency;
- explicit provider-reported or snapshot-calculated dollar cost.

Missing provider data remains missing. ContextLens does not treat all token
categories as equally priced and does not substitute initial-context estimates
for provider usage.

## Decision rules

- A candidate with an observed quality regression is rejected regardless of
  footprint savings.
- A smaller context that increases provider or uncached input without a
  measured quality gain is an economics regression.
- A dollar claim requires a complete, explicit, dated pricing snapshot or
  provider-reported cost.
- A combined configuration must be verified; individual mutation projections
  cannot simply be added.
- A static finding is never a production-savings claim.

## Legacy projection API

`contextlens analyze` and `SavingsAnalyzer` remain available for callers with
paired measurements and a declared workload. Their projections are credible
only when measurements represent the target model, real workload, actual
provider usage/cost, mechanical outcome, and combined deployed configuration.

Use `contextlens verify` for new repository-context comparisons because it
makes cache and end-to-end usage visible alongside footprint.
