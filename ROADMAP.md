# ContextLens roadmap

## Milestone 1 — Foundation

- Define the product boundary and threat model.
- Specify a versioned, provider-neutral context trace.
- Establish Python package, test, lint, and contribution conventions.
- Publish an architecture decision record for the MVP.

## Milestone 2 — Record

- Implement the trace and context-source data model.
- Add JSONL serialization with secret redaction hooks.
- Provide an SDK recorder and a subprocess/CLI recording path.
- Add import adapters for common agent event formats.

## Milestone 3 — Replay and ablate

- Define task suites and agent adapter contracts.
- Reconstruct full-context baseline runs.
- Generate leave-one-source-out variants by source, kind, or tag.
- Control model settings, seeds, retries, timeouts, and concurrency.
- Cache equivalent runs using content-addressed inputs.

## Milestone 4 — Evaluate

- Support exact, programmatic, test-suite, and model-graded evaluators.
- Track success, score, latency, input/output tokens, and estimated cost.
- Compute paired effect sizes and bootstrap confidence intervals.
- Detect unstable baselines and insufficient sample sizes.

## Milestone 5 — Report

- Produce terminal, JSON, CSV, and self-contained HTML reports.
- Rank helpful, neutral, harmful, and uncertain context sources.
- Show quality gained or lost per 1,000 tokens.
- Drill down from an aggregate result to individual replay evidence.

## Milestone 6 — Open-source release

- Ship end-to-end examples and deterministic fixtures.
- Add CI, issue templates, a code of conduct, and security policy.
- Document provider integrations and third-party data handling.
- Publish an initial package and tagged release.

## Later investigations

- Grouped and interaction ablations for correlated context sources.
- Shapley-value approximations when leave-one-out is misleading.
- Online shadow experiments and production telemetry ingestion.
- Context selection recommendations and budget optimization.

