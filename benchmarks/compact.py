"""Small CPU evidence-delivery benchmark; no inference or network calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from contextlens.context_tools import RepositoryContext


def fixture(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    noise = "\n".join(f"UNRELATED_{i} = 'irrelevant value {i}'" for i in range(300))
    (root / "auth.py").write_text(
        "TIMEOUT = 30\n\ndef refresh_token():\n    return TIMEOUT\n" + noise,
        encoding="utf-8",
    )
    (root / "client.py").write_text(
        "class Client:\n    TIMEOUT = 30\n"
        "    def refresh_token(self):\n        return self.TIMEOUT\n"
        + "".join(
            f"    def unrelated_{i}(self):\n        return {i}\n" for i in range(100)
        ),
        encoding="utf-8",
    )
    (root / "helpers.py").write_text(
        "LIMIT = 30\n\ndef helper():\n    return LIMIT\n"
        "\ndef refresh_token():\n    return helper()\n" + noise,
        encoding="utf-8",
    )


def measure(
    root: Path,
    state: Path,
    path: str,
    symbol: str,
    required: tuple[str, ...],
    repeats: int,
    kind: str,
) -> dict[str, object]:
    service = RepositoryContext(root, state, encoding="o200k_base")
    source = (root / path).read_bytes().decode("utf-8")
    # Compare equally cited exact text, not a JSON legacy bundle.
    full = service.call("read", {"path": path, "budget": 16000})
    if full.startswith("Evidence "):
        raise RuntimeError(f"full-file baseline exceeds budget: {path}")
    elapsed: list[float] = []
    counts: list[int] = []
    for _ in range(repeats):
        started = time.perf_counter()
        found = service.call("find", {"query": symbol, "focus": path, "limit": 1})
        if len(found.splitlines()) < 2:
            raise RuntimeError(f"discovery failed: {found}")
        handle = found.splitlines()[1].split()[0]
        if f" {path}:" not in found.splitlines()[1]:
            raise RuntimeError(f"unexpected first match for {path}: {found}")
        evidence = service.call("read", {"handle": handle, "budget": 16000})
        elapsed.append((time.perf_counter() - started) * 1000)
        if any(anchor not in evidence for anchor in required):
            raise RuntimeError(f"required evidence missing: {path}")
        if "UNRELATED_" in evidence or "def unrelated_" in evidence:
            raise RuntimeError(f"unrelated fixture source leaked: {path}")
        counts.append(service.count(found) + service.count(evidence))
    if len(set(counts)) != 1:
        raise RuntimeError("non-deterministic returned-text token counts")
    return {
        "case": f"{kind}: {path} / {symbol}",
        "kind": kind,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "full_read_tokens": service.count(full),
        "find_plus_read_tokens": counts[0],
        "returned_text_reduction_percent": round(
            (1 - counts[0] / service.count(full)) * 100, 1
        ),
        "cold_ms": round(elapsed[0], 1),
        "warm_median_ms": round(statistics.median(elapsed[1:]), 1),
        "required_anchors": list(required),
        "evidence_check": "passed",
        "repeats": repeats,
    }


def run(root: Path, repeats: int = 3) -> dict[str, object]:
    if repeats < 2:
        raise ValueError("at least two repeats are needed for warm latency")
    with tempfile.TemporaryDirectory(prefix="contextlens-compact-") as temporary:
        work = Path(temporary)
        synthetic = work / "fixture"
        fixture(synthetic)
        cases = [
            (
                synthetic,
                "auth.py",
                "refresh_token",
                ("def refresh_token", "TIMEOUT = 30"),
                "fixture",
            ),
            (
                synthetic,
                "client.py",
                "refresh_token",
                ("class Client:", "def refresh_token", "TIMEOUT = 30"),
                "fixture",
            ),
            (
                synthetic,
                "helpers.py",
                "refresh_token",
                ("def refresh_token", "def helper", "LIMIT = 30"),
                "fixture",
            ),
            (
                root,
                "src/contextlens/context_tools.py",
                "_subtract",
                ("def _subtract", "remaining.append"),
                "repository",
            ),
            (
                root,
                "src/contextlens/context_index.py",
                "_scope_declarations",
                ("def _scope_declarations", "ast.Global", "ast.Nonlocal"),
                "repository",
            ),
        ]
        rows = [
            measure(repo, work / f"state-{i}", path, symbol, anchors, repeats, kind)
            for i, (repo, path, symbol, anchors, kind) in enumerate(cases)
        ]
    return {
        "benchmark": "compact_evidence_delivery",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repository_revision": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "encoding": "o200k_base",
        "limits": (
            "Returned text and required source anchors only; no solver, "
            "fix accuracy, provider tokens or dollar savings measured."
        ),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/results/compact.json")
    )
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    report = run(args.root.resolve(), args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
