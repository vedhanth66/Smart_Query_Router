"""Integration tests connecting the rule-based router to the model gateway.

Verifies:
1. Local-eligible route bypasses remote gateway execution (0 latency, no remote invocation).
2. Simple-model candidate executes ONLY the fast/cheap model (SmallModelAdapter / gpt-4o-mini).
3. Complex-model candidate executes ONLY the stronger model (StrongModelAdapter / gpt-4o).
4. Needs-evaluation route executes comparative evaluation across both models via compare_execution.
5. Safe fallback on gateway timeout: fails open to NO_OPTIMIZATION, records TIMEOUT, never blocks workflow.
6. Safe fallback on retry exhaustion: fails open to NO_OPTIMIZATION, records RETRY_EXHAUSTED, no repeated loops.
7. Safe fallback on generic provider error: fails open to NO_OPTIMIZATION, records PROVIDER_ERROR.
8. execute_route=False skips model execution for backwards compatibility.
9. Telemetry schema records route, model_version, latency, and failure_category.
"""

import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.gateway import (
    ModelTier,
    GatewayRequest,
    GatewayResponse,
    default_gateway,
    GatewayTimeoutError,
    GatewayRetryExhaustedError,
    GatewayError,
)
from app.schemas.contract import (
    CoarseRoute,
    DecisionType,
    RouteExecutionMetadata,
    PerformanceTelemetryRecord,
)

client = TestClient(app)

BASE_CLIENT_METADATA = {
    "extension_version": "0.1.0",
    "client_type": "chrome_extension",
    "schema_version": "1.0",
    "hostname": "claude.ai",
}


# --- 1. Single Route Execution: Local-Eligible Route Bypasses Remote Gateway ---

