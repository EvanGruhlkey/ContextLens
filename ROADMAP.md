# ContextLens roadmap

ContextLens is an experimental context and action controller for coding tasks.
The primary outcome remains lower end-to-end input volume without reducing task
success. The September 20 controller pilot did not meet that gate.

## Product rules

- Condition every selection on the current task or narrower runtime focus.
- Keep semantic evidence and structural support as separate signals.
- Preserve exact originals and make every omission recoverable.
- Fail open when scoring, parsing, validation, or recovery is uncertain.
- Measure complete task trajectories, not isolated prompt size.
- Add a format only after its dependency rules have task-level tests.

## Implemented foundation

- Stable request, result, reason, and omission-range contracts.
- SWE-Pruner-compatible HTTP semantic scoring adapter.
- Python AST dependency closure for imports, definitions, scopes, decorators,
  control flow, and bounded symbol hops.
- Parseable skeleton rendering with line-range markers.
- Content-addressed local receipts and exact range recovery.
- Command-line and local HTTP entry points.
- Reduction, latency, backend, bypass, and kept-line telemetry.
- Descriptor-first Jev evidence ranking with exact-source verification.
- Typed bounded action selection exposed through MCP.
- Recoverable active and deferred observation working sets.
- Jev-assisted retention and capability filtering with deterministic fallback.
- Iterative controller sessions and complete controller-usage telemetry.
- Paired whole-trajectory accounting that includes Jev overhead.

## Current decision

Keep the controller experimental. The three-task paired pilot produced 2/3
verified fixes in both conditions, one control timeout, and 156.21% higher total
input across the two complete pairs. Mandatory decisions before each major tool
operation are not the shipping policy.

## Next: sparse controller invocation

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
