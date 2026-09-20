# Integrated controller benchmark — September 20, 2026

The live benchmark exercised the full bounded control cycle over three fixed
decision states: retain compact observations, filter offered capabilities, and
choose the next action. One case re-evaluated after a new repository-search
observation; another routed a post-patch test failure.

- Top-1 action accuracy: 3/3 (100%)
- Provider fallbacks: 0
- Jev calls: 8
- Jev input tokens: 4,179
- Jev output tokens: 437
- Combined gateway latency: 2,923 ms
- Vercel-reported cost: $0
- Observations incorrectly deferred: 0

Every action was selected from the offered typed candidates. The controller did
not execute an action, generate a command, or edit source. The second decision
used the observation created after the first decision, demonstrating the
observe/decide loop rather than a fixed up-front plan.

This is a small development benchmark with known labels. It does not run a
coding model or establish patch quality, general action accuracy, or complete
trajectory savings. `contextlens.trajectory_evaluation` now provides paired
whole-run accounting that includes coding-model tokens, all Jev tokens, tool
calls, recoveries, rereads, duration, tests, and patch identity. A future live
paired coding run must use that accounting before the project claims savings.

Reproduce it with:

```powershell
$env:AI_GATEWAY_API_KEY = "your-vercel-ai-gateway-key"
python -m benchmarks.controller_loop `
  --output benchmarks/results/controller-loop.json
```

The recorded result is in
[`benchmarks/results/controller-loop-2026-09-20.json`](../benchmarks/results/controller-loop-2026-09-20.json).
