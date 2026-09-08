"""Unit tests for OutcomeFeedbackEvent schema and /api/v1/feedback API endpoint."""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.schemas.contract import (
    OutcomeFeedbackEvent,
    FeedbackOutcomeType,
    FeedbackSource,
    UserRating,
    UserFeedbackDetails,
)


@pytest.fixture
def client():
    return TestClient(app)


def test_outcome_feedback_schema_valid():
    """Test valid outcome feedback event schema instantiation."""
    event = OutcomeFeedbackEvent(
        feedback_id="fb_12345_test",
        correlation_id="corr_98765",
        outcome_type=FeedbackOutcomeType.SUCCESSFUL_COMPLETION,
        source=FeedbackSource.SYSTEM,
        routing_metadata={
            "coarse_route": "simple-model candidate",
            "model_route": "fast_cheap",
            "task_category": "greeting",
        },
        execution_metadata={
            "duration_ms": 320.5,
            "failure_reason": None,
        }
    )
    assert event.feedback_id == "fb_12345_test"
    assert event.correlation_id == "corr_98765"
    assert event.outcome_type == FeedbackOutcomeType.SUCCESSFUL_COMPLETION
    assert event.source == FeedbackSource.SYSTEM
    assert event.routing_metadata["model_route"] == "fast_cheap"
    assert event.execution_metadata["duration_ms"] == 320.5
    assert event.privacy_preserving is True


def test_all_five_outcome_types():
    """Verify that all 5 requested outcome types are supported."""
    expected_types = {
        "SUCCESSFUL_COMPLETION",
        "USER_REJECTION",
        "OPTIMIZATION_BYPASS",
        "ESCALATION",
        "ERROR",
    }
    actual_types = {t.value for t in FeedbackOutcomeType}
    assert actual_types == expected_types


def test_user_feedback_data_model():
    """Test prepared data model for future optional user feedback control."""
    user_details = UserFeedbackDetails(
        rating=UserRating.NEGATIVE,
        rejection_reason="UNWANTED_REWRITE",
        notes="Preferred original prompt wording.",
        submitted_at=1700000000000,
    )
    event = OutcomeFeedbackEvent(
        feedback_id="fb_user_001",
        correlation_id="corr_user_001",
        outcome_type=FeedbackOutcomeType.USER_REJECTION,
        source=FeedbackSource.USER,
        user_feedback=user_details,
    )
    assert event.source == FeedbackSource.USER
    assert event.outcome_type == FeedbackOutcomeType.USER_REJECTION
    assert event.user_feedback is not None
    assert event.user_feedback.rating == UserRating.NEGATIVE
    assert event.user_feedback.rejection_reason == "UNWANTED_REWRITE"


def test_forbidden_extra_fields():
    """Verify that extra fields are forbidden to prevent schema drift or leaks."""
    with pytest.raises(Exception):
        OutcomeFeedbackEvent(
            feedback_id="fb_leak",
            correlation_id="corr_leak",
            outcome_type=FeedbackOutcomeType.SUCCESSFUL_COMPLETION,
            raw_prompt="Leak attempt",  # Extra field!
        )


def test_post_feedback_endpoint_success(client):
    """Test POST /api/v1/feedback endpoint with valid payload."""
    payload = {
        "feedback_id": "fb_api_001",
        "correlation_id": "corr_api_001",
        "outcome_type": "SUCCESSFUL_COMPLETION",
        "source": "SYSTEM",
        "routing_metadata": {
            "coarse_route": "simple-model candidate",
            "model_route": "gpt-4o-mini",
        },
        "execution_metadata": {
            "duration_ms": 250,
        },
    }
    response = client.post(
        "/api/v1/feedback",
        json=payload,
        headers={"X-Correlation-ID": "corr_api_001"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["received"] is True
    assert data["feedback_id"] == "fb_api_001"
    assert response.headers.get("X-Correlation-ID") == "corr_api_001"


def test_post_feedback_with_user_control_data(client):
    """Test POST /api/v1/feedback with user feedback control data."""
    payload = {
        "feedback_id": "fb_user_ctrl_002",
        "correlation_id": "corr_user_ctrl_002",
        "outcome_type": "USER_REJECTION",
        "source": "USER",
        "user_feedback": {
            "rating": "NEGATIVE",
            "rejection_reason": "HIGH_LATENCY",
            "notes": "Response took too long to stream.",
        }
    }
    response = client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["received"] is True
    assert data["feedback_id"] == "fb_user_ctrl_002"


def test_post_feedback_invalid_outcome_type(client):
    """Test POST /api/v1/feedback rejects invalid outcome types."""
    payload = {
        "feedback_id": "fb_bad",
        "correlation_id": "corr_bad",
        "outcome_type": "INVALID_OUTCOME_NAME",
    }
    response = client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 422
