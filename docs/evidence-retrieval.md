# Versioned evidence retrieval

ContextLens first retrieves complete source units using local BM25-style ranking,
then follows static dependencies before spending the remaining source budget on
lower-ranked matches. No inference service is required for this path.

```sh
uv pip install -e ".[dev,evidence]"
contextlens retrieve --root . --task "fix refresh-token timeout" --budget 3000
contextlens mcp --root . --state .contextlens --encoding o200k_base
```

Python AST units retain decorators and full function/class bodies. Optional
Tree-sitter grammars provide JavaScript and TypeScript units and relative-import
resolution. Unsupported text and unavailable JS/TS grammars fall back to complete
files. Invalid Python is explicitly skipped. Git file discovery respects ignored
untracked files; files larger than 1 MiB are skipped. Static analysis resolves
conservative aliases and module attributes, including cross-file imports, and
reports unresolved imports and dependencies that exceed the budget. Dynamic
calls and ambiguous bindings remain limitations: semantic completeness is never
asserted.

Every selected span contains its path, original line range, content hash and
receipt ID. Content-addressed parse caching is invalidated by source edits and
parser-version changes. Receipts preserve original UTF-8 source and line endings
and validate their contents on recovery. Hash verification rejects stale current
source; snapshot expansion deliberately returns historical bytes.

## Agent tools

Configure a stdio MCP server with command `contextlens` and arguments
`["mcp", "--root", "/absolute/repository", "--state", "/absolute/state",
"--encoding", "o200k_base"]`. Keep the state directory outside the repository or
use `.contextlens`, which discovery excludes. The server exposes:

- `evidence_retrieve`: ranked, budgeted evidence with dependency diagnostics.
- `evidence_read`: current-source range, optional expected hash and opt-in deduplication.
- `evidence_expand`: exact receipt recovery, including after working-copy changes.
- `evidence_verify`: reject stale source before edits.
- `evidence_remember`: externalize an observation as a recoverable handle.
- `evidence_view`: bounded memory prioritizing pinned and recent observations.
- `evidence_observe`: optional neural observation pruning; unconfigured mode retains source.

Memory persistence stores handles rather than complete observations. These tools
cannot remove messages already sent to a hosted model. Agents that own their
conversation must explicitly substitute the bounded view during compaction.
Deduplication is opt-in because a caller may need source again after compaction.
Per-call JSONL accounting records operation, latency and token counts without
logging source bodies.

## Budgets and optional inference

`--budget` counts retained source only. `--response-budget` also bounds the JSON
representation and reports removed spans as response-budget omissions requiring
expansion. Counts default to the approximate UTF-8-bytes/4 method; an explicit
encoding uses exact local tokenization for that encoding, which is not a claim
about any provider's tokenizer. `response_tokens` counts `json.dumps` serialization
without pretty-print indentation. Omissions, skipped files and unresolved imports
are summarized with bounded previews and full counts.

Policies are `dependency`, `lexical` and `full`. The full-file comparison uses the
three highest-ranked matching files, preserving the same discovery index; it is
not an exhaustive repository dump. Production dependency retrieval remains
experimental pending broader task-quality evaluation.

The existing SWE-Pruner backend can optionally run locally or via HTTP for tool
observations. Explicit checkpoint identifiers and an opt-in bounded score cache
are supported. Change the cache namespace when checkpoint/runtime settings change.
No newly trained CoACT or LaMR model is included. Training a replacement requires
separate data, compute and evaluation; published methods are research directions,
not implemented performance claims.
