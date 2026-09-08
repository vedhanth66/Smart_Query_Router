"""Thread-safe in-memory candidate repository with review lifecycle and export gates."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from app.schemas.dataset import (
    CandidateRejectionCategory,
    CandidateSourceType,
    DatasetSplit,
    DatasetStatsResponse,
    ReviewDecisionRequest,
    ReviewStatus,
    TrainingCandidate,
)


class CandidateNotFoundError(KeyError):
    """Raised when candidate ID does not exist."""
    pass


class InvalidReviewStateTransitionError(ValueError):
    """Raised on invalid review state transition."""
    pass


class CandidateRepository:
    """Thread-safe repository managing training/eval candidates and review lifecycle.
    
    GUARANTEES:
    1. Review boundary: Only candidates with review_status=APPROVED can be exported.
    2. Audit trail: Stores reviewer ID, timestamp, and rejection reason.
    3. Memory capped: Bounded capacity to prevent memory bloat.
    """

    def __init__(self, max_capacity: int = 2000) -> None:
        self._candidates: dict[str, TrainingCandidate] = {}
        self._max_capacity = max_capacity
        self._lock = threading.RLock()

    def add_candidate(self, candidate: TrainingCandidate) -> TrainingCandidate:
        """Adds a candidate to the staging repository."""
        with self._lock:
            # Capacity guard: evict oldest rejected or pending if needed
            if len(self._candidates) >= self._max_capacity and candidate.candidate_id not in self._candidates:
                # Evict oldest rejected first, then oldest pending
                eviction_candidates = sorted(
                    self._candidates.values(),
                    key=lambda c: (0 if c.review_status == ReviewStatus.REJECTED else 1, c.created_at)
                )
                if eviction_candidates:
                    del self._candidates[eviction_candidates[0].candidate_id]

            self._candidates[candidate.candidate_id] = candidate
            return candidate

    def get_candidate(self, candidate_id: str) -> TrainingCandidate | None:
        """Retrieves candidate by ID."""
        with self._lock:
            return self._candidates.get(candidate_id)

    def get_by_correlation_id(self, correlation_id: str) -> list[TrainingCandidate]:
        """Retrieves candidates associated with a correlation ID."""
        with self._lock:
            return [
                c for c in self._candidates.values()
                if c.correlation_id == correlation_id
            ]

    def list_candidates(
        self,
        status: ReviewStatus | None = None,
        source_type: CandidateSourceType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TrainingCandidate]:
        """Lists candidates with optional status and source filtering."""
        with self._lock:
            items = list(self._candidates.values())

            if status is not None:
                items = [c for c in items if c.review_status == status]
            if source_type is not None:
                items = [c for c in items if c.source_type == source_type]

            # Sort newest first
            items.sort(key=lambda c: c.created_at, reverse=True)
            return items[offset:offset + limit]

    def review_candidate(
        self,
        candidate_id: str,
        decision: ReviewDecisionRequest
    ) -> TrainingCandidate:
        """Applies a human review decision to stage or reject a candidate.
        
        Args:
            candidate_id: Unique candidate identifier.
            decision: Reviewer decision payload.
            
        Returns:
            Updated TrainingCandidate instance.
        """
        with self._lock:
            candidate = self._candidates.get(candidate_id)
            if not candidate:
                raise CandidateNotFoundError(f"Candidate {candidate_id} not found")

            if decision.status not in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
                raise InvalidReviewStateTransitionError(
                    f"Invalid review status: {decision.status}. Must be APPROVED or REJECTED."
                )

            now_ms = int(time.time() * 1000)

            # Build updated model
            update_kwargs: dict[str, Any] = {
                "review_status": decision.status,
                "reviewed_by": decision.reviewer_id,
                "reviewed_at": now_ms,
            }

            if decision.status == ReviewStatus.APPROVED:
                update_kwargs["rejection_category"] = None
                update_kwargs["rejection_notes"] = None
                if decision.target_split is not None:
                    update_kwargs["target_split"] = decision.target_split
            else:
                update_kwargs["rejection_category"] = (
                    decision.rejection_category or CandidateRejectionCategory.OTHER
                )
                update_kwargs["rejection_notes"] = decision.rejection_notes

            updated = candidate.model_copy(update=update_kwargs)
            self._candidates[candidate_id] = updated
            return updated

    def export_approved(
        self,
        split: DatasetSplit | None = None,
        format: str = "jsonl"
    ) -> list[dict[str, Any]] | str:
        """Exports ONLY candidates that have passed human review (review_status=APPROVED).
        
        Rejected and pending review items are strictly excluded.
        
        Args:
            split: Optional dataset split filter (EVAL, TRAIN, BENCHMARK).
            format: 'jsonl' for newline-delimited JSON or 'json' for list of dicts.
            
        Returns:
            Newline-delimited string (if jsonl) or list of dictionaries (if json).
        """
        with self._lock:
            approved = [
                c for c in self._candidates.values()
                if c.review_status == ReviewStatus.APPROVED
            ]

            if split is not None:
                approved = [c for c in approved if c.target_split == split]

            # Sort chronologically for deterministic dataset generation
            approved.sort(key=lambda c: c.created_at)

            records: list[dict[str, Any]] = [
                c.model_dump(mode="json") for c in approved
            ]

            if format.lower() == "json":
                return records

            # Default: JSONL
            return "\n".join(json.dumps(r) for r in records)

    def get_stats(self) -> DatasetStatsResponse:
        """Returns aggregate metrics across review statuses and dataset splits."""
        with self._lock:
            total = len(self._candidates)
            pending = 0
            approved = 0
            rejected = 0
            by_source: dict[str, int] = {}
            by_split: dict[str, int] = {}

            for c in self._candidates.values():
                if c.review_status == ReviewStatus.PENDING_REVIEW:
                    pending += 1
                elif c.review_status == ReviewStatus.APPROVED:
                    approved += 1
                elif c.review_status == ReviewStatus.REJECTED:
                    rejected += 1

                by_source[c.source_type.value] = by_source.get(c.source_type.value, 0) + 1
                by_split[c.target_split.value] = by_split.get(c.target_split.value, 0) + 1

            return DatasetStatsResponse(
                total_candidates=total,
                pending_review_count=pending,
                approved_count=approved,
                rejected_count=rejected,
                by_source=by_source,
                by_split=by_split,
            )

    def clear(self) -> None:
        """Resets all candidate storage (primarily for testing)."""
        with self._lock:
            self._candidates.clear()
