"""Terminal, JSON, CSV, and self-contained HTML rendering."""

from __future__ import annotations

import csv
import html
import io
import json

from contextlens.reports.model import Report


def render_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"


def render_csv(report: Report) -> str:
    stream = io.StringIO(newline="")
    fields = [
        "source_id",
        "name",
        "kind",
        "evidence_level",
        "verdict",
        "tokens",
        "effect",
        "confidence_low",
        "confidence_high",
        "tokens_saved",
        "cost_saved_usd",
        "quality_per_1k_tokens",
        "action",
        "projected_runs",
        "projected_input_tokens_saved",
        "projected_cost_saved_usd",
        "projected_net_cost_saved_usd",
        "projected_latency_saved_seconds",
        "break_even_runs",
        "removal_quality_change",
        "context_percentage",
        "observed_usage",
        "redundancy_score",
        "experiment_priority",
        "experiment_status",
        "detail",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for finding in report.findings:
        writer.writerow(
            {
                field: getattr(finding, field)
                for field in fields
            }
        )
    return stream.getvalue()


def render_terminal(report: Report) -> str:
    lines = [report.title, "=" * len(report.title)]
    if report.summary:
        lines.append("")
        for key, value in report.summary.items():
            lines.append(f"{key.replace('_', ' ').title()}: {_display(value)}")
    if report.findings:
        lines.extend(("", "Findings", "--------"))
        headers = (
            "Source",
            "Tokens",
            "% Context",
            "Usage",
            "Redundancy",
            "Priority",
            "Effect",
            "Evidence",
            "Verdict",
            "Action",
            "Projected tokens",
            "Projected $",
        )
        rows = [
            (
                finding.name,
                str(finding.tokens),
                (
                    f"{finding.context_percentage:.1%}"
                    if finding.context_percentage is not None
                    else "—"
                ),
                finding.observed_usage or "—",
                _number(finding.redundancy_score),
                _number(finding.experiment_priority),
                _number(finding.effect),
                finding.evidence_level,
                finding.verdict,
                finding.action or "—",
                _whole(finding.projected_input_tokens_saved),
                _money(finding.projected_net_cost_saved_usd),
            )
            for finding in report.findings
        ]
        widths = [
            max(len(headers[index]), *(len(row[index]) for row in rows))
            for index in range(len(headers))
        ]
        lines.append(_row(headers, widths))
        lines.append(_row(tuple("-" * width for width in widths), widths))
        lines.extend(_row(row, widths) for row in rows)
    if report.experiment_tree:
        lines.extend(("", "Experiment tree", "---------------"))
        for node in report.experiment_tree:
            prefix = "  " * node.depth
            delta = _number(node.quality_delta)
            lines.append(
                f"{prefix}{node.group_id}: {node.decision} "
                f"(tokens={node.removed_tokens}, delta={delta})"
            )
            if node.reason:
                lines.append(f"{prefix}  {node.reason}")
    if report.runs:
        lines.extend(("", "Replay runs", "-----------"))
        for run in report.runs:
            lines.append(
                f"{run.variant_id}: {run.status}, "
                f"{run.context_tokens} context tokens, "
                f"{run.duration_seconds:.3f}s"
            )
            if run.changed_files:
                lines.append(f"  changed: {', '.join(run.changed_files)}")
            if run.test_results:
                lines.append(f"  tests: {'; '.join(run.test_results)}")
            if run.error:
                lines.append(f"  error: {run.error}")
    if report.warnings:
        lines.extend(("", "Warnings", "--------"))
        lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines) + "\n"


