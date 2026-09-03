"""End-to-end task-conditioned pruning pipeline."""

from __future__ import annotations

import ast
import time
from collections import defaultdict

from contextlens.pruning.model import (
    LineDecision,
    LineReason,
    ObservationKind,
    PruneRequest,
    PruneResult,
    estimate_tokens,
)
from contextlens.pruning.receipts import Receipt, ReceiptStore
from contextlens.pruning.render import render_python_skeleton
from contextlens.pruning.scoring import SemanticScorer
from contextlens.pruning.structure import close_python_dependencies


class ContextPruner:
    """Combine semantic evidence with deterministic structural support."""

    def __init__(self, scorer: SemanticScorer, receipts: ReceiptStore) -> None:
        self.scorer = scorer
        self.receipts = receipts

    def prune(self, request: PruneRequest) -> PruneResult:
        started = time.perf_counter()
        receipt = self.receipts.save(request)
        original_tokens = estimate_tokens(request.content)
        if not request.content:
            return self._passthrough(
                request, receipt, original_tokens, started, "empty_observation"
            )
        if original_tokens < request.minimum_tokens:
            return self._passthrough(
                request, receipt, original_tokens, started, "below_minimum_tokens"
            )
        if request.kind is not ObservationKind.CODE:
            return self._passthrough(
                request, receipt, original_tokens, started, "unsupported_kind"
            )
        if request.language not in {None, "py", "python"}:
            return self._passthrough(
                request, receipt, original_tokens, started, "unsupported_language"
            )

        try:
            scores = self.scorer.score(request)
        except Exception:
            return self._passthrough(
                request, receipt, original_tokens, started, "semantic_backend_error"
            )
        line_count = len(request.content.splitlines())
        covered_lines = set(scores.line_scores) | set(scores.dependency_scores)
        combined = {
            line: (
                scores.semantic_weight * scores.line_scores.get(line, 0.0)
                + (1 - scores.semantic_weight)
                * scores.dependency_scores.get(line, 0.0)
            )
            for line in covered_lines
            if line <= line_count
        }
        selected = {
            line for line, score in combined.items() if score >= request.threshold
        }
        if not selected:
            return self._passthrough(
                request, receipt, original_tokens, started, "no_relevant_lines"
            )
        structural = close_python_dependencies(
            request.content,
            selected,
            max_hops=request.dependency_hops,
        )
        if structural.parse_error:
            return self._passthrough(
                request, receipt, original_tokens, started, "source_parse_error"
            )

        reasons: dict[int, set[LineReason]] = defaultdict(set)
        for line in selected:
            if scores.line_scores.get(line, 0.0) > 0:
                reasons[line].add(LineReason.SEMANTIC)
            if scores.dependency_scores.get(line, 0.0) > 0:
                reasons[line].add(LineReason.DEPENDENCY)
        for line, line_reasons in structural.reasons.items():
            reasons[line].update(line_reasons)
        for line in selected:
            for nearby in range(
                max(1, line - request.context_radius),
                min(line_count, line + request.context_radius) + 1,
            ):
                if nearby not in selected:
                    reasons[nearby].add(LineReason.LOCAL_CONTEXT)

        rendered = render_python_skeleton(
            request.content,
            set(reasons),
            receipt_id=receipt.receipt_id,
        )
        try:
            ast.parse(rendered.text)
        except SyntaxError:
            return self._passthrough(
                request, receipt, original_tokens, started, "render_validation_error"
            )
        retained_tokens = estimate_tokens(rendered.text)
        if retained_tokens >= original_tokens:
            return self._passthrough(
                request, receipt, original_tokens, started, "no_net_reduction"
            )
        decisions = tuple(
            LineDecision(
                line_number=line,
                semantic_score=float(scores.line_scores.get(line, 0.0)),
                reasons=tuple(sorted(line_reasons, key=lambda item: item.value)),
                dependency_score=float(scores.dependency_scores.get(line, 0.0)),
                combined_score=float(combined.get(line, 0.0)),
            )
            for line, line_reasons in sorted(reasons.items())
        )
        return PruneResult(
            text=rendered.text,
            receipt_id=receipt.receipt_id,
            content_hash=request.content_hash,
            backend=scores.backend,
            decisions=decisions,
            omitted_ranges=rendered.omitted_ranges,
            original_lines=line_count,
            retained_lines=len(decisions),
            original_tokens=original_tokens,
            retained_tokens=retained_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _passthrough(
        self,
        request: PruneRequest,
        receipt: Receipt,
        original_tokens: int,
        started: float,
        reason: str,
    ) -> PruneResult:
        return PruneResult(
            text=request.content,
            receipt_id=receipt.receipt_id,
            content_hash=request.content_hash,
            backend="passthrough",
            decisions=(),
            omitted_ranges=(),
            original_lines=receipt.line_count,
            retained_lines=receipt.line_count,
            original_tokens=original_tokens,
            retained_tokens=original_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
            bypass_reason=reason,
        )
