from types import SimpleNamespace

from benchmarks.goal import analyze_coding, comma, markdown_report, prompt
from benchmarks.host_agent import CODING_POLICIES, coding_prompt, run_host_agent
from contextlens.context_adapter import Answer, ToolCall
from contextlens.jev_gateway import Evaluation


def test_coding_prompts_are_identical_and_do_not_name_contextlens():
    texts = {prompt("fix the timeout", policy) for policy in CODING_POLICIES}
    assert texts == {coding_prompt("fix the timeout")}
    text = next(iter(texts))
    assert "ContextLens" not in text
    assert "context_filter" not in text
    assert "context_next" not in text


def test_comma_formats_large_totals():
    assert comma(1_191_403) == "1,191,403"
    assert comma(0) == "0"


def _coding_row(policy, passed=True, tokens=100, injected=80, raw=80):
    return {
        "case": "repo-task",
        "repo": "owner/repo",
        "trial": 0,
        "policy": policy,
        "status": "completed",
        "verified_success": passed,
        "input_tokens": tokens,
        "cached_input_tokens": 20,
        "uncached_input_tokens": tokens - 20,
        "output_tokens": 10,
        "total_tokens": tokens + 10,
        "agent_turns": 4,
        "raw_tool_output_tokens": raw,
        "injected_tool_output_tokens": injected,
        "tool_output_tokens_removed": raw - injected,
        "filter_calls": 0 if policy == "baseline" else 2,
        "recovery_calls": 0 if policy == "baseline" else 1,
        "recovered_tokens": 0 if policy == "baseline" else 12,
        "jev_input_tokens": 0 if policy == "baseline" else 30,
        "jev_output_tokens": 0 if policy == "baseline" else 6,
        "jev_cost": "0" if policy == "baseline" else "0.01",
        "agent_seconds": 1,
    }


def test_analyze_coding_keeps_absolute_token_totals():
    rows = [
        _coding_row("baseline", tokens=200, injected=90, raw=90),
        _coding_row("jev_filter", tokens=150, injected=50, raw=88),
        _coding_row("contextlens", tokens=120, injected=40, raw=91),
    ]
    result = analyze_coding(rows, 1)
    assert result["conditions"]["baseline"]["input_tokens"] == 200
    assert result["conditions"]["contextlens"]["input_tokens"] == 120
    assert result["deltas"]["contextlens"]["input_tokens"] == {
        "absolute": -80,
        "percent": -40.0,
    }
    assert result["conditions"]["contextlens"]["tool_output_tokens_removed"] == 51
    text = markdown_report(result)
    assert "1" in text
    assert "-80" in text
    assert "owner/repo/repo-task" in text


def test_host_agent_filters_tool_output_before_the_model(tmp_path):
    source = 'TIMEOUT = 30\nUNRELATED = "noise"\n\n' + "\n".join(
        f"def unused_{index}():\n    return UNRELATED\n" for index in range(80)
    )
    (tmp_path / "auth.py").write_text(source)
    subprocess_git = __import__("subprocess")
    subprocess_git.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    def judge(state, questions):
        probabilities = {
            key: (
                0.9
                if "TIMEOUT" in str(candidate) or "unused_0" in str(candidate)
                else 0.1
            )
            for key, candidate in state["candidates"].items()
        }
        return Evaluation(probabilities, "fixture", 20, 4, "0", 1.0)

    seen = []

    def solver(messages):
        seen.append(messages[-1].content)
        if len(messages) == 2:
            return ToolCall("read_file", {"path": "auth.py"})
        return Answer("done")

    row = run_host_agent(
        tmp_path,
        "fix the refresh-token timeout",
        "contextlens",
        model="unused",
        timeout=30,
        state=tmp_path / "state",
        solver=solver,
        judge=SimpleNamespace(evaluate=judge),
    )
    assert row["status"] == "completed"
    assert source not in seen[-1]
    assert row["injected_tool_output_tokens"] < row["raw_tool_output_tokens"]
    assert "ContextLens" not in seen[0]
