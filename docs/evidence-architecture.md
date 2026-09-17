# ContextLens architecture

ContextLens selects repository evidence on demand. It preserves exact source and
recovery information locally, while returning compact text to the coding agent.
The implementation has two integration modes with different control boundaries.

## Compact repository tools

`RepositoryContext` exposes `find`, `read` and `expand`; the default MCP profile
maps these to `context_find`, `context_read` and `context_expand`.

1. Git discovery and the content-hash parsing cache feed lexical ranking.
2. Python methods/nested functions and optional JS/TS source units provide fine granularity.
3. Bounded static support closure collects referenced helpers, constants, imports and enclosing declarations. Ambiguity and exhausted limits are reported.
4. Discovery returns only locations and short handles. Provenance and exact source receipts stay on disk.
5. A read checks current source hashes and returns the complete selected evidence/support group. An oversized group is refused; an explicit range is available with a support-omission notice.
6. Expansion recovers the original snapshot, clearly labeled historical. It does not establish that current code is unchanged.

Budgets count the complete rendered response, including citations and notices.
MCP returns plain text rather than an inline JSON evidence envelope. Transport JSON
is decoded by the client. Select an explicit tokenizer encoding for exact counts;
the default estimator is approximate. Limits apply to context text, not protocol
metadata, tool schemas or the whole conversation.

Reads are root-confined and capped at 1 MiB. Handles are bound to a repository,
metadata is integrity-checked, and receipts preserve original bytes as UTF-8 text,
including line endings. Hash checks apply to the bytes returned. An editing client
must still guard against changes between reading and writing.

## Controlled solver adapter

`ContextAdapter` owns a callback solver loop. The host supplies inference and tool
executors; the adapter makes no model-service or billing calls itself.

```python
from pathlib import Path
from contextlens.context_adapter import ContextAdapter, HostTool
from contextlens.context_tools import RepositoryContext
from contextlens.pruning import ReceiptStore

repository = RepositoryContext(Path('.'), Path('.contextlens'), encoding='o200k_base')
adapter = ContextAdapter(repository, ReceiptStore(Path('.contextlens/observations')))
# Your solver accepts a sequence of Message objects and returns ToolCall or Answer.
# It may request find/read/expand; tool output is appended before its next call.
result = adapter.run(your_solver, 'Fix the refresh-token timeout')
```

Register additional executors as `HostTool(execute=callback, prune=True)` and supply
a task-matching `PruningSession` to transform their observations. Each executor
runs once. Raw output is stored before transformation; only the transformed output
enters solver history. Edits and tests can use unpruned executors. Failures produce
generic solver errors without leaking raw output. `adapter.recover(receipt_id)`
returns exact observations to the host for inspection or further processing.

The repository service tracks visible line intervals only in this owned loop,
after output is appended and acknowledged. Overlapping reads return unseen lines;
`reread=True` forces a full read. Coverage is versioned by source hash. Starting a
new run resets the visibility epoch. If the host compacts or removes history, it
must call `repository.begin_context()` before further reads. Ordinary MCP sessions
do not suppress repeat reads because they cannot establish what remains visible.

## Boundaries and compatibility

MCP cannot intercept native shell outputs or remove hosted conversation messages.
The callback adapter supplies that observation boundary only for registered tools;
it is not a native CLI interceptor. Automatic history compaction is not implemented.
Static support is conservative, not a guarantee of semantic completeness. Dynamic
imports, dispatch and parser fallbacks may require additional discovery or reads.

The earlier eager bundle workflow remains available through `retrieve` and
`mcp --profile legacy`. Neural pruning backends use the legacy MCP profile or an
explicit controlled `PruningSession`. The core compact path does not load a model.
Installation still includes the existing neural dependencies.

## Validation and research status

Local tests cover discovery, support closure, scope/shadowing, exact recovery,
stale source, root confinement, budgets, repeat reads, visibility resets and the
pre-history observation boundary. Independent correctness review found dependency
scope and partial-read disclosure issues; both were fixed with regression tests.

The [stopped 85-run evaluation](comprehensive-benchmark.md) tested the previous
eager workflow and found higher gross provider token usage than normal tools.
It does not validate the new architecture. Further live agent runs remain stopped
at the user's request; no new token-saving or task-quality claims are made.

These changes implement the integration corrections from the
[research reassessment](research-context-efficiency-2026-09.md). They do not reproduce
or train CoACT/LaMR, prove a globally optimal architecture, or depend on withdrawn
FastContext artifacts. Optional trained-model integration requires separate
compatibility and quality evaluation.
