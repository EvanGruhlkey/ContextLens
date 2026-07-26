"""Local content-addressed artifact storage."""

from __future__ import annotations

import hashlib
from pathlib import Path

from contextlens.trace.model import ContentRef


class ArtifactStore:
    """Store and verify large trace payloads by SHA-256 digest."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(
        self,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
    ) -> ContentRef:
        hex_digest = hashlib.sha256(content).hexdigest()
        target = self._path(hex_digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != content:
                raise OSError(f"artifact digest collision at {target}")
        else:
            target.write_bytes(content)
        return ContentRef(
            digest=f"sha256:{hex_digest}",
            byte_length=len(content),
            media_type=media_type,
        )

    def get(self, reference: ContentRef) -> bytes:
        content = self._path(reference.digest[7:]).read_bytes()
        if len(content) != reference.byte_length:
            raise ValueError(f"artifact length mismatch for {reference.digest}")
        actual = hashlib.sha256(content).hexdigest()
        if actual != reference.digest[7:]:
            raise ValueError(f"artifact digest mismatch for {reference.digest}")
        return content

    def _path(self, hex_digest: str) -> Path:
        return self.root / "sha256" / hex_digest[:2] / hex_digest[2:]

