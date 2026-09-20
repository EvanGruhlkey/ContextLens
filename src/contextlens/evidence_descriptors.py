"""Compact, source-free descriptions of exact repository evidence units."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from contextlens.evidence_index import Unit

MAX_BINDINGS = 12
MAX_REFERENCES = 12
MAX_SIGNATURE_CHARS = 240


@dataclass(frozen=True)
class EvidenceDescriptor:
    handle: str
    path: str
    symbol: str
    kind: str
    signature: str
    start_line: int
    end_line: int
    role: str
    lexical_score: float
    bindings: tuple[str, ...]
    relationships: tuple[str, ...]

    def to_state(self) -> dict[str, Any]:
        state = asdict(self)
        state["bindings"] = list(self.bindings)
        state["relationships"] = list(self.relationships)
        return state


def describe_unit(
    unit: Unit,
    *,
    handle: str,
    role: str,
    score: float,
    owners: tuple[str, ...],
) -> EvidenceDescriptor:
    """Describe a unit without including its complete source body."""
    bindings = tuple(dict.fromkeys(unit.bindings))[:MAX_BINDINGS]
    symbol = bindings[0] if bindings else _fallback_symbol(unit)
    relationships = tuple(
        [*(f"owned_by:{owner}" for owner in owners if owner != unit.key)]
        + [
            f"references:{reference}"
            for reference in dict.fromkeys(unit.references)
        ][:MAX_REFERENCES]
    )
    return EvidenceDescriptor(
        handle=handle,
        path=unit.path,
        symbol=symbol,
        kind=_kind(unit),
        signature=_signature(unit),
        start_line=unit.start_line,
        end_line=unit.end_line,
        role=role,
        lexical_score=round(float(score), 6),
        bindings=bindings,
        relationships=relationships,
    )


def _signature(unit: Unit) -> str:
    lines = [line.strip() for line in unit.text.splitlines() if line.strip()]
    if not lines:
        return ""
    signature = lines[0]
    if signature.startswith("@") and len(lines) > 1:
        signature = lines[1]
    return signature[:MAX_SIGNATURE_CHARS]


def _kind(unit: Unit) -> str:
    signature = _signature(unit)
    stripped = signature.lstrip()
    original = next((line for line in unit.text.splitlines() if line.strip()), "")
    if re.match(r"(?:async\s+)?def\s+", stripped):
        return "method" if original[:1].isspace() else "function"
    if re.match(r"class\s+", stripped):
        return "class"
    if "function" in stripped or "=>" in stripped:
        return "method" if original[:1].isspace() else "function"
    if unit.fallback:
        return "file"
    if unit.start_line == unit.end_line and unit.bindings:
        return "declaration"
    return "source"


def _fallback_symbol(unit: Unit) -> str:
    name = unit.path.rsplit("/", 1)[-1]
    return name or unit.key
