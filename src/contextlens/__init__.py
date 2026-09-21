"""ContextLens reduces the context a coding model has to read.

Two layers, both driven only by cheap Jev relevance decisions:

* live pruning -- one large tool result is chunked, critical lines are kept
  deterministically, and Jev answers KEEP or DROP for the rest;
* transcript compaction -- old tool calls and results become compact
  descriptors, and Jev's two answers per interaction map to KEEP, TRUNCATE, or
  DROP.

Both fail open, and everything removed stays exactly recoverable by receipt.
"""

from contextlens.compaction import (
    CompactionConfig,
    CompactionResult,
    compact_transcript,
)
from contextlens.filtering import (
    OutputPruner,
    PruneConfig,
    PruneOutcome,
    PruneRequest,
    PruneSession,
)
from contextlens.jev import Evaluation, JevError, JevGateway, JevUsage, Judge
from contextlens.models import Message, ToolResult, ToolUse, estimate_tokens
from contextlens.receipts import Receipt, ReceiptStore

__version__ = "0.2.0"

__all__ = [
    "CompactionConfig",
    "CompactionResult",
    "Evaluation",
    "JevError",
    "JevGateway",
    "JevUsage",
    "Judge",
    "Message",
    "OutputPruner",
    "PruneConfig",
    "PruneOutcome",
    "PruneRequest",
    "PruneSession",
    "Receipt",
    "ReceiptStore",
    "ToolResult",
    "ToolUse",
    "__version__",
    "compact_transcript",
    "estimate_tokens",
]
