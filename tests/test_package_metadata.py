import tomllib
from pathlib import Path


def test_default_install_matches_jev_context_selection():
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = metadata["project"]["dependencies"]
    dependency_names = {item.split("<", 1)[0].split(">", 1)[0] for item in dependencies}

    required = {
        "tiktoken",
        "tree-sitter",
        "tree-sitter-javascript",
        "tree-sitter-typescript",
    }
    assert required <= dependency_names
    assert {"torch", "transformers", "swe-pruner", "hf-xet"}.isdisjoint(
        dependency_names
    )
    assert "neural" in metadata["project"]["optional-dependencies"]
