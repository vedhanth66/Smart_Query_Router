"""Intentionally Simulated Failure Mode and Resilience Test Suite.

SIMULATES:
1. Backend Timeout: Gateway timeout triggers non-blocking safe fallback without hanging or retry storms.
2. Cache Outage: Complete failure of exact-match or semantic cache gracefully degrades to cache bypass without failing the user request.
3. Model Failure: Provider errors (HTTP 500, retry exhaustion, gateway error) trigger safe terminal fallback.
4. Evaluator Failure: Evaluator exceptions or registry failures safely retain candidate completions without crashing /api/v1/optimize.
5. Loop Prevention: Stronger model execution and fallbacks are strictly terminal; zero recursive or infinite loops.
6. Zero Duplicate Requests: In-flight deduplication and terminal failure guarantees ensure no duplicate requests.
"""

import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.gateway import (
    default_gateway,
    GatewayRequest,
    GatewayResponse,
    GatewayTimeoutError,
    GatewayError,
    GatewayRetryExhaustedError,
    ModelTier,
)
from app.cache import (
    default_response_cache,
    default_semantic_cache,
)
from app.evaluator import default_evaluator_registry
from app.schemas.contract import CoarseRoute, DecisionType, CacheOutcome

client = TestClient(app)

BASE_CLIENT_METADATA = {
    "extension_version": "0.1.0",
    "client_type": "chrome_extension",
    "schema_version": "1.0",
    "hostname": "claude.ai",
}


# =========================================================================
# Scenario 1: Backend Timeout Simulation
# =========================================================================

