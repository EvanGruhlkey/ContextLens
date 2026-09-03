from __future__ import annotations

from textwrap import dedent

from contextlens.pruning import LineReason, close_python_dependencies


def test_closure_restores_scope_control_flow_and_imports() -> None:
    source = dedent(
        """\
        from transport import BaseTransport
        from retry import RetryConfig

        class OAuthClient(BaseTransport):
            def refresh(self):
                try:
                    return self.transport.request(
                        "/token",
                        options=RetryConfig(timeout=30),
                    )
                except TimeoutError:
                    return None

        def unrelated():
            return "noise"
        """
    )

    result = close_python_dependencies(source, {9}, max_hops=2)

    assert result.parse_error is None
    assert LineReason.DEPENDENCY in result.reasons[1]
    assert LineReason.DEPENDENCY in result.reasons[2]
    assert LineReason.SCOPE in result.reasons[4]
    assert LineReason.SCOPE in result.reasons[5]
    assert LineReason.CONTROL_FLOW in result.reasons[6]
    assert LineReason.CONTROL_FLOW in result.reasons[11]
    assert LineReason.SYNTAX in result.reasons[7]
    assert LineReason.SYNTAX in result.reasons[10]
    assert 14 not in result.reasons
    assert 15 not in result.reasons


def test_closure_keeps_complete_small_statement() -> None:
    source = dedent(
        """\
        OPTIONS = {
            "timeout": 30,
            "retries": 2,
        }
        print(OPTIONS)
        """
    )

    result = close_python_dependencies(source, {3})

    assert set(result.reasons) == {1, 2, 4}
    assert all(LineReason.SYNTAX in reasons for reasons in result.reasons.values())


def test_closure_reports_invalid_source_without_guessing() -> None:
    result = close_python_dependencies("def broken(:\n", {1})

    assert result.reasons == {}
    assert result.parse_error is not None


def test_empty_evidence_needs_no_parse() -> None:
    result = close_python_dependencies("not valid python", set())

    assert result.reasons == {}
    assert result.parse_error is None
