# ContextLens revision plan

## Current architecture

ContextLens is a Python 3.11+, dependency-free, local-first library and CLI.
It records ordered context sources to versioned JSONL, optionally externalizes
large content into a content-addressed artifact store, profiles a completed
request with deterministic lexical signals, runs agent adapters in copied
temporary workspaces, evaluates replay output, performs adaptive ablation and
paired bootstrap analysis, and renders terminal, JSON, CSV, and HTML reports.

The main data path is:

```text
instrumented agent -> JSONL context events -> passive profile
                                         -> adaptive replay planner
repository snapshot -> isolated replay worker -> evaluator
                                             -> paired effect analysis
                                             -> report / savings estimate
```

There is no server, authentication layer, frontend application, or configured
database to preserve. The HTML renderer is the existing web-facing surface.
The package deliberately has no runtime dependencies.

## What works

- First-class context source boundaries, ordering, provenance, token counts,
  redaction hooks, and content-addressed large artifacts.
- Deterministic one-run usage and duplication signals that are explicitly
  labeled non-causal.
- Isolated directory-copy replays, subprocess adapters, timeouts, retries,
  resource preflight, in-memory idempotency cache, and workspace diffs.
- Adaptive group ablation, fixed-answer screening, target-model verification,
  paired bootstrap intervals, evidence-scope checks, and savings projections.
- Portable reports and a CLI for record, scan, analyze, optimize, and report.
- A clean baseline: tests, Ruff, strict mypy, and wheel build all pass.

## Problems found

- A trace contains context-addition events only. It does not yet model the
  overall agent run, model/tool steps, outcome, repository commit, or aggregate
  usage.
- Source kinds and metadata are close to the target model but do not explicitly
  carry insertion step/position, target agent/phase, URI, or a content hash on
  every inline item.
- Persistence is JSONL plus artifacts only. Experiment, replay, evaluation,
  effect, recommendation, and policy records are not queryable normalized
  entities.
- Replay variants only express removal. Summarization, lazy loading, and
  agent/phase scoping are absent.
- The adaptive search is deterministic but optimized around group ablation;
  it does not expose the requested explainable per-candidate priority or paired
  run plan.
- Evaluation contracts preserve numeric scores and strings but do not provide
  a coding-task dimension model or visible objective weights.
- Reports can recommend keep/remove/investigate but cannot export and validate
  an executable context-loading policy.
- The existing examples and benchmark validate individual layers, not a single
  fixture repository demonstrating the complete vertical slice.
- Secrets can be redacted with user-supplied regexes, but there is no safe
  built-in redactor. Retention and deletion behavior are undocumented.

## Components to retain

- Package structure, Python/dataclass conventions, strict typing, and
  dependency-free runtime.
- JSONL as the portable capture format and the artifact store for large
  payloads.
- Existing context model and source IDs, extended compatibly.
- Passive profiler, replay adapter protocol, `DirectorySnapshot`,
  `ReplayWorker`, cache, adaptive planner, evaluators, paired analyzer,
  optimizer, and report renderers.
- Existing CLI commands and configuration-driven workflow.

## Components to replace or simplify

- Extend `ContextVariant` rather than replacing replay infrastructure.
- Add a mutation application layer before adapter invocation; preserve
  `removed_source_ids` as a compatibility shorthand.
- Add an explicit deterministic candidate coordinator alongside adaptive group
  search. It will plan one-item paired experiments within a hard run budget.
- Add normalized SQLite persistence as an optional local index and job store;
  JSONL remains the portable trace interchange format.
- Add mechanical coding evaluation and policy export without adding a generic
  evaluator framework or model provider.

## Proposed data flow

```text
Agent/runtime
  -> trace header + structured context + model/tool/outcome steps
  -> JSONL/artifacts and optional normalized SQLite index
  -> deterministic passive profile
  -> ranked one-item mutation candidates
  -> budgeted paired experiment plan
  -> isolated baseline/variant replay workers
  -> mechanical coding evaluators
  -> paired effect estimates with uncertainty
  -> evidence-backed recommendations
  -> validated YAML/JSON context policy
  -> terminal/JSON/CSV/HTML report
```

SQLite stores queryable core fields and JSON only for provider-specific
metadata and evidence. Project ID is part of every read path that can expose
trace content. Replay job IDs and cache keys are stable uniqueness boundaries.

## Implementation phases

1. Extend trace/run/step and context metadata while retaining schema-reader
   compatibility; add built-in secret redaction.
2. Add the normalized SQLite schema, migrations, project-scoped access, and
   trace ingestion.
3. Expand passive profiles with normalized relevance, usage, redundancy,
   contradiction, staleness, cost, priority, and inspectable reasons.
4. Implement exactly remove, summarize, lazy-load, and scope mutations and
   integrate them with isolated replay requests.
5. Add deterministic budgeted paired planning and an explicit replay lifecycle.
6. Add coding-task evaluation dimensions, visible balanced weights, effect
   evidence labels, recommendations, and validated policy serialization.
7. Add a deterministic coding fixture and end-to-end tests, then update
   architecture and CLI documentation.

## Risks and assumptions

- Remote agents and model providers cannot be made reproducible by this
  library. Seeds and versions are recorded when available; confidence comes
  from paired repetitions rather than a determinism claim.
- Directory-copy isolation does not provide OS, network, or secret isolation.
  The runtime interface remains replaceable by Docker; the MVP documents this
  limitation and defaults subprocess environments to an allowlist.
- A summarizer is an injected provider. ContextLens records its prompt, model,
  source hash, and generated item; it never silently selects another provider.
- Lazy loading is an explicit adapter contract. ContextLens can make a source
  retrievable and account for retrieval, but cannot force an external agent to
  call it.
- SQLite encryption depends on filesystem/platform encryption because the
  standard library SQLite build has no encryption extension. Secret redaction
  occurs before persistence, raw content is excluded from reports, and provider
  use remains explicit.
- The repository has no application authentication or frontend. Project-scoped
  storage checks and the existing HTML report are the honest MVP boundaries;
  a multi-user service and polished dashboard are follow-on work.
- The implementation will preserve compatibility with current trace schema
  `1.0` and CLI configs where practical. New fields are optional and additive.
