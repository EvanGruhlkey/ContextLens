# Implemented evidence architecture

The default production path is local Git discovery, content-hash parsing cache,
BM25-style ranking, conservative dependency expansion, and complete source units
under explicit budgets. The dependency policy reserves supporting definitions
before spending tokens on additional lexical matches. Receipt recovery supplies
omitted source on demand, while hash verification distinguishes snapshots from
current files. JSON envelopes have a separate optional response budget.

The root-confined stdio MCP boundary exposes seven agent tools and records local
request/response token counts and latency. Optional neural observation pruning is
kept behind this boundary and fails open through the existing pruner. Explicit
checkpoint namespaces prevent cached semantic scores being silently reused across
model versions. The default pipeline does not load a neural model or need a GPU.

External memory preserves original observations as handles, offers a bounded view
with pinned/recent priority, and supports exact expansion. The agent must own and
apply its conversation compaction; MCP cannot delete hosted conversation messages.
Built-in shell reads also remain available, so installing the server alone does
not guarantee savings. The live benchmark measures the resulting complete agent
usage, including rereads and recovery, rather than counting selected source alone.

## Review findings and practical limits

Complete AST units avoid misleading pass stubs, but large classes can exceed the
source budget and require range expansion. Dependency resolution is static and
conservative: aliases, relative imports and selected module attributes work;
dynamic dispatch, import side effects, ambiguous bindings and re-export chains
can require additional reads. The output always disclaims semantic completeness.
JS/TS grammars are optional, with explicit complete-file fallback. Invalid Python,
large files and parser/read failures are reported instead of silently treated as
irrelevant. Ignored untracked files are excluded from discovery.

Hashes invalidate changed parses and protect current-source verification. Receipts
validate recovered content and use atomic unique temporary writes. The local MCP
service never edits repository source or executes verification commands; the
agent and external evaluation harness perform those operations independently.
A verification-to-edit race still requires the editing client to check versions
at the write boundary; this service cannot enforce atomic edits made by a shell.

## Evaluation gate

The real-task pilot uses pinned historical repository commits and fresh checkouts,
randomized condition order, the same model and discovery index, and mechanical
verification hidden from the agent. Both policies must call live verification
and source-read tools. Full-file retrieval retains up to three ranked files under
a 30,000-source-token budget; dependency retrieval uses 3,000. The comparison
therefore measures these policies together with their chosen budgets.

Provider input/output usage is the end-to-end token measure. Cached input is
reported separately and dollars remain unknown. Infra failures, missing usage,
or observed paired quality regressions prevent a favorable policy decision.
Repeated trials of three tasks are a pilot, not a proof of quality preservation.
The separate CPU lexical ablation measures retrieval overhead and serialization,
not task completion. No new custom CoACT/LaMR model has been trained or claimed.

The completed pilot observed 8/9 dependency successes versus 9/9 full-file, so
full-file is now the production default. Dependency and lexical policies require
explicit opt-in. See [the measured results](evidence-benchmark.md).
