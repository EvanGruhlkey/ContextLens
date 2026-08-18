# ContextLens roadmap

ContextLens is CI and regression testing for AI-agent repository context.

The roadmap follows an adoption ladder: useful static evidence first, a small
mechanical suite next, reusable checked-in evals after that, and historical
issue/PR imports for mature teams.

## Product principles

- Value before instrumentation: `scan` works immediately.
- Static is not causal: heuristics generate review candidates only.
- Git-native: context changes are configuration changes.
- End-to-end economics: footprint and provider usage stay distinct.
- Cache-aware: smaller prompts can still cost more.
- Fail closed: an observed quality regression cannot ship as verified-safe.
- Mechanical evaluation before model judging.
- Simple public UX over sophisticated retained experimentation internals.

## Phase 1 — Repository context core (implemented)

- Convention discovery for AGENTS.md, CLAUDE.md, Copilot/Cursor rules, skills,
  MCP configs, and static tool schemas.
- Deterministic footprint, duplicate, nested-scope, path, conflict, scoping,
  and tool-schema findings.
- Git-tree base comparison and worktree diff.
- Terminal, JSON, and Markdown output with explicit static labels.
- Repository-footprint versus target-effective context reporting.
- Provider-specific Codex, Claude, Copilot, and Cursor scope resolvers with
  documented/approximated labels.
- `contextlens init` detection for Python, Node, Rust, Go, Codex, and generic
  subprocess agents.

Next:

- Provider-specific discovery plugins with declared scope semantics.
- Better Markdown-aware instruction identity and configurable ignore rules.
- Optional exact tokenizer plugins without adding a default provider
  dependency.

## Phase 2 — Context regression evaluation (implemented)

- Base and candidate context as first-class versions.
- Matched isolated tasks and alternating repeated trial order.
- Mechanical command and exact-output evaluation.
- Quality, catastrophic failure, footprint, provider usage, cache, behavior,
  and latency reporting.
- PASS/WARN/CONTEXT REGRESSION/INCONCLUSIVE verdicts.
- Generic subprocess and Codex CLI paths.

Next:

- Stratified/randomized execution order for larger suites.
- Confidence intervals over multi-task resource deltas.
- Container/worktree isolation adapters and distributed runners.
- Historical issue/PR task import with leakage controls.

## Phase 3 — Verified minimization (hardened initial implementation)

- Exact duplicate, stale-guidance removal, and scoped-move candidate generation.
- Interpretable savings × confidence prioritization.
- Isolated candidate verification followed by combined final verification.
- Patch artifact only after a PASS verdict; no source auto-edit.

Next:

- Explicit summarizer and lazy-retrieval adapters using the existing mutation model.
- Full adaptive group splitting over instruction sections, not only candidates.
- Human-review annotations explaining every patch hunk.

## Phase 4 — CI (implemented)

- Static and verified CLI modes.
- Composite GitHub Action, JSON artifact, Markdown step summary, stable exits.
- Path-filtered dogfood workflow and reusable examples.
- Optional effective-context deltas from explicit targets or eval task metadata.

Next:

- Published tagged action releases.
- Optional PR-comment/update integration with minimal permissions.
- Baseline artifact caching and budget-aware verified scheduling.
- Policy templates for monorepos and high-cost task suites.

## Phase 5 — Bring existing telemetry (initial interface)

- Common OpenAI, Anthropic, and generic provider-usage normalization.
- Existing ContextLens trace and evaluation records retained.

Next:

- OpenTelemetry GenAI span ingestion.
- Codex execution-log import without replay.
- Claude agent/API telemetry import.
- Generic JSON trace mapping profiles.

ContextLens will not become a general observability backend. Adapters normalize
evidence needed to compare context versions.

## Phase 6 — Evaluation suites

Progression:

0. Static analysis only.
1. A few existing deterministic tasks/commands.
2. Checked-in `.contextlens/evals/` suites.
3. Imported historical issues and PR tasks.

Future work includes task sampling, flaky-test handling, evaluation budget
estimation, and repository-specific quality tolerances.

The real-context-change corpus currently pins seven changes across six public
repositories and checks in one reproducible static report. Cases stay
`static_ready` until realistic task definitions and repeated agent evidence are
available; static history is not mislabeled as a verified benchmark.

## Retained research engine

Versioned traces, passive profiling, isolated replay workers, mutation records,
adaptive search, paired bootstrap analysis, mechanical evaluators, normalized
persistence, reports, policy generation, predictor screening, and runtime
policy application remain supported. They power future minimization and
research workflows without burdening first-time users.

## Release work

- Update package/repository metadata and publish the first pivot release.
- Run static ContextLens CI on ContextLens itself.
- Publish a verified example with reproducible, non-secret provider settings.
- Establish a compatibility policy for eval config and CI JSON schemas.
- Add repository description and topics listed in the README/release docs.
