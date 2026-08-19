# Invalid case-study attempts

These attempts are retained to demonstrate fail-closed behavior. They are not
included in causal performance results.

## Browser Use — redaction cascade

The retained cleanup-ACL attempt completed both model executions and both
hidden graders, but the candidate workspace contained a Windows sandbox-created
deny ACL under `.uv-cache`. Cleanup failed, so the pair was correctly classified
as infrastructure-invalid and excluded.

- [`browser-use-redaction-cascade-invalid-cleanup-acl.json`](browser-use/results/browser-use-redaction-cascade-invalid-cleanup-acl.json)
- [`browser-use-redaction-cascade-invalid-cleanup-acl.md`](browser-use/results/browser-use-redaction-cascade-invalid-cleanup-acl.md)

Earlier smoke artifacts for Infisical and Langfuse likewise remain in their
study result directories and are marked `INCONCLUSIVE`.
