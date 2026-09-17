# Focused evidence retrieval

Use deterministic retrieval before paying for neural pruning:

```sh
contextlens retrieve --root . --task "fix refresh-token timeout" --budget 2000
```

The command prints JSON containing complete Python top-level functions, classes,
and statements. Decorators, implementation bodies, original line endings, paths,
line ranges, and content hashes are retained. Matching uses lexical task terms
and paths, with conservative same-file name-based dependency expansion.

Each span includes a receipt for the original file snapshot. Expand a selected
file using the existing recovery command, even after its working copy changes:

```sh
contextlens recover RECEIPT_ID --start-line 1 --end-line 100
```

The budget measures retained source using UTF-8 bytes divided by four, rounded up
per span. It excludes the JSON envelope and is approximate. Metadata includes
omitted units and dependencies that exceeded the budget. Oversized units are
omitted whole; no pass stubs are synthesized. Empty results mean no matching unit
fit the budget, not proof that no relevant implementation exists.

This initial implementation requires Git, respects ignored untracked files, and
supports Python only. It does not resolve cross-file calls, dynamic dispatch,
or prove semantic completeness. Receipts recover captured bytes; check content
hashes against current files before applying edits. Parser/read failures are
listed in skipped entries. Neural scoring is not invoked by this command.
