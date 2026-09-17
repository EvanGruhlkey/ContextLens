# ContextLens research review and proposed direction

Research checked September 17, 2026. Scope: primary papers, author repositories, and official implementation documentation, including work published through September 15, 2026. This is a targeted literature review, not a systematic review or an independent reproduction of the cited results. “Best” below means my recommendation for ContextLens's constraints, not a universal benchmark winner.

**Second-pass correction:** the original review missed FastContext's June 30 withdrawal. Its older HTML remains accessible, but the latest arXiv record states product IP issues and the referenced repository is unavailable. It is historical inspiration, not an available dependency or validated reproduction target. The review below also now distinguishes observation replacement from additive retrieval. See the implementation reassessment at the end.

## Decision

Develop ContextLens into a recoverable, task-aware evidence layer with three stages: retrieve relevant repository regions, preserve the code needed to act correctly, and manage what remains in the agent's working context. Keep the existing neural pruner as a selectable component.

The most useful combination to investigate is conditional focused exploration, separate evidence/support selection, and behavior-preserving observation compression. LaMR and CoACT inform these choices; withdrawn FastContext is historical inspiration only. This combination is a proposed synthesis, not a validated combined system. Start with a lean deterministic integration; evaluate available trained components before considering another training run.

For hosted frontier models, operate at the tool boundary using exact source spans. For self-hosted models with accessible hidden states, evaluate in-model pruning separately. Do not make access to the solver's internals a requirement of the main product.

## Evidence most relevant to this decision

### 1. FastContext — June 2026, withdrawn: historical exploration design

A specialized explorer searches the repository and returns file paths and line ranges. The older paper reports up to 60% fewer main-agent tokens across its settings; that is not automatically the reduction in all system tokens. Its cost appendix includes a separate explorer audit. The latest version is withdrawn for stated product IP issues. The referenced repository returned 404 during the second-pass review; current public model availability was not established. Withdrawal does not establish that the reported results were falsified.

**Application:** investigate an optional discovery layer that keeps intermediate search history outside the solver. Begin with lexical and symbol search, invoked when location is unknown. Do not depend on the withdrawn artifacts or forecast its headline reduction for ContextLens.

