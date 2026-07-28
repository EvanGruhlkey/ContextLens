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
from contextlens.experiments.search import (
    AblationGroup,
    AdaptiveAblationPlanner,
    AdaptiveSearchRun,
    AdaptiveSearchRunner,
    GroupDecision,
    ScoreObservation,
    SearchConfig,
    SearchNode,
    SearchReport,
)
from contextlens.experiments.workspace import DirectorySnapshot

__all__ = [
    "AgentAdapter",
    "AgentOutcome",
    "AgentSettings",
    "AblationGroup",
    "AdaptiveAblationPlanner",
    "AdaptiveSearchRun",
    "AdaptiveSearchRunner",
    "ContextVariant",
    "DirectorySnapshot",
    "Evaluation",
    "Evaluator",
    "FileChange",
    "GroupDecision",
    "MemoryReplayCache",
    "ReplayCoordinator",
    "ReplayCache",
    "ReplayRequest",
    "ReplayResult",
    "ReplayStatus",
    "ReplayTask",
    "ReplayWorker",
    "ResourceLimits",
    "ScoreObservation",
    "SearchConfig",
    "SearchNode",
    "SearchReport",
    "SubprocessAgentAdapter",
]
