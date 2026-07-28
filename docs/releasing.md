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
   pytest
   python benchmarks/adaptive_vs_exhaustive.py
   python -m build
   python -m twine check dist/*
   ```

4. Install the wheel in a fresh environment and run:

   ```bash
   contextlens --version
   contextlens --help
   ```

5. Confirm reports do not contain fixture secrets or raw private traces.
6. Confirm the adaptive benchmark retains the critical source and uses fewer
   experiments than exhaustive leave-one-out.
7. Create a signed version tag only after CI passes.
8. Publish artifacts using a trusted release workflow.
9. Verify package metadata and the CLI from the published package.

The repository does not contain publishing credentials. Local release
validation builds artifacts but never uploads them.

