# Migration and backwards compatibility

ContextLens 0.1 originally centered the workflow on recorded traces and global
context optimization. The repository-context regression workflow is additive.

## Retained

- Trace schema `1.0`, `TraceWriter`, `TraceReader`, artifact storage, and
  redaction.
- Profiler, replay adapters/workers, mutations, adaptive search, paired
  analysis, evaluators, optimizer, reports, SQLite storage, policy export, and
  runtime policy application.
- `record`, `analyze`, `optimize`, `report`, `policy`, and `trim` commands.
- Existing optimization JSON, measurement JSON, report JSON, and policy schema.

## Changed

- `contextlens scan` with no trace now scans the current repository by default.
- The old `contextlens scan trace.jsonl` spelling remains accepted.
- `contextlens profile trace.jsonl` is the new explicit name for the old
  one-run trace profiler.
- Product reports no longer use injected-context reduction as a synonym for
  economic savings. Initial context and provider usage are distinct fields.
- Base-versus-candidate context versions are now the primary public
  abstraction. Global optimization remains an advanced engine.

## Added

- Repository convention discovery and static findings.
- Git-aware context diffs.
- Matched `verify` task suites with cache-aware usage and behavior metrics.
- Fail-closed `minimize` patch generation.
- Static and verified CI modes plus a composite GitHub Action.
- Provider-usage normalization for common OpenAI/Anthropic/generic objects.

## Deprecated

No API or command is removed in this release. The ambiguous phrase
"production savings" is deprecated for injected-context reduction unless
end-to-end usage or explicit context-only scope is stated.

The old trace-first onboarding path is no longer recommended for teams that
only need repository context scanning or regression tests.

## Removed

No stable subsystem or persisted schema was removed.