def render_html(report: Report) -> str:
    summary = "".join(
        "<div class='card'><span>"
        + html.escape(key.replace("_", " ").title())
        + "</span><strong>"
        + html.escape(_display(value))
        + "</strong></div>"
        for key, value in report.summary.items()
    )
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(finding.name)}</td>"
        f"<td>{finding.tokens}</td>"
        f"<td>{html.escape(finding.kind)}</td>"
        f"<td>{html.escape(_percentage(finding.context_percentage))}</td>"
        f"<td>{html.escape(finding.observed_usage or '—')}</td>"
        f"<td>{html.escape(_number(finding.redundancy_score))}</td>"
        f"<td>{html.escape(finding.experiment_status or 'completed')}</td>"
        f"<td>{html.escape(_number(finding.effect))}</td>"
        f"<td>{html.escape(finding.verdict)}</td>"
        f"<td>{html.escape(finding.action or '—')}</td>"
        f"<td>{html.escape(_whole(finding.projected_input_tokens_saved))}</td>"
        f"<td>{html.escape(_money(finding.projected_net_cost_saved_usd))}</td>"
        f"<td><span class='badge'>{html.escape(finding.evidence_level)}</span></td>"
        "</tr>"
        for finding in report.findings
    )
    tree = "".join(
        "<li style='margin-left:"
        + str(node.depth * 24)
        + "px'><strong>"
        + html.escape(node.group_id)
        + "</strong>: "
        + html.escape(node.decision)
        + " — "
        + html.escape(node.reason)
        + "</li>"
        for node in report.experiment_tree
    )
    warnings = "".join(
        f"<li>{html.escape(warning)}</li>"
        for warning in report.warnings
    )
    runs = "".join(
        "<tr>"
        f"<td>{html.escape(run.variant_id)}</td>"
        f"<td>{html.escape(run.status)}</td>"
        f"<td>{run.context_tokens}</td>"
        f"<td>{run.duration_seconds:.3f}s</td>"
        f"<td>{html.escape(', '.join(run.changed_files))}</td>"
        f"<td>{html.escape('; '.join(run.test_results))}</td>"
        "</tr>"
        for run in report.runs
    )
    data = html.escape(json.dumps(report.to_dict(), ensure_ascii=False))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(report.title)}</title>
<style>
body{{font:14px system-ui;margin:0;background:#f4f6f8;color:#18202a}}
main{{max-width:1100px;margin:auto;padding:32px}}
h1{{font-size:30px}} .cards{{display:flex;gap:12px;flex-wrap:wrap}}
.card{{background:white;padding:14px 18px;border-radius:8px;min-width:170px}}
.card span{{display:block;color:#647080;font-size:12px}} table{{width:100%;
border-collapse:collapse;background:white;margin-top:12px}} th,td{{padding:10px;
border-bottom:1px solid #e3e7eb;text-align:left}} .badge{{background:#e8eef8;
padding:3px 7px;border-radius:10px}} section{{margin-top:28px}} li{{margin:7px}}
</style>
</head>
<body><main>
<h1>{html.escape(report.title)}</h1>
<p>Generated {html.escape(report.generated_at)}</p>
<div class="cards">{summary}</div>
<section><h2>Findings</h2><table>
  <thead><tr><th>Source</th><th>Tokens</th><th>Type</th><th>% context</th>
  <th>Observed usage</th><th>Redundancy</th><th>Experiment</th><th>Effect</th>
<th>Verdict</th><th>Action</th><th>Projected tokens</th>
<th>Projected net savings</th><th>Evidence</th></tr></thead>
<tbody>{rows}</tbody></table></section>
<section><h2>Experiment tree</h2><ul>{tree}</ul></section>
<section><h2>Replay runs</h2><table><thead><tr><th>Variant</th>
<th>Status</th><th>Context tokens</th><th>Duration</th>
<th>Changed files</th><th>Tests</th></tr></thead><tbody>{runs}</tbody>
</table></section>
<section><h2>Warnings</h2><ul>{warnings}</ul></section>
<details><summary>Embedded report data</summary><pre>{data}</pre></details>
</main></body></html>
"""


def _row(values: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(
        value.ljust(widths[index])
        for index, value in enumerate(values)
    )


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:+.4f}"


def _percentage(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _interval(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "—"
    return f"[{low:+.4f}, {high:+.4f}]"


def _display(value: object) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _whole(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"
