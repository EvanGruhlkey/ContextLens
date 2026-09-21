# Changelog

## Unreleased

Rewrote ContextLens around two layers and deleted everything else.

### Added

- **Live tool-output pruning** (`contextlens.filtering`): a configurable
  minimum-size gate, line chunking capped at 200 chunks, deterministic
  protection of diagnostics and results and their neighbours, batched Jev
  KEEP/DROP over the remaining chunks, a state split into bounded groups so
  every chunk is scored with the same task in view, an uncertainty safeguard
  that keeps anything not confidently disposable, and receipts for everything
  omitted.
- **Transcript compaction** (`contextlens.compaction`): tool calls paired with
  their results, compact descriptors instead of payloads, two Jev questions per
  interaction, and a deterministic KEEP / TRUNCATE / DROP table. The original
  task, recent messages, host-pinned content, every edit, and recent failures
  are protected. A pass that would not produce a meaningful reduction keeps the
  original transcript. Modelled on `tamaratran/fast-jev-compaction`.
- A `prune` / `compact` / `recover` / `mcp` CLI and a two-tool MCP server
  (`context_prune`, `context_recover`).
- A rebuilt paired coding-agent benchmark with three conditions -- baseline,
  live pruning, live pruning plus compaction -- over the same ten frozen real
  GitHub issues, and an offline harness check that needs no credentials and
  makes no network request.

### Changed

- The package has no runtime dependencies. `tiktoken`, `tree-sitter`, `torch`,
  `transformers`, and `swe-pruner` are gone.
- `src/contextlens/` is seven modules instead of eighty-two files.
- The Jev client validates every response strictly and raises on anything
  unexpected, so both layers fail open.

### Removed

- Structural Python AST expansion from the default path, after it measured
  1,105,615 coding-model input tokens against 379,716 for Jev filtering alone
  and 583,459 for baseline. It is retired to
  `experiments/structural_expansion/` with the measurement that retired it.
- The `context_next` action controller, bounded next-action routing, controller
  sessions, and Jev-chosen capabilities.
- The repository evidence index, adaptive search, AST-based expansion
  infrastructure, and evidence-selection abstractions.
- SWE-Pruner neural line scoring and its model server.
- The observation working set with mandatory Jev garbage collection.
- The trace format, replay workers, SQLite storage, telemetry, reports,
  profiler, context-optimization solver, regression CLI, and GitHub Action.
- Benchmark and eval infrastructure for all of the above.

Measured results and design notes for the removed architectures are preserved
under `docs/history/`; no negative result was deleted.

## Earlier history

Entries for the architectures above are in the Git history of this file.
