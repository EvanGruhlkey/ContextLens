# Architecture

ContextLens sits between a coding agent's tools and the coding model. It
reduces what the model has to read, and it does so without taking part in the
agent's reasoning.

```text
Live pruning:         tool output -> Jev KEEP/DROP -> model
Transcript compaction: old transcript -> Jev KEEP/TRUNCATE/DROP -> smaller transcript
```

## Boundary

Jev answers bounded relevance questions. It never chooses the agent's next
action, writes code or commands, executes tools, plans, summarizes, or manages
the agent loop. Neither layer spends a frontier-model turn on filtering: live
pruning runs on the tool-response path, and compaction rewrites a transcript the
host already owns.

Every failure path returns the original text. A missing key, a gateway error,
a malformed answer, a missing answer, a state that will not fit, or a reduction
too small to be worth it all mean "unchanged".

Nothing is rewritten or synthesized. Content is kept verbatim, truncated to a
bounded prefix plus metadata, or removed -- and what is removed stays exactly
recoverable through a receipt handle.

## Modules

| File | Responsibility |
| --- | --- |
| [`models.py`](../src/contextlens/models.py) | `Message`, `ToolUse`, `ToolResult`, `LineRange`, `OutputCategory`, token estimates |
| [`jev.py`](../src/contextlens/jev.py) | The gateway, strict response validation, question construction, request batching, usage accounting |
| [`receipts.py`](../src/contextlens/receipts.py) | Content-addressed store for originals; exact text or line-range recovery |
| [`filtering.py`](../src/contextlens/filtering.py) | Live pruning of one tool result |
| [`compaction.py`](../src/contextlens/compaction.py) | Relevance-based garbage collection over a transcript |
| [`cli.py`](../src/contextlens/cli.py) | `prune`, `compact`, `recover`, `mcp` |
| [`mcp.py`](../src/contextlens/mcp.py) | `context_prune` and `context_recover` over stdio |

`filtering.py` and `compaction.py` both depend on `models.py`, `jev.py`, and
`receipts.py`, and on nothing else. They do not depend on each other. A host can
use either alone.

## Live pruning

`OutputPruner.prune` takes one `PruneRequest` (task, raw output, tool name and
arguments, optional focus) and returns a `PruneOutcome`.

1. **Record.** The original is written to a receipt before anything else, so it
   is recoverable even when pruning is skipped.
2. **Gate.** Output below `minimum_tokens` passes through. So does binary
   output, valid JSON, and unified diffs: cutting a hole in a machine-readable
   document leaves something that still looks complete but is not.
3. **Chunk.** Lines are grouped into chunks of `chunk_lines`, capped at 200
   chunks, with lines over 2,000 characters split first. Fewer than three
   chunks means there is nothing worth deciding.
4. **Protect deterministically.** The first and last chunks always survive, as
   does any chunk holding a recognized diagnostic or result -- errors,
   warnings, tracebacks, test totals, exit status, artifact paths -- or sitting
   next to one.
5. **Ask.** One boolean question per remaining chunk: does any line in it still
   matter for the task? The state carries the task, the focus, the tool call, a
   category and its guidance, the distinct diagnostic lines from the whole
   output, and the chunk texts. When the output is too large for one state it is
   split into several states, each repeating the same context, so every decision
   is made with the same task in view.
6. **Keep on doubt.** A chunk stays when its probability reaches
   `keep_threshold`, when it exceeds `uncertain_keep_probability` (0.1), or when
   it was never scored. Removal requires confidence, not the absence of it.
7. **Render.** Kept chunks are joined verbatim. Each run of dropped chunks
   becomes one marker naming the omitted line range and the receipt handle. If
   the result is not smaller, the original is returned.

## Transcript compaction

`compact_transcript` takes the transcript and returns a `CompactionResult`.
Its design follows [`fast-jev-compaction`](https://github.com/tamaratran/fast-jev-compaction).

1. **Trigger.** Below `trigger_tokens` nothing happens.
2. **Pair.** Every `tool_use` is matched with its `tool_result` by
   `tool_use_id`. A call without a result is not a candidate.
3. **Protect.** Never touched: the first message (the original task), the newest
   `preserve_recent_messages` messages, messages the host pinned (explicit user
   constraints, recovered content), every edit -- a patch cannot be regenerated
   from the transcript -- and failures inside twice the recent window.
4. **Describe.** Jev sees a compact descriptor per interaction, never the result
   itself: tool name, truncated arguments, success or failure, output size in
   characters and lines, a short preview, and how many messages ago it
   happened. The state holds the whole conversation in that form and shrinks in
   stages until it fits `max_state_tokens`: arguments truncated to 600, then
   200, then 60 characters; long message text abridged oldest-first; old
   non-protected text collapsed to a note; old call-less messages left out.
5. **Ask two questions per interaction.** Does the call still matter? Does its
   full result still need to stay verbatim?
6. **Decide deterministically.**

   | Condition | Action |
   | --- | --- |
   | pinned | `KEEP` |
   | `keep_result >= keep_threshold` | `KEEP` the call and the full result |
   | else `keep_call >= keep_threshold` | `TRUNCATE` to a bounded prefix plus a recovery note |
   | else | `DROP` the call together with its result |

7. **Rebuild.** A dropped call disappears with its result, so no result is ever
   orphaned. Untouched messages are returned as the same objects. Before a
   result is truncated its full text goes to a receipt, and the truncation
   marker names the handle.
8. **Bail out.** If the pass removes less than `minimum_reduction` of the
   transcript's tokens, the original transcript is kept.

## What was removed

The default path used to include deterministic Python AST expansion around
whatever Jev kept. It measured badly and is now an experiment; see
[`experiments/structural_expansion/`](../experiments/structural_expansion/).
An action controller, bounded next-action routing, controller sessions, a
repository evidence index, SWE-Pruner neural scoring, and a mandatory
observation working set were removed outright. Their measurements are in
[`history/`](history/).
