"""Unit and integration tests for the Internal Model Gateway Abstraction."""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.gateway import (
    ModelTier,
    GatewayRequest,
    GatewayResponse,
    ModelCapabilities,
    BaseModelAdapter,
    ClaudeAdapter,
    ModelGateway,
    default_gateway,
)
from app.schemas.contract import CoarseRoute

client = TestClient(app)


# --- 1. Unit Tests for ClaudeAdapter Operations ---

@pytest.mark.anyio
async def test_claude_adapter_fast_operation():
    """Verify minimum operation for fast/cheap model on ClaudeAdapter."""
    adapter = ClaudeAdapter()
    req = GatewayRequest(
        prompt="Explain photosynthesis in one sentence",
        tier=ModelTier.FAST_CHEAP,
        max_tokens=256
    )
    res = await adapter.execute_fast(req)

    assert isinstance(res, GatewayResponse)
    assert res.tier == ModelTier.FAST_CHEAP
    assert res.provider_name == "claude"
    assert res.model_id == "claude-3-5-haiku-20241022"
    assert len(res.content) > 0
    assert res.input_tokens > 0
    assert res.output_tokens > 0
    assert res.latency_ms >= 0.0


@pytest.mark.anyio
async def test_claude_adapter_strong_operation():
    """Verify minimum operation for stronger model on ClaudeAdapter."""
    adapter = ClaudeAdapter()
    req = GatewayRequest(
        prompt="Write a deadlock-free concurrent queue in C++ with memory barriers",
        tier=ModelTier.STRONG,
        max_tokens=1024
    )
    res = await adapter.execute_strong(req)

    assert isinstance(res, GatewayResponse)
    assert res.tier == ModelTier.STRONG
    assert res.provider_name == "claude"
    assert res.model_id == "claude-3-5-sonnet-20241022"
    assert len(res.content) > 0
    assert res.input_tokens > 0
    assert res.output_tokens > 0
    assert res.latency_ms >= 0.0


@pytest.mark.anyio
async def test_claude_adapter_capabilities_and_health():
    """Verify adapter capabilities and health probe."""
    adapter = ClaudeAdapter()
    assert await adapter.check_health() is True

    caps = adapter.get_capabilities()
    assert isinstance(caps, ModelCapabilities)
    assert caps.provider_name == "claude"
    assert ModelTier.FAST_CHEAP in caps.supported_tiers
    assert ModelTier.STRONG in caps.supported_tiers
    assert caps.fast_model_id == "claude-3-5-haiku-20241022"
    assert caps.strong_model_id == "claude-3-5-sonnet-20241022"


# --- 2. ModelGateway Routing Decoupling Tests ---

def test_gateway_route_to_tier_resolution():
    """Verify gateway cleanly maps coarse routes to abstract model tiers."""
    gw = ModelGateway(ClaudeAdapter())

    assert gw.resolve_tier_from_route(CoarseRoute.SIMPLE_MODEL_CANDIDATE.value) == ModelTier.FAST_CHEAP
    assert gw.resolve_tier_from_route(CoarseRoute.COMPLEX_MODEL_CANDIDATE.value) == ModelTier.STRONG
    assert gw.resolve_tier_from_route(CoarseRoute.NEEDS_EVALUATION.value) == ModelTier.STRONG
    assert gw.resolve_tier_from_route(CoarseRoute.LOCAL_ELIGIBLE.value) is None
    assert gw.resolve_tier_from_route(None) is None


def test_gateway_model_recommendation_isolation():
    """Verify gateway provides model recommendations without leaking provider details to router core."""
    gw = ModelGateway(ClaudeAdapter())

    fast_rec = gw.get_model_recommendation(ModelTier.FAST_CHEAP)
    strong_rec = gw.get_model_recommendation(ModelTier.STRONG)

    assert fast_rec == "claude-3-5-haiku-20241022"
    assert strong_rec == "claude-3-5-sonnet-20241022"


# --- 3. Pluggability Guardrail: Future Adapters Without Routing Rewrites ---

