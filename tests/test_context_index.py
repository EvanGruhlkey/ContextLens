import subprocess
from pathlib import Path

import pytest

from contextlens.context_index import discover_candidates


def repository(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "example.py").write_bytes(source.encode("utf-8"))
    return root


def test_method_is_independent_and_support_is_exact(tmp_path: Path) -> None:
    source = (
        "@decorator\nclass Service(Base):\n    timeout = 20\n"
        "    unrelated = 99\n    @property\n    def refresh_token(self):\n"
        "        return self.timeout\n"
        + "    def irrelevant(self):\n        return 0\n"
        * 80
    )
    root = repository(tmp_path, source)
    candidates = discover_candidates(root, tmp_path / "state", "refresh_token")
    selected = candidates[0]
    assert selected.unit.bindings == ["refresh_token"]
    assert selected.unit.text == (
        "    @property\n    def refresh_token(self):\n        return self.timeout\n"
    )
    assert {unit.text for unit in selected.support} == {
        "@decorator\nclass Service(Base):\n",
        "    timeout = 20\n",
    }
    lines = source.splitlines(keepends=True)
    for unit in (selected.unit, *selected.support):
        assert unit.text == "".join(lines[unit.start_line - 1 : unit.end_line])
    assert len(selected.content_hash) == len(selected.repository_version) == 64


def test_nested_function_has_enclosing_header_and_closure(tmp_path: Path) -> None:
    source = (
        "def outer(value):\n    captured = value + 1\n"
        "    def nested_target():\n        return captured\n    return nested_target\n"
    )
    root = repository(tmp_path, source)
    selected = discover_candidates(root, tmp_path / "state", "nested_target")[0]
    assert selected.unit.bindings == ["nested_target"]
    assert {unit.text for unit in selected.support} == {
        "def outer(value):\n",
        "    captured = value + 1\n",
    }


def test_no_match_abstains(tmp_path: Path) -> None:
    root = repository(tmp_path, "def visible():\n    return 1\n")
    assert discover_candidates(root, tmp_path / "state", "zzzxxyyunknown") == []


def test_javascript_method_and_nested_function(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_javascript")
    root = repository(tmp_path, "")
    (root / "service.js").write_bytes(
        b"class Service {\n  timeout = 20;\n  refreshToken() {\n"
        b"    return this.timeout;\n  }\n  unrelated() { return 0; }\n}\n"
        b"function outer() {\n  const captured = 1;\n"
        b"  function nestedTarget() {\n    return captured;\n  }\n}\n"
    )
    selected = discover_candidates(root, tmp_path / "state", "refreshToken")[0]
    assert selected.unit.bindings == ["refreshToken"]
    assert {unit.text for unit in selected.support} == {
        "class Service {\n",
        "  timeout = 20;\n",
    }
    nested = discover_candidates(root, tmp_path / "state", "nestedTarget")[0]
    assert nested.unit.bindings == ["nestedTarget"]
    assert {unit.text for unit in nested.support} == {
        "function outer() {\n",
        "  const captured = 1;\n",
    }
