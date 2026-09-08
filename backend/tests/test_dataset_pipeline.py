"""Unit and integration tests for backend dataset curation pipeline.

Verifies:
1. Selective ingestion: Only informative signals (errors, rejections, escalations) are staged.
2. Metadata sanitization: Forbidden keys (cookies, tokens, raw prompts) are stripped.
3. Minimal text & authorization gate: Snippets are bounded, redacted, and gated on authorization.
4. Manual review boundary: Human approval is strictly required before records enter exportable datasets.
5. Rejection handling: Privacy-sensitive or low-quality candidates can be rejected with categories.
6. No-automatic-retraining guarantee: Decoupled staging ensures no automatic retraining occurs.
7. FastAPI endpoint functionality: Full CRUD, review, export, and stats endpoints.
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dataset.sanitizer import sanitize_metadata, sanitize_text_snippet
from app.dataset.repository import (
    CandidateRepository,
    CandidateNotFoundError,
    InvalidReviewStateTransitionError,
)
from app.dataset.pipeline import DatasetCurationPipeline
from app.dataset import default_candidate_repository
from app.schemas.contract import (
    FeedbackOutcomeType,
    FeedbackSource,
    OutcomeFeedbackEvent,
    RouteExecutionMetadata,
    UserFeedbackDetails,
    UserRating,
)
from app.schemas.dataset import (
    CandidateRejectionCategory,
    CandidateSourceType,
    DatasetSplit,
    ReviewDecisionRequest,
    ReviewStatus,
    TrainingCandidate,
)


@pytest.fixture(autouse=True)
def clean_repository():
    """Ensure repository is empty before each test."""
    default_candidate_repository.clear()
    yield
    default_candidate_repository.clear()


@pytest.fixture
def client():
    return TestClient(app)


# -----------------------------------------------------------------------------
# 1. Metadata Sanitizer Tests
# -----------------------------------------------------------------------------

def test_sanitize_metadata_purges_sensitive_keys():
    """Verify that forbidden credential and conversational keys are recursively removed."""
    dirty_meta = {
        "coarse_route": "simple-model candidate",
        "cookie": "session_id=secret123",
        "auth_token": "Bearer abcxyz",
        "api_key": "sk-1234567890abcdef",
        "raw_prompt": "My private query",
        "nested": {
            "task_category": "coding",
            "session_token": "jwt.secret.here",
            "safe_param": 42,
        },
        "list_data": [
            {"safe": True, "password": "hidden"},
            "regular_string"
        ]
    }

    clean = sanitize_metadata(dirty_meta)
    assert "cookie" not in clean
    assert "auth_token" not in clean
    assert "api_key" not in clean
    assert "raw_prompt" not in clean
    assert clean["coarse_route"] == "simple-model candidate"
    assert clean["nested"]["task_category"] == "coding"
    assert clean["nested"]["safe_param"] == 42
    assert "session_token" not in clean["nested"]
    assert clean["list_data"][0]["safe"] is True
    assert "password" not in clean["list_data"][0]


# -----------------------------------------------------------------------------
# 2. Text Snippet Sanitizer & Authorization Gate Tests
# -----------------------------------------------------------------------------

def test_sanitize_text_snippet_authorization_gate():
    """Verify that text is completely omitted when authorized_for_eval is False."""
    text = "Write a python script to parse CSV files."
    snippet, redacted = sanitize_text_snippet(text, max_chars=500, authorized=False)
    assert snippet is None
    assert redacted is False


def test_sanitize_text_snippet_bounding_and_pii_redaction():
    """Verify bounded character ceiling and PII redaction (email, IP, API key, bearer)."""
    text_with_pii = (
        "Contact alice@example.com or server 192.168.1.100. "
        "Use Bearer eyJhbGciOiJIUzI1NiJ9.secret and api_key='sk-abcdef1234567890'. "
        "Here is more filler text: " + ("x" * 600)
    )

    snippet, redacted = sanitize_text_snippet(text_with_pii, max_chars=300, authorized=True)
    assert snippet is not None
    assert len(snippet) <= 300
    assert redacted is True
    assert "alice@example.com" not in snippet
    assert "[EMAIL]" in snippet
    assert "192.168.1.100" not in snippet
    assert "[IP_ADDRESS]" in snippet
    assert "Bearer [REDACTED_SECRET]" in snippet
    assert "[REDACTED_SECRET]" in snippet


# -----------------------------------------------------------------------------
# 3. Selective Ingestion & Conversion Pipeline Tests
# -----------------------------------------------------------------------------

def test_pipeline_filters_routine_clean_completions():
    """Verify that standard clean completions without negative feedback are not staged."""
    pipeline = DatasetCurationPipeline(repository=CandidateRepository())

    clean_event = OutcomeFeedbackEvent(
        feedback_id="fb_clean_001",
        correlation_id="corr_clean_001",
        outcome_type=FeedbackOutcomeType.SUCCESSFUL_COMPLETION,
        source=FeedbackSource.SYSTEM,
        routing_metadata={"coarse_route": "simple-model candidate"},
        execution_metadata={"duration_ms": 120.0},
    )

    # should_select should return False
    assert pipeline.should_select_feedback(clean_event) is False
    candidate = pipeline.convert_feedback_event(clean_event)
    assert candidate is None


def test_pipeline_selects_and_converts_negative_feedback():
    """Verify that negative feedback (USER_REJECTION, negative rating) is staged in PENDING_REVIEW."""
    repo = CandidateRepository()
    pipeline = DatasetCurationPipeline(repository=repo)

    negative_event = OutcomeFeedbackEvent(
        feedback_id="fb_neg_001",
        correlation_id="corr_neg_001",
        outcome_type=FeedbackOutcomeType.USER_REJECTION,
        source=FeedbackSource.USER,
        routing_metadata={"coarse_route": "simple-model candidate", "task_category": "coding"},
        execution_metadata={"duration_ms": 250.0},
        user_feedback=UserFeedbackDetails(
            rating=UserRating.NEGATIVE,
            rejection_reason="UNWANTED_REWRITE",
            notes="Changed my function name.",
        )
    )

    assert pipeline.should_select_feedback(negative_event) is True
    candidate = pipeline.convert_feedback_event(
        negative_event,
        raw_text="def parse(user): pass",
        authorized=True
    )

    assert candidate is not None
    assert candidate.source_type == CandidateSourceType.FEEDBACK
    assert candidate.correlation_id == "corr_neg_001"
    assert candidate.review_status == ReviewStatus.PENDING_REVIEW
    assert candidate.sanitized_metadata.user_rating == "NEGATIVE"
    assert candidate.sanitized_metadata.rejection_reason == "UNWANTED_REWRITE"
    assert candidate.text_snippet == "def parse(user): pass"
    assert candidate.authorized_for_eval is True
    assert repo.get_candidate(candidate.candidate_id) is not None


def test_pipeline_selects_and_converts_escalation_records():
    """Verify that model escalations are selected and staged in PENDING_REVIEW."""
    repo = CandidateRepository()
    pipeline = DatasetCurationPipeline(repository=repo)

    route_meta = RouteExecutionMetadata(
        route="simple-model candidate",
        model_id="gpt-4o",
        latency_ms=1850.0,
        escalation_occurred=True,
        escalation_reason="INCOMPLETE (code_block_incomplete)",
        evaluation_metadata={
            "evaluator_id": "heuristic-incompleteness-v1",
            "completeness": 0.55,
            "confidence": 0.85,
            "original_model_id": "gpt-4o-mini",
            "detected_issues": [{"issue_code": "code_block_incomplete", "severity": "HIGH"}],
        }
    )

    assert pipeline.should_select_escalation(route_meta) is True
    candidate = pipeline.convert_escalation_record(
        correlation_id="corr_esc_001",
        route_metadata=route_meta,
        query_text="Write a red-black tree in C++",
        task_category="coding",
        authorized=True,
    )

    assert candidate is not None
    assert candidate.source_type == CandidateSourceType.ESCALATION
    assert candidate.correlation_id == "corr_esc_001"
    assert candidate.review_status == ReviewStatus.PENDING_REVIEW
    assert candidate.sanitized_metadata.escalation_occurred is True
    assert candidate.sanitized_metadata.model_id == "gpt-4o-mini"
    assert candidate.sanitized_metadata.escalated_model_id == "gpt-4o"
    assert candidate.sanitized_metadata.evaluator_metrics["detected_issues_count"] == 1


# -----------------------------------------------------------------------------
# 4. Manual Review Boundary & Export Tests
# -----------------------------------------------------------------------------

def test_manual_review_approval_and_rejection_lifecycle():
    """Verify state transitions and that only APPROVED candidates are exportable."""
    repo = CandidateRepository()
    pipeline = DatasetCurationPipeline(repository=repo)

    # Create two candidates
    event1 = OutcomeFeedbackEvent(
        feedback_id="fb_001",
        correlation_id="corr_001",
        outcome_type=FeedbackOutcomeType.USER_REJECTION,
        source=FeedbackSource.USER,
        user_feedback=UserFeedbackDetails(rating=UserRating.NEGATIVE)
    )
    event2 = OutcomeFeedbackEvent(
        feedback_id="fb_002",
        correlation_id="corr_002",
        outcome_type=FeedbackOutcomeType.ERROR,
        source=FeedbackSource.SYSTEM,
        execution_metadata={"failure_reason": "TIMEOUT"}
    )

    cand1 = pipeline.convert_feedback_event(event1, raw_text="Query 1", authorized=True)
    cand2 = pipeline.convert_feedback_event(event2, raw_text="Query 2 with secret sk-123456789012", authorized=True)

    # 1. Prior to review, export must be empty
    assert repo.export_approved(format="json") == []
    assert repo.export_approved(format="jsonl") == ""

    # 2. Reject candidate 2 as PRIVACY_RISK
    dec_reject = ReviewDecisionRequest(
        status=ReviewStatus.REJECTED,
        reviewer_id="human_reviewer_01",
        rejection_category=CandidateRejectionCategory.PRIVACY_RISK,
        rejection_notes="Contains API key in prompt.",
    )
    rejected_cand = repo.review_candidate(cand2.candidate_id, dec_reject)
    assert rejected_cand.review_status == ReviewStatus.REJECTED
    assert rejected_cand.rejection_category == CandidateRejectionCategory.PRIVACY_RISK
    assert rejected_cand.reviewed_by == "human_reviewer_01"

    # 3. Approve candidate 1 into EVAL split
    dec_approve = ReviewDecisionRequest(
        status=ReviewStatus.APPROVED,
        reviewer_id="human_reviewer_02",
        target_split=DatasetSplit.EVAL,
    )
    approved_cand = repo.review_candidate(cand1.candidate_id, dec_approve)
    assert approved_cand.review_status == ReviewStatus.APPROVED
    assert approved_cand.target_split == DatasetSplit.EVAL
    assert approved_cand.reviewed_by == "human_reviewer_02"

    # 4. Verify export contains ONLY the approved candidate
    exported_json = repo.export_approved(format="json")
    assert len(exported_json) == 1
    assert exported_json[0]["candidate_id"] == cand1.candidate_id
    assert exported_json[0]["review_status"] == "APPROVED"

    exported_jsonl = repo.export_approved(format="jsonl")
    lines = [line for line in exported_jsonl.split("\n") if line.strip()]
    assert len(lines) == 1
    parsed_record = json.loads(lines[0])
    assert parsed_record["candidate_id"] == cand1.candidate_id

    # 5. Check statistics
    stats = repo.get_stats()
    assert stats.total_candidates == 2
    assert stats.pending_review_count == 0
    assert stats.approved_count == 1
    assert stats.rejected_count == 1


def test_invalid_review_transitions():
    """Verify that invalid review transitions or unknown IDs raise errors."""
    repo = CandidateRepository()
    with pytest.raises(CandidateNotFoundError):
        repo.review_candidate(
            "unknown_id",
            ReviewDecisionRequest(status=ReviewStatus.APPROVED, reviewer_id="rev_01")
        )

    cand = TrainingCandidate(
        candidate_id="cand_test_01",
        source_type=CandidateSourceType.MANUAL,
        correlation_id="corr_test",
        sanitized_metadata={"correlation_id": "corr_test"},
    )
    repo.add_candidate(cand)

    with pytest.raises(InvalidReviewStateTransitionError):
        repo.review_candidate(
            "cand_test_01",
            ReviewDecisionRequest(status=ReviewStatus.PENDING_REVIEW, reviewer_id="rev_01")
        )


# -----------------------------------------------------------------------------
# 5. No Automatic Retraining Architectural Guard Tests
# -----------------------------------------------------------------------------

def test_no_automatic_retraining_guarantee():
    """Verify that the curation pipeline explicitly enforces no automatic retraining."""
    pipeline = DatasetCurationPipeline(repository=CandidateRepository())
    assert pipeline.automatic_retraining_enabled is False

    # Ingesting and approving candidates must remain purely staging/curation operations
    event = OutcomeFeedbackEvent(
        feedback_id="fb_guard_001",
        correlation_id="corr_guard_001",
        outcome_type=FeedbackOutcomeType.USER_REJECTION,
        user_feedback=UserFeedbackDetails(rating=UserRating.NEGATIVE),
    )
    cand = pipeline.convert_feedback_event(event)
    assert cand is not None
    assert pipeline.automatic_retraining_enabled is False


# -----------------------------------------------------------------------------
# 6. FastAPI Endpoints Integration Tests
# -----------------------------------------------------------------------------

def test_api_feedback_auto_staging_integration(client):
    """Test that POST /api/v1/feedback automatically stages negative feedback in PENDING_REVIEW."""
    payload = {
        "feedback_id": "fb_api_neg_001",
        "correlation_id": "corr_api_neg_001",
        "outcome_type": "USER_REJECTION",
        "source": "USER",
        "routing_metadata": {
            "coarse_route": "simple-model candidate",
            "task_category": "analysis",
        },
        "execution_metadata": {"duration_ms": 350.0},
        "user_feedback": {
            "rating": "NEGATIVE",
            "rejection_reason": "INCORRECT_ANSWER",
            "notes": "Formula was wrong.",
        }
    }

    res = client.post("/api/v1/feedback", json=payload)
    assert res.status_code == 200

    # Verify staged candidate appears in candidates endpoint
    cand_res = client.get("/api/v1/dataset/candidates?status=PENDING_REVIEW")
    assert cand_res.status_code == 200
    candidates = cand_res.json()
    assert len(candidates) >= 1
    assert candidates[0]["correlation_id"] == "corr_api_neg_001"
    assert candidates[0]["review_status"] == "PENDING_REVIEW"
    assert candidates[0]["sanitized_metadata"]["rejection_reason"] == "INCORRECT_ANSWER"


def test_api_candidate_staging_and_review_flow(client):
    """Test full staging, inspection, review, and export cycle via REST endpoints."""
    # 1. Stage escalation candidate via endpoint
    esc_payload = {
        "correlation_id": "corr_api_esc_001",
        "route_metadata": {
            "route": "simple-model candidate",
            "model_id": "gpt-4o",
            "latency_ms": 1400.0,
            "escalation_occurred": True,
            "escalation_reason": "LOW_CONFIDENCE (0.45 < 0.70)",
            "evaluation_metadata": {
                "evaluator_id": "heuristic-incompleteness-v1",
                "completeness": 0.6,
                "confidence": 0.45,
                "original_model_id": "gpt-4o-mini",
            }
        },
        "query_text": "What is the capital of France?",
        "task_category": "factual",
        "authorized_for_eval": True,
    }

    stage_res = client.post("/api/v1/dataset/candidates/from-escalation", json=esc_payload)
    assert stage_res.status_code == 200
    candidate = stage_res.json()
    candidate_id = candidate["candidate_id"]
    assert candidate["review_status"] == "PENDING_REVIEW"
    assert candidate["text_snippet"] == "What is the capital of France?"

    # 2. Inspect candidate details
    get_res = client.get(f"/api/v1/dataset/candidates/{candidate_id}")
    assert get_res.status_code == 200
    assert get_res.json()["candidate_id"] == candidate_id

    # 3. Verify export is currently empty (unapproved)
    export_empty = client.get("/api/v1/dataset/export?format=json")
    assert export_empty.status_code == 200
    assert export_empty.json() == []

    # 4. Approve candidate
    review_payload = {
        "status": "APPROVED",
        "reviewer_id": "qa_evaluator_01",
        "target_split": "EVAL",
    }
    review_res = client.post(f"/api/v1/dataset/candidates/{candidate_id}/review", json=review_payload)
    assert review_res.status_code == 200
    reviewed = review_res.json()
    assert reviewed["review_status"] == "APPROVED"
    assert reviewed["reviewed_by"] == "qa_evaluator_01"

    # 5. Export dataset in JSONL format
    export_res = client.get("/api/v1/dataset/export?format=jsonl&split=EVAL")
    assert export_res.status_code == 200
    assert "application/x-ndjson" in export_res.headers["content-type"]
    lines = [l for l in export_res.text.strip().split("\n") if l]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["candidate_id"] == candidate_id

    # 6. Check stats endpoint
    stats_res = client.get("/api/v1/dataset/stats")
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["total_candidates"] == 1
    assert stats["approved_count"] == 1
    assert stats["pending_review_count"] == 0
