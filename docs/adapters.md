# Telemetry and agent adapters

ContextLens does not require its JSONL trace schema for `scan`, `diff`, or the
generic subprocess verification path.

## Agent adapter contract

The subprocess adapter receives an exact `ReplayRequest` through
`CONTEXTLENS_REQUEST` and writes an `AgentOutcome` through
`CONTEXTLENS_RESULT`. It can wrap an existing agent CLI, API harness, or local
deterministic tool.

The request includes task, context version, model settings, isolated workspace,
and timeout. The result can include provider tokens, cache tokens, cost,
commands, tests, tool calls, retries, and extensible behavior/latency metadata.

An adapter used for context regression must not silently load another copy of
repository instructions. Otherwise base and candidate runs do not differ only
by the declared context intervention.

The built-in Codex CLI adapter disables ambient rules and renders the supplied
context explicitly. Existing custom adapters remain supported through the
`AgentAdapter` protocol.

## Provider usage normalization

`normalize_provider_usage()` accepts common shapes:

- OpenAI-style `input_tokens`, `input_tokens_details.cached_tokens`,
  `output_tokens`, and `output_tokens_details.reasoning_tokens`;
- Anthropic-style `input_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`, and `output_tokens`;
- generic `input`, `cached`, and `output` names.

It returns distinct total input, cached input, uncached input, cache-write
input, visible output, and reasoning categories. Missing fields remain `None`.

## Existing ContextLens traces

The versioned trace model remains the richest native ingestion path. It stores
ordered context events plus optional agent-run and model/tool/evaluation steps.
Use `contextlens profile` for deterministic one-run analysis and the existing
SQLite store for normalized local queries.

## OpenTelemetry direction

The normalization layer is intentionally separate from replay orchestration so
an OpenTelemetry adapter can map GenAI agent spans and provider usage into the
same internal evidence without requiring agent reinstrumentation. Relevant
upstream specifications:

- [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)
- [GenAI agent spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)

The current release provides the normalization interface, not a complete OTLP
collector or observability backend. `normalize_otel_genai_attributes()` accepts
the current `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
`gen_ai.usage.cache_read.input_tokens`, and
`gen_ai.usage.cache_creation.input_tokens` span attributes. Per the upstream
convention, total input includes cache categories; ContextLens makes the cost
categories mutually exclusive during normalization.
