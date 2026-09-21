# Experiments

Retired research code. Nothing here is imported by `src/contextlens`, shipped
in the wheel, exercised by the test suite, or reachable from the CLI or the MCP
server. It is kept because the measurements that retired it are worth keeping,
not because the code still works against the current package.

| Directory | What it was | Why it is not in the product |
| --- | --- | --- |
| [`structural_expansion/`](structural_expansion/) | Deterministic Python AST closure that re-added imports, class headers, scopes, and referenced constants around whatever Jev kept in a source read | On the 21 September 2026 paired coding-agent run it produced pathological agent trajectories: **1,105,615** coding-model input tokens against **583,459** for the baseline and **379,716** for Jev filtering alone |

Earlier architectures that were deleted outright -- a `context_next` action
controller, bounded next-action routing, controller sessions, a repository
evidence index with adaptive search, SWE-Pruner neural scoring, and the
observation working set with mandatory garbage collection -- are recoverable
from Git history. Their measured results are preserved under
[`docs/history/`](../docs/history/).
