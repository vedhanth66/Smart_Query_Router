"""Unit and integration tests for SmallModelAdapter and ModelGateway dev connection."""

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
    ClaudeAdapter,
    ModelGateway,
    default_gateway,
    GatewayError,
    GatewayTimeoutError,
    GatewayRetryExhaustedError,
    GatewayResponseSizeLimitError,
)

client = TestClient(app)


# --- 1. Deterministic Execution & Clear Model-Version Metadata ---

@pytest.mark.anyio
async def test_small_model_adapter_deterministic_fast_execution():
    """Verify deterministic fast/cheap model execution without live inference."""
    adapter = SmallModelAdapter(test_mode=True)
    req = GatewayRequest(
        prompt="Summarize Newton's first law of motion",
        tier=ModelTier.FAST_CHEAP,
        max_tokens=256
    )
    res = await adapter.execute_fast(req)

    assert isinstance(res, GatewayResponse)
    assert res.tier == ModelTier.FAST_CHEAP
    assert res.provider_name == "small_model"
    assert res.model_id == "gpt-4o-mini-2024-07-18"
    assert res.model_version == "2024-07-18"
    assert res.finish_reason == "stop"
    assert len(res.content) > 0
    assert res.input_tokens > 0
    assert res.output_tokens > 0
    assert res.raw_metadata["model_version"] == "2024-07-18"
    assert res.raw_metadata["pricing_tier"] == "inexpensive"
    assert res.raw_metadata["test_mode"] is True


@pytest.mark.anyio
async def test_small_model_adapter_strong_execution():
    """Verify strong execution maps to small model with request settings."""
    adapter = SmallModelAdapter(test_mode=True)
    req = GatewayRequest(
        prompt="Explain event-driven architecture with pros and cons",
        tier=ModelTier.STRONG,
        max_tokens=512
    )
    res = await adapter.execute_strong(req)

    assert isinstance(res, GatewayResponse)
    assert res.tier == ModelTier.STRONG
    assert res.model_id == "gpt-4o-mini-2024-07-18"
    assert res.model_version == "2024-07-18"
    assert res.provider_name == "small_model"


# --- 2. Backend-Only Credentials Isolation ---

@pytest.mark.anyio
async def test_small_model_credentials_isolation():
    """Verify API keys are strictly kept on backend and never leaked into responses."""
    secret_key = "sk-proj-supersecretbackendkey9876543210"
    adapter = SmallModelAdapter(api_key=secret_key, test_mode=True)

    req = GatewayRequest(
        prompt="Explain quicksort in Python",
        tier=ModelTier.FAST_CHEAP
    )
    res = await adapter.execute_fast(req)

    # Verify secret is never serialized in response or metadata
    res_dict = res.model_dump()
    assert secret_key not in str(res_dict)
    assert secret_key not in res.content
    assert "api_key" not in res.raw_metadata
    assert res.raw_metadata.get("has_backend_key") is True

    # Verify health check does not expose secret
    health_ok = await adapter.check_health()
    assert health_ok is True
    assert secret_key not in str(adapter.get_capabilities().model_dump())


# --- 3. Bounded Timeouts ---

@pytest.mark.anyio
async def test_small_model_mock_timeout():
    """Verify timeout triggers typed GatewayTimeoutError."""
    adapter = SmallModelAdapter(timeout_seconds=5.0, test_mode=True)
    adapter.set_mock_timeout(True)

    req = GatewayRequest(prompt="Test timeout prompt")
    with pytest.raises(GatewayTimeoutError) as exc_info:
        await adapter.execute_fast(req)

    assert "timed out" in str(exc_info.value).lower()


# --- 4. Bounded Retries with Backoff ---

@pytest.mark.anyio
async def test_small_model_mock_bounded_retries_success():
    """Verify transient errors retry up to max_retries and succeed on attempt 3."""
    adapter = SmallModelAdapter(max_retries=2, backoff_factor=0.01, test_mode=True)
    adapter.set_mock_failures(transient_failures=2)

    req = GatewayRequest(prompt="Prompt with transient failure")
    res = await adapter.execute_fast(req)

    assert res.finish_reason == "stop"
    assert res.raw_metadata["retries_performed"] == 2


