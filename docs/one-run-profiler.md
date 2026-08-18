# One-run profiler

The one-run profiler extracts inexpensive, deterministic signals from a
completed agent request. It does not call a model and does not claim to measure
causal value.

The profiler is the legacy trace-based analysis path. Repository context can be
scanned without instrumentation through `contextlens scan`; recorded traces use
`contextlens profile trace.jsonl`.

```python
from contextlens.profiler import ContextProfiler, RunObservation
from contextlens.trace import TraceReader

events = list(TraceReader("trace.jsonl").events())
observation = RunObservation(
    output_text="The final agent response",
    accessed_source_ids=frozenset({"agents-md"}),
    commands=("pytest",),
    changed_files=("src/parser.py",),
)
report = ContextProfiler().profile(events, observation)
print(report.to_dict())
```

## Built-in signals

For each context source, the profiler reports:

- Recorded tokens, or a clearly labeled byte-based estimate.
- Relative position from the start (`0`) to the end (`1`) of the request.
- Direct access recorded by the agent runtime.
- References to the source name, path, URI, or URL.
- Meaningful token overlap and likely supporting output spans.
- Exact or near-text duplication with other sources.
- Age and retrieval rank when present in provenance metadata.

It assigns an apparent-utilization label:

- `used` — at least one direct access, reference, or content-support signal.
- `unused` — readable content had no observed use signal.
- `duplicated` — unused content substantially repeated another source.
- `uncertain` — the content was unavailable or too small to judge.

“Unused” means only “no use was observed in this run.” It does not mean the
source can safely be removed.

## Optional adapters

`ContentSimilarity` can supply embedding or other semantic similarity scores
without adding a provider dependency to the core package.

`ModelInternalsAdapter` can attach log-probability, attention, gradient, or
other model-specific observations. These remain `observed` evidence. ContextLens
does not treat attention or any other internal signal as a verified causal
effect.

## Report contract

Serialized profiler reports always contain:

```json
{"evidence_level":"observed","causal":false}
```

Later adaptive experiments use these observations to choose which context
groups are worth verifying with isolated counterfactual workers.
