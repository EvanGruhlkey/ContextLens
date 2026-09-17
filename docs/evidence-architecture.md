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

## Research reassessment after the expanded evaluation

The stopped expanded evaluation recorded 85 finished attempts. It found higher
gross provider token usage for the tested eager ContextLens workflows than for
normal tools. This does not isolate the cause of individual incorrect patches,
or establish proportional dollar costs. See
[the collected results](comprehensive-benchmark.md).

Three independent reviews found that the current implementation is a foundation,
not the complete proposed research system. `PruningSession.observe` exposes an
internal transformation boundary, but the native CLI adapter does not call it
before tool observations enter the solver's history. Capturing CLI events after
execution supports auditing, not interception. MCP controls its own responses;
it cannot automatically replace native shell output or remove hosted history.

The proposed next integration has two explicit modes:

- **Lean MCP tools:** optional discovery and direct exact reads, compact handles,
  internal freshness checks, and budgets on complete solver-facing responses.
  It should avoid eager seeds and required duplicate reads. Savings remain an
  empirical outcome; native tools remain outside its control.
- **A controlled agent scaffold:** store raw observations outside model history,
  transform them before appending, and manage versioned visible-context coverage.
  This is the boundary needed for observation replacement and actual history
  masking. It is not implemented by the current CLI subprocess adapter.

Selection should preserve evidence/support groups within the rendered budget
rather than dropping spans after alphabetical sorting. Method-level source units
and explicit unresolved-support recovery address large-class granularity without
claiming semantic completeness. Deduplication must account for compaction and
explicit rereads; a stored receipt does not prove its text remains visible.

FastContext was withdrawn for stated product IP reasons; its old artifact links
are not an available integration dependency. CoACT has a public trained checkpoint
that could be evaluated before custom training, but compatibility and end-to-end
quality remain unverified here. The
[updated research study](research-context-efficiency-2026-09.md#second-pass-study-why-the-current-workflow-misses-the-mechanism)
records primary sources, implementation gaps and the revised sequence. These
paragraphs describe planned corrections, not newly implemented capabilities.
