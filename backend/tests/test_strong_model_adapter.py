"""Unit and integration tests for StrongModelAdapter and Comparative Execution Engine."""

import pytest
from unittest.mock import patch, MagicMock
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.gateway import (
    ModelTier,
    GatewayRequest,
    GatewayResponse,
    ModelCapabilities,
    SmallModelAdapter,
    StrongModelAdapter,
    ClaudeAdapter,
    ModelGateway,
    default_gateway,
    GatewayError,
    GatewayTimeoutError,
    GatewayRetryExhaustedError,
    GatewayResponseSizeLimitError,
    ModelExecutionComparison,
    compare_executions,
)

client = TestClient(app)


# --- 1. Deterministic Execution & Clear Model-Version Metadata ---

@pytest.mark.anyio
async def test_strong_model_adapter_deterministic_execution():
    """Verify deterministic execution and explicit model version metadata on StrongModelAdapter."""
    adapter = StrongModelAdapter(test_mode=True)
    req = GatewayRequest(
        prompt="Design a distributed cache with Raft consensus and write-ahead log",
        tier=ModelTier.STRONG,
        max_tokens=1024
    )
    res = await adapter.execute_strong(req)

    assert isinstance(res, GatewayResponse)
    assert res.tier == ModelTier.STRONG
    assert res.provider_name == "strong_model"
    assert res.model_id == "gpt-4o-2024-08-06"
    assert res.model_version == "2024-08-06"
    assert res.finish_reason == "stop"
    assert len(res.content) > 0
    assert res.input_tokens > 0
    assert res.output_tokens > 0
    assert res.raw_metadata["model_version"] == "2024-08-06"
    assert res.raw_metadata["pricing_tier"] == "premium"
    assert res.raw_metadata["test_mode"] is True


@pytest.mark.anyio
async def test_strong_model_adapter_logical_contract_parity():
    """Verify StrongModelAdapter supports the exact same logical request contract as SmallModelAdapter."""
    strong_adapter = StrongModelAdapter(test_mode=True)
    small_adapter = SmallModelAdapter(test_mode=True)

    shared_request = GatewayRequest(
        prompt="Compare SQL vs NoSQL scalability tradeoffs",
        system_prompt="You are a senior database architect",
        tier=ModelTier.STRONG,
        max_tokens=512,
        temperature=0.2,
        context_turns=[{"role": "user", "content": "Initial context turn"}],
        correlation_id="corr-contract-parity-test"
    )

    strong_res = await strong_adapter.execute_strong(shared_request)
    small_res = await small_adapter.execute_fast(shared_request)

    # Both return valid GatewayResponse adhering to the universal contract
    assert isinstance(strong_res, GatewayResponse)
    assert isinstance(small_res, GatewayResponse)
    assert strong_res.finish_reason == "stop"
    assert small_res.finish_reason == "stop"
    assert strong_res.model_version == "2024-08-06"
    assert small_res.model_version == "2024-07-18"


# --- 2. Backend-Only Credentials Isolation ---

@pytest.mark.anyio
async def test_strong_model_credentials_isolation():
    """Verify credentials for the stronger model remain on the backend and are never leaked."""
    secret_key = "sk-strong-backend-secret-key-999888777"
    adapter = StrongModelAdapter(api_key=secret_key, test_mode=True)

    req = GatewayRequest(
        prompt="Formal proof of consensus safety",
        tier=ModelTier.STRONG
    )
    res = await adapter.execute_strong(req)

    res_dict = res.model_dump()
    assert secret_key not in str(res_dict)
    assert secret_key not in res.content
    assert "api_key" not in res.raw_metadata
    assert res.raw_metadata.get("has_backend_key") is True


# --- 3. Bounded Timeouts, Retries, and Size Limits ---

@pytest.mark.anyio
async def test_strong_model_mock_timeout():
    """Verify timeout on StrongModelAdapter triggers GatewayTimeoutError."""
    adapter = StrongModelAdapter(timeout_seconds=8.0, test_mode=True)
    adapter.set_mock_timeout(True)

    req = GatewayRequest(prompt="Heavy reasoning prompt")
    with pytest.raises(GatewayTimeoutError) as exc_info:
        await adapter.execute_strong(req)

    assert "timed out" in str(exc_info.value).lower()


@pytest.mark.anyio
async def test_strong_model_mock_bounded_retries():
    """Verify bounded retries on StrongModelAdapter."""
    adapter = StrongModelAdapter(max_retries=2, backoff_factor=0.01, test_mode=True)
    adapter.set_mock_failures(transient_failures=2)

    req = GatewayRequest(prompt="Prompt needing retries")
    res = await adapter.execute_strong(req)
    assert res.raw_metadata["retries_performed"] == 2

    # Retries exhausted
    adapter.set_mock_failures(transient_failures=4)
    with pytest.raises(GatewayRetryExhaustedError):
        await adapter.execute_strong(req)


@pytest.mark.anyio
async def test_strong_model_response_size_bounding():
    """Verify response size bounding on StrongModelAdapter."""
    adapter = StrongModelAdapter(max_response_chars=120, test_mode=True)
    adapter.set_mock_oversized_response(True)

    req = GatewayRequest(prompt="Generate large proof")
    res = await adapter.execute_strong(req)

    assert res.finish_reason == "size_limit_exceeded"
    assert res.raw_metadata["size_truncated"] is True
    assert len(res.content) <= 120 + len("\n[RESPONSE TRUNCATED: Size limit exceeded]")


# --- 4. Provider-Agnostic Comparative Execution Engine ---

