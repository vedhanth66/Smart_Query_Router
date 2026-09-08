"""Schemas for backend training and evaluation dataset curation pipeline."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contract import (
    OutcomeFeedbackEvent,
    RouteExecutionMetadata,
)


class CandidateSourceType(str, Enum):
    """Origin source of the candidate item."""
    FEEDBACK = "FEEDBACK"
    ESCALATION = "ESCALATION"
    SYNTHETIC_BENCHMARK = "SYNTHETIC_BENCHMARK"
    MANUAL = "MANUAL"


class ReviewStatus(str, Enum):
    """Lifecycle state of a candidate in the manual review boundary."""
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CandidateRejectionCategory(str, Enum):
    """Standardized reasons for rejecting a candidate during manual review."""
    PRIVACY_RISK = "PRIVACY_RISK"
    LOW_QUALITY = "LOW_QUALITY"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    DUPLICATE = "DUPLICATE"
    OTHER = "OTHER"


class DatasetSplit(str, Enum):
    """Target evaluation or training split for approved records."""
    EVAL = "EVAL"
    TRAIN = "TRAIN"
    BENCHMARK = "BENCHMARK"


class SanitizedCandidateMetadata(BaseModel):
    """Sanitized operational and routing metadata for evaluation."""
    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(..., min_length=1, max_length=128)
    task_category: str | None = Field(default=None, max_length=64)
    complexity_level: str | None = Field(default=None, max_length=32)
    route: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=128)
    escalated_model_id: str | None = Field(default=None, max_length=128)
    latency_ms: float | None = Field(default=None, ge=0.0)
    outcome_type: str | None = Field(default=None, max_length=64)
    user_rating: str | None = Field(default=None, max_length=32)
    rejection_reason: str | None = Field(default=None, max_length=128)
    escalation_occurred: bool = False
    escalation_reason: str | None = Field(default=None, max_length=512)
    evaluator_metrics: dict[str, Any] = Field(default_factory=dict)
    has_rich_content: bool = False
    notes: str | None = Field(default=None, max_length=256)


class TrainingCandidate(BaseModel):
    """Core dataset candidate staged for manual review and offline evaluation.
    
    GUARANTEES:
    1. Text snippets are stored only if authorized_for_eval=True and bounded to <= 500 chars.
    2. PII patterns (emails, IPs, API tokens) are redacted prior to storage.
    3. Cannot enter training or evaluation datasets until explicitly approved by a reviewer.
    4. Decoupled from online training: never triggers automated model updates.
    """
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., min_length=1, max_length=128)
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))
    source_type: CandidateSourceType
    correlation_id: str = Field(..., min_length=1, max_length=128)
    sanitized_metadata: SanitizedCandidateMetadata
    text_snippet: str | None = Field(
        default=None,
        max_length=1000,
        description="Minimum bounded, redacted text necessary for authorized offline evaluation"
    )
    text_redacted: bool = False
    authorized_for_eval: bool = False
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    reviewed_by: str | None = Field(default=None, max_length=128)
    reviewed_at: int | None = None
    rejection_category: CandidateRejectionCategory | None = None
    rejection_notes: str | None = Field(default=None, max_length=1000)
    target_split: DatasetSplit = DatasetSplit.EVAL


class ReviewDecisionRequest(BaseModel):
    """Human reviewer decision payload."""
    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    reviewer_id: str = Field(..., min_length=1, max_length=128)
    rejection_category: CandidateRejectionCategory | None = None
    rejection_notes: str | None = Field(default=None, max_length=1000)
    target_split: DatasetSplit | None = None


class DatasetStatsResponse(BaseModel):
    """Summary metrics of candidates across review statuses and splits."""
    model_config = ConfigDict(extra="forbid")

    total_candidates: int = Field(ge=0)
    pending_review_count: int = Field(ge=0)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    by_source: dict[str, int] = Field(default_factory=dict)
    by_split: dict[str, int] = Field(default_factory=dict)


class CandidateIngestFeedbackRequest(BaseModel):
    """Payload to stage a candidate from outcome feedback with optional authorized text."""
    model_config = ConfigDict(extra="forbid")

    event: OutcomeFeedbackEvent
    raw_text: str | None = Field(default=None, max_length=5000)
    authorized_for_eval: bool = False


class CandidateIngestEscalationRequest(BaseModel):
    """Payload to stage a candidate from an escalation execution record."""
    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(..., min_length=1, max_length=128)
    route_metadata: RouteExecutionMetadata
    query_text: str | None = Field(default=None, max_length=5000)
    task_category: str | None = Field(default=None, max_length=64)
    authorized_for_eval: bool = False
