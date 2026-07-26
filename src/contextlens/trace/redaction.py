"""Composable content redaction hooks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from contextlens.trace.model import ContextSource


class Redactor(Protocol):
    """Transform a source immediately before it is persisted."""

    def redact(self, source: ContextSource) -> ContextSource:
        """Return the source to persist."""


@dataclass(frozen=True, slots=True)
class RegexRedactor:
    """Replace matches in inline text while preserving source identity."""

    pattern: str
    replacement: str = "[REDACTED]"
    flags: int = 0

    def redact(self, source: ContextSource) -> ContextSource:
        if source.content is None:
            return source
        content = re.sub(
            self.pattern,
            self.replacement,
            source.content,
            flags=self.flags,
        )
        return ContextSource(
            source_id=source.source_id,
            kind=source.kind,
            name=source.name,
            content=content,
            token_count=source.token_count,
            token_count_method=source.token_count_method,
            provenance=source.provenance,
            tags=source.tags,
        )