[Latest withdrawal record](https://arxiv.org/abs/2606.14066v4) · [Historical paper, including cost audit](https://arxiv.org/html/2606.14066v2)

### 2. LaMR — May 2026: distinguish relevant evidence from necessary support

LaMR gives semantic evidence and dependency support separate learned scoring components, then fuses their decisions. Its SWE-bench Verified table reports Sonnet 4.5 token reduction of 30.5%, with success 70.6% to 71.8%; for Opus 4.6, savings are only 3.0%, with success 75.6% to 76.0%. It uses a 0.6B backbone and Python-centered evaluation. Training used eight GPUs; a ready-to-use public checkpoint was not verified in this review.

**Application:** this is the closest architectural match to ContextLens's observed failure. A helper body can be necessary even when its identifiers have low similarity to the task. Borrow the two-part objective before committing to its training recipe.

[Paper and result table](https://arxiv.org/html/2605.15315v1)

### 3. CoACT — July 2026: train against behavioral damage

CoACT trains a 4B compressor using candidate compressions rewarded for shortening observations while preserving the next action. In a 200-instance SWE-bench Verified sample, Qwen3.5-35B-A3B used 36% fewer total tokens and pass@1 rose from 57.0% to 60.5%; Deepseek-v4-Pro used 19% fewer tokens but fell from 76.5% to 75.0%. In the same comparison, SWE-Pruner increased total tokens by 17–18%. Next-action similarity is a proxy, not a guarantee of final correctness.

**Application:** supervise which evidence to keep using successful behavior and eventual tests, rather than similarity alone. Preserve exact spans with ContextLens's renderer even if a teacher proposes free-form compression.

The official implementation now exposes a public merged Qwen3.5-4B compressor checkpoint. Reusing it is an inference experiment distinct from reproducing its training; compatibility, memory use, exact source fidelity and task quality remain unverified locally. Its output must not automatically be treated as verbatim source or safe edit anchors.

[Paper, Tables I–II](https://arxiv.org/html/2607.02911v1) · [Official implementation](https://github.com/THU-Agent/CoACT) · [Released checkpoint](https://huggingface.co/Kndy666/CoACT)

### 4. SWE-Pruner Pro — July 2026: attractive when hosting the solver

A small head predicts retained lines from the coding model's hidden states. The paper reports up to roughly 39% prompt-plus-completion token savings, with mixed quality changes across tasks and models.

The implementation requires a patched SGLang backend exposing hidden states. Therefore, this is not a portable replacement for a tool-boundary compressor used with arbitrary hosted frontier APIs.

**Application:** maintain as a separate experimental backend if ContextLens later controls model serving. It could avoid running an independent relevance model, but requires a substantially different deployment.

[Paper](https://arxiv.org/html/2607.18213v1) · [Serving requirements](https://github.com/Ayanami1314/swe-pruner-pro)

### 5. The Complexity Trap — 2025: make simple masking a serious baseline

This study compares old-observation masking against LLM summaries inside SWE-agent. For Qwen3-Coder-480B, its table reports cost 1.29 to 0.61 dollars per instance and solve rate 53.4% to 54.8%. That is a 1.4-percentage-point solve increase, not 2.6 percentage points. Effects vary: another reported configuration loses solve rate.

**Application:** retain recent evidence, keep a compact task state, and replace stale output with recoverable references. Do not assume a summarizing model earns its extra cost. Do not assume a fixed masking window transfers across solvers.

[Paper and configuration-specific results](https://arxiv.org/html/2508.21433v1)

### 6. Agent Retrieval Bench — July 2026: no universal retrieval winner

The benchmark contains 427 samples over 25 repositories and tests several forms of repository context acquisition. Different retrieval families win different metrics: embedding models lead some ranking/recall metrics, while RepoMap leads budgeted context yield at 8K tokens. No-gold examples also expose abstention calibration problems.

**Application:** evaluate lexical, symbol/graph, embedding, and hybrid retrieval independently. Include tasks where the right answer is “no supporting file found.” Relevant filenames alone do not prove a correct patch.

[Paper](https://arxiv.org/abs/2607.24882) · [Benchmark project](https://agent-retrieval-bench.github.io/)

### 7. RepoAtlas — September 15, 2026: maintain a changing repository view

This very recent preprint selects a bounded region of a code graph, presents textual and visual views, and refreshes them as exploration changes. Its comparisons are against particular graph-based baselines and use vision-language models.

**Application:** borrow the idea of a bounded view that follows current evidence and edit state. Start with a textual representation; adding images introduces another cost and another capability requirement. Independent confirmation of this fresh result is still needed.

[Paper](https://arxiv.org/html/2609.16936v1)

### 8. Paritok-4B — August 2026: strong compression is not preserved quality

The paper reports retaining 25.7% of context with 86.5% of uncompressed single-shot solve quality; line-numbered inputs retain 27.8% of context and 89.3% of quality. Those are measurable relative quality losses. A nonsignificant paired test does not establish equivalence. The 264 MB release is an adapter, not the complete 4B model; the described deployment uses a 24 GB GPU.

**Application:** useful comparison candidate, but not the default for a strict quality-preservation objective. Check actual source-span fidelity instead of treating a generative compressor's “extractive” description as a byte-level guarantee.

[Paper](https://arxiv.org/abs/2608.24188) · [Author model card](https://huggingface.co/paritok/paritok-4b-v1)

## Other approaches screened

- **SWE-Pruner:** ContextLens's existing 0.6B line-selector foundation. The original paper reports 23–54% agent-task token reduction. Retain it as a baseline, not as proof of ContextLens's total savings. [Paper](https://arxiv.org/abs/2601.16746)
- **LongCodeZip:** function-level selection followed by finer block selection under a budget. Relevant to hierarchical chunking, but completion/QA compression results do not by themselves establish agent efficiency. [Paper](https://arxiv.org/abs/2510.00446)
- **LLMLingua-2:** efficient task-agnostic token classification. Useful general baseline; its original evaluation is not a multi-step coding-agent validation. [Paper](https://arxiv.org/abs/2403.12968)
- **Repoformer:** learns when retrieval is worth doing for code completion. Borrow conditional retrieval, while recognizing the task differs from repository issue resolution. [Paper](https://arxiv.org/abs/2403.10059)
- **ACON:** refines compression instructions using full-context successes paired with compressed-context failures. Useful failure-analysis loop; primary benchmarks include AppWorld, OfficeBench, and QA rather than repository repair. [Paper](https://arxiv.org/abs/2510.00615)
- **ACM:** agent-controlled offloading to external memory and later retrieval. Motivates explicit recovery, but stored content being recoverable does not mean the current working context retains all needed information. [Paper](https://arxiv.org/abs/2607.23809)
- **Implicit compression negative result:** an in-context autoencoder works on some single-shot tasks but fails in the paper's multi-step coding experiments. Do not start with soft prompts or latent embeddings as the portable product interface. [Paper](https://arxiv.org/html/2605.11051v1)
- **Repository compression empirical study:** evaluates discrete, latent, and visual representations on completion/generation. Useful representation comparison, but BLEU and inference latency are different from issue-resolution success and total agent cost. [Paper](https://arxiv.org/abs/2604.13725)
- **Aider's repository map:** a working example of budgeted graph-ranked repository summaries and targeted expansion. Signatures help discovery; implementation bodies remain necessary for many behavioral questions. [Official documentation](https://aider.chat/docs/repomap.html)

## Proposed ContextLens architecture

This section is an engineering proposal inferred from the literature and our local audit.

### A. Capture current intent

Take the overall task, current tool request, active symbols, recent error, and current hypothesis. Let the agent supply an optional short information need with its tool call. Avoid paying for a separate frontier call just to restate every read.

“Fix refresh-token timeout” may require finding timeout configuration, checking units, tracing callers, inspecting retries, and locating tests. The relevant evidence changes across those stages.

### B. Retrieve with a low-cost cascade

1. Exact path/symbol/identifier matches and lexical search.
2. AST or language-server definitions, references, imports, and related tests.
3. Embedding retrieval or a trained explorer when the first stages are insufficient.
4. A reranker for a bounded candidate set, not a blind pass over the whole repository.

Track index/content versions, including uncommitted edits. Use language-specific extraction with explicit fallback when parsing or resolution fails.

### C. Select complete evidence units

Rank functions or coherent statement blocks before dropping individual lines. Track two separate reasons to keep content: it directly answers the question, or it supports the interpretation of another retained unit.

For behavioral questions, retain the relevant implementation, branch conditions, constants, and exception behavior. For editing, retain exact source anchors and enough adjacent code to apply a patch. Never present a synthesized pass stub as evidence of actual runtime behavior.

A complete dependency closure can explode in size, and static analysis cannot resolve every dynamic call. When uncertainty exceeds the budget, surface the unresolved dependency and provide an expansion action instead of silently implying completeness.

### D. Make omission explicit and recovery reliable

Return a structured bundle containing:
- repository and content version;
- source path and original line range;
- verbatim retained text;
- selection reason;
- omitted ranges and unresolved dependencies;
- a recovery handle tied to the original bytes.

Separate original source from annotations. Recover specific functions/ranges as well as full files. Before applying edits, verify content hashes and re-read changed regions. A recovery handle helps only if the agent knows when to use it; measure recovery behavior.

### E. Manage history with attention to caches

Compress incoming tool observations once before appending them. Keep recent failures, current edits, constraints, and unresolved hypotheses accessible. Test periodic masking/compaction of older observations as a separate policy.

Changing old prefixes may reduce cache reuse. Record cached and uncached usage separately; fewer logical tokens is not automatically lower cost or latency. Preserve stable ordering and avoid regenerating equivalent summaries every turn.

### F. Learn from outcomes after the interface works

Collect paired trajectories and categorize omitted-evidence failures. Train or distill a selector only after establishing that learned selection improves upon lexical/graph retrieval plus conservative preservation.

Useful training signals include evidence coverage, action consistency, valid edits, tests passed, recoveries, and end-to-end cost. Exact imitation of a baseline action can preserve a bad decision; use action consistency as a diagnostic/proxy alongside final success.

## What would give ContextLens a defensible edge?

The concept “a small model prunes code for a larger model” already has substantial prior art. AST repair and retrieval are also established. A plausible contribution is a **solver-independent, versioned evidence interface that measures and controls the quality cost of omission across whole coding tasks**.

Testable differentiators:
1. Adaptive expansion when a needed dependency was omitted.
2. Evidence coverage and unresolved dependencies visible to the solver.
3. Exact recovery that remains valid across repository edits.
4. Policy selection based on total task cost and a measured quality constraint.
5. Cross-language, cross-solver evaluation with failure cases published.

This is a promising research direction, not a novelty claim. A broader related-work and patent review would be needed before asserting uniqueness.

## Evaluation needed to choose the best policy

Use the same solver, scaffold, task snapshots, tool permissions, timeouts, and generation settings. Separate policy development tasks from held-out evaluation repositories. Include:
- full observations;
- simple lexical/RepoMap retrieval;
- old-observation masking;
- current ContextLens/SWE-Pruner;
- retrieval plus dependency-aware selection;
- optional trained explorer;
- an action-trained compressor where feasible.

Run ablations before combining every component. A token-matched random selector is a useful diagnostic, not a meaningful deployment target. Retrieval evaluation and end-to-end patch evaluation answer different questions; report both.

Primary outcomes: executable task success, regressions, invalid edits, total tokens, total cost per task, and aggregate cost per successful task. Include failed tasks in total cost. Track solver and auxiliary model usage separately, cached versus uncached input, outputs, recovery reads, repeated exploration, compression overhead, and median/tail latency.

Predeclare a tolerable success-rate loss, such as one percentage point if that matches product needs; it is an example decision threshold, not an established universal standard. Use paired task outcomes, confidence intervals, and repeated seeds for finalists. A small pilot cannot certify a tight non-inferiority margin, and p > 0.05 is not evidence of no harm.

Include adversarially ordinary cases: misleading names, timeout-unit conversions, helper indirection, imported constants, async cancellation, cross-file effects, ambiguous tasks, no relevant evidence, and files edited after retrieval.

Our earlier 75.45% result is returned-text reduction across three files, repeated three times, using an experimental T4 kernel. It excludes full trajectories and does not validate any new design here. Three post-hoc questions also did not establish preservation: relevant implementation was absent in two pruned contexts.

## Sequence under the existing free-compute constraint

1. Implement and evaluate deterministic retrieval, source provenance, and selective expansion locally. No trained auxiliary model is required.
2. Add a real read/recover integration and record whole trajectories.
3. Reuse public artifacts where available; check dataset overlap and licenses before training.
4. Use available free GPU sessions for bounded inference comparisons, keeping model and data versions fixed.
5. Distill or fine-tune only when a measured quality/cost bottleneck justifies it.

Free inference experiments are feasible, as our T4 run showed. Full reproduction of frontier-agent papers, large-scale teacher labeling, and multi-GPU training are not demonstrated feasible for free. No paid runs or subscriptions were initiated for this research.

## Recommendation to implement first

Build **conditional focused retrieval + compact exact observations + explicit recovery + a real solver-boundary adapter**. Keep the end-to-end quality/cost harness. Evaluate the released CoACT checkpoint as a separate backend before custom training; LaMR remains a distinct learned implementation. Investigate focused exploration without depending on withdrawn FastContext artifacts. Reserve SWE-Pruner Pro for self-hosted solver experiments.

The desired output is a Pareto curve—success versus total cost and latency—so users can choose their acceptable trade-off. A single maximum compression percentage is not an adequate product objective.

## Second-pass study: why the current workflow misses the mechanism

This reassessment combines three independent read-only reviews: primary research,
implementation, and benchmark design. The parent review checked their findings
against source and current primary pages. No new agent benchmarks were run;
the stopped 85-attempt evaluation remains the measured result.

### What the papers change about the implementation plan

CoACT operates on an observation after a tool produces it but **before the solver
receives it**. Its trained compressor replaces that observation; it does not add
a second description beside the original. It preserves the existing history
prefix. Next-action similarity is a training proxy, not a correctness guarantee.
[Method and deployment](https://arxiv.org/html/2607.02911v1)

LaMR separates learned semantic and dependency rubrics, then repairs structural
support. Our lexical ranker and static graph are a deterministic approximation
of evidence/support selection, not a reproduction of LaMR. Its ablations also
warn against stacking aggressive upstream compression before the learned pruner:
upstream retrieval should retain candidate recall.
[Architecture and ablations](https://arxiv.org/html/2605.15315v1)

The Complexity Trap has a newer v3 than the original review's v1. Its history
masking and hybrid policy require the scaffold to construct the actual solver
prompt. A memory endpoint alone does not enact either policy. Cache behavior and
summary-generation costs matter; rewriting old prefixes is not automatically the
first or cheapest intervention.
[Latest paper](https://arxiv.org/html/2508.21433v3)

Conditional retrieval has support in Repoformer's code-completion setting, but
that is not a repository-repair validation. Agent Retrieval Bench provides more
appropriate retrieval tasks and natural no-support examples; it finds that no
retrieval family wins every metric and that abstention calibration can fail.
These results motivate independent selection and abstention evaluation rather
than treating a positive BM25 score as permission to fill the context budget.
[Repoformer](https://arxiv.org/abs/2403.10059v2) ·
[Agent Retrieval Bench](https://arxiv.org/abs/2607.24882)

### Findings established from our code and saved artifacts

- The median dependency seed contains 3,000 source tokens but 9,903 tokens in the
  complete inline JSON. Lexical is 2,999 versus 6,874; full files are 20,189 versus
  26,666.5. These are exact local counts of saved initial prompts, not estimates
  of provider billing. Rich metadata and JSON escaping enlarge the prompt.
- `retrieve_evidence` already supports a response budget, but it is optional.
  Its final trimming removes spans after path sorting, so it can sever support
  groups. A tighter budget alone is not a quality-preserving fix.
- Deduplication also exists, but is opt-in and tracks exact `read` ranges only.
  Retrieval, expansion, overlapping reads and context resets are not integrated
  into a shared coverage policy.
- Python classes and JS top-level wrappers can be indivisible retrieval units.
  Preserving a large complete class can spend the entire budget; skipping it can
  hide a useful method. Complete evidence must be selected at useful granularity.
- The benchmark always supplies seed evidence and requires verify/read calls.
  Normal tools receive no seed or MCP server. This measures an eager additive
  workflow against a capable agent, rather than isolating observation replacement.
- `EvidenceSession.observe` accepts raw content in tool arguments. If the agent
  already saw that content, sending it back and returning another version does
  not erase the first copy. Without a configured scorer, it returns full text.
- `PruningSession.observe` is a usable internal boundary, but no production
  solver adapter calls it before native observations enter history. The Codex
  CLI adapter captures events after the subprocess finishes; event parsing cannot
  retroactively modify the CLI's internal conversation.
- Memory tools explicitly leave the hosted conversation unchanged. Recovery
  storage is useful, but its presence does not establish history savings.

Source: [selection and rendering](../src/contextlens/evidence.py),
[index units](../src/contextlens/evidence_index.py),
[session tools](../src/contextlens/evidence_session.py),
[middleware](../src/contextlens/pruning/runtime.py),
[CLI adapter](../src/contextlens/experiments/codex_cli.py), and
[measured report](../benchmarks/results/comprehensive.json).

The core diagnosis is an **integration mismatch**. Missing learned models do not
by themselves explain the negative benchmark: the earlier proposal explicitly
recommended deterministic integration first. We have not proved that omitted
dependencies caused individual patch failures. Full-file runs also fail, and
some failures are ordinary coding mistakes. Our gross provider-token increases
do not imply equal percentage increases in dollars; most input was cached.

### Revised implementation sequence

1. **Separate discovery from source delivery.** Default discovery returns a few
   paths, symbols, ranges and compact handles. Deliver exact bodies only when
   requested. If a task already identifies a path/range, read it directly; avoid
   a repository-wide seed. Keep uncertain or absent matches explicit.
2. **Introduce a compact solver-facing renderer.** Keep full hashes, provenance,
   reasons and metrics in local audit storage. Return verbatim source with short
   per-file headers and opaque handles. Budget the actual decoded text delivered
   to the solver, not only source bytes or outer transport JSON. Native clients
   may add schema/wrapper tokens, which remain separate overhead.
3. **Select evidence/support groups within the whole-response budget.** Rank
   candidates before spending tokens; avoid filling capacity merely because a
   score is positive. Preserve required high-confidence support with its seed,
   or expose an unresolved-support handle. Do not remove spans alphabetically.
   Index methods and nested functions with exact enclosing context.
4. **Make freshness checking part of a read.** Resolve a compact handle to its
   full stored hash and check current source internally. Return a concise stale
   response and a refresh action when changed. Snapshot recovery stays explicitly
   historical. Editing clients still need write-time version checks; a read
   cannot eliminate a later verification-to-edit race.
5. **Make repeat avoidance context-aware.** Track versioned intervals across
   retrieve/read/expand, but deduplicate only while the owner confirms the source
   is still visible in the current conversation epoch. Allow explicit rereads.
   Returning only a reference after the owner masked source could harm correctness.
6. **Connect the actual observation boundary.** A controlled agent scaffold must
   obtain raw tool output, store it locally, transform it once, and append only
   the returned observation. Short reads and uncertain transformations should
   pass through unchanged. An MCP-only integration can offer lean optional reads;
   it cannot promise interception of native shell reads or removal of history.
7. **Evaluate a released learned component before custom training.** CoACT's
   public merged checkpoint is a candidate, not a drop-in verified backend.
   Pin its revision, use bounded observations, record auxiliary compute, and
   keep generated summaries separate from exact source anchors. Training is a
   later option if the released model cannot meet the objective.
8. **Treat history management as a separate policy.** First preserve stable
   prefixes while reducing new observations. Add recoverable masking only in a
   scaffold with message ownership, and account for changed cache reuse.

These steps are engineering recommendations inferred from the reviewed work and
our audit. They are not implemented changes or predicted savings percentages.

### Available compute and evaluation boundaries

CoACT's public checkpoint revision is
`1b2d660dfa5fccf80a5e3c508a9f0d3c1930ccf5`. It is approximately 4.2B parameters
with 8.4 GB of BF16 weights. The author's CUDA/vLLM configuration is different
from this Windows environment. Short single-request inference on a free 16 GB T4
is plausible, not demonstrated; runtime compatibility and activation/cache memory
still need checking. The repository's code license does not by itself establish
the checkpoint's licensing terms. No weights were downloaded in this review.
[Model files](https://huggingface.co/Kndy666/CoACT/tree/1b2d660dfa5fccf80a5e3c508a9f0d3c1930ccf5) ·
[Author setup](https://github.com/THU-Agent/CoACT)

The next approved model evaluation should compare compact on-demand integration
with normal tools, then separately ablate eager seeds, selection and required
workflow calls. Separate code correctness from protocol compliance. Preserve
failed-attempt costs, and report gross/cached/uncached input, output, auxiliary
compute, tool turns and latency. Include navigation-heavy and multi-file tasks,
easy named-symbol tasks, no-support cases, and changed source.

The existing task-cluster bootstrap can become degenerate when opposite trial
outcomes cancel inside a task. A zero-width interval is not quality equivalence.
Future uncertainty must reflect repeated-trial variability and repository
correlation, with frozen verifiers and a declared quality tolerance. Existing
public historical tasks remain development diagnostics; tune on them without
presenting them as held-out confirmation. Further agent runs remain stopped.
