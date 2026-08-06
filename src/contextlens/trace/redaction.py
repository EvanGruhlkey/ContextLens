"""Composable content redaction hooks."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
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
        return replace(
            source,
            content=content,
            content_ref=None,
            content_hash=None,
        )


@dataclass(frozen=True, slots=True)
class SecretRedactor:
    """Redact common credentials before trace persistence."""

    replacement: str = "[REDACTED]"

    _patterns = (
        re.compile(r"(?i)\b(bearer\s+)[a-z0-9._~+/=-]{12,}"),
        re.compile(
            r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)"
            r"([^\s,;]{6,})"
        ),
        re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_]{16,}\b"),
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
            r"-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    )

    def redact(self, source: ContextSource) -> ContextSource:
        if source.content is None:
            return source
        content = source.content
        content = self._patterns[0].sub(rf"\1{self.replacement}", content)
        content = self._patterns[1].sub(
            rf"\1\2{self.replacement}",
            content,
        )
        for pattern in self._patterns[2:]:
            content = pattern.sub(self.replacement, content)
        return replace(
            source,
            content=content,
            content_ref=None,
            content_hash=None,
            tags=tuple(dict.fromkeys((*source.tags, "redacted"))),
        )