def test_compare_executions_metrics():
    """Verify compare_executions computes accurate latency, token, and consensus metrics."""
    small_res = GatewayResponse(
        content="Quick summary of caching mechanisms with Redis.",
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini-2024-07-18",
        model_version="2024-07-18",
        provider_name="small_model",
        input_tokens=20,
        output_tokens=10,
        latency_ms=100.0,
        finish_reason="stop"
    )
    strong_res = GatewayResponse(
        content="In-depth analysis of caching mechanisms including write-through, write-back, and Redis replication.",
        tier=ModelTier.STRONG,
        model_id="gpt-4o-2024-08-06",
        model_version="2024-08-06",
        provider_name="strong_model",
        input_tokens=20,
        output_tokens=30,
        latency_ms=350.0,
        finish_reason="stop"
    )

    comp = compare_executions(small_res, strong_res, comparison_id="comp-unit-test-1")

    assert isinstance(comp, ModelExecutionComparison)
    assert comp.comparison_id == "comp-unit-test-1"
    assert comp.small_response.model_id == "gpt-4o-mini-2024-07-18"
    assert comp.strong_response.model_id == "gpt-4o-2024-08-06"
    assert comp.latency_delta_ms == 250.0  # 350 - 100
    assert comp.latency_ratio == 3.5      # 350 / 100
    assert comp.token_delta == 20         # 50 total - 30 total
    assert comp.token_ratio == 3.0        # 30 output / 10 output
    assert comp.content_length_delta > 0
    assert comp.both_successful is True
    assert 0.0 < comp.agreement_score <= 1.0


# --- 5. Gateway Concurrent Comparison ---

@pytest.mark.anyio
async def test_gateway_concurrent_comparison_operation():
    """Verify ModelGateway.compare_execution runs both models concurrently and computes comparative signals."""
    gw = ModelGateway()
    gw.register_adapter(ClaudeAdapter(), set_active=True)
    gw.register_adapter(SmallModelAdapter(test_mode=True), set_active=False)
    gw.register_adapter(StrongModelAdapter(test_mode=True), set_active=False)

    req = GatewayRequest(
        prompt="Explain trade-offs of B-Trees vs LSM-Trees in database engines",
        max_tokens=256,
        correlation_id="corr-compare-eval-101"
    )
    comparison = await gw.compare_execution(
        request=req,
        small_provider="small_model",
        strong_provider="strong_model"
    )

    assert isinstance(comparison, ModelExecutionComparison)
    assert comparison.small_response.provider_name == "small_model"
    assert comparison.small_response.model_id == "gpt-4o-mini-2024-07-18"
    assert comparison.strong_response.provider_name == "strong_model"
    assert comparison.strong_response.model_id == "gpt-4o-2024-08-06"
    assert comparison.both_successful is True
    assert comparison.comparison_id == "corr-compare-eval-101"


# --- 6. Provider-Agnostic Decoupling Guarantee ---

@pytest.mark.anyio
async def test_comparison_works_across_arbitrary_adapters():
    """Verify comparison engine is completely decoupled and works across any two providers (e.g. Claude vs StrongModel)."""
    gw = ModelGateway()
    gw.register_adapter(ClaudeAdapter(), set_active=True)
    gw.register_adapter(StrongModelAdapter(test_mode=True), set_active=False)

    req = GatewayRequest(prompt="General algorithmic problem", max_tokens=128)
    comparison = await gw.compare_execution(
        request=req,
        small_provider="claude",
        strong_provider="strong_model"
    )

    assert comparison.small_response.provider_name == "claude"
    assert comparison.strong_response.provider_name == "strong_model"
    assert comparison.both_successful is True


# --- 7. API Endpoint POST /api/v1/gateway/compare ---

def test_api_gateway_compare_endpoint():
    """Verify POST /api/v1/gateway/compare executes small and strong models and returns ModelExecutionComparison."""
    payload = {
        "prompt": "Evaluate algorithm time complexity for Dijkstra with min-heap",
        "max_tokens": 128,
        "correlation_id": "test-api-compare-corr"
    }
    res = client.post("/api/v1/gateway/compare", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert "comparison_id" in data
    assert "small_response" in data
    assert "strong_response" in data
    assert data["small_response"]["model_id"] == "gpt-4o-mini-2024-07-18"
    assert data["strong_response"]["model_id"] == "gpt-4o-2024-08-06"
    assert "latency_delta_ms" in data
    assert "latency_ratio" in data
    assert "token_delta" in data
    assert "token_ratio" in data
    assert "agreement_score" in data
    assert data["both_successful"] is True


# --- 8. Live HTTP Execution Path Mocking ---

@pytest.mark.anyio
async def test_strong_model_live_http_path_success():
    """Verify live HTTP path serialization and token parsing on StrongModelAdapter."""
    adapter = StrongModelAdapter(
        api_key="backend-strong-secret-key",
        test_mode=False
    )

    fake_openai_response = {
        "id": "chatcmpl-strong-456",
        "object": "chat.completion",
        "created": 1721000000,
        "model": "gpt-4o-2024-08-06",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Comprehensive synthesis from gpt-4o."
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 25,
            "completion_tokens": 40,
            "total_tokens": 65
        }
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.is_error = False
    mock_response.content = b'{"mock": true}'
    mock_response.json.return_value = fake_openai_response

    with patch.object(httpx.AsyncClient, "post", return_value=mock_response) as mock_post:
        req = GatewayRequest(
            prompt="Architectural analysis",
            tier=ModelTier.STRONG,
            max_tokens=200
        )
        res = await adapter.execute_strong(req)

        assert res.content == "Comprehensive synthesis from gpt-4o."
        assert res.input_tokens == 25
        assert res.output_tokens == 40
        assert res.model_id == "gpt-4o-2024-08-06"
        assert res.model_version == "2024-08-06"
        assert res.raw_metadata["test_mode"] is False

        mock_post.assert_called_once()
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer backend-strong-secret-key"
