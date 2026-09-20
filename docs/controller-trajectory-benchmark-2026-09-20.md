# Controller trajectory benchmark — September 20, 2026

This paired pilot compared ordinary coding-agent runs with runs instructed to use
the completed ContextLens controller. Both conditions used the same model,
reasoning level, frozen repository revisions, tasks, timeouts, and external
mechanical graders. The control condition used repository selection, recoverable
observations, retention, capability filtering, and bounded next-action decisions.

## Result

- Baseline: 2/3 verified fixes; all three attempts completed.
- Control: 2/3 verified fixes; two completed and one timed out.
- Complete matched pairs: 2/3 planned pairs.
- Control decisions: 16 across all attempts.
- Jev usage: 30,086 input tokens and 3,044 output tokens.
- Complete-pair coding-model input: 467,470 baseline versus 1,171,854 control.
- Complete-pair control input including Jev: 1,197,714 tokens.
- Complete-pair input change including Jev: **156.21% higher** under control.

The conditions failed on different tasks. Control fixed the Click task that the
baseline missed, and both fixed tslib. Control timed out on Luigi while baseline
passed. Because one pair is incomplete, the sample produces no success verdict
and no quality-preservation claim.

## Interpretation

The controller is functional: every control attempt used repository tools, and
all completed control attempts used bounded action decisions. The experiment does
not support enabling mandatory controller use as a token-saving default. Requiring
a decision before every major operation created more turns and substantially more
coding-model input than ordinary tool use.

The result suggests a narrower future policy: invoke the controller only at
high-value uncertainty points, reuse a decision across several operations, and
avoid requiring observation writes after routine steps. Those are future
experiments rather than claims about the current implementation.

This is a three-task, one-trial development pilot with familiar public bugs. It
can expose practical regressions, but it cannot establish general patch-quality
effects or statistical non-inferiority.

The machine-readable report is
[`benchmarks/results/controller-trajectory-2026-09-20.json`](../benchmarks/results/controller-trajectory-2026-09-20.json).
