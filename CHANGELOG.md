# Changelog

## Unreleased

- Added complete agent-run and step records alongside structured context JSONL.
- Added content hashing, expanded coding-agent source kinds, and built-in secret
  redaction.
- Added normalized project-scoped SQLite storage and an initial migration.
- Added explicit remove, summarize, lazy-load, and scope mutations.
- Added deterministic budgeted paired planning and validated replay lifecycle
  transitions.
- Added coding-task evaluation dimensions, effect evidence labels, policy
  export/reapplication, richer HTML context breakdowns, and an end-to-end
  fixture repository.
- Made real-repository evaluation use three trials by default and report
  injected context separately from provider input usage.
- Added a post-control deployment gate that refuses to export pruning after any
  observed final regression.
- Fixed matched-random controls so they match the target token total without
  overshooting or duplicating the ContextLens candidate subset.
- Made fail-closed deployable injected-context reduction the headline metric;
  provider input tokens remain a secondary diagnostic.

All notable project changes will be documented here.

The format follows Keep a Changelog principles, and releases use semantic
versioning.

## [Unreleased]

## [0.1.0] - 2026-07-27

### Added

- Versioned JSONL context traces and content-addressed artifacts.
- Deterministic one-run utilization profiler.
- Isolated parallel replay workers and subprocess agent contract.
- Adaptive group ablation with budget-aware stopping.
- Built-in evaluators, paired bootstrap analysis, and cost accounting.
- Context optimization with combined verification and learned predictions.
- Terminal, JSON, CSV, and self-contained HTML reports.
- Real-agent keep/remove/investigate decisions and workload savings projections.
- `record`, `scan`, `analyze`, `optimize`, and `report` CLI workflows.
- Deterministic adaptive-versus-exhaustive benchmark.
