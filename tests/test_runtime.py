from __future__ import annotations

from contextlens.policy import ContextPolicy, PolicyRule, PolicyStrategy
from contextlens.runtime import apply_context_policy
from contextlens.trace import ContextSource, SourceKind


def _source(source_id: str, name: str, tokens: int) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=SourceKind.FILE,
        name=name,
        content=f"content for {name}",
        token_count=tokens,
        token_count_method="fixture",
    )


def test_runtime_applies_verified_policy_and_measures_prompt_savings() -> None:
    context = (
        _source("instructions", "AGENTS.md", 100),
        _source("history", "history.txt", 300),
        _source("tools", "tool-schemas.json", 200),
        _source("new", "new-context.txt", 50),
    )
    policy = ContextPolicy(
        context={
            "instructions": PolicyRule(
                sources=("AGENTS.md",),
                strategy=PolicyStrategy.ALWAYS_INCLUDE,
                parameters={"source_id": "instructions"},
            ),
            "history": PolicyRule(
                sources=("history.txt",),
                strategy=PolicyStrategy.EXCLUDE,
                parameters={"source_id": "history"},
            ),
            "tools": PolicyRule(
                sources=("tool-schemas.json",),
                strategy=PolicyStrategy.LAZY_LOAD,
                parameters={"source_id": "tools"},
            ),
        }
    )

    applied = apply_context_policy(context, policy)

    assert [source.source_id for source in applied.included] == [
        "instructions",
        "new",
    ]
    assert [source.source_id for source in applied.lazy] == ["tools"]
    assert applied.excluded_source_ids == ("history",)
    assert applied.before_tokens == 650
    assert applied.after_tokens == 150
    assert applied.saved_tokens == 500
    assert applied.reduction_fraction == 500 / 650
    assert applied.uncovered_source_ids == ("new",)
    assert applied.savings_dict()["token_count_quality"] == "recorded"
    assert "content" not in str(applied.audit_dict()["decisions"])


def test_runtime_fails_closed_for_missing_summary_and_scope() -> None:
    context = (
        _source("long", "long.txt", 100),
        _source("phase", "deploy.txt", 80),
    )
    policy = ContextPolicy(
        context={
            "long": PolicyRule(
                sources=("long.txt",),
                strategy=PolicyStrategy.SUMMARIZE,
                max_tokens=20,
            ),
            "phase": PolicyRule(
                sources=("deploy.txt",),
                strategy=PolicyStrategy.SCOPED,
                target_phases=("deploy",),
            ),
        }
    )

    without_runtime_scope = apply_context_policy(context, policy)
    build_phase = apply_context_policy(context, policy, phase="build")

    assert without_runtime_scope.after_tokens == 180
    assert len(without_runtime_scope.warnings) == 2
    assert build_phase.after_tokens == 100
    assert build_phase.excluded_source_ids == ("phase",)


def test_policy_json_round_trip_and_strict_unmatched_rule() -> None:
    policy = ContextPolicy(
        context={
            "missing": PolicyRule(
                sources=("missing.txt",),
                strategy=PolicyStrategy.EXCLUDE,
                parameters={"source_id": "missing"},
            )
        }
    )
    restored = ContextPolicy.from_json(policy.to_json())

    assert restored.to_dict() == policy.to_dict()
    try:
        apply_context_policy(
            (_source("present", "present.txt", 10),),
            restored,
            strict=True,
        )
    except ValueError as error:
        assert "matched no context source" in str(error)
    else:
        raise AssertionError("strict policy application should reject unmatched rules")


def test_runtime_keeps_source_when_verified_content_changed() -> None:
    source = _source("history", "history.txt", 100)
    policy = ContextPolicy(
        context={
            "history": PolicyRule(
                sources=("history.txt",),
                strategy=PolicyStrategy.EXCLUDE,
                parameters={
                    "source_id": "history",
                    "content_hash": "0" * 64,
                },
            )
        }
    )

    applied = apply_context_policy((source,), policy)

    assert applied.included == (source,)
    assert applied.saved_tokens == 0
    assert "content changed" in applied.warnings[0]
