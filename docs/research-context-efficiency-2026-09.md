# ContextLens research review and proposed direction

Research checked September 17, 2026. Scope: primary papers, author repositories, and official implementation documentation, including work published through September 15, 2026. This is a targeted literature review, not a systematic review or an independent reproduction of the cited results. “Best” below means my recommendation for ContextLens's constraints, not a universal benchmark winner.

## Decision

Develop ContextLens into a recoverable, task-aware evidence layer with three stages: retrieve relevant repository regions, preserve the code needed to act correctly, and manage what remains in the agent's working context. Keep the existing neural pruner as a selectable component.

The most useful combination to investigate is FastContext-style focused exploration, LaMR-style semantic and dependency selection, and CoACT-style behavior-preserving supervision. This combination is a proposed synthesis; the cited papers do not validate it as a combined system. Start with a deterministic implementation and establish a strong benchmark before training another model.

For hosted frontier models, operate at the tool boundary using exact source spans. For self-hosted models with accessible hidden states, evaluate in-model pruning separately. Do not make access to the solver's internals a requirement of the main product.

## Evidence most relevant to this decision

### 1. FastContext — June 2026: reduce exploration entering the solver

A specialized explorer searches the repository and returns file paths and line ranges. The paper reports up to 60% fewer main-agent tokens across its settings; that is not automatically the reduction in all system tokens. Its cost appendix includes a separate explorer audit. The authors provide models from 4B to 30B.

**Application:** add an optional discovery layer before file-read pruning. A small model can explore without forwarding its entire search history to the solver. Begin with ordinary lexical and symbol search; escalate only when necessary. The headline maximum is not a forecast for ContextLens.

[Paper, including cost audit](https://arxiv.org/html/2606.14066v2) · [Author repository linked by the paper](https://github.com/microsoft/fastcontext)

### 2. LaMR — May 2026: distinguish relevant evidence from necessary support

LaMR gives semantic evidence and dependency support separate learned scoring components, then fuses their decisions. Its SWE-bench Verified table reports Sonnet 4.5 token reduction of 30.5%, with success 70.6% to 71.8%; for Opus 4.6, savings are only 3.0%, with success 75.6% to 76.0%. It uses a 0.6B backbone and Python-centered evaluation. Training used eight GPUs; a ready-to-use public checkpoint was not verified in this review.

**Application:** this is the closest architectural match to ContextLens's observed failure. A helper body can be necessary even when its identifiers have low similarity to the task. Borrow the two-part objective before committing to its training recipe.

[Paper and result table](https://arxiv.org/html/2605.15315v1)

### 3. CoACT — July 2026: train against behavioral damage

CoACT trains a 4B compressor using candidate compressions rewarded for shortening observations while preserving the next action. In a 200-instance SWE-bench Verified sample, Qwen3.5-35B-A3B used 36% fewer total tokens and pass@1 rose from 57.0% to 60.5%; Deepseek-v4-Pro used 19% fewer tokens but fell from 76.5% to 75.0%. In the same comparison, SWE-Pruner increased total tokens by 17–18%. Next-action similarity is a proxy, not a guarantee of final correctness.

**Application:** supervise which evidence to keep using successful behavior and eventual tests, rather than similarity alone. Preserve exact spans with ContextLens's renderer even if a teacher proposes free-form compression.

[Paper, Tables I–II](https://arxiv.org/html/2607.02911v1) · [Official implementation](https://github.com/THU-Agent/CoACT)

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

Build **focused retrieval + complete evidence spans + explicit recovery + an end-to-end quality/cost harness**. Then compare LaMR-inspired selection and CoACT-inspired training. Evaluate FastContext as an optional explorer, and reserve SWE-Pruner Pro for self-hosted solver experiments.

The desired output is a Pareto curve—success versus total cost and latency—so users can choose their acceptable trade-off. A single maximum compression percentage is not an adequate product objective.

