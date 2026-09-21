# ADR 0001: Python, local-first, event-oriented core

- Status: Accepted
- Date: 2026-07-26

## Decision

Build the initial ContextLens implementation as a Python 3.11+ library and CLI.
Use versioned JSONL events for traces, content-addressed artifacts for large
payloads, and protocol-based adapters for agent runtimes and evaluators.

The core package will depend on abstractions rather than provider SDKs. Provider
integrations will be optional extras or separate packages.

## Why

Python is the common denominator for evaluation tooling, model SDKs, statistics,
and test harnesses. JSONL is inspectable, append-friendly, and resilient to
partial recordings. A local-first design is necessary because recorded context
commonly includes private code and credentials.

## Consequences

- The public trace schema must remain language-neutral.
- Optional integrations must not inflate the base installation.
- Schema compatibility and migrations become explicit maintenance work.
- A future TypeScript SDK can emit the same event format without sharing the
  Python implementation.

