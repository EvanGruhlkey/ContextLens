# Contributing to ContextLens

ContextLens is at a pre-alpha stage. Design discussions, trace-format examples,
adapter prototypes, evaluators, and reproducible benchmark tasks are welcome.

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
- Do not commit real traces containing private prompts, source code, credentials,
  personal data, or customer data.
- New trace fields require a schema-compatibility decision and tests.
- Evaluators should preserve evidence and explain how scores were produced.
- Performance claims should include the task suite, sample size, model settings,
  and uncertainty.
- Avoid network access in the default test suite.

Open an issue before making a breaking schema change or adding a required
runtime dependency.

## Pull requests

Keep changes focused, add tests for behavior, and update user-facing
documentation when interfaces change. By contributing, you agree that your
contributions are licensed under the MIT License.
