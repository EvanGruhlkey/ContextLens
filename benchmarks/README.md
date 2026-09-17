# Small local benchmarks

The current benchmark measures compact evidence delivery on three deterministic
fixtures and two named functions in the ContextLens repository. It uses no GPU,
model inference, network calls or subscription capacity.

```bash
python -m pip install -e ".[benchmark]"
python -m benchmarks.compact --root . --repeats 3 --output benchmarks/results/compact.json
```

Each case compares an exact full-file read with **discovery plus a grouped read**.
Both token counts include the complete returned text, using `o200k_base`. Each
case runs three times with a fresh state directory: the first discovery/read is
cold, and the remaining two provide the warm median. The full-file baseline is
read before timing and excluded from discovery/read latency. Reads do not suppress
repeat content in these ordinary service sessions.

The run fails if the first match is the wrong file, required source anchors are
missing, irrelevant fixture code appears, or returned token counts differ across
repeats. Source hashes and the repository revision are recorded in the report.
Anchor checks validate evidence retention; they are not coding-agent accuracy.

These measurements do not establish total provider-token savings, successful
bug fixes or generalization to other repositories. Timings depend on the machine
and repository contents. Cases are development diagnostics selected by symbol.

Old published result files and reports were removed. Legacy benchmark utilities
remain for existing regression checks; their old results are not current evidence.