@pytest.mark.anyio
async def test_small_model_mock_retries_exhausted():
    """Verify exceeding max_retries raises GatewayRetryExhaustedError."""
    adapter = SmallModelAdapter(max_retries=2, backoff_factor=0.01, test_mode=True)
    adapter.set_mock_failures(transient_failures=4)

    req = GatewayRequest(prompt="Prompt exceeding retries")
    with pytest.raises(GatewayRetryExhaustedError) as exc_info:
        await adapter.execute_fast(req)

    assert "exhausted" in str(exc_info.value).lower()


# --- 5. Response-Size Limits ---

@pytest.mark.anyio
async def test_small_model_response_size_limiting():
    """Verify responses exceeding maximum size are bounded and tagged."""
    adapter = SmallModelAdapter(
        max_response_chars=100,
        test_mode=True
    )
    adapter.set_mock_oversized_response(True)

    req = GatewayRequest(prompt="Generate oversized text")
    res = await adapter.execute_fast(req)

    assert res.finish_reason == "size_limit_exceeded"
    assert res.raw_metadata["size_truncated"] is True
    assert len(res.content) <= 100 + len("\n[RESPONSE TRUNCATED: Size limit exceeded]")


# --- 6. Capabilities and Health Probe ---

@pytest.mark.anyio
async def test_small_model_capabilities_and_health():
    """Verify adapter capabilities metadata and health status."""
    adapter = SmallModelAdapter(test_mode=True)
    assert await adapter.check_health() is True

    caps = adapter.get_capabilities()
    assert isinstance(caps, ModelCapabilities)
    assert caps.provider_name == "small_model"
    assert ModelTier.FAST_CHEAP in caps.supported_tiers
    assert ModelTier.STRONG in caps.supported_tiers
    assert caps.fast_model_id == "gpt-4o-mini-2024-07-18"
    assert caps.strong_model_id == "gpt-4o-mini-2024-07-18"
    assert caps.max_context_window == 128_000


# --- 7. Gateway Routing Integration & Explicit Provider Dispatch ---

@pytest.mark.anyio
async def test_gateway_explicit_provider_dispatch():
    """Verify GatewayRequest with provider='small_model' routes to SmallModelAdapter."""
    gw = ModelGateway()
    gw.register_adapter(ClaudeAdapter(), set_active=True)
    gw.register_adapter(SmallModelAdapter(test_mode=True), set_active=False)

    # Default without provider parameter routes to active (Claude)
    res_default = await gw.execute(GatewayRequest(prompt="Hello Claude", tier=ModelTier.FAST_CHEAP))
    assert res_default.provider_name == "claude"
    assert res_default.model_id == "claude-3-5-haiku-20241022"

    # Explicit provider parameter routes to small_model
    res_small = await gw.execute(GatewayRequest(
        prompt="Hello Small Model",
        tier=ModelTier.FAST_CHEAP,
        provider="small_model"
    ))
    assert res_small.provider_name == "small_model"
    assert res_small.model_id == "gpt-4o-mini-2024-07-18"
    assert res_small.model_version == "2024-07-18"


@pytest.mark.anyio
async def test_gateway_switch_active_adapter_in_dev():
    """Verify switching active adapter in development mode routes queries cleanly."""
    gw = ModelGateway()
    gw.register_adapter(ClaudeAdapter(), set_active=True)
    gw.register_adapter(SmallModelAdapter(test_mode=True), set_active=False)

    assert gw.get_model_recommendation(ModelTier.FAST_CHEAP) == "claude-3-5-haiku-20241022"

    # Switch active adapter to small_model
    gw.set_active_adapter("small_model")
    assert gw.get_active_adapter().provider_name == "small_model"
    assert gw.get_model_recommendation(ModelTier.FAST_CHEAP) == "gpt-4o-mini-2024-07-18"

    res = await gw.execute(GatewayRequest(prompt="Quick query in dev"))
    assert res.provider_name == "small_model"
    assert res.model_version == "2024-07-18"


