from __future__ import annotations

import unittest
from dataclasses import replace

from contextlens.experiments import Evaluation, ReplayResult, ReplayStatus
from contextlens.optimization import (
    ContextCandidate,
    OptimizationObjective,
    VerifiedConfiguration,
)
from contextlens.policy import PolicyStrategy, policy_from_verified_configuration
from contextlens.trace import ContextSource, SourceKind


def source(source_id: str, name: str) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=SourceKind.FILE,
        name=name,
        content=f"Content for {name}.",
        token_count=4,
        token_count_method="fixture",
    )


def verified_configuration(*, accepted: bool = True) -> VerifiedConfiguration:
    candidate = ContextCandidate(
        candidate_id="candidate",
        removed_source_ids=("stale",),
        retained_source_ids=("current",),
        retained_tokens=4,
        removed_tokens=4,
        individually_verified_removals=("stale",),
        predicted_removals=(),
        objective=OptimizationObjective.MIN_COST,
        rationale=("verified removal",),
    )
    result = ReplayResult(
        run_id="verification-run",
        task_id="case",
        variant_id="candidate",
        removed_source_ids=("stale",),
        status=ReplayStatus.COMPLETED,
        attempt=1,
        duration_seconds=1,
        context_source_ids=("current",),
        context_tokens=4,
    )
    return VerifiedConfiguration(
        candidate=candidate,
        accepted=accepted,
        baseline_score=1,
        candidate_score=1,
        quality_change=0,
        replay_result=result,
        evaluation=Evaluation(scores={"quality": 1}),
        rejection_reasons=() if accepted else ("quality regression",),
        baseline_objective_value=1,
        candidate_objective_value=1,
        objective_improvement=0,
    )


class VerifiedPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = (
            source("current", "Current instructions"),
            source("stale", "Stale instructions"),
        )

    def test_accepted_configuration_excludes_only_verified_removals(self) -> None:
        policy = policy_from_verified_configuration(
            self.context,
            verified_configuration(),
            objective="cost_without_regression",
        )

        rules = {rule.parameters["source_id"]: rule for rule in policy.context.values()}
        self.assertEqual(rules["current"].strategy, PolicyStrategy.ALWAYS_INCLUDE)
        self.assertEqual(rules["stale"].strategy, PolicyStrategy.EXCLUDE)
        self.assertEqual(
            rules["stale"].parameters["content_hash"],
            self.context[1].content_hash,
        )
        self.assertEqual(
            rules["stale"].parameters["verification_run_id"],
            "verification-run",
        )
        self.assertEqual(rules["stale"].parameters["evidence_scope"], "target_model")
        self.assertEqual(policy.objective, "cost_without_regression")

    def test_rejected_configuration_never_emits_exclusions(self) -> None:
        policy = policy_from_verified_configuration(
            self.context,
            verified_configuration(accepted=False),
        )

        self.assertTrue(
            all(
                rule.strategy is PolicyStrategy.NEEDS_MORE_EVIDENCE
                for rule in policy.context.values()
            )
        )

    def test_requires_a_disjoint_complete_candidate_partition(self) -> None:
        verified = verified_configuration()
        overlap = replace(
            verified.candidate,
            retained_source_ids=("current", "stale"),
        )
        with self.assertRaisesRegex(ValueError, "overlap"):
            policy_from_verified_configuration(
                self.context,
                replace(verified, candidate=overlap),
            )

        incomplete = replace(verified.candidate, retained_source_ids=())
        with self.assertRaisesRegex(ValueError, "partition"):
            policy_from_verified_configuration(
                self.context,
                replace(verified, candidate=incomplete),
            )


if __name__ == "__main__":
    unittest.main()
