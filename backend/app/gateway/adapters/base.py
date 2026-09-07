"""Abstract base adapter defining minimum model operations."""

from abc import ABC, abstractmethod
from ..base import ModelTier, GatewayRequest, GatewayResponse, ModelCapabilities


class BaseModelAdapter(ABC):
    """Abstract base class for model provider adapters.
    
    Encapsulates all provider-specific details (API calling, authentication,
    request serialization, model naming) away from the router core.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier (e.g. 'claude', 'mock')."""
        pass

    @abstractmethod
    async def execute_fast(self, request: GatewayRequest) -> GatewayResponse:
        """Minimum operation for a fast/cheap model.
        
        Suited for lightweight inquiries, simple classification, and low-latency tasks.
        """
        pass

    @abstractmethod
    async def execute_strong(self, request: GatewayRequest) -> GatewayResponse:
        """Minimum operation for a stronger model.
        
        Suited for complex reasoning, multi-step debugging, coding, and in-depth analysis.
        """
        pass

    async def execute(self, tier: ModelTier, request: GatewayRequest) -> GatewayResponse:
        """General operation dispatching to the appropriate tier method."""
        if tier == ModelTier.FAST_CHEAP:
            return await self.execute_fast(request)
        elif tier == ModelTier.STRONG:
            return await self.execute_strong(request)
        else:
            raise ValueError(f"Unsupported model tier: '{tier}'")

    @abstractmethod
    async def check_health(self) -> bool:
        """Readiness and health probe for the provider adapter."""
        pass

    @abstractmethod
    def get_model_id_for_tier(self, tier: ModelTier) -> str:
        """Returns the concrete provider model identifier for the given tier."""
        pass

    def get_model_version_for_tier(self, tier: ModelTier) -> str | None:
        """Returns provider model release version for given tier if available."""
        return getattr(self, "_model_version", None)

    @abstractmethod
    def get_capabilities(self) -> ModelCapabilities:
        """Returns the capabilities and metadata of the provider adapter."""
        pass
