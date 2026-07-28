# Trace format 1.0

A ContextLens trace is UTF-8 JSON Lines. The first non-empty line is a
`trace_started` record. Every subsequent line is a `context_added` record.
Unknown event types are rejected in version 1.0.

## Header

```json
{"event":"trace_started","schema_version":"1.0","trace_id":"7de...","created_at":"2026-07-26T12:00:00+00:00","producer":"contextlens","producer_version":"0.1.0","metadata":{}}
```

## Context event

```json
{"event":"context_added","schema_version":"1.0","request_id":"request-1","sequence":0,"recorded_at":"2026-07-26T12:00:01+00:00","source":{"source_id":"agents-md","kind":"agent_instruction","name":"AGENTS.md","content":"Run tests before committing.","token_count":5,"token_count_method":"example","provenance":{"path":"AGENTS.md"},"tags":["repository"]}}
```

`sequence` starts at zero independently for every `request_id`. A `source_id`
identifies the unit removed by a single-source ablation and should therefore
remain stable across equivalent replays.

Exactly one of `content` and `content_ref` must be present. A reference uses:

```json
{"digest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","byte_length":42,"media_type":"text/plain; charset=utf-8"}
```

The built-in local artifact store maps this digest to
`sha256/01/23456789abcdef...` beneath its configured root and verifies the
length and digest whenever content is read.

## Compatibility

Readers currently require an exact `1.0` schema match. Future compatible fields
may be added in a new version, accompanied by migration tooling. Traces should
be treated as immutable experimental inputs.
