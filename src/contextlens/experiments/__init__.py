"""Controlled replay experiments."""

from contextlens.experiments.adapters import AgentAdapter, SubprocessAgentAdapter
from contextlens.experiments.cache import MemoryReplayCache, ReplayCache
from contextlens.experiments.codex_cli import (
    CodexCliAgentAdapter,
    CodexCliExecutionError,
    CodexCliTimeoutError,
    render_codex_prompt,
)
from contextlens.experiments.coordinator import (
    DeterministicExperimentCoordinator,
    ExperimentCandidate,
    ExperimentLifecycle,
    ExperimentPlan,
    ExperimentStatus,
    PlannedExperiment,
    PlannedRun,
)
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
from contextlens.experiments.mutations import (
    ContextMutation,
    MutationApplication,
    MutationOperation,
    Summarizer,
    SummaryResult,
    apply_mutations,
)
from contextlens.experiments.paired_runner import (
    PairedAdaptiveSearchRun,
    PairedAdaptiveSearchRunner,
    PairedInvocation,
    PairedRunError,
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
from contextlens.experiments.verification import (
    CommandWorkspaceVerifier,
    WorkspaceVerification,
    WorkspaceVerifier,
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
    "ContextMutation",
    "CodexCliAgentAdapter",
    "CodexCliExecutionError",
    "CodexCliTimeoutError",
    "CommandWorkspaceVerifier",
    "DeterministicExperimentCoordinator",
    "DirectorySnapshot",
    "Evaluation",
    "Evaluator",
    "ExperimentCandidate",
    "ExperimentLifecycle",
    "ExperimentPlan",
    "ExperimentStatus",
    "FileChange",
    "GroupDecision",
    "MemoryReplayCache",
    "MutationApplication",
    "MutationOperation",
    "PairedAdaptiveSearchRun",
    "PairedAdaptiveSearchRunner",
    "PairedInvocation",
    "PairedRunError",
    "PlannedExperiment",
    "PlannedRun",
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
    "Summarizer",
    "SummaryResult",
    "WorkspaceVerification",
    "WorkspaceVerifier",
    "apply_mutations",
    "render_codex_prompt",
]
