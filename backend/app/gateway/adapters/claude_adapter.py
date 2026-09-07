"""Claude provider adapter isolating all Anthropic/Claude specific details."""

import time
from typing import Any
from .base import BaseModelAdapter
from ..base import ModelTier, GatewayRequest, GatewayResponse, ModelCapabilities


class ClaudeAdapter(BaseModelAdapter):
    """Adapter for Claude models (Anthropic).
    
    ISOLATION GUARANTEE:
    - All Claude-specific model IDs ('claude-3-5-haiku', 'claude-3-5-sonnet')
      are strictly confined inside this adapter.
    - Provides deterministic local responses when no external API key is present.
    """

    DEFAULT_FAST_MODEL = "claude-3-5-haiku-20241022"
    DEFAULT_STRONG_MODEL = "claude-3-5-sonnet-20241022"

    def __init__(
        self,
        fast_model_id: str = DEFAULT_FAST_MODEL,
        strong_model_id: str = DEFAULT_STRONG_MODEL,
        api_key: str | None = None
    ):
        self._fast_model_id = fast_model_id
        self._strong_model_id = strong_model_id
        self._api_key = api_key
        self._is_healthy = True

    @property
    def provider_name(self) -> str:
        return "claude"

    def get_model_id_for_tier(self, tier: ModelTier) -> str:
        if tier == ModelTier.FAST_CHEAP:
            return self._fast_model_id
        elif tier == ModelTier.STRONG:
            return self._strong_model_id
        raise ValueError(f"Unknown tier: {tier}")

    def get_model_version_for_tier(self, tier: ModelTier) -> str | None:
        return "20241022"

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider_name=self.provider_name,
            supported_tiers=[ModelTier.FAST_CHEAP, ModelTier.STRONG],
            fast_model_id=self._fast_model_id,
            strong_model_id=self._strong_model_id,
            supports_streaming=True,
            max_context_window=200_000
        )

    async def check_health(self) -> bool:
        return self._is_healthy

    def set_healthy(self, healthy: bool) -> None:
        """Utility for test simulation."""
        self._is_healthy = healthy

    def _build_claude_messages(self, request: GatewayRequest) -> dict[str, Any]:
        """Builds Claude-specific messages API payload format."""
        messages = []
        for turn in request.context_turns:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt
        return payload

    async def execute_fast(self, request: GatewayRequest) -> GatewayResponse:
        """Execute fast/cheap model operation (Claude 3.5 Haiku)."""
        start = time.perf_counter()
        model_id = self.get_model_id_for_tier(ModelTier.FAST_CHEAP)
        _ = self._build_claude_messages(request)

        # Simulated response when running offline or without live external API
        simulated_content = f"[Claude Fast Model: {model_id}] Processed: {request.prompt[:60]}..."
        latency_ms = (time.perf_counter() - start) * 1000.0

        return GatewayResponse(
            content=simulated_content,
            tier=ModelTier.FAST_CHEAP,
            model_id=model_id,
            provider_name=self.provider_name,
            input_tokens=max(1, len(request.prompt) // 4),
            output_tokens=max(1, len(simulated_content) // 4),
            latency_ms=round(latency_ms, 2),
            finish_reason="stop",
            raw_metadata={"claude_tier": "fast_cheap", "simulated": True}
        )

    async def execute_strong(self, request: GatewayRequest) -> GatewayResponse:
        """Execute strong model operation (Claude 3.5 Sonnet / Opus)."""
        start = time.perf_counter()
        model_id = self.get_model_id_for_tier(ModelTier.STRONG)
        _ = self._build_claude_messages(request)

        # Simulated response when running offline or without live external API
        simulated_content = f"[Claude Strong Model: {model_id}] Deep reasoning completed: {request.prompt[:60]}..."
        latency_ms = (time.perf_counter() - start) * 1000.0

        return GatewayResponse(
            content=simulated_content,
            tier=ModelTier.STRONG,
            model_id=model_id,
            provider_name=self.provider_name,
            input_tokens=max(1, len(request.prompt) // 4),
            output_tokens=max(1, len(simulated_content) // 4),
            latency_ms=round(latency_ms, 2),
            finish_reason="stop",
            raw_metadata={"claude_tier": "strong", "simulated": True}
        )
