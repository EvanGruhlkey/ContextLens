# Repository-context regression pivot

This document records the architectural decision that moved ContextLens from a
trace-first global context optimizer to CI and regression testing for
repository-owned agent context.

## Problem found

The original system could measure and optimize injected `ContextSource` tuples,
but its first-use workflow required custom recording and knowledge of replay
internals. More importantly, reduced injected context was too easy to describe
as economic savings even when an agent compensated through extra turns,
repository exploration, cache loss, or reasoning.

At the same time, Codex, Claude, and editor harnesses increasingly implement
runtime compaction, prompt caching, context editing, and deferred tool loading.
Competing primarily as a runtime compressor was not a durable product boundary.

## Decision

The primary abstraction is now:

```text
base repository context vs candidate repository context
```

The default commands are `scan`, `diff`, `verify`, and `minimize`. Static
analysis supplies immediate value and candidates; controlled matched trials
supply causal evidence.

## What was reused

The pivot is an orchestration and UX layer, not a rewrite. It reuses immutable
context sources, isolated workers, adapters, mechanical evaluators, repeated
trials, mutations, adaptive search, paired analysis, optimization safety,
storage, reports, and policies.

## Metric correction

Reports now distinguish context footprint from provider economics. Cached,
uncached, cache-write, output, and reasoning categories remain separate. A
candidate that reduces footprint but regresses quality or end-to-end economics
fails closed.

## Compatibility

Trace and policy schemas were retained. `scan trace.jsonl` continues to work,
with `profile` added as the clearer spelling. See [migration.md](migration.md).
