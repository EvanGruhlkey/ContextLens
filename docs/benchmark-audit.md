# ContextLens architecture and benchmark audit

Audited main commit `595094ea3ad7e474450fb17d7be6ab620aa6c848` with local fixes
on `codex/benchmark-quality-audit`. Cloud runs used the uploaded source snapshot
SHA256 `da44e0cdbda95529596a0a701135cd4a35f2def7048f80b54000f46527a8e6ef`.

## Assessment

Task-conditioned evidence selection followed by structural repair and exact
recovery is a reasonable starting architecture. It is **not demonstrated to be
the best architecture for saving total agent tokens while preserving outcomes**.
The current gate proves only parse validity and reduced observation size.
Recovered function/class headers can contain `pass` instead of implementations;
bounded dependency hops can omit required definitions. These are observations
for reading, not executable replacements. Correct code comprehension and actual
task completion need separate measurement.

The installed command currently prunes Python source only. Other languages,
search output, logs and JSON bypass pruning, so savings depend on traffic mix.
Repeated reads re-run the model; the inference lock serializes local requests.
Goals are deterministic task/focus strings rather than agent-generated current
information needs. The local/HTTP boundary and fail-open behavior are useful.

## Fixes made

- Resolve assignments within lexical scopes instead of a global last-write map.
  Preserve all assignments in the nearest scope conservatively; parameters stop
  accidental restoration of similarly named globals.
- Follow names used in enclosing control-flow conditions.
- Restore complete multiline function/control headers and long statements.
- Preserve original newline bytes in receipts, including CRLF and mixed newlines.
- Parse invalid source before expensive inference.
- Allow a downstream tokenizer counter for the minimum-size and net-savings gates.

Scope handling is still not a complete Python name resolver: comprehensions,
`global`/`nonlocal`, dynamic imports and runtime attribute dispatch need further
work. These fixes do not guarantee semantic equivalence.

## Measurements

Structural audit: seven constructed cases with oracle evidence and independently
specified support lines. Before fixes, support passed 5/7 and pruning completed
4/7; after fixes, both passed 7/7. Exact full recovery improved from 1/3 newline
cases to 3/3. Synthetic token reductions are not production savings.

Free Google Colab T4, Python 3.13.15, PyTorch 2.11.0+cu128, swe-pruner 0.1.1,
transformers 4.57.6, tiktoken 0.14.0. Three actual ContextLens files, three
repeats, threshold 0.5, exact `o200k_base` counts including omission comments.

The default runtime exhausted the T4's GPU memory: 4/9 backend failures. Its
25.6% aggregate observation reduction is an **invalid run**, preserved in
`benchmarks/results/colab-t4-initial.json` for diagnosis.

An explicit experimental memory-efficient SDPA variant expands grouped keys and
values and uses PyTorch's efficient attention kernel. It retains the upstream
8,192-token window, checkpoint, overlap, pruning head and threshold. It is
implemented only as a separate benchmark runner, not a silent production change.
Numerical equivalence with the default kernel has not been established.

The variant completed 9/9 reads, all parsed and recovered exactly. Returned-text
tokens fell from 62,676 to 15,390 (**75.45%**). First request took **17.69 seconds**,
including local lazy loading from the already downloaded checkpoint; warm median
was **2.83 seconds**. Peak PyTorch allocated GPU memory was **9.23 GiB**.
Raw outputs and provenance are in `benchmarks/results/colab-t4-efficient.json`.

Each distinct file's first repeat:

- Repository scope: 11,533 to 1,622 tokens (85.94% reduction).
- AST repair: 2,407 to 913 tokens (62.07% reduction).
- Paired trial ordering: 6,952 to 2,595 tokens (62.67% reduction).

Three repeats do not create nine independent quality tasks. The observed saving
covers returned source text only. It excludes agent prompts/history, output,
recoveries, retries, model-server costs and provider cache effects. `o200k_base`
counts are exact for that encoding, not asserted to match every deployed model.
The CPU attempt timed out at 300 seconds without a completed observation.

## Exploratory output check

A free T4 also ran Qwen2.5-Coder-1.5B-Instruct on three post-hoc code questions,
with one greedy generation per full/pruned condition and alternating order.
Both conditions answered the factual questions correctly (3/3). Full context
had one extra JSON field: exact schema compliance was 2/3 versus 3/3 pruned.
This formatting difference is not a factual quality improvement.

Inspection found the relevant helper implementation missing in two of the three
pruned contexts, despite the correct answers and instructions to abstain when
implementation is omitted. These answers do not demonstrate evidence preservation.
Only the paired-order question retained its required implementation. The questions
were chosen after seeing pruning outputs; this small smoke check has no statistical
quality claim and does not measure an agent trajectory or recovery behavior.
Generations and revised grading are preserved in
`benchmarks/results/colab-t4-qa.json`; the initial strict-schema grading is retained
separately. No inference was rerun to revise grading.

## Architecture priorities

1. Gate policies on paired task correctness, using full-context, pruned and
   token-matched-random controls on held-out tasks. Tune thresholds on a separate
   development set. Measure entire trajectories with all recovery calls.
2. Follow definitions to the implementation depth required by the task, or expose
   an explicit omitted-implementation signal and recovery action. Header stubs
   must not be treated as sufficient evidence for behavior questions.
3. Use the deployed agent's tokenizer and enforce a useful savings/latency budget.
   Include receipt markers and recovery overhead. Keep the current cheap bypass
   before inference, with adaptive thresholds calibrated against quality.
4. Cache scores for identical content, goal, checkpoint revision and inference
   settings. Keep receipts separate and preserve current focus/version in keys.
5. Pin checkpoint revisions and model/runtime dependencies, record errors and
   inference settings, and validate accelerator-specific kernels before deployment.

The older `evals/` harness evaluates repository context policies, not this
observation middleware. It cannot establish current pruner quality unchanged.
General claims require a new integration into real agent read/recover tools.