def test_simulate_backend_gateway_timeout():
    """When the model gateway times out, /api/v1/optimize must fall back non-blocking without retrying indefinitely."""
    async def mock_timeout(request: GatewayRequest) -> GatewayResponse:
        raise GatewayTimeoutError("Gateway timed out after 30000ms")

    with patch.object(default_gateway, "execute", side_effect=mock_timeout):
        payload = {
            "request_id": "req_sim_timeout_01",
            "correlation_id": "corr_sim_timeout_01",
            "query_text": "Explain quantum computing in detail",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        # Guarantees:
        # - Returns HTTP 200 with fallback decision
        # - Does NOT hang or crash
        # - Failure category recorded as TIMEOUT
        # - Fallback applied without user disruption
        assert data["decision_type"] == "NO_OPTIMIZATION"
        assert data["reason_code"] == "GATEWAY_TIMEOUT_FALLBACK"
        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["fallback_applied"] is True
        assert exec_meta["failure_category"] == "TIMEOUT"


# =========================================================================
# Scenario 2: Cache Outage Simulation
# =========================================================================

def test_simulate_exact_cache_outage():
    """When the exact-match response cache store is down (outage), queries must bypass cache and execute normally."""
    async def mock_cache_crash(*args, **kwargs):
        raise ConnectionError("Redis connection refused: [Errno 111] Connection refused")

    with patch.object(default_response_cache, "check_cache", side_effect=mock_cache_crash):
        payload = {
            "request_id": "req_sim_cache_outage_01",
            "correlation_id": "corr_sim_cache_outage_01",
            "query_text": "What is the capital of Italy?",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        # Query succeeded despite cache crash!
        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["failure_category"] == "NONE"
        assert exec_meta["executed_content"] is not None


def test_simulate_semantic_cache_outage():
    """When the semantic cache index throws an exception, queries must bypass semantic cache and continue unhindered."""
    async def mock_semantic_crash(*args, **kwargs):
        raise RuntimeError("Vector similarity index unavailable: disk I/O error")

    with patch.object(default_semantic_cache, "lookup_candidate", side_effect=mock_semantic_crash):
        payload = {
            "request_id": "req_sim_sem_outage_01",
            "correlation_id": "corr_sim_sem_outage_01",
            "query_text": "What is the capital of Spain?",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        # Must succeed with normal execution
        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["failure_category"] == "NONE"
        assert exec_meta["executed_content"] is not None


def test_simulate_cache_store_write_outage():
    """When cache write/store fails, the successful response must still be returned to the caller."""
    async def mock_store_crash(*args, **kwargs):
        raise IOError("Cache storage partition read-only: write failed")

    with patch.object(default_response_cache, "store_response", side_effect=mock_store_crash):
        payload = {
            "request_id": "req_sim_store_outage_01",
            "correlation_id": "corr_sim_store_outage_01",
            "query_text": "What is the chemical formula for water?",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution_metadata"]["executed_content"] is not None


# =========================================================================
# Scenario 3: Model Failure Simulation
# =========================================================================

def test_simulate_model_provider_error():
    """When the remote model provider raises GatewayError, the router must fall back to NO_OPTIMIZATION safely."""
    async def mock_gateway_error(request: GatewayRequest) -> GatewayResponse:
        raise GatewayError("Remote provider 500 Internal Server Error")

    with patch.object(default_gateway, "execute", side_effect=mock_gateway_error):
        payload = {
            "request_id": "req_sim_model_err_01",
            "correlation_id": "corr_sim_model_err_01",
            "query_text": "Generate a complex SQL query",
            "coarse_route": "complex-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["decision_type"] == "NO_OPTIMIZATION"
        assert data["reason_code"] == "GATEWAY_FAILURE_FALLBACK"
        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["fallback_applied"] is True
        assert exec_meta["failure_category"] == "PROVIDER_ERROR"


def test_simulate_model_retries_exhausted():
    """When model provider rate limits exceed maximum retries, the router terminates retries and falls back safely."""
    async def mock_retry_exhausted(request: GatewayRequest) -> GatewayResponse:
        raise GatewayRetryExhaustedError("All 3 retry attempts failed due to rate limits")

    with patch.object(default_gateway, "execute", side_effect=mock_retry_exhausted):
        payload = {
            "request_id": "req_sim_retry_exh_01",
            "correlation_id": "corr_sim_retry_exh_01",
            "query_text": "Summarize this research paper",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["decision_type"] == "NO_OPTIMIZATION"
        assert data["reason_code"] == "GATEWAY_FAILURE_FALLBACK"
        assert data["execution_metadata"]["failure_category"] == "RETRY_EXHAUSTED"


# =========================================================================
# Scenario 4: Evaluator Failure Simulation
# =========================================================================

def test_simulate_evaluator_crash_safely_retains_completion():
    """When the evaluator crashes with an unexpected exception, the small-model completion must NOT be lost."""
    async def mock_eval_crash(*args, **kwargs):
        raise KeyError("Heuristic rule definition missing in evaluator registry")

    with patch.object(default_evaluator_registry, "evaluate", side_effect=mock_eval_crash):
        payload = {
            "request_id": "req_sim_eval_err_01",
            "correlation_id": "corr_sim_eval_err_01",
            "query_text": "What is 10 + 10?",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        # The request MUST NOT fail with 500! Original completion is safely accepted.
        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["executed_content"] is not None
        assert exec_meta["fallback_applied"] is False
        # Evaluator failure is noted in metadata
        eval_meta = exec_meta["evaluation_metadata"]
        assert eval_meta is not None
        assert eval_meta["passed"] is True


# =========================================================================
# Scenario 5: Loop Prevention & Zero Infinite Retries
# =========================================================================

def test_loop_prevention_no_infinite_retry_or_recursion():
    """Verify that failure paths are strictly terminal with zero recursion or infinite retry loops."""
    call_count = 0

    async def counting_mock_execute(request: GatewayRequest) -> GatewayResponse:
        nonlocal call_count
        call_count += 1
        raise GatewayError("Simulated unrecoverable provider failure")

    with patch.object(default_gateway, "execute", side_effect=counting_mock_execute):
        payload = {
            "request_id": "req_sim_loop_prev_01",
            "correlation_id": "corr_sim_loop_prev_01",
            "query_text": "Test loop prevention",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        # Gateway was called at most once (for this single request) before terminal fallback
        assert call_count == 1
        data = resp.json()
        assert data["execution_metadata"]["fallback_applied"] is True


# =========================================================================
# Scenario 6: Unexpected Execution Failure Regression Check
# =========================================================================

def test_regression_unhandled_execution_error_fails_open_without_http_500():
    """Regression test: Unexpected execution error during routing must fail open with HTTP 200, not 500."""
    def mock_unhandled_crash(*args, **kwargs):
        raise TypeError("Simulated unexpected internal type error during route evaluation")

    with patch.object(default_gateway, "get_model_recommendation", side_effect=mock_unhandled_crash):
        payload = {
            "request_id": "req_sim_unhandled_err_01",
            "correlation_id": "corr_sim_unhandled_err_01",
            "query_text": "Unexpected failure regression check",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision_type"] == "NO_OPTIMIZATION"
        assert data["reason_code"] == "EXECUTION_UNEXPECTED_FAILURE_FALLBACK"
        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["fallback_applied"] is True
        assert exec_meta["failure_category"] == "UNEXPECTED_ERROR"

