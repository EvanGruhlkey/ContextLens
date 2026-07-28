# Adaptive group ablation

Adaptive search reduces replay cost by testing related context together and
drilling down only when a group contains context that materially affects task
quality.

## Search loop

1. Run and score the full-context baseline.
2. Group sources by context kind, or use a caller-provided partition.
3. Rank groups using removable tokens, group size, and one-run profiler labels.
4. Test the highest-priority groups concurrently.
5. Compare each ablated score with the baseline and configured quality
   tolerance.
6. Recommend a group for removal when its lower uncertainty bound stays within
   tolerance.
7. Keep a single source when removing it causes material quality loss.
8. Split a harmful multi-source group and test its children.
9. Stop when the tree is resolved or an experiment, token, or cost budget is
   exhausted.

```text
files: [architecture, old notes]
              |
       removal hurts quality
          /             \
 architecture          old notes
 removal hurts         no quality loss
      keep                 remove
```

## Usage

```python
from contextlens.experiments import (
    AdaptiveAblationPlanner,
    AdaptiveSearchRunner,
    SearchConfig,
)

planner = AdaptiveAblationPlanner(
    context_sources,
    config=SearchConfig(
        quality_tolerance=0.01,
        max_experiments=20,
        batch_size=4,
        max_planned_context_tokens=1_000_000,
        max_estimated_cost_usd=10,
        estimated_cost_per_1k_tokens=0.002,
    ),
    profiles=one_run_report.profiles,
)

search = AdaptiveSearchRunner(
    planner,
    replay_coordinator,
    evaluator,
    score_name="quality",
).run()

print(search.report.recommended_removals)
```

The planner supports maximizing a quality score or minimizing a loss score.
Evaluator metadata may include an `uncertainty` radius. When the resulting
interval crosses the tolerance boundary, the group is marked `inconclusive`
rather than forced into a keep/remove decision.

## Prioritization

Groups receive higher priority when they:

- Contain more removable tokens.
- Contain more sources and therefore offer more information when split.
- Were labeled `unused` or `duplicated` by the one-run profiler.

Profiler signals affect experiment order only. A removal recommendation still
requires a counterfactual replay and evaluator score.

## Decision tree

Every node preserves:

- Group and parent IDs.
- Source IDs and removable tokens.
- Priority and depth.
- Planned variant ID.
- Score, uncertainty, and baseline-relative quality change.
- `remove`, `keep`, `split`, `inconclusive`, `skipped`, or `error`.
- A human-readable decision reason and child IDs.

The report also records planned experiments, context tokens, estimated cost,
and the exact stopping reason.

## Limitations

A group recommendation means that removing that group alone stayed within the
quality tolerance. Several individually safe removals may interact when
combined. ContextLens therefore does not yet call the union a verified optimal
configuration. The context-optimization milestone will construct and verify
combined candidates before recommending them for deployment.

