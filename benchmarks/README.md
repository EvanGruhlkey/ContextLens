# Benchmarks

## Jev primary-first evidence benchmark

The live Jev component benchmark checks fixed primary/support cases and records
both returned exact-source tokens and the gateway tokens used to select them:

```powershell
$env:AI_GATEWAY_API_KEY = "your-vercel-ai-gateway-key"
python -m benchmarks.jev_selection --root . --output benchmarks/results/jev-selection.json
```

It fails when required anchors are absent or fixture noise is returned. This is a
component diagnostic, not a coding-agent accuracy or whole-system savings result.
See the [measured September 20 run](../docs/jev-benchmark-2026-09-20.md).

Compare the historical full-source decision with descriptor-first selection:

```powershell
python -m benchmarks.jev_selection_comparison `
  --root . `
  --output benchmarks/results/jev-selection-comparison.json
```

The comparison includes both descriptor and exact-source provider usage. See the
[measured descriptor comparison](../docs/jev-descriptor-benchmark-2026-09-20.md).

## Bounded next-action benchmark

The action benchmark offers Jev six typed capabilities without executing them:

```powershell
python -m benchmarks.action_selection `
  --root . `
  --output benchmarks/results/action-selection.json
```

It reports top-1 accuracy, top-3 recall, provider usage, latency, and fallbacks.
See the [measured action-selection run](../docs/action-selection-benchmark-2026-09-20.md).

## Integrated controller loop benchmark

Exercise retention, capability filtering, and action routing across repeated
observations:

```powershell
python -m benchmarks.controller_loop `
  --output benchmarks/results/controller-loop.json
```

This includes every Jev call made by the controller cycle. It does not execute a
coding model. See the [measured controller run](../docs/controller-loop-benchmark-2026-09-20.md).

## Goal evaluation: verified patches and agent input tokens

The README goal is lower input-token usage with enough repository context to
produce correct results. `benchmarks.goal` tests both in a paired real-agent pilot.

```bash
python -m benchmarks.goal --output evals/artifacts/goal-new --trials 1 --timeout 240
```

This starts live agent runs using existing signed-in Codex subscription capacity.
It removes API-key environment overrides, initiates no paid API calls, and does
not redeem reset credits. Use a new output directory for each evaluation.

Three public historical bugs cover Click empty-default help, tslib array-like
inputs, and Luigi boolean parsing defaults. Commits, tasks, hidden assertions and
known-fix patches are frozen in `benchmarks/fixtures/goal`. Before any agent runs,
every original checkout must fail and every reference patch must pass. Checks
cover the bug and selected regressions, not full upstream test suites.

Each pair uses fresh identical checkouts, the same model (`gpt-5.6-luna`), low
reasoning effort, timeout and ordinary edit/test tools. A seeded shuffle changes
condition order within each pair; runs execute sequentially. Normal conditions
use native tools. Compact conditions additionally have the three current MCP tools
and one preference instruction to use them when helpful. Both start with task text
only: no eager bundle, forced verification or mandatory duplicate read.

Frozen external assertions grade a fresh base checkout with the submitted patch
replayed, including new files. Source is snapshotted before execution. Reports
preserve patches, raw JSONL, errors, usage and verifier output. Checks live outside
the solver workspace; the prompt forbids parent reads and network access. This is
a local sandboxed pilot, not a hermetic evaluation.

**Primary metrics:** passed patches and gross provider input tokens across whole
attempts, including failed fixes, discovery, tool schemas and repeated context.
Cached input is a subset of input, recorded separately; uncached is input minus
cached. Output is separate; total is input plus output. Terminal `turn.completed`
events supply usage; unknown or incomplete usage stays unknown. Cached input is
never added to input again. See the
[official CLI event format](https://learn.chatgpt.com/docs/non-interactive-mode).

Matched input reduction uses completed, usage-known pairs. Invalid attempts and
missing costs remain visible. Correct-to-incorrect regressions and tool uptake
are reported. The sample meets the goal only if all planned pairs finish,
candidate reads are exercised, gross input is lower and every candidate patch
passes. This does not establish statistical accuracy preservation or dollar savings.
These three public, named-symbol development tasks are not held out. The callback
adapter and neural pruning are **not** exercised by this MCP evaluation.

## Small local evidence-delivery diagnostics

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
