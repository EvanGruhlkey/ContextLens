from contextlens.trajectory_evaluation import TrajectoryRun, compare_trajectories


def run(policy, **changes):
    values = {
        "task_id": "expiry",
        "trial": 1,
        "policy": policy,
        "success": True,
        "tests_passed": True,
        "provider_input_tokens": 1000,
        "provider_output_tokens": 100,
        "tool_calls": 8,
        "duration_ms": 5000,
        "recovery_calls": 0,
        "rereads": 2,
        "jev_input_tokens": 0,
        "jev_output_tokens": 0,
        "jev_cost": "0",
        "patch_sha256": "abc",
    }
    values.update(changes)
    return TrajectoryRun(**values)


def test_comparison_includes_complete_control_cost():
    report = compare_trajectories(
        [
            run("baseline"),
            run(
                "control",
                provider_input_tokens=700,
                jev_input_tokens=100,
                jev_output_tokens=20,
                tool_calls=6,
                recovery_calls=1,
                rereads=0,
            ),
        ]
    )
    assert report["complete_pairs"] == 1
    assert report["total_input_tokens"]["baseline"] == 1000
    assert report["total_input_tokens"]["control"] == 800
    assert report["input_reduction_percent"] == 20.0
    assert report["quality_regressions"] == []


def test_quality_regression_blocks_savings_claim():
    report = compare_trajectories(
        [run("baseline"), run("control", success=False, tests_passed=False)]
    )
    assert report["quality_regressions"] == ["expiry"]
    assert report["observed_sample_meets_goal"] is False
