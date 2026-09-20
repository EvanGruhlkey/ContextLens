from contextlens.evidence_descriptors import describe_unit
from contextlens.evidence_index import Unit


def test_descriptor_is_compact_structured_and_source_free():
    unit = Unit(
        "src/auth.py",
        84,
        126,
        "def refresh_token(user, ttl=TOKEN_TTL):\n"
        "    token = encode(user, ttl)\n"
        "    return token\n",
        ["refresh_token"],
        ["TOKEN_TTL", "encode"],
        [],
        "python",
    )

    descriptor = describe_unit(
        unit,
        handle="h_123",
        role="primary",
        score=82.5,
        owners=(unit.key,),
    ).to_state()

    assert descriptor == {
        "handle": "h_123",
        "path": "src/auth.py",
        "symbol": "refresh_token",
        "kind": "function",
        "signature": "def refresh_token(user, ttl=TOKEN_TTL):",
        "start_line": 84,
        "end_line": 126,
        "role": "primary",
        "lexical_score": 82.5,
        "bindings": ["refresh_token"],
        "relationships": ["references:TOKEN_TTL", "references:encode"],
    }
    assert "source" not in descriptor


def test_descriptor_identifies_methods_and_bounds_metadata():
    unit = Unit(
        "src/client.py",
        10,
        12,
        "    async def refresh(self):\n        return await fetch()\n",
        ["refresh"],
        [f"reference_{index}" for index in range(30)],
        [],
        "python",
    )

    descriptor = describe_unit(
        unit,
        handle="h_method",
        role="support",
        score=1 / 3,
        owners=("src/client.py:1:20",),
    ).to_state()

    assert descriptor["kind"] == "method"
    assert descriptor["signature"] == "async def refresh(self):"
    assert descriptor["lexical_score"] == 0.333333
    assert len(descriptor["relationships"]) == 13
    assert descriptor["relationships"][0] == "owned_by:src/client.py:1:20"
