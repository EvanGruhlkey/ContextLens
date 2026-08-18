# Changelog

## Unreleased

- Fixed clean editable installs so the development-only `evals` harness is
  importable without shipping it in the production wheel or mutating `sys.path`.
- Added `contextlens init` with conservative ecosystem, check, context, and
  agent detection plus explicit non-runnable TODO fallbacks.
- Split repository footprint from target-effective context and added Codex,
  Claude, Copilot, and Cursor scope resolvers with accuracy labels.
- Hardened minimization with prioritized deduplicate/remove/scope proposals,
  isolated candidate screening, and a separate final combined verification.
- Added effective-context CI reporting, cross-platform argument generation,
  and robust action result output on failing gates.
- Added a pinned seven-change public repository corpus and a checked-in,
  reproducible static VS Code report without inventing agent results.
- Repositioned ContextLens as CI and regression testing for repository-owned
  agent context: "test your AGENTS.md like you test your code."
- Added credential-free repository discovery and static `scan` for AGENTS.md,
  CLAUDE.md, Copilot/Cursor rules, skills, MCP configs, and tool schemas.
- Added Git-aware `diff` that compares the worktree with an immutable base tree
  and reports footprint, duplicate, and stale-reference deltas.
- Added matched `verify` trials with mechanical evaluation, fail-closed
  PASS/WARN/CONTEXT REGRESSION/INCONCLUSIVE verdicts, and separate quality,
  economics, behavior, and latency reporting.
- Added provider-usage normalization for cached, uncached, cache-write, output,
  and reasoning categories plus explicit historical pricing snapshots.
- Added conservative `minimize`: static evidence generates in-memory
  candidates, isolated and combined verification gates them, and source files
  are never edited.
- Added static and verified `ci` modes, stable exit codes, JSON/Markdown
  artifacts, a composite GitHub Action, and path-filtered workflow examples.
- Added `profile` as the explicit trace-profiler command while preserving
  `scan trace.jsonl` compatibility.
- Added migration, CI, adapter, architecture, and deterministic local-demo
  documentation.
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
- Previously made fail-closed deployable injected-context reduction the eval
  harness headline metric; the repository-regression pivot now retains it as a
  compatibility footprint field rather than an economic-savings claim.
- Added a production `trim` command and runtime API that apply policies before
  an agent request, emit prompt and lazy-load payloads, report context savings,
  enforce token/reduction thresholds, and fail closed on policy drift.

All notable project changes will be documented here. The format follows Keep a
Changelog principles, and releases use semantic versioning.

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
