# ContextLens roadmap

ContextLens reduces the context coding agents have to read. The goal is lower
frontier coding-model input per successful task, without extra agent turns.

## Product rules

- Filter on the tool-response path. Do not add coding-model turns to decide
  what context to keep.
- Use Jev only for closed-form KEEP/DROP over already-discovered candidates.
- Keep semantic relevance and structural dependency support separate.
- Preserve exact originals and make every omission recoverable.
- Fail open when scoring, parsing, validation, or recovery is uncertain.
- Measure complete task trajectories. Never mix Jev tokens into the primary
  coding-model input metric.

## Implemented foundation

- Transparent observation filtering for source, search, tests, and logs.
- Configurable bypass for small, ranged, and known-symbol reads.
- Python AST dependency closure after Jev-selected primary code.
- Content-addressed receipts and observation handles.
- Active / pinned / deferred working sets with Jev garbage collection.
- Default MCP surface: filter, read, recover, pin, list.
- Local three-condition fixture evaluation.
- Paired real coding-agent harness on ten frozen Python issues with
  transparent host-side filtering.

## Current decision

The default product is the filter layer, not an action controller. Mandatory
`context_next` routing increased agent input and turns in the paired pilot
(2/3 verified fixes, 156.21% more complete input on finished pairs) and stays
experimental (`--profile controller`).

The coding-agent harness is the main evaluation. The Jev-enabled 21 September
2026 gpt-5.6-luna run completed 30/30 attempts. Verified fixes were Baseline
5/10, Jev Filter 7/10, ContextLens 5/10. Jev Filter used 203,743 fewer
coding-model input tokens and two fewer turns. ContextLens used 522,156 more
coding-model input tokens after two large unreduced trajectories. That is one
trial, not a shipping-gate claim.

## Next: live paired coding-agent gate

- Re-run baseline / Jev-filter / full ContextLens with a frontier coding model
  and Jev credentials.
- Require no task-success regression, a meaningful drop in coding-model input,
  and no material turn increase.
- Report Jev tokens and cost separately.

## Experimental controller

The older `context_next` loop remains available for reproduction:

- Trigger decisions only at measurable uncertainty or phase boundaries.
- Reuse one decision across related reads or verification operations.
- Compare optional routing against evidence selection without action routing.
- Require a complete paired run with no timeout before considering a default.

## Later: adaptive retention

- Replace the fixed semantic threshold with a query-adaptive gate.
- Calibrate semantic evidence and dependency support independently.
- Add a budget controller that expands or contracts around structural risk.
- Distinguish complete definition retention from lightweight interface views.
- Add explicit confidence and repair-cost fields to every decision.

## Later: more observation shapes

- Search results: retain matching hits plus file, symbol, and result-group headers.
- Tracebacks and logs: retain causal chains, exception boundaries, and local time
  windows while collapsing repeated frames and noise.
- JSON: retain matching leaves plus ancestor keys, referenced identifiers, and
  schema-critical siblings.
- Plain text: use semantic spans with headings, list boundaries, and local windows.
- Add JavaScript and TypeScript parsing after Python behavior is calibrated.

## Implemented runtime integration

- A middleware adapter handles repository reads and observations.
- One task goal persists while focus changes between decisions.
- Source and observation recovery are first-class MCP tools.
- Add deterministic caching keyed by task, focus, content, and configuration.
- Add concurrency limits and backend health/circuit-breaker behavior.

## Evaluation gates

Every change must report:

- total task success and mechanical evaluator results;
- complete input and output volume across the run;
- recovery calls and reread behavior;
- pruning latency and backend failures;
- retained-line precision by reason;
- syntax and structural-repair failures; and
- paired confidence intervals over repeated trials.

The first release target is a Python coding-task suite with paired baseline and
pruned runs. A default threshold ships only after it meets a no-regression gate
across tasks and demonstrates net end-to-end reduction after recovery traffic.

## Deliberately removed from the main product

- repository instruction-file inventory and linting;
- configuration minimization and patch generation;
- provider-specific repository scope resolution;
- repository-context CI verdicts; and
- static savings claims that are not tied to task trajectories.

Useful paired-runner, evaluator, artifact, redaction, and telemetry internals
may be reused behind the new runtime path. They are implementation support, not
the user-facing product.
