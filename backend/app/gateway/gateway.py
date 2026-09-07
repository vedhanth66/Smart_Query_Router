import asyncio
from typing import Any
import os
from .base import (
    ModelTier,
    GatewayRequest,
    GatewayResponse,
    ModelCapabilities,
    ModelExecutionComparison,
    compare_executions,
)
from .adapters.base import BaseModelAdapter
from .adapters.claude_adapter import ClaudeAdapter
from .adapters.small_model_adapter import SmallModelAdapter
from .adapters.strong_model_adapter import StrongModelAdapter


class ModelGateway:
    """Internal model gateway coordinator.
    
    Responsibilities:
    - Decouples routing decisions (CoarseRoute) from provider implementations.
    - Exposes minimum operations for fast/cheap and strong models.
    - Manages registered provider adapter(s) without connecting multiple providers at once.
    - Makes future provider adapters pluggable without rewriting routing logic.
    """

    def __init__(self, default_adapter: BaseModelAdapter | None = None):
        self._adapters: dict[str, BaseModelAdapter] = {}
        self._active_provider: str | None = None

        if default_adapter:
            self.register_adapter(default_adapter, set_active=True)

    def register_adapter(self, adapter: BaseModelAdapter, set_active: bool = False) -> None:
        """Register a model provider adapter."""
        if not isinstance(adapter, BaseModelAdapter):
            raise TypeError("Adapter must inherit from BaseModelAdapter")
        self._adapters[adapter.provider_name] = adapter
        if set_active or self._active_provider is None:
            self._active_provider = adapter.provider_name

    def set_active_adapter(self, provider_name: str) -> None:
        """Switch active provider adapter without changing routing logic."""
        if provider_name not in self._adapters:
            raise KeyError(f"Provider '{provider_name}' is not registered")
        self._active_provider = provider_name

    def get_adapter(self, provider_name: str | None = None) -> BaseModelAdapter:
        """Return adapter by name or the currently active provider adapter."""
        if provider_name:
            if provider_name not in self._adapters:
                raise KeyError(f"Provider '{provider_name}' is not registered in gateway")
            return self._adapters[provider_name]
        return self.get_active_adapter()

    def get_active_adapter(self) -> BaseModelAdapter:
        """Return the currently active provider adapter."""
        if not self._active_provider or self._active_provider not in self._adapters:
            raise RuntimeError("No active model provider adapter configured in gateway")
        return self._adapters[self._active_provider]

    def list_adapters(self) -> list[str]:
        """List registered provider adapter identifiers."""
        return list(self._adapters.keys())

    def resolve_tier_from_route(self, coarse_route: str | None) -> ModelTier | None:
        """Maps a coarse routing decision to an abstract model tier.
        
        Decoupled from specific model names:
        - 'simple-model candidate' -> FAST_CHEAP
        - 'complex-model candidate' -> STRONG
        - 'needs-evaluation' -> STRONG (conservative default)
        - 'local-eligible' -> None (handled on device)
        """
        if not coarse_route:
            return None

        val = coarse_route.lower() if isinstance(coarse_route, str) else str(coarse_route).lower()
        if "simple" in val:
            return ModelTier.FAST_CHEAP
        elif "complex" in val:
            return ModelTier.STRONG
        elif "evaluation" in val:
            return ModelTier.STRONG
        elif "local" in val:
            return None
        return None

    def get_model_recommendation(self, tier: ModelTier, provider_name: str | None = None) -> str:
        """Resolves concrete model identifier from the target/active adapter.
        
        Keeps provider model names completely outside the router core.
        """
        adapter = self.get_adapter(provider_name)
        return adapter.get_model_id_for_tier(tier)

    def get_model_version_recommendation(self, tier: ModelTier, provider_name: str | None = None) -> str | None:
        """Resolves concrete model release version from the target/active adapter."""
        adapter = self.get_adapter(provider_name)
        if hasattr(adapter, "get_model_version_for_tier"):
            return adapter.get_model_version_for_tier(tier)
        return getattr(adapter, "_model_version", None)

    async def execute_fast(self, request: GatewayRequest) -> GatewayResponse:
        """Execute minimum fast/cheap model operation."""
        adapter = self.get_adapter(request.provider)
        request_copy = request.model_copy(update={"tier": ModelTier.FAST_CHEAP})
        return await adapter.execute_fast(request_copy)

    async def execute_strong(self, request: GatewayRequest) -> GatewayResponse:
        """Execute minimum strong model operation."""
        adapter = self.get_adapter(request.provider)
        request_copy = request.model_copy(update={"tier": ModelTier.STRONG})
        return await adapter.execute_strong(request_copy)

    async def execute(self, request: GatewayRequest) -> GatewayResponse:
        """General operation dispatching based on request tier and provider."""
        adapter = self.get_adapter(request.provider)
        return await adapter.execute(request.tier, request)

    async def health_check(self) -> dict[str, Any]:
        """Check gateway health and active adapter status."""
        try:
            adapter = self.get_active_adapter()
            healthy = await adapter.check_health()
            caps = adapter.get_capabilities()
            return {
                "gateway_status": "ok" if healthy else "degraded",
                "active_provider": adapter.provider_name,
                "healthy": healthy,
                "registered_providers": self.list_adapters(),
                "capabilities": caps.model_dump()
            }
        except Exception as e:
            return {
                "gateway_status": "error",
                "active_provider": self._active_provider,
                "healthy": False,
                "registered_providers": self.list_adapters(),
                "error": str(e)
            }

    async def compare_execution(
        self,
        request: GatewayRequest,
        small_provider: str = "small_model",
        strong_provider: str = "strong_model",
        comparison_id: str | None = None,
    ) -> ModelExecutionComparison:
        """Executes the request concurrently across both small and strong models and compares outcomes.
        
        Guarantees:
        - Runs both models concurrently via asyncio.gather.
        - Emits standardized ModelExecutionComparison with latency/token deltas and lexical overlap.
        - Completely decoupled from provider-specific implementations.
        """
        small_adapter = self.get_adapter(small_provider)
        strong_adapter = self.get_adapter(strong_provider)

        req_small = request.model_copy(update={"provider": small_provider, "tier": ModelTier.FAST_CHEAP})
        req_strong = request.model_copy(update={"provider": strong_provider, "tier": ModelTier.STRONG})

        small_res, strong_res = await asyncio.gather(
            small_adapter.execute_fast(req_small),
            strong_adapter.execute_strong(req_strong),
        )

        return compare_executions(
            small_response=small_res,
            strong_response=strong_res,
            comparison_id=comparison_id or request.correlation_id,
        )


def create_default_gateway() -> ModelGateway:
    """Creates default gateway pre-configured with Claude, SmallModelAdapter, and StrongModelAdapter."""
    gw = ModelGateway()
    # Claude adapter (kept untouched) registered as default active provider
    gw.register_adapter(ClaudeAdapter(), set_active=True)
    # Small inexpensive model connected for development & fast routing
    gw.register_adapter(SmallModelAdapter(), set_active=False)
    # Stronger model connected for deep reasoning and comparative evaluations
    gw.register_adapter(StrongModelAdapter(), set_active=False)

    # Optional environment override to set active provider in development
    env_provider = os.environ.get("GATEWAY_PROVIDER", "").strip().lower()
    if env_provider in ("small_model", "strong_model"):
        gw.set_active_adapter(env_provider)

    return gw


# Default singleton instance
default_gateway = create_default_gateway()

