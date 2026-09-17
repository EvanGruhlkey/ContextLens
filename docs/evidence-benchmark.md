# Live evidence agent pilot — 2026-09-17

Full-file retrieval passed 9/9 runs. Dependency retrieval passed 8/9 and used 20.8% fewer total provider input/output tokens. The quality gate rejected dependency retrieval for default deployment. Full-file retrieval is now the CLI, MCP and library default; dependency and neural pruning remain explicit experimental options.

This is an observed regression, not proof that compression caused the failed patch. The failed tslib trial changed both implementations but still concatenated the non-spreadable array-like object instead of its elements.

## Measurements

Full-file: 3,609,218 total tokens; dependency: 2,857,950. These totals include cached input. Uncached input was 484,337 versus 378,149.

- spotify-luigi-bool-default: full 3/3, dependency 3/3; total token reduction 44.3%; median wall time 53.7s versus 70.1s.
- spotify-luigi-run-arguments: full 3/3, dependency 3/3; total token reduction 14.1%; median wall time 60.1s versus 67.2s.
- microsoft-tslib-spread-array: full 3/3, dependency 2/3; total token reduction -15.9%; median wall time 59.9s versus 70.9s.

## Method and limits

Three pinned historical tasks, three trials each, two conditions: 18 actual agent runs with gpt-5.6-luna. Each condition starts from a fresh checkout; order is randomized. Both must exercise live hash verification and current-source reads. All unmodified tasks fail with assertion errors; verification commands are withheld from the model prompt. The tslib verifier checks CommonJS, ESM, ordinary arrays and packed sparse arrays. Checks are focused rather than complete project test suites. Public historical tasks may overlap model training data.

Full-file uses the same discovery/ranking index and its top three matching files with a 30,000-source-token budget. Dependency uses 3,000 tokens; this compares whole policies, including budget effects, rather than a controlled dependency-only ablation. The CPU ablation separately compares full, lexical and dependency retrieval over 27 runs; response metadata can erase much of the source reduction on small files.

Provider usage includes all agent turns, rereads, failed tool attempts and recovery. Cached tokens are recorded separately; local o200k_base counts describe seed/tool payloads and do not assert provider tokenizer equivalence. No paid API calls or new trained model were used. Existing subscription capacity and one explicitly authorized free reset credit were consumed. Dollar cost/savings are unavailable.

One regression and this sample size prevent any quality-preservation claim. Token reductions also do not establish latency improvements. The conservative production default uses the tested full-file policy while experimental compression remains available for further evaluation.

## Artifacts

- [Complete paired report](../benchmarks/results/evidence-agent-pilot.json).
- [CPU retrieval ablation](../benchmarks/results/evidence-retrieval-cpu.json).
- Local raw prompts, patches, JSONL events, baseline checks and tool accounting: `evals/artifacts/evidence-agent-suite/`.
- [Implemented architecture and limits](evidence-architecture.md).
