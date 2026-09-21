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

1. Run the paired benchmark live and publish the measured three-condition
   numbers. Until that happens the README reports the September 2026 run that
   measured the previous architecture.
2. Tune the live-pruning threshold and chunk size against measured trajectories
   rather than intuition.
3. Decide whether compaction should run on a token trigger, a turn cadence, or
   both, from measurements.
4. Report recovery rate as a quality signal: frequent recovery means pruning is
   too aggressive.

## Explicitly out of scope

- Choosing the agent's next action, or any form of agent control.
- LLM summarization of history.
- Structural AST expansion in the default path. It measured badly; see
  [`experiments/structural_expansion/`](experiments/structural_expansion/).
