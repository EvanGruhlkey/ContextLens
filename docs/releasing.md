# Release process

ContextLens uses semantic versioning. A release is created only from a clean
revision that passes the complete local and CI validation matrix.

## Checklist

1. Confirm `CHANGELOG.md` contains the release version and date.
2. Keep `pyproject.toml` and `contextlens.__version__` synchronized.
3. Run:

   ```bash
   ruff check .
   mypy
   mypy --ignore-missing-imports benchmarks tests
   pytest
   python -m benchmarks.offline --output benchmarks/results/offline.json
   python -m build
   python -m twine check dist/*
   ```

4. Install the wheel in a fresh environment and run:

   ```bash
   contextlens --help
   contextlens prune --help
   ```

5. Confirm the offline harness still shows live pruning reducing injected
   tool-output tokens and compaction reducing final transcript tokens.
6. Confirm no report in `benchmarks/results/` or `docs/history/` contains a
   credential or a private repository path.
7. Create a signed version tag only after CI passes.
8. Publish artifacts using a trusted release workflow.
9. Verify package metadata and the CLI from the published package.

The repository does not contain publishing credentials. Local release
validation builds artifacts but never uploads them.
