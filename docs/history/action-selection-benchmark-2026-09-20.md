# Bounded next-action benchmark — September 20, 2026

## Result

Jev selected the expected next capability in all six fixed cases after one
ambiguous fixture was corrected. Top-1 accuracy and top-3 recall were both 100%.
The six decisions used 5,148 input tokens and 600 output tokens. Vercel reported
zero cost under promotional pricing.

| State | Expected | Selected |
| --- | --- | --- |
| Unknown source location | Search repository | Search repository |
| Known source handle | Read source | Read source |
| Known reproduction target | Run targeted test | Run targeted test |
| Failure after an edit | Inspect failure | Inspect failure |
| Sufficient implementation evidence | Ready to edit | Ready to edit |
| Passing completed task | Stop | Stop |

The controller did not execute any action. ContextLens constructed six bounded
candidates, Jev returned probabilities for those identifiers, and deterministic
code selected the highest valid probability. No shell command or free-form tool
call was generated.

## Failure behavior observed

The first run had one provider failure. The controller returned no selected action
and exposed all available identifiers to the caller. The benchmark now reports
fallbacks separately from valid-decision accuracy.

The second run scored 5/6 top-1 because the reproduction fixture left source
location unknown, making repository search a defensible choice. The corrected
fixture states that both source and test are known. All three artifacts remain
available so these changes are auditable.

## Limits

These are hand-authored development states with a small fixed action taxonomy.
The benchmark does not execute tools, run a coding model, edit a repository, or
measure final task success. A live trajectory comparison is still required before
Jev should control execution or before any quality or token-savings claim.

## Artifacts

- [Final unambiguous run](../benchmarks/results/action-selection-final-2026-09-20.json)
- [Ambiguous-fixture rerun](../benchmarks/results/action-selection-rerun-2026-09-20.json)
- [Initial run with provider fallback](../benchmarks/results/action-selection-2026-09-20.json)
