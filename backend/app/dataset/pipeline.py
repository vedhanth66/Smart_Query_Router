"""Dataset curation pipeline converting selected feedback and escalation records.

GUARANTEES:
1. Selective ingestion: Only informative signals (errors, rejections, escalations) are staged.
2. Privacy preservation: Metadata is sanitized; text is bounded and redacted.
3. Authorization gate: Text snippets are discarded unless authorized_for_eval is True.
4. Manual review boundary: All candidates start in PENDING_REVIEW; only APPROVED records enter datasets.
5. NO automatic retraining: Conversion and staging strictly decouple signals from model training.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.dataset.repository import CandidateRepository
from app.dataset.sanitizer import sanitize_metadata, sanitize_text_snippet
from app.schemas.contract import (
    FeedbackOutcomeType,
    OutcomeFeedbackEvent,
    RouteExecutionMetadata,
    UserRating,
)
from app.schemas.dataset import (
    CandidateSourceType,
    DatasetSplit,
    ReviewStatus,
    SanitizedCandidateMetadata,
    TrainingCandidate,
)


class DatasetCurationPipeline:
    """Orchestrates candidate selection, sanitization, and staging for human review."""

    def __init__(self, repository: CandidateRepository | None = None) -> None:
        self.repository = repository or CandidateRepository()

    @property
    def automatic_retraining_enabled(self) -> bool:
        """Strict architectural guard: pipeline NEVER triggers automatic model retraining."""
        return False

    def should_select_feedback(self, event: OutcomeFeedbackEvent) -> bool:
        """Determines if feedback signal meets high-signal criteria for evaluation/training.
        
        Selection criteria:
        - User rejection (outcome_type == USER_REJECTION)
        - Negative user rating (user_feedback.rating == NEGATIVE)
        - System error or failure (outcome_type == ERROR)
        - Escalation feedback (outcome_type == ESCALATION)
        - Explicit user rejection reason provided
        """
        if event.outcome_type in (
            FeedbackOutcomeType.USER_REJECTION,
            FeedbackOutcomeType.ERROR,
            FeedbackOutcomeType.ESCALATION,
        ):
            return True

        if event.user_feedback and event.user_feedback.rating == UserRating.NEGATIVE:
            return True

        if event.user_feedback and event.user_feedback.rejection_reason:
            return True

        if event.execution_metadata.get("failure_reason"):
            return True

        return False

    def should_select_escalation(self, route_metadata: RouteExecutionMetadata) -> bool:
        """Determines if routing execution meets criteria for candidate staging.
        
        Selection criteria:
        - Escalation occurred (escalation_occurred == True)
        - Evaluator detected issues (incompleteness, low confidence)
        - Route execution failed or required fallback
        """
        if route_metadata.escalation_occurred:
            return True

        if route_metadata.failure_category != "NONE" or route_metadata.fallback_applied:
            return True

        eval_meta = route_metadata.evaluation_metadata or {}
        detected_issues = eval_meta.get("detected_issues") or []
        if len(detected_issues) > 0:
            return True

        return False

    def convert_feedback_event(
        self,
        event: OutcomeFeedbackEvent,
        raw_text: str | None = None,
        authorized: bool = False,
        force_select: bool = False,
    ) -> TrainingCandidate | None:
        """Converts an outcome feedback event into a reviewable TrainingCandidate.
        
        Args:
            event: The ingested OutcomeFeedbackEvent.
            raw_text: Optional raw text snippet (only retained if authorized=True).
            authorized: Whether text retention is authorized for evaluation.
            force_select: If True, bypasses selection filter for synthetic/benchmarking.
            
        Returns:
            TrainingCandidate staged in PENDING_REVIEW, or None if not selected.
        """
        if not force_select and not self.should_select_feedback(event):
            return None

        clean_routing = sanitize_metadata(event.routing_metadata or {})
        clean_execution = sanitize_metadata(event.execution_metadata or {})

        # Extract user feedback details
        user_rating = None
        rejection_reason = None
        notes = None
        if event.user_feedback:
            user_rating = event.user_feedback.rating.value if event.user_feedback.rating else None
            rejection_reason = event.user_feedback.rejection_reason
            notes = event.user_feedback.notes

        # Process bounded text
        snippet, is_redacted = sanitize_text_snippet(
            raw_text,
            max_chars=500,
            authorized=authorized
        )

        sanitized_meta = SanitizedCandidateMetadata(
            correlation_id=event.correlation_id,
            task_category=clean_routing.get("task_category"),
            complexity_level=clean_routing.get("complexity_level"),
            route=clean_routing.get("coarse_route") or clean_routing.get("route"),
            model_id=clean_routing.get("model_route") or clean_routing.get("model_id"),
            escalated_model_id=None,
            latency_ms=clean_execution.get("duration_ms"),
            outcome_type=event.outcome_type.value,
            user_rating=user_rating,
            rejection_reason=rejection_reason,
            escalation_occurred=bool(clean_routing.get("escalation_occurred", False)),
            escalation_reason=clean_routing.get("escalation_reason"),
            evaluator_metrics={},
            has_rich_content=bool(clean_routing.get("has_rich_input", False)),
            notes=notes,
        )

        candidate_id = f"cand_fb_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

        candidate = TrainingCandidate(
            candidate_id=candidate_id,
            source_type=CandidateSourceType.FEEDBACK,
            correlation_id=event.correlation_id,
            sanitized_metadata=sanitized_meta,
            text_snippet=snippet,
            text_redacted=is_redacted,
            authorized_for_eval=authorized and snippet is not None,
            review_status=ReviewStatus.PENDING_REVIEW,
            target_split=DatasetSplit.EVAL,
        )

        return self.repository.add_candidate(candidate)

    def convert_escalation_record(
        self,
        correlation_id: str,
        route_metadata: RouteExecutionMetadata,
        query_text: str | None = None,
        task_category: str | None = None,
        authorized: bool = False,
        force_select: bool = False,
    ) -> TrainingCandidate | None:
        """Converts an escalation execution record into a reviewable TrainingCandidate.
        
        Args:
            correlation_id: Request correlation identifier.
            route_metadata: Execution metadata from gateway and evaluator.
            query_text: Optional query text snippet (retained only if authorized=True).
            task_category: Optional task category.
            authorized: Whether text retention is authorized for evaluation.
            force_select: If True, bypasses selection filter.
            
        Returns:
            TrainingCandidate staged in PENDING_REVIEW, or None if not selected.
        """
        if not force_select and not self.should_select_escalation(route_metadata):
            return None

        clean_eval = sanitize_metadata(route_metadata.evaluation_metadata or {})

        # Process bounded text
        snippet, is_redacted = sanitize_text_snippet(
            query_text,
            max_chars=500,
            authorized=authorized
        )

        sanitized_meta = SanitizedCandidateMetadata(
            correlation_id=correlation_id,
            task_category=task_category,
            complexity_level=None,
            route=route_metadata.route,
            model_id=clean_eval.get("original_model_id") or route_metadata.model_id,
            escalated_model_id=route_metadata.model_id if route_metadata.escalation_occurred else None,
            latency_ms=route_metadata.latency_ms,
            outcome_type=FeedbackOutcomeType.ESCALATION.value if route_metadata.escalation_occurred else "EVAL_FAIL",
            user_rating=None,
            rejection_reason=None,
            escalation_occurred=route_metadata.escalation_occurred,
            escalation_reason=route_metadata.escalation_reason,
            evaluator_metrics={
                "evaluator_id": clean_eval.get("evaluator_id"),
                "completeness": clean_eval.get("completeness"),
                "confidence": clean_eval.get("confidence"),
                "detected_issues_count": len(clean_eval.get("detected_issues") or []),
            },
            has_rich_content=False,
            notes=f"Failure category: {route_metadata.failure_category}" if route_metadata.failure_category != "NONE" else None,
        )

        candidate_id = f"cand_esc_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

        candidate = TrainingCandidate(
            candidate_id=candidate_id,
            source_type=CandidateSourceType.ESCALATION,
            correlation_id=correlation_id,
            sanitized_metadata=sanitized_meta,
            text_snippet=snippet,
            text_redacted=is_redacted,
            authorized_for_eval=authorized and snippet is not None,
            review_status=ReviewStatus.PENDING_REVIEW,
            target_split=DatasetSplit.EVAL,
        )

        return self.repository.add_candidate(candidate)
