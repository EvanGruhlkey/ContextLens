# ContextLens

ContextLens removes low-value lines from coding-task observations before they
enter the next model call. It conditions every decision on the current task,
then restores the definitions, imports, scopes, and control-flow structure
needed to use the selected evidence.

The result is smaller working context without turning source into disconnected
snippets.

## Core idea

A semantic scorer answers one question:

> Which lines directly help with the current task?

The structural pass answers a different question:

> Which additional lines are required to understand or safely use them?

For example, a timeout task may directly select `refresh_token()` while the
structural pass retains `BaseTransport`, `RetryConfig`, the enclosing method,
and relevant branch headers. ContextLens records the reasons independently
instead of collapsing both signals into one relevance score.

```text
task + observation
        |
        v
semantic line scores
        |
        v
Python dependency closure
  - imports and local definitions
  - enclosing scopes and decorators
  - branch and exception structure
  - bounded dependency hops
        |
        v
parseable source skeleton + recovery receipt
```

## Install

ContextLens currently requires Python 3.11 or newer.

```bash
python -m pip install -e ".[dev]"
```

Run a line-scoring backend that implements the released SWE-Pruner `/prune`
contract, then point ContextLens at it:

```bash
set CONTEXTLENS_BACKEND_URL=http://127.0.0.1:8000/prune
```

On PowerShell:

```powershell
$env:CONTEXTLENS_BACKEND_URL = "http://127.0.0.1:8000/prune"
```

## Prune an observation

```bash
contextlens prune \
  --task "Fix the OAuth refresh-token timeout" \
  --input src/oauth/client.py
```

Add a narrower focus when the task changes during a run:

```bash
contextlens prune \
  --task "Fix the OAuth refresh-token timeout" \
  --focus "Trace retry options passed into the refresh request" \
  --tool read_file \
  --argument path=src/oauth/client.py \
  --input src/oauth/client.py \
  --json
```

The JSON result includes retained-line reasons, exact omitted ranges, measured
reduction, backend identity, latency, and a content-addressed receipt ID.

## Recover omitted source

Every observation is saved locally before pruning. Recover the complete source:

```bash
contextlens recover cl_0123456789abcdef01234567
```

Or recover only an omitted range:

```bash
contextlens recover cl_0123456789abcdef01234567 \
  --start-line 80 \
  --end-line 130
```

Receipts default to `.contextlens/receipts`. Skeleton markers include the
receipt ID and original line range, so a runtime can fetch detail on demand.

## Local service

```bash
contextlens serve --host 127.0.0.1 --port 8765
```

Endpoints:

- `GET /health`
- `POST /v1/prune`
- `POST /v1/recover`

Example request:

```json
{
  "task": "Fix the OAuth refresh-token timeout",
  "content": "from transport import BaseTransport\n...",
  "kind": "code",
  "language": "python",
  "threshold": 0.5,
  "minimum_tokens": 256,
  "dependency_hops": 2,
  "context_radius": 1
}
```

The service binds to loopback by default and limits request bodies to 16 MiB.

## Safety behavior

ContextLens keeps the original observation when:

- the observation is already below the configured minimum;
- the language or observation type is not yet supported;
- semantic scoring fails;
- Python parsing or output validation fails; or
- omission markers would cost at least as many tokens as the original lines.

Generated Python skeletons are parsed again before release. Structural repair
is deterministic and bounded; semantic scoring can be replaced independently.

## Current scope

The first runtime path supports Python source. Search output, logs, JSON, and
plain text are represented in the request schema but currently pass through
unchanged. Their structural rules will be added only with task-level retention
tests, rather than treating every format as generic text.

## Development

```bash
python -m pytest -q
ruff check src tests
mypy
```

## Method references

- [SWE-Pruner: Self-Adaptive Context Pruning for Coding Tasks](https://arxiv.org/abs/2601.16746)
- [LaMR: Layer-Aware Modeling and Repair for Context Pruning](https://arxiv.org/abs/2605.15315)

ContextLens implements the reusable method: task-conditioned semantic evidence,
separate dependency support, syntax-aware repair, and recoverable omissions.
It does not copy paper prose or bundle paper checkpoints.

## License

ContextLens is released under the [MIT License](LICENSE).
