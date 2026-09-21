"""Paired analysis and the markdown report for one benchmark run."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

from benchmarks.agent import COMPACTION_CONDITIONS, CONDITIONS

LABELS = {
    "baseline": "Baseline",
    "live_pruning": "Live Pruning",
    "compaction_only": "Compaction Only",
    "full_contextlens": "Full ContextLens",
}

TOTAL_FIELDS = (
    "input_tokens",
    "uncached_input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "total_tokens",
    "raw_tool_output_tokens",
    "injected_tool_output_tokens",
    "tool_output_tokens_removed",
    "agent_turns",
    "tool_calls",
    "compaction_events",
    "compaction_tokens_removed",
    "recovery_calls",
    "recovered_tokens",
    "jev_requests",
    "jev_input_tokens",
    "jev_output_tokens",
)

HEADLINE_COLUMNS = (
    ("passed", "Verified Fixes"),
    ("input_tokens", "Coding Input"),
    ("uncached_input_tokens", "Uncached Input"),
    ("cached_input_tokens", "Cached Input"),
    ("output_tokens", "Output"),
    ("total_tokens", "Total Coding"),
    ("raw_tool_output_tokens", "Raw Tool Output"),
    ("injected_tool_output_tokens", "Injected Tool Output"),
    ("tool_output_tokens_removed", "Tokens Removed"),
    ("agent_turns", "Agent Turns"),
    ("tool_calls", "Tool Calls"),
    ("compaction_events", "Compaction Events"),
    ("compaction_tokens_removed", "Compaction Tokens Removed"),
    ("tasks_with_compaction", "Tasks Compacted"),
    ("recovery_calls", "Recovery Calls"),
    ("recovered_tokens", "Recovered Tokens"),
    ("jev_input_tokens", "Jev Input"),
    ("jev_output_tokens", "Jev Output"),
    ("jev_cost", "Jev Cost"),
    ("wall_clock_seconds", "Wall Clock (s)"),
)

DELTA_METRICS = (
    ("passed", "Verified fixes"),
    ("input_tokens", "Coding-model input tokens"),
    ("uncached_input_tokens", "Uncached coding-model input"),
    ("cached_input_tokens", "Cached coding-model input"),
    ("output_tokens", "Output tokens"),
    ("total_tokens", "Total coding-model tokens"),
    ("raw_tool_output_tokens", "Raw tool output"),
    ("injected_tool_output_tokens", "Injected tool output"),
    ("agent_turns", "Agent turns"),
    ("tool_calls", "Tool calls"),
    ("wall_clock_seconds", "Wall clock"),
)


def analyze(rows: Sequence[dict[str, Any]], expected_pairs: int) -> dict[str, Any]:
    """Total each condition, then compare every candidate to the baseline."""

    conditions: dict[str, dict[str, Any]] = {}
    for condition in CONDITIONS:
        selected = [row for row in rows if row["condition"] == condition]
        cost = 0.0
        for row in selected:
            try:
                cost += float(row.get("jev_cost") or 0)
            except (TypeError, ValueError):
                continue
        conditions[condition] = {
            "attempts": len(selected),
            "passed": sum(bool(row["verified_success"]) for row in selected),
            "incomplete_or_invalid": sum(
                row["status"] != "completed" for row in selected
            ),
            **{field: _total(selected, field) for field in TOTAL_FIELDS},
            "tasks_with_compaction": sum(
                bool(row.get("compaction_triggered")) for row in selected
            ),
            "jev_cost": format(cost, "f"),
            "wall_clock_seconds": (
                round(
                    sum(
                        float(row.get("agent_seconds") or 0)
                        + float(row.get("verification_seconds") or 0)
                        for row in selected
                    ),
                    1,
                )
                if selected
                else None
            ),
            "median_agent_seconds": (
                round(statistics.median(row["agent_seconds"] for row in selected), 1)
                if selected
                else None
            ),
        }
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        group = grouped.setdefault((row["case"], row["trial"]), {})
        if row["condition"] in group:
            raise ValueError("duplicate attempt for one case, trial and condition")
        group[row["condition"]] = row
    pairs = [
        group for group in grouped.values() if all(name in group for name in CONDITIONS)
    ]

    def delta(field: str, condition: str) -> dict[str, Any]:
        first = conditions["baseline"].get(field)
        second = conditions[condition].get(field)
        if not isinstance(first, (int, float)) or not isinstance(second, (int, float)):
            return {"absolute": None, "percent": None}
        absolute = second - first
        return {
            "absolute": round(absolute, 1) if isinstance(absolute, float) else absolute,
            "percent": round(100 * absolute / first, 1) if first else None,
        }

    candidates = [name for name in CONDITIONS if name != "baseline"]
    regressions = {
        name: sorted(
            group["baseline"]["case"]
            for group in pairs
            if group["baseline"]["verified_success"]
            and not group[name]["verified_success"]
        )
        for name in candidates
    }
    recoveries = {
        name: sorted(
            group["baseline"]["case"]
            for group in pairs
            if group[name]["verified_success"]
            and not group["baseline"]["verified_success"]
        )
        for name in candidates
    }
    return {
        "conditions": conditions,
        "complete_pairs": len(pairs),
        "expected_pairs": expected_pairs,
        "complete": len(pairs) == expected_pairs
        and len(rows) == expected_pairs * len(CONDITIONS),
        "all_agent_unavailable": bool(rows)
        and all(row["status"] == "agent_unavailable" for row in rows),
        "any_agent_unavailable": any(
            row["status"] == "agent_unavailable" for row in rows
        ),
        "jev_never_scored": bool(rows)
        and all(
            (row.get("jev_requests") or 0) == 0
            for row in rows
            if row["condition"] != "baseline"
        ),
        "deltas": {
            name: {field: delta(field, name) for field, _title in DELTA_METRICS}
            for name in candidates
        },
        "quality_regressions": regressions,
        "quality_gains": recoveries,
        "tasks": [
            {
                "case": group["baseline"]["case"],
                **{
                    name: {
                        "passed": group[name]["verified_success"],
                        "input_tokens": group[name].get("input_tokens"),
                        "injected_tool_output_tokens": group[name].get(
                            "injected_tool_output_tokens"
                        ),
                        "compaction_triggered": bool(
                            group[name].get("compaction_triggered")
                        ),
                        "status": group[name]["status"],
                    }
                    for name in CONDITIONS
                },
            }
            for group in pairs
        ],
        "statistical_quality_preservation_claim": None,
    }


def markdown(report: dict[str, Any]) -> str:
    """Render the measured tables. Missing numbers print as n/a, never zero."""

    analysis = report["analysis"]
    conditions = analysis["conditions"]
    lines = [
        f"# Paired coding-agent benchmark — {report.get('started_at', 'unknown')}",
        "",
        f"Model `{report.get('model')}`, reasoning `{report.get('reasoning')}`, "
        f"{report.get('max_turns')} turns, {report.get('timeout')}s timeout, "
        f"{report.get('trials')} trial(s) over "
        f"{len(report.get('tasks', []))} frozen task(s). "
        f"Implementation `{str(report.get('implementation_revision') or 'n/a')[:12]}`.",
        "",
        "All four conditions share the coding model, reasoning effort, issue "
        "prompt, repository commit, tool set, timeout, turn limit, and hidden "
        "grader. The agent is never told that ContextLens exists. Jev tokens "
        "are counted separately and never enter coding-model input.",
        "",
        "## Aggregate",
        "",
    ]
    header = "| Condition | " + " | ".join(
        title for _field, title in HEADLINE_COLUMNS
    ) + " |"
    rule = "| --- | " + " | ".join("---:" for _ in HEADLINE_COLUMNS) + " |"
    lines += [header, rule]
    for condition in CONDITIONS:
        row = conditions[condition]
        cells = [comma(row.get(field)) for field, _title in HEADLINE_COLUMNS]
        lines.append(f"| {LABELS[condition]} | " + " | ".join(cells) + " |")
    candidates = [name for name in CONDITIONS if name != "baseline"]
    lines += [
        "",
        "## Versus baseline",
        "",
        "| Metric | " + " | ".join(LABELS[name] for name in candidates) + " |",
        "| --- | " + " | ".join("---:" for _ in candidates) + " |",
    ]
    for field, title in DELTA_METRICS:
        cells = []
        for name in candidates:
            item = analysis["deltas"][name].get(field, {})
            absolute = item.get("absolute")
            percent = item.get("percent")
            if absolute is None:
                cells.append("n/a")
            elif field == "passed" or percent is None:
                cells.append(f"{absolute:+,}")
            else:
                cells.append(f"{absolute:+,} ({percent:+.1f}%)")
        lines.append(f"| {title} | " + " | ".join(cells) + " |")
    lines += ["", "## Per task", ""]
    lines += [
        "| Task | " + " | ".join(f"{LABELS[name]} Fix" for name in CONDITIONS) + " | "
        + " | ".join(f"{LABELS[name]} Input" for name in CONDITIONS) + " | Compacted |",
        "| --- | " + " | ".join("---" for _ in CONDITIONS) + " | "
        + " | ".join("---:" for _ in CONDITIONS) + " | --- |",
    ]
    for task in analysis["tasks"]:
        marks = [mark(task[name]["passed"]) for name in CONDITIONS]
        inputs = [comma(task[name]["input_tokens"]) for name in CONDITIONS]
        compacted = ", ".join(
            LABELS[name]
            for name in CONDITIONS
            if name in COMPACTION_CONDITIONS and task[name]["compaction_triggered"]
        )
        lines.append(
            f"| {task['case']} | "
            + " | ".join([*marks, *inputs, compacted or "none"])
            + " |"
        )
    lines += ["", "## Regressions", ""]
    lines += _regressions(analysis)
    lines += ["", "## Notes", ""]
    lines += _notes(report, analysis)
    return "\n".join(lines) + "\n"


def _regressions(analysis: dict[str, Any]) -> list[str]:
    """Tasks the baseline fixed and a ContextLens condition did not."""

    lines: list[str] = []
    total = 0
    for name in CONDITIONS:
        if name == "baseline":
            continue
        cases = analysis["quality_regressions"][name]
        gains = analysis["quality_gains"][name]
        total += len(cases)
        lines.append(
            f"- **{LABELS[name]}**: "
            + (
                f"{len(cases)} regression(s) — {', '.join(cases)}."
                if cases
                else "no task that baseline fixed regressed."
            )
            + (f" Newly fixed: {', '.join(gains)}." if gains else "")
        )
    baseline_passed = analysis["conditions"]["baseline"]["passed"]
    lines.append("")
    if not analysis["tasks"]:
        lines.append(
            "No task completed every condition, so no regression comparison is "
            "possible."
        )
    elif baseline_passed == 0:
        lines.append(
            "The baseline fixed no task, so there was nothing a ContextLens "
            "condition could regress. This is not evidence that ContextLens "
            "preserves fixes."
        )
    elif total == 0:
        lines.append(
            f"No ContextLens condition lost any of the {baseline_passed} fix(es) "
            "the baseline achieved."
        )
    return lines


def _notes(report: dict[str, Any], analysis: dict[str, Any]) -> list[str]:
    conditions = analysis["conditions"]
    notes = [
        "Verified fixes: "
        + ", ".join(
            f"{LABELS[name]} {conditions[name]['passed']}/"
            f"{conditions[name]['attempts']}"
            for name in CONDITIONS
        )
        + ".",
        "",
    ]
    if analysis["all_agent_unavailable"]:
        notes += [
            "**This run executed no coding model.** Every attempt ended in "
            "`agent_unavailable` because neither `OPENAI_API_KEY` nor "
            "`AI_GATEWAY_API_KEY` was set. Every number below is a blocked "
            "run, not a measured result. Do not read any token figure, fix "
            "count, or delta here as evidence about ContextLens.",
            "",
        ]
    elif analysis["any_agent_unavailable"]:
        notes += [
            "**Some attempts never reached the coding model** "
            "(`agent_unavailable`). Condition totals are not comparable.",
            "",
        ]
    if analysis["jev_never_scored"] and not analysis["all_agent_unavailable"]:
        notes += [
            "**Jev never scored** (`AI_GATEWAY_API_KEY` unset), so every "
            "candidate condition failed open to passthrough. Differences "
            "between conditions are trajectory noise, not ContextLens.",
            "",
        ]
    if not analysis["complete"]:
        notes += [
            f"Incomplete run: {analysis['complete_pairs']} of "
            f"{analysis['expected_pairs']} tasks finished all "
            f"{len(CONDITIONS)} conditions.",
            "",
        ]
    for name in CONDITIONS:
        if name == "baseline":
            continue
        row = conditions[name]
        deltas = analysis["deltas"][name]
        detail = (
            f"**{LABELS[name]}**: coding-model input "
            f"{_change(deltas['input_tokens'])}; injected tool output "
            f"{_change(deltas['injected_tool_output_tokens'])}; agent turns "
            f"{_change(deltas['agent_turns'])}; tool calls "
            f"{_change(deltas['tool_calls'])}. Jev used "
            f"{comma(row['jev_input_tokens'])} input / "
            f"{comma(row['jev_output_tokens'])} output tokens over "
            f"{comma(row['jev_requests'])} requests, cost {row['jev_cost']}. "
            f"{comma(row['recovery_calls'])} recovery call(s) restored "
            f"{comma(row['recovered_tokens'])} tokens."
        )
        if name in COMPACTION_CONDITIONS:
            detail += (
                f" Compaction fired on {comma(row['tasks_with_compaction'])} of "
                f"{comma(row['attempts'])} attempt(s) across "
                f"{comma(row['compaction_events'])} event(s), removing "
                f"{comma(row['compaction_tokens_removed'])} transcript tokens."
            )
        notes += [detail, ""]
    if report.get("note"):
        notes += [str(report["note"]), ""]
    notes.append(
        f"{report.get('trials')} trial(s) over {len(report.get('tasks', []))} "
        "task(s) is not a statistical quality claim."
    )
    return notes


def _change(delta: dict[str, Any]) -> str:
    absolute = delta.get("absolute")
    if absolute is None:
        return "n/a"
    if absolute == 0:
        return "unchanged"
    percent = delta.get("percent")
    direction = "down" if absolute < 0 else "up"
    suffix = f" ({percent:+.1f}%)" if percent is not None else ""
    return f"{direction} {comma(abs(absolute))}{suffix}"


def _total(rows: Sequence[dict[str, Any]], field: str) -> int | None:
    if not rows or any(row.get(field) is None for row in rows):
        return None
    return sum(int(row[field]) for row in rows)


def comma(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return mark(value)
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def mark(passed: bool) -> str:
    return "✅" if passed else "❌"
