"""Paired pilot analysis and conservative deployment decisions."""

from __future__ import annotations

import math
import random
from typing import Any


def analyze_pairs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["case"], row["trial"]), {})[row["policy"]] = row
    pairs = []
    regressions = []
    improvements = []
    for (case, trial), conditions in groups.items():
        if "full" not in conditions or "dependency" not in conditions:
            continue
        full, candidate = conditions["full"], conditions["dependency"]
        if full["status"] != "completed" or candidate["status"] != "completed":
            continue
        before, after = (
            full["verification"]["success"],
            candidate["verification"]["success"],
        )
        pair = {
            "case": case,
            "trial": trial,
            "full_success": before,
            "candidate_success": after,
            "success_difference": int(after) - int(before),
        }
        pairs.append(pair)
        if before and not after:
            regressions.append(pair)
        elif after and not before:
            improvements.append(pair)
    discordant = len(regressions) + len(improvements)
    p_value = (
        min(
            1.0,
            2
            * sum(
                math.comb(discordant, k)
                for k in range(min(len(regressions), len(improvements)) + 1)
            )
            / 2**discordant,
        )
        if discordant
        else 1.0
    )
    rng = random.Random(731)
    differences = [p["success_difference"] for p in pairs]
    bootstrap = (
        sorted(
            sum(rng.choices(differences, k=len(differences))) / len(differences)
            for _ in range(2000)
        )
        if differences
        else []
    )
    valid = (
        bool(pairs)
        and len(pairs) == len(groups)
        and all(row["status"] == "completed" for row in rows)
    )
    usage_complete = bool(rows) and all(
        row.get("input_tokens") is not None and row.get("output_tokens") is not None
        for row in rows
    )
    full_tokens = (
        sum(
            row["input_tokens"] + row["output_tokens"]
            for row in rows
            if row["policy"] == "full"
        )
        if usage_complete
        else None
    )
    candidate_tokens = (
        sum(
            row["input_tokens"] + row["output_tokens"]
            for row in rows
            if row["policy"] == "dependency"
        )
        if usage_complete
        else None
    )
    economical = usage_complete and candidate_tokens < full_tokens
    return {
        "complete_pairs": len(pairs),
        "pairs": pairs,
        "observed_quality_regressions": regressions,
        "observed_quality_improvements": improvements,
        "mcnemar_exact_two_sided_p": p_value,
        "paired_trial_bootstrap_success_difference_interval": [
            bootstrap[49],
            bootstrap[1949],
        ]
        if bootstrap
        else None,
        "interval_caveat": "Repeated trials share tasks. This interval does not "
        "establish repository-level non-inferiority.",
        "total_provider_tokens_full": full_tokens,
        "total_provider_tokens_candidate": candidate_tokens,
        "provider_token_reduction": 1 - candidate_tokens / full_tokens
        if full_tokens and candidate_tokens is not None and valid
        else None,
        "deployment_policy": "dependency_experimental"
        if valid and not regressions and economical
        else "full",
        "deployment_decision_scope": "pilot_only_not_quality_certification",
        "statistical_quality_preservation_claim": None,
        "dollar_savings_claim": None,
    }
