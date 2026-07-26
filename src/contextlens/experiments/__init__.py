"""Controlled replay experiments."""

from contextlens.experiments.adapters import AgentAdapter, SubprocessAgentAdapter
from contextlens.experiments.cache import MemoryReplayCache, ReplayCache
from contextlens.experiments.evaluation import Evaluation, Evaluator
from contextlens.experiments.model import (
    AgentOutcome,
    AgentSettings,
    ContextVariant,
    FileChange,
    ReplayRequest,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
    ResourceLimits,
)
from contextlens.experiments.runner import ReplayCoordinator, ReplayWorker
from contextlens.experiments.workspace import DirectorySnapshot

__all__ = [
    "AgentAdapter",
    "AgentOutcome",
    "AgentSettings",
    "ContextVariant",
    "DirectorySnapshot",
    "Evaluation",
    "Evaluator",
    "FileChange",
    "MemoryReplayCache",
    "ReplayCoordinator",
    "ReplayCache",
    "ReplayRequest",
    "ReplayResult",
    "ReplayStatus",
    "ReplayTask",
    "ReplayWorker",
    "ResourceLimits",
    "SubprocessAgentAdapter",
]
