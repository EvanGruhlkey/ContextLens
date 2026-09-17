"""Apply documented verifier corrections uniformly, preserving initial scores."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.evidence_agent import dump, verify
from evals.repository_cases import load_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    root = args.input.resolve().parent
    report = json.loads(args.input.read_text(encoding="utf-8"))
    revisions = json.loads(
        (root / "verification-revisions.json").read_text(encoding="utf-8")
    )
    cases = {}
    project = Path(__file__).resolve().parents[1]
    for revision in revisions:
        name = revision["case"]
        paths = list((project / "evals" / "cases").rglob("*.yaml"))
        manifest = next(
            load_manifest(p) for p in paths if load_manifest(p).case_id == name
        )
        if (
            hashlib.sha256(manifest.path.read_bytes()).hexdigest()
            != revision["revised_manifest_sha256"]
        ):
            raise ValueError("verifier changed without an audit revision")
        cases[name] = (manifest, revision)
    for row in report["rows"]:
        if row["case"] not in cases:
            continue
        manifest, revision = cases[row["case"]]
        run_dir = root / f"{row['case']}-{row['trial']}-{row['policy']}"
        cached = run_dir / "rescored.json"
        if cached.exists():
            revised = json.loads(cached.read_text(encoding="utf-8"))
            if revised["manifest_sha256"] != revision["revised_manifest_sha256"]:
                raise ValueError("cached regrade used a different checker")
        else:
            revised = {
                "manifest_sha256": revision["revised_manifest_sha256"],
                "verification": verify(run_dir / "workspace", manifest.verification),
            }
            dump(cached, revised)
        row["original_verification"] = row["verification"]
        row["verification"] = revised["verification"]
        row["verifier_regraded_offline"] = True
    report["verification_revisions"] = revisions
    dump(root / "report-scored.json", report)
    print(
        json.dumps(
            {
                "regraded": sum(
                    r.get("verifier_regraded_offline", False) for r in report["rows"]
                ),
                "attempts": len(report["rows"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