# --- 8. API Endpoint Integration with Small Model ---

def test_api_gateway_execute_small_model_endpoint():
    """Verify POST /api/v1/gateway/execute with provider='small_model'."""
    payload = {
        "prompt": "Evaluate this query quickly",
        "tier": "fast_cheap",
        "provider": "small_model",
        "max_tokens": 128
    }
    res = client.post("/api/v1/gateway/execute", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["provider_name"] == "small_model"
    assert data["model_id"] == "gpt-4o-mini-2024-07-18"
    assert data["model_version"] == "2024-07-18"
    assert data["tier"] == "fast_cheap"
    assert data["finish_reason"] == "stop"


def test_api_gateway_health_shows_registered_providers():
    """Verify GET /api/v1/gateway/health shows both claude and small_model."""
    res = client.get("/api/v1/gateway/health")
    assert res.status_code == 200
    data = res.json()
    assert data["gateway_status"] == "ok"
    assert "small_model" in data.get("registered_providers", [])
    assert "claude" in data.get("registered_providers", [])


# --- 9. Live HTTP Client Execution (Mocked via httpx AsyncClient) ---

@pytest.mark.anyio
async def test_small_model_live_http_path_success():
    """Verify live HTTP path serialization, token parsing, and header auth with mocked HTTP transport."""
    adapter = SmallModelAdapter(
        api_key="backend-secret-key-xyz",
        test_mode=False
    )

    fake_openai_response = {
        "id": "chatcmpl-test-123",
        "object": "chat.completion",
        "created": 1720000000,
        "model": "gpt-4o-mini-2024-07-18",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "This is a live completion from gpt-4o-mini."
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 10,
            "total_tokens": 25
        }
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.is_error = False
    mock_response.content = b'{"mock": true}'
    mock_response.json.return_value = fake_openai_response

    with patch.object(httpx.AsyncClient, "post", return_value=mock_response) as mock_post:
        req = GatewayRequest(
            prompt="Hello live gpt-4o-mini",
            tier=ModelTier.FAST_CHEAP,
            max_tokens=100
        )
        res = await adapter.execute_fast(req)

        assert res.content == "This is a live completion from gpt-4o-mini."
        assert res.input_tokens == 15
        assert res.output_tokens == 10
        assert res.model_id == "gpt-4o-mini-2024-07-18"
        assert res.model_version == "2024-07-18"
        assert res.raw_metadata["test_mode"] is False

        # Verify Authorization header was sent with Bearer key
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer backend-secret-key-xyz"


@pytest.mark.anyio
async def test_small_model_live_http_transient_retry_and_recovery():
    """Verify live HTTP client retries on HTTP 429 and recovers on next attempt."""
    adapter = SmallModelAdapter(
        api_key="backend-secret-key-xyz",
        max_retries=2,
        backoff_factor=0.01,
        test_mode=False
    )

    # First call returns 429 (rate limit), second call returns 200
    mock_resp_429 = MagicMock(spec=httpx.Response)
    mock_resp_429.status_code = 429
    mock_resp_429.is_error = True

    mock_resp_200 = MagicMock(spec=httpx.Response)
    mock_resp_200.status_code = 200
    mock_resp_200.is_error = False
    mock_resp_200.content = b'{"ok": true}'
    mock_resp_200.json.return_value = {
        "choices": [{"message": {"content": "Recovered after rate limit"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5}
    }

    with patch.object(httpx.AsyncClient, "post", side_effect=[mock_resp_429, mock_resp_200]):
        req = GatewayRequest(prompt="Rate limit retry test")
        res = await adapter.execute_fast(req)

        assert res.content == "Recovered after rate limit"
        assert res.raw_metadata["retries_performed"] == 1
