"""Explicit experimental T4 runner using memory-efficient SDPA.

Keep the official 8,192-token window, checkpoint and pruning head unchanged.
Expand grouped keys/values for the efficient kernel's equal-head requirement.
Kernel changes can alter floating-point scores; these results must be labeled
separately from the default runtime and do not establish task quality.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import torch
import torch.nn.functional as functional
from torch.nn.attention import SDPBackend, sdpa_kernel

from benchmarks import pruning_runtime


@contextmanager
def use_efficient_sdpa():
    """Scope the experimental kernel override to the current process operation."""
    original = functional.scaled_dot_product_attention

    def efficient(query, key, value, *args, **kwargs):
        if kwargs.pop("enable_gqa", False):
            key = key.repeat_interleave(query.size(-3) // key.size(-3), dim=-3)
            value = value.repeat_interleave(query.size(-3) // value.size(-3), dim=-3)
        with sdpa_kernel(SDPBackend.EFFICIENT_ATTENTION):
            return original(query, key, value, *args, **kwargs)

    functional.scaled_dot_product_attention = efficient
    try:
        yield
    finally:
        functional.scaled_dot_product_attention = original


def main() -> int:
    if not torch.cuda.is_available():
        raise RuntimeError("The experimental T4 runner requires CUDA")
    torch.cuda.reset_peak_memory_stats()
    with use_efficient_sdpa():
        status = pruning_runtime.main()
        if "--output" in sys.argv:
            output = Path(sys.argv[sys.argv.index("--output") + 1])
            report = json.loads(output.read_text(encoding="utf-8"))
            report["runtime_variant"] = "experimental_efficient_sdpa_expanded_gqa"
            report["official_window_tokens"] = 8192
            report["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated()
            report["numerical_equivalence_verified"] = False
            output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return status


if __name__ == "__main__":
    raise SystemExit(main())
