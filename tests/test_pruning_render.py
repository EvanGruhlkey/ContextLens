from __future__ import annotations

import ast
from textwrap import dedent

from contextlens.pruning import render_python_skeleton


def test_renderer_marks_exact_original_ranges() -> None:
    source = "one\ntwo\nthree\nfour\nfive"

    rendered = render_python_skeleton(source, {2, 4})

    assert [item.to_dict() for item in rendered.omitted_ranges] == [
        {"start_line": 1, "end_line": 1, "line_count": 1},
        {"start_line": 3, "end_line": 3, "line_count": 1},
        {"start_line": 5, "end_line": 5, "line_count": 1},
    ]
    assert "original lines 3-3" in rendered.text
    assert rendered.text.index("two") < rendered.text.index("four")


def test_renderer_inserts_pass_for_empty_suite() -> None:
    source = dedent(
        """\
        def selected():
            return 1

        def omitted():
            print("large body")
            return 2
        """
    )

    rendered = render_python_skeleton(source, {1, 2, 4})

    ast.parse(rendered.text)
    assert "pass  # [ContextLens omitted original lines 5-6]" in rendered.text


def test_renderer_keeps_nonempty_suite_parseable() -> None:
    source = dedent(
        """\
        class Client:
            def send(self):
                before = "noise"
                return request(
                    timeout=30,
                )

        def other():
            return None
        """
    )

    rendered = render_python_skeleton(source, {1, 2, 4, 5, 6})

    ast.parse(rendered.text)
    assert "original lines 3-3" in rendered.text
    assert "original lines 7-9" in rendered.text


def test_renderer_handles_empty_and_fully_omitted_inputs() -> None:
    assert render_python_skeleton("", set()).text == ""

    rendered = render_python_skeleton("a\nb", set())

    assert rendered.text == "# [ContextLens omitted original lines 1-2]"
    assert rendered.omitted_ranges[0].line_count == 2
