"""Unit tests for backend contract schema and API endpoints."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.schemas.contract import (
    NormalizedQueryPackage,
    ClientMetadata,
    LocalFeatures,
    ContextCandidateTurn,
    DecisionType,
    CoarseRoute,
    TaskCategory,
    ComplexityLevel,
    OptimizationDecisionResponse,
    CacheOutcome,
    ErrorCategory,
    PerformanceTelemetryRecord,
)

client = TestClient(app)


def test_health_check():
    """Verify health probe returns 200 OK with service identifier."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "smart-query-router-backend"
    assert "version" in data


def test_valid_minimal_request():
    """Verify minimal valid query package receives a compliant decision."""
    payload = {
        "request_id": "req_123_abc",
        "query_text": "What is PostgreSQL connection pooling?",
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "hostname": "claude.ai"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == "req_123_abc"
    assert data["decision_type"] in [d.value for d in DecisionType]
    assert 0.0 <= data["confidence"] <= 1.0
    assert "reason_code" in data
    assert "timestamp" in data
    assert "server_version" in data


def test_valid_full_request_with_context_and_features():
    """Verify request with bounded context candidates and local features."""
    payload = {
        "request_id": "req_full_456",
        "query_text": "How do I optimize the pool idle timeout?",
        "context_candidates": [
            {
                "turn_id": "turn_1",
                "role": "user",
                "content": "Configure PostgreSQL with pgBouncer",
                "original_index": 0,
                "relevance_score": 0.65
            },
            {
                "turn_id": "turn_2",
                "role": "assistant",
                "content": "Here is the recommended configuration file.",
                "original_index": 1,
                "relevance_score": 0.40
            }
        ],
        "local_features": {
            "character_count": 42,
            "word_count": 7,
            "has_code": False,
            "has_math": False,
            "has_questions": True,
            "has_urls": False,
            "is_normalized": True,
            "detected_cues": ["how do i"]
        },
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == "req_full_456"
    assert data["confidence"] >= 0.0


def test_short_passthrough_classification():
    """Verify short query receives QUERY_SHORT_PASSTHROUGH reason code."""
    payload = {
        "request_id": "req_short_1",
        "query_text": "hello",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["decision_type"] == "NO_OPTIMIZATION"
    assert data["reason_code"] == "QUERY_SHORT_PASSTHROUGH"


def test_code_feature_triggers_backend_candidate():
    """Verify query with has_code feature is classified as BACKEND_CANDIDATE."""
    payload = {
        "request_id": "req_code_1",
        "query_text": "def test(): return 42",
        "local_features": {
            "has_code": True
        },
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["decision_type"] == "BACKEND_CANDIDATE"
    assert data["reason_code"] == "CODE_ANALYSIS_CANDIDATE"
    assert data["optimization_instructions"] is not None


def test_rejection_of_empty_query():
    """Reject empty or whitespace-only query with HTTP 422."""
    payload = {
        "request_id": "req_empty",
        "query_text": "   ",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 422


def test_rejection_of_missing_metadata():
    """Reject payload missing client_metadata."""
    payload = {
        "request_id": "req_no_meta",
        "query_text": "Valid query without metadata"
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 422


def test_rejection_of_extra_forbidden_fields():
    """Reject payload containing unauthorized extra fields."""
    payload = {
        "request_id": "req_injected",
        "query_text": "Valid query",
        "injected_field": "unauthorized_data",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 422


def test_rejection_of_oversized_query():
    """Reject query text exceeding maximum boundary (100,000 characters)."""
    oversized_text = "a" * 100_001
    payload = {
        "request_id": "req_oversized",
        "query_text": oversized_text,
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 422


def test_rejection_of_excessive_context_candidates():
    """Reject context_candidates list exceeding max_length of 10 turns."""
    excessive_turns = [
        {
            "turn_id": f"turn_{i}",
            "role": "user",
            "content": f"Turn content {i}",
            "original_index": i
        }
        for i in range(11)
    ]
    payload = {
        "request_id": "req_too_many_turns",
        "query_text": "Valid query",
        "context_candidates": excessive_turns,
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 422


def test_zero_credential_exposure_in_response():
    """Verify response payload never contains secrets, keys, or provider credentials."""
    payload = {
        "request_id": "req_security_audit",
        "query_text": "How do I secure an API gateway?",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()

    forbidden_substrings = [
        "api_key", "secret", "token", "password", "credential",
        "anthropic_api_key", "openai_api_key", "bearer"
    ]
    for key in data.keys():
        for forbidden in forbidden_substrings:
            assert forbidden not in key.lower(), f"Forbidden key '{key}' found in response"


def test_correlation_id_tracking_in_payload_and_headers():
    """Verify correlation_id provided in body is echoed in JSON response and X-Correlation-ID header."""
    test_corr_id = "corr_1710000000_audit123"
    payload = {
        "request_id": "req_corr_test",
        "correlation_id": test_corr_id,
        "query_text": "Explain Python asyncio event loop internals in detail",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post("/api/v1/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["correlation_id"] == test_corr_id
    assert response.headers.get("X-Correlation-ID") == test_corr_id


def test_correlation_id_from_custom_header_precedence():
    """Verify X-Correlation-ID header is honored when package lacks correlation_id."""
    test_corr_id = "corr_hdr_override_456"
    payload = {
        "request_id": "req_hdr_test",
        "query_text": "What is tail call optimization?",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    response = client.post(
        "/api/v1/optimize",
        json=payload,
        headers={"X-Correlation-ID": test_corr_id}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["correlation_id"] == test_corr_id
    assert response.headers.get("X-Correlation-ID") == test_corr_id


def test_performance_telemetry_record_schema():
    """Verify PerformanceTelemetryRecord enforces strict schema and forbids raw query leakage."""
    record = PerformanceTelemetryRecord(
        correlation_id="corr_987654321_abc",
        client_timestamp=1710000000000,
        backend_timestamp=1710000000150,
        decision_type=DecisionType.NO_OPTIMIZATION,
        model_route=None,
        cache_outcome=CacheOutcome.NOT_CHECKED,
        latency_ms=150.5,
        error_category=ErrorCategory.NONE,
        feature_identifiers={
            "char_bucket": "50-200",
            "has_code": False,
            "cue_count": 1
        },
        version_identifiers={
            "extension": "0.1.0",
            "server": "0.1.0",
            "schema": "1.0"
        }
    )
    assert record.correlation_id == "corr_987654321_abc"
    assert record.debug_metadata is None
    assert record.cache_outcome == CacheOutcome.NOT_CHECKED
    assert record.error_category == ErrorCategory.NONE

    # Injected raw text field must be rejected by extra="forbid"
    with pytest.raises(ValidationError):
        PerformanceTelemetryRecord(
            correlation_id="corr_test",
            client_timestamp=1710000000000,
            decision_type=DecisionType.NO_OPTIMIZATION,
            latency_ms=10.0,
            raw_query="SELECT * FROM users"  # Unauthorized extra field
        )


def test_coarse_route_echo_and_default_derivation():
    """Verify coarse_route is echoed if provided, or derived deterministically."""
    # 1. Echo explicitly provided coarse route
    payload_explicit = {
        "request_id": "req_coarse_1",
        "coarse_route": CoarseRoute.COMPLEX_MODEL_CANDIDATE.value,
        "query_text": "Write a recursive descent parser",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res1 = client.post("/api/v1/optimize", json=payload_explicit)
    assert res1.status_code == 200
    assert res1.json()["coarse_route"] == CoarseRoute.COMPLEX_MODEL_CANDIDATE.value

    # 2. Derive complex-model candidate from code feature when not provided
    payload_derived = {
        "request_id": "req_coarse_2",
        "query_text": "def foo(): pass",
        "local_features": {
            "has_code": True
        },
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res2 = client.post("/api/v1/optimize", json=payload_derived)
    assert res2.status_code == 200
    assert res2.json()["coarse_route"] == CoarseRoute.COMPLEX_MODEL_CANDIDATE.value

    # 3. Simple inquiry derived as simple-model candidate
    payload_simple = {
        "request_id": "req_coarse_3",
        "query_text": "What is the capital of France and its primary landmarks?",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res3 = client.post("/api/v1/optimize", json=payload_simple)
    assert res3.status_code == 200
    assert res3.json()["coarse_route"] == CoarseRoute.SIMPLE_MODEL_CANDIDATE.value


def test_invalid_coarse_route_rejection():
    """Reject unlisted coarse route with HTTP 422."""
    payload = {
        "request_id": "req_invalid_route",
        "coarse_route": "ultra-model-candidate",  # Invalid enum value
        "query_text": "Sample text",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res = client.post("/api/v1/optimize", json=payload)
    assert res.status_code == 422


def test_task_category_in_query_package_and_response():
    """Verify task_category is validated, echoed in response, and guides default coarse route."""
    # 1. Coding task category yields complex-model candidate
    payload_coding = {
        "request_id": "req_tc_coding",
        "task_category": TaskCategory.CODING.value,
        "query_text": "Write a binary search tree in Python",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res1 = client.post("/api/v1/optimize", json=payload_coding)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["task_category"] == "coding"
    assert data1["coarse_route"] == CoarseRoute.COMPLEX_MODEL_CANDIDATE.value

    # 2. Greeting task category yields local-eligible
    payload_greeting = {
        "request_id": "req_tc_greeting",
        "task_category": TaskCategory.GREETING.value,
        "query_text": "Hello there!",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res2 = client.post("/api/v1/optimize", json=payload_greeting)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["task_category"] == "greeting"
    assert data2["coarse_route"] == CoarseRoute.LOCAL_ELIGIBLE.value

    # 3. Summarization task category yields simple-model candidate
    payload_summarize = {
        "request_id": "req_tc_sum",
        "task_category": TaskCategory.SUMMARIZATION.value,
        "query_text": "Summarize the key points of the report",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res3 = client.post("/api/v1/optimize", json=payload_summarize)
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["task_category"] == "summarization"
    assert data3["coarse_route"] == CoarseRoute.SIMPLE_MODEL_CANDIDATE.value


def test_invalid_task_category_rejection():
    """Reject invalid/unsupported task category with HTTP 422."""
    payload = {
        "request_id": "req_invalid_tc",
        "task_category": "quantum-simulation",  # Unsupported category
        "query_text": "Simulate a qubit circuit",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res = client.post("/api/v1/optimize", json=payload)
    assert res.status_code == 422


def test_telemetry_schema_with_task_category():
    """Verify PerformanceTelemetryRecord safely accepts task_category."""
    record = PerformanceTelemetryRecord(
        correlation_id="corr_tc_1",
        client_timestamp=1710000000000,
        decision_type=DecisionType.NO_OPTIMIZATION,
        coarse_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE,
        task_category=TaskCategory.CODING,
        latency_ms=14.2
    )
    assert record.task_category == TaskCategory.CODING
    assert record.coarse_route == CoarseRoute.COMPLEX_MODEL_CANDIDATE


def test_complexity_score_and_level_in_query_package_and_response():
    """Verify complexity_score and complexity_level are validated and echoed in response."""
    payload = {
        "request_id": "req_complex_score_1",
        "query_text": "Debug memory leak in C++ shared pointer circular reference",
        "task_category": TaskCategory.DEBUGGING.value,
        "complexity_score": 0.88,
        "complexity_level": ComplexityLevel.VERY_HIGH.value,
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res = client.post("/api/v1/optimize", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["complexity_score"] == 0.88
    assert data["complexity_level"] == "VERY_HIGH"
    assert data["task_category"] == "debugging"


def test_invalid_complexity_score_range_rejection():
    """Reject complexity_score outside [0.0, 1.0] with HTTP 422."""
    payload = {
        "request_id": "req_invalid_cs",
        "query_text": "Sample text",
        "complexity_score": 1.5,  # Out of bounds (> 1.0)
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res = client.post("/api/v1/optimize", json=payload)
    assert res.status_code == 422


def test_invalid_complexity_level_rejection():
    """Reject unlisted complexity_level with HTTP 422."""
    payload = {
        "request_id": "req_invalid_cl",
        "query_text": "Sample text",
        "complexity_level": "EXTREME_CHAOS",  # Invalid enum value
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    res = client.post("/api/v1/optimize", json=payload)
    assert res.status_code == 422


def test_telemetry_schema_with_complexity():
    """Verify PerformanceTelemetryRecord safely accepts complexity fields."""
    record = PerformanceTelemetryRecord(
        correlation_id="corr_cx_1",
        client_timestamp=1710000000000,
        decision_type=DecisionType.NO_OPTIMIZATION,
        complexity_score=0.74,
        complexity_level=ComplexityLevel.HIGH,
        latency_ms=12.5
    )
    assert record.complexity_score == 0.74
    assert record.complexity_level == ComplexityLevel.HIGH


