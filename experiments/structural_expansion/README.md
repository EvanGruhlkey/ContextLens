# Structural expansion (retired)

`structure.py` takes the set of source lines a relevance filter decided to
keep and deterministically closes over the Python AST: enclosing scopes,
`import` statements, class and function headers, control-flow headers needed
for syntactic validity, and the definitions of names referenced by the kept
lines, up to a bounded number of hops.

It was the "structural" half of an earlier ContextLens, where a source read was
reduced by Jev and then re-expanded to stay compilable.

## Why it is retired

The 21 September 2026 paired coding-agent benchmark (ten frozen real Python
issues, `gpt-5.6-luna` at `reasoning.effort=low`, 20 turns, 300 s) measured
three conditions on identical tasks, tools, prompts, and revisions:

| Condition | Coding-model input tokens | Verified fixes |
| --- | ---: | ---: |
| Baseline (raw tool output) | 583,459 | 5/10 |
| Jev filtering only | 379,716 | 7/10 |
| Jev filtering + structural expansion | 1,105,615 | 5/10 |

Structural expansion did reduce its own raw tool output (138,931 to 119,054
tokens), but the trajectories it produced fetched far more context overall. Two
tasks dominated: `click-short-help` reached 490,067 coding-model input tokens
against a 27,291 baseline, and `spotify-luigi-run-arguments` reached 264,521
against 81,371.

The full report is in
[`docs/history/coding-agent-2026-09-21.md`](../../docs/history/coding-agent-2026-09-21.md).

## State of the code

`structure.py` is standalone: it carries its own `LineReason` enum and imports
nothing from `contextlens`. It has no tests in the current suite and no
callers. Reviving it means re-integrating it behind an explicit opt-in and
re-running the paired benchmark to show it no longer inflates trajectories.
