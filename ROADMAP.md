# ContextLens roadmap

The goal is fewer coding-model input tokens per verified fix, with no extra
agent turns and no drop in task success.

## Product rules

- Reduce context on the tool-response path and on the host's own transcript.
  Never spend a frontier-model turn deciding what to keep.
- Use Jev only for closed-form relevance questions over content the agent
  already discovered. Jev does not choose actions, write code, run tools, plan,
  or summarize.
- Never rewrite or synthesize. Keep verbatim, truncate to a bounded prefix, or
  remove.
- Keep every omission exactly recoverable behind a stable handle.
- Fail open when scoring, validation, or fitting is uncertain.
- Measure complete task trajectories. Never mix Jev tokens into the
  coding-model input metric.

## Shipped

- Live tool-output pruning: chunking, deterministic protection of diagnostics
  and results, batched Jev KEEP/DROP, bounded state, receipts for everything
  omitted.
- Transcript compaction: descriptor-based KEEP/TRUNCATE/DROP over old tool
  interactions, with the task, recent messages, edits, recent failures, and
  pinned content protected.
- A `prune` / `compact` / `recover` CLI and a two-tool MCP server.
- A paired coding-agent benchmark with baseline, live pruning, and live pruning
  plus compaction over ten frozen real GitHub issues, and an offline harness
  check that needs no credentials.

## Next

1. Benchmark on a workload that actually produces large tool outputs and long
   sessions. The 21 September four-condition run showed both layers idle on
   ten single-file Python bug fixes: compaction never reached its 20,000-token
   trigger (largest transcript 14,022 tokens) and live pruning found only 2.7%
   of raw tool output above its 1,500-token gate.
2. Establish the noise floor before claiming anything. In that run the
   `compaction_only` condition was mechanically identical to baseline and still
   differed by +2 fixes and +3.0% coding-model input.
3. Report threshold sensitivity as its own labelled experiment. Do not tune
   `minimum_tokens` or `trigger_tokens` inside the headline benchmark.
4. Decide whether compaction should run on a token trigger, a turn cadence, or
   both, from measurements.
5. Report recovery rate as a quality signal: frequent recovery means pruning is
   too aggressive. It was zero in the measured run.

## Explicitly out of scope

- Choosing the agent's next action, or any form of agent control.
- LLM summarization of history.
- Structural AST expansion in the default path. It measured badly; see
  [`experiments/structural_expansion/`](experiments/structural_expansion/).