def test_local_eligible_route_bypasses_remote_gateway():
    """Verify local-eligible queries bypass remote gateway execution completely."""
    payload = {
        "request_id": "req_test_local_01",
        "correlation_id": "corr_test_local_01",
        "query_text": "hello there",
        "coarse_route": "local-eligible",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    with patch.object(default_gateway, "execute", wraps=default_gateway.execute) as spy_exec:
        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["coarse_route"] == "local-eligible"
        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["route"] == "local-eligible"
        assert exec_meta["model_id"] is None
        assert exec_meta["model_version"] is None
        assert exec_meta["latency_ms"] == 0.0
        assert exec_meta["failure_category"] == "NONE"
        assert exec_meta["fallback_applied"] is False
        assert exec_meta["executed_content"] is None
        assert exec_meta["evaluation_metadata"] is None

        # Verify remote execute was never called
        spy_exec.assert_not_called()


# --- 2. Single Route Execution: Simple-Model Candidate Executes Only Fast/Cheap Model ---

def test_simple_model_candidate_executes_only_small_model():
    """Verify simple-model candidate executes ONLY the small model route."""
    payload = {
        "request_id": "req_test_simple_01",
        "correlation_id": "corr_test_simple_01",
        "query_text": "What is the capital of France?",
        "coarse_route": "simple-model candidate",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["coarse_route"] == "simple-model candidate"
    assert data["model_tier"] == "fast_cheap"

    exec_meta = data["execution_metadata"]
    assert exec_meta is not None
    assert exec_meta["route"] == "simple-model candidate"
    assert exec_meta["model_id"] == "gpt-4o-mini-2024-07-18"
    assert exec_meta["model_version"] == "2024-07-18"
    assert exec_meta["latency_ms"] >= 0.0
    assert exec_meta["failure_category"] == "NONE"
    assert exec_meta["fallback_applied"] is False
    assert exec_meta["executed_content"] is not None
    assert len(exec_meta["executed_content"]) > 0
    # Evaluation metadata is populated by the small-model evaluator, with no escalation needed
    assert exec_meta["evaluation_metadata"] is not None
    assert exec_meta["evaluation_metadata"]["escalation_recommendation"] == "no_escalation"
    assert exec_meta["escalation_occurred"] is False
    assert exec_meta["escalation_reason"] is None


# --- 3. Single Route Execution: Complex-Model Candidate Executes Only Strong Model ---

def test_complex_model_candidate_executes_only_strong_model():
    """Verify complex-model candidate executes ONLY the stronger model route."""
    payload = {
        "request_id": "req_test_complex_01",
        "correlation_id": "corr_test_complex_01",
        "query_text": "Write a multi-producer multi-consumer lock-free ring buffer in Rust",
        "coarse_route": "complex-model candidate",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["coarse_route"] == "complex-model candidate"
    assert data["model_tier"] == "strong"

    exec_meta = data["execution_metadata"]
    assert exec_meta is not None
    assert exec_meta["route"] == "complex-model candidate"
    assert exec_meta["model_id"] == "gpt-4o-2024-08-06"
    assert exec_meta["model_version"] == "2024-08-06"
    assert exec_meta["latency_ms"] >= 0.0
    assert exec_meta["failure_category"] == "NONE"
    assert exec_meta["fallback_applied"] is False
    assert exec_meta["executed_content"] is not None
    assert len(exec_meta["executed_content"]) > 0
    # Crucial: evaluation_metadata must be None because only ONE route executed
    assert exec_meta["evaluation_metadata"] is None


# --- 4. Comparative Evaluation: Needs-Evaluation Executes Both Models ---

def test_needs_evaluation_executes_comparative_dual_model():
    """Verify needs-evaluation triggers comparative execution across both models."""
    payload = {
        "request_id": "req_test_eval_01",
        "correlation_id": "corr_test_eval_01",
        "query_text": "Compare microservices architecture with modular monolith for our scale",
        "coarse_route": "needs-evaluation",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["coarse_route"] == "needs-evaluation"

    exec_meta = data["execution_metadata"]
    assert exec_meta is not None
    assert exec_meta["route"] == "needs-evaluation"
    assert exec_meta["model_id"] == "gpt-4o-2024-08-06"
    assert exec_meta["model_version"] == "2024-08-06"
    assert exec_meta["failure_category"] == "NONE"
    assert exec_meta["fallback_applied"] is False
    assert exec_meta["executed_content"] is not None

    # Comparative evaluation metadata must be populated
    eval_meta = exec_meta["evaluation_metadata"]
    assert eval_meta is not None
    assert "small_response" in eval_meta
    assert "strong_response" in eval_meta
    assert eval_meta["small_response"]["provider_name"] == "small_model"
    assert eval_meta["strong_response"]["provider_name"] == "strong_model"
    assert "latency_delta_ms" in eval_meta
    assert "token_delta" in eval_meta


# --- 5. Safe Fallback: Gateway Timeout Error ---

def test_safe_fallback_on_gateway_timeout():
    """Verify timeout fails open to NO_OPTIMIZATION without blocking workflow or throwing 5xx."""
    payload = {
        "request_id": "req_test_timeout_01",
        "correlation_id": "corr_test_timeout_01",
        "query_text": "Calculate the nth Fibonacci number efficiently",
        "coarse_route": "simple-model candidate",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    async def mock_timeout(*args, **kwargs):
        raise GatewayTimeoutError("Mock timeout exceeded")

    with patch.object(default_gateway, "execute", side_effect=mock_timeout):
        resp = client.post("/api/v1/optimize", json=payload)
        # Never fail with 5xx or block workflow
        assert resp.status_code == 200
        data = resp.json()

        assert data["decision_type"] == "NO_OPTIMIZATION"
        assert data["reason_code"] == "GATEWAY_TIMEOUT_FALLBACK"
        assert data["confidence"] == 0.0

        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["route"] == "simple-model candidate"
        assert exec_meta["failure_category"] == "TIMEOUT"
        assert exec_meta["fallback_applied"] is True
        assert exec_meta["executed_content"] is None
        assert exec_meta["latency_ms"] >= 0.0


# --- 6. Safe Fallback: Gateway Retries Exhausted ---

def test_safe_fallback_on_retries_exhausted():
    """Verify retry exhaustion fails open without repeated retry loops or workflow blocking."""
    payload = {
        "request_id": "req_test_retry_01",
        "correlation_id": "corr_test_retry_01",
        "query_text": "Explain quantum teleportation in simple terms",
        "coarse_route": "simple-model candidate",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    async def mock_exhausted(*args, **kwargs):
        raise GatewayRetryExhaustedError("Retries exhausted after 2 attempts")

    with patch.object(default_gateway, "execute", side_effect=mock_exhausted):
        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["decision_type"] == "NO_OPTIMIZATION"
        assert data["reason_code"] == "GATEWAY_FAILURE_FALLBACK"
        assert data["confidence"] == 0.0

        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["failure_category"] == "RETRY_EXHAUSTED"
        assert exec_meta["fallback_applied"] is True


# --- 7. Safe Fallback: Generic Provider Error ---

def test_safe_fallback_on_provider_error():
    """Verify provider error fails open to NO_OPTIMIZATION with PROVIDER_ERROR failure category."""
    payload = {
        "request_id": "req_test_prov_err_01",
        "correlation_id": "corr_test_prov_err_01",
        "query_text": "Write a distributed commit protocol",
        "coarse_route": "complex-model candidate",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    async def mock_provider_err(*args, **kwargs):
        raise GatewayError("Upstream model service 503 Unavailable")

    with patch.object(default_gateway, "execute", side_effect=mock_provider_err):
        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["decision_type"] == "NO_OPTIMIZATION"
        assert data["reason_code"] == "GATEWAY_FAILURE_FALLBACK"

        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["failure_category"] == "PROVIDER_ERROR"
        assert exec_meta["fallback_applied"] is True


# --- 8. Skip Execution When execute_route=False ---

def test_execute_route_flag_false_skips_execution():
    """Verify execute_route=False returns routing recommendation without executing model."""
    payload = {
        "request_id": "req_test_skip_01",
        "correlation_id": "corr_test_skip_01",
        "query_text": "Explain topological sorting",
        "coarse_route": "simple-model candidate",
        "execute_route": False,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["coarse_route"] == "simple-model candidate"
    assert data["execution_metadata"] is None


# --- 9. Telemetry Schema Parity Verification ---

def test_telemetry_schema_records_execution_metadata():
    """Verify PerformanceTelemetryRecord serialization with new route execution metadata."""
    record = PerformanceTelemetryRecord(
        correlation_id="corr_telemetry_meta_01",
        client_timestamp=1700000000000,
        backend_timestamp=1700000000050,
        decision_type=DecisionType.BACKEND_CANDIDATE,
        coarse_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE,
        model_version="2024-08-06",
        execution_latency_ms=124.5,
        failure_category="NONE",
        latency_ms=130.0,
    )

    data = record.model_dump()
    assert data["correlation_id"] == "corr_telemetry_meta_01"
    assert data["coarse_route"] == "complex-model candidate"
    assert data["model_version"] == "2024-08-06"
    assert data["execution_latency_ms"] == 124.5
    assert data["failure_category"] == "NONE"
    # Ensure zero prompt leakage in telemetry
    raw_str = record.model_dump_json()
    assert "query_text" not in raw_str
    assert "prompt" not in raw_str