class MockFutureAdapter(BaseModelAdapter):
    """Custom adapter simulating a future model provider (e.g. OpenAI / Local LLM)."""

    @property
    def provider_name(self) -> str:
        return "mock_future_provider"

    def get_model_id_for_tier(self, tier: ModelTier) -> str:
        return "future-fast-v1" if tier == ModelTier.FAST_CHEAP else "future-strong-v1"

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider_name=self.provider_name,
            fast_model_id="future-fast-v1",
            strong_model_id="future-strong-v1"
        )

    async def check_health(self) -> bool:
        return True

    async def execute_fast(self, request: GatewayRequest) -> GatewayResponse:
        return GatewayResponse(
            content="[Mock Future Provider Fast]",
            tier=ModelTier.FAST_CHEAP,
            model_id="future-fast-v1",
            provider_name=self.provider_name
        )

    async def execute_strong(self, request: GatewayRequest) -> GatewayResponse:
        return GatewayResponse(
            content="[Mock Future Provider Strong]",
            tier=ModelTier.STRONG,
            model_id="future-strong-v1",
            provider_name=self.provider_name
        )


@pytest.mark.anyio
async def test_future_adapter_pluggability_without_changing_router():
    """Verify that a new adapter can be registered and active without changing routing logic."""
    gw = ModelGateway(ClaudeAdapter())
    assert gw.get_active_adapter().provider_name == "claude"

    # Register and activate new adapter
    mock_adapter = MockFutureAdapter()
    gw.register_adapter(mock_adapter, set_active=True)

    assert gw.get_active_adapter().provider_name == "mock_future_provider"
    assert gw.get_model_recommendation(ModelTier.FAST_CHEAP) == "future-fast-v1"
    assert gw.get_model_recommendation(ModelTier.STRONG) == "future-strong-v1"

    # Execute request through new adapter
    req = GatewayRequest(prompt="Test prompt", tier=ModelTier.FAST_CHEAP)
    res = await gw.execute(req)
    assert res.provider_name == "mock_future_provider"
    assert res.model_id == "future-fast-v1"


# --- 4. Integration with API Endpoints ---

def test_gateway_health_endpoint():
    """Verify GET /api/v1/gateway/health returns active provider and capabilities."""
    res = client.get("/api/v1/gateway/health")
    assert res.status_code == 200
    data = res.json()
    assert data["gateway_status"] == "ok"
    assert data["active_provider"] == "claude"
    assert data["healthy"] is True
    assert "capabilities" in data


def test_gateway_execute_endpoint_fast():
    """Verify POST /api/v1/gateway/execute with FAST_CHEAP tier."""
    payload = {
        "prompt": "What is the capital of France?",
        "tier": "fast_cheap",
        "max_tokens": 128
    }
    res = client.post("/api/v1/gateway/execute", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["tier"] == "fast_cheap"
    assert data["provider_name"] == "claude"
    assert data["model_id"] == "claude-3-5-haiku-20241022"
    assert len(data["content"]) > 0


def test_gateway_execute_endpoint_strong():
    """Verify POST /api/v1/gateway/execute with STRONG tier."""
    payload = {
        "prompt": "Analyze architectural tradeoffs between microservices and monoliths",
        "tier": "strong",
        "max_tokens": 512
    }
    res = client.post("/api/v1/gateway/execute", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["tier"] == "strong"
    assert data["provider_name"] == "claude"
    assert data["model_id"] == "claude-3-5-sonnet-20241022"
    assert len(data["content"]) > 0


def test_gateway_execute_validation_failure():
    """Verify POST /api/v1/gateway/execute rejects empty prompt and invalid tier."""
    # Empty prompt
    res1 = client.post("/api/v1/gateway/execute", json={"prompt": "", "tier": "fast_cheap"})
    assert res1.status_code == 422

    # Invalid tier
    res2 = client.post("/api/v1/gateway/execute", json={"prompt": "Hello", "tier": "ultra_expensive"})
    assert res2.status_code == 422
