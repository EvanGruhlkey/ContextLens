"""Exact recovery for anything ContextLens removes.

Every observation is written verbatim before it is reduced, keyed by a stable
handle derived from its content. The coding agent only needs the handle printed
in an omission marker; it never has to re-run the tool to get the text back.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from contextlens.models import estimate_tokens

RECEIPT_PATTERN = re.compile(r"cl_[0-9a-f]{24}")


@dataclass(frozen=True, slots=True)
class Receipt:
    """Metadata for one recoverable original observation."""

    receipt_id: str
    content_hash: str
    line_count: int
    char_count: int
    token_count: int
    created_at: str
    tool: str | None
    category: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "content_hash": self.content_hash,
            "line_count": self.line_count,
            "char_count": self.char_count,
            "token_count": self.token_count,
            "created_at": self.created_at,
            "tool": self.tool,
            "category": self.category,
        }


class ReceiptStore:
    """Persist originals locally and recover exact text or line ranges."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.recoveries = 0
        self.recovered_tokens = 0

    def save(
        self,
        content: str,
        *,
        tool: str | None = None,
        category: str | None = None,
    ) -> Receipt:
        self.root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        receipt = Receipt(
            receipt_id=f"cl_{digest[:24]}",
            content_hash=digest,
            line_count=len(content.splitlines()),
            char_count=len(content),
            token_count=estimate_tokens(content),
            created_at=datetime.now(UTC).isoformat(),
            tool=tool,
            category=category,
        )
        content_path, metadata_path = self._paths(receipt.receipt_id)
        if content_path.exists():
            if content_path.read_bytes().decode("utf-8") != content:
                raise RuntimeError("receipt hash collision")
        else:
            _atomic_write(content_path, content)
        if not metadata_path.exists():
            _atomic_write(
                metadata_path,
                json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n",
            )
        return receipt

    def metadata(self, receipt_id: str) -> Receipt:
        _, metadata_path = self._paths(receipt_id)
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise KeyError(receipt_id) from error
        return Receipt(
            receipt_id=str(value["receipt_id"]),
            content_hash=str(value["content_hash"]),
            line_count=int(value["line_count"]),
            char_count=int(value["char_count"]),
            token_count=int(value["token_count"]),
            created_at=str(value["created_at"]),
            tool=str(value["tool"]) if value.get("tool") else None,
            category=str(value["category"]) if value.get("category") else None,
        )

    def read(
        self,
        receipt_id: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Return the exact original text, optionally one 1-based line range."""

        content_path, _ = self._paths(receipt_id)
        try:
            content = content_path.read_bytes().decode("utf-8")
        except FileNotFoundError as error:
            raise KeyError(receipt_id) from error
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if f"cl_{digest[:24]}" != receipt_id:
            raise RuntimeError("receipt content integrity check failed")
        if start_line is not None or end_line is not None:
            if start_line is None or end_line is None:
                raise ValueError("start_line and end_line must be supplied together")
            if start_line < 1 or end_line < start_line:
                raise ValueError("invalid line range")
            lines = content.splitlines(keepends=True)
            content = "".join(lines[start_line - 1 : end_line])
        self.recoveries += 1
        self.recovered_tokens += estimate_tokens(content)
        return content

    def _paths(self, receipt_id: str) -> tuple[Path, Path]:
        if RECEIPT_PATTERN.fullmatch(receipt_id) is None:
            raise ValueError("invalid receipt identifier")
        return self.root / f"{receipt_id}.txt", self.root / f"{receipt_id}.json"


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content.encode("utf-8"))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
