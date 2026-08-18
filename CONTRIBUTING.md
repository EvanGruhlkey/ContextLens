# Contributing to ContextLens

ContextLens is at a pre-alpha stage. Discovery conventions, telemetry adapters,
mechanical eval tasks, replay isolation, and reproducible context-regression
studies are welcome.

## Development setup

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
pytest
ruff check .
mypy
```

## Contribution boundaries

- Keep the core provider-neutral; provider SDKs belong in optional integrations.
- Keep static findings explicitly non-causal and fail closed on verified
  quality regressions.
- Report initial context separately from provider input and prompt-cache usage.
- Do not commit real traces containing private prompts, source code, credentials,
  personal data, or customer data.
- New trace fields require a schema-compatibility decision and tests.
- Evaluators should preserve evidence and explain how scores were produced.
- Performance claims should include the task suite, sample size, model settings,
  and uncertainty.
- Avoid network access in the default test suite.

Open an issue before making a breaking schema change or adding a required
runtime dependency.

## Repository metadata

Recommended GitHub description:

> CI and regression testing for AI-agent context. Measure whether AGENTS.md,
> skills, MCP tools, and agent instructions improve performance or waste tokens.

Recommended topics: `coding-agents`, `context-engineering`, `agents-md`,
`llm-evaluation`, `ai-agents`, `developer-tools`, `prompt-engineering`, `mcp`,
and `llm-observability`.

## Pull requests

Keep changes focused, add tests for behavior, and update user-facing
documentation when interfaces change. By contributing, you agree that your
contributions are licensed under the MIT License.
