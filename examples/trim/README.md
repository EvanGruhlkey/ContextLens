# Trim example

Apply the sample policy without calling a model:

```bash
contextlens trim examples/trim/context.json \
  --policy examples/trim/context-policy.json \
  --output runs/prompt-context.json \
  --audit-output runs/context-savings.json \
  --min-reduction 0.80 \
  --max-tokens 1000 \
  --strict
```

Use `--dry-run` and omit `--output` to verify the thresholds without writing
artifacts.
