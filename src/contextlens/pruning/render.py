"""Render retained lines as compact, traceable source skeletons."""

from __future__ import annotations

from dataclasses import dataclass

from contextlens.pruning.model import OmittedRange


@dataclass(frozen=True, slots=True)
class RenderedObservation:
    """Pruned text and the original ranges represented by markers."""

    text: str
    omitted_ranges: tuple[OmittedRange, ...]


def render_python_skeleton(content: str, kept_lines: set[int]) -> RenderedObservation:
    """Preserve source order and replace omitted runs with comments or pass."""

    lines = content.splitlines()
    if not lines:
        return RenderedObservation("", ())
    valid_kept = sorted(line for line in kept_lines if 1 <= line <= len(lines))
    if not valid_kept:
        omitted = OmittedRange(1, len(lines))
        return RenderedObservation(_marker(omitted, ""), (omitted,))

    ranges = _omitted_ranges(len(lines), set(valid_kept))
    by_start = {item.start_line: item for item in ranges}
    output: list[str] = []
    line_number = 1
    while line_number <= len(lines):
        omitted = by_start.get(line_number)
        if omitted is None:
            output.append(lines[line_number - 1])
            line_number += 1
            continue
        previous = _previous_kept(lines, valid_kept, omitted.start_line)
        following = _next_kept(lines, valid_kept, omitted.end_line)
        needs_pass = _needs_pass(lines, previous, following)
        indent = (
            _suite_indent(lines, omitted, previous)
            if needs_pass
            else _marker_indent(lines, omitted, previous, following)
        )
        marker = _marker(omitted, indent)
        if needs_pass:
            marker = f"{indent}pass  {marker.lstrip()}"
        output.append(marker)
        line_number = omitted.end_line + 1
    return RenderedObservation("\n".join(output), tuple(ranges))


def _omitted_ranges(total_lines: int, kept: set[int]) -> list[OmittedRange]:
    result: list[OmittedRange] = []
    start: int | None = None
    for line in range(1, total_lines + 1):
        if line not in kept and start is None:
            start = line
        elif line in kept and start is not None:
            result.append(OmittedRange(start, line - 1))
            start = None
    if start is not None:
        result.append(OmittedRange(start, total_lines))
    return result


def _previous_kept(lines: list[str], kept: list[int], start: int) -> int | None:
    return next((line for line in reversed(kept) if line < start), None)


def _next_kept(lines: list[str], kept: list[int], end: int) -> int | None:
    return next((line for line in kept if line > end), None)


def _marker_indent(
    lines: list[str],
    omitted: OmittedRange,
    previous: int | None,
    following: int | None,
) -> str:
    candidates = [
        _indent(lines[index - 1])
        for index in (previous, following, omitted.start_line)
        if index is not None and 1 <= index <= len(lines) and lines[index - 1].strip()
    ]
    return min(candidates, key=len, default="")


def _needs_pass(
    lines: list[str],
    previous: int | None,
    following: int | None,
) -> bool:
    if previous is None or not lines[previous - 1].rstrip().endswith(":"):
        return False
    previous_indent = len(_indent(lines[previous - 1]))
    if following is None:
        return True
    return len(_indent(lines[following - 1])) <= previous_indent


def _suite_indent(
    lines: list[str],
    omitted: OmittedRange,
    previous: int | None,
) -> str:
    assert previous is not None
    observed = _indent(lines[omitted.start_line - 1])
    parent = _indent(lines[previous - 1])
    return observed if len(observed) > len(parent) else parent + "    "


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _marker(omitted: OmittedRange, indent: str) -> str:
    return (
        f"{indent}# [ContextLens omitted original lines "
        f"{omitted.start_line}-{omitted.end_line}]"
    )
