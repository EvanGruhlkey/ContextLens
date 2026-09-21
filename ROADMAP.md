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

1. Measure transcript compaction. It has never fired in a real run: the longest
   transcript on the benchmark reached 14,178 tokens against its 20,000-token
   trigger. It needs sessions long enough to reach that, or a trigger chosen from
   evidence rather than intuition.
2. Keep a mechanically inert control condition in every run. On the 21 September
   run `compaction_only` did nothing and still showed -22.7% total coding-model
   input, which is the only honest way to see this suite's noise floor.
3. Cut live pruning's latency. It cost 20.0% more wall clock because every large
   observation waits on Jev.
4. Reduce the share of observations that fall below the size gate. 103 of 204
   were ineligible; smaller eligible outputs may still be worth chunking.
5. Report recovery rate as a quality signal: frequent recovery means pruning is
   too aggressive. It was zero across every measured run so far.

## Explicitly out of scope

- Choosing the agent's next action, or any form of agent control.
- LLM summarization of history.
- Structural AST expansion in the default path. It measured badly; see
  [`experiments/structural_expansion/`](experiments/structural_expansion/).
