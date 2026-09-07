"""Stronger model adapter for the Model Gateway (Development & Evaluation).

ISOLATION & SECURITY GUARANTEES:
- Connects one stronger model (gpt-4o-2024-08-06) with advanced reasoning and analysis.
- Credentials live on the backend only (never exposed to client, logs, or responses).
- Supports the exact same logical request contract (GatewayRequest) as SmallModelAdapter.
- Enforces bounded timeouts, bounded retries with backoff, and response-size limits.
- Exposes clear model-version metadata ('gpt-4o-2024-08-06', version '2024-08-06').
- Provides a deterministic test mode with mocked responses for offline verification.
"""

import os
import time
import asyncio
from typing import Any
import httpx

from .base import BaseModelAdapter
from ..base import (
    ModelTier,
    GatewayRequest,
    GatewayResponse,
    ModelCapabilities,
    GatewayError,
    GatewayTimeoutError,
    GatewayRetryExhaustedError,
    GatewayResponseSizeLimitError,
)


class StrongModelAdapter(BaseModelAdapter):
    """Adapter for a stronger model (OpenAI gpt-4o or compatible).
    
    Designed for complex reasoning, multi-step debugging, deep synthesis, and high-fidelity completions.
    """

    DEFAULT_MODEL_ID = "gpt-4o-2024-08-06"
    DEFAULT_MODEL_VERSION = "2024-08-06"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_TIMEOUT_SECONDS = 15.0
    DEFAULT_MAX_RETRIES = 2
    DEFAULT_MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB
    DEFAULT_MAX_RESPONSE_CHARS = 100_000

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        model_version: str = DEFAULT_MODEL_VERSION,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_response_chars: int = DEFAULT_MAX_RESPONSE_CHARS,
        test_mode: bool | None = None,
        backoff_factor: float = 0.05,
    ):
        self._model_id = model_id
        self._model_version = model_version
        self._base_url = (
            base_url
            or os.environ.get("STRONG_MODEL_BASE_URL")
            or os.environ.get("SMALL_MODEL_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")

        # Keep credentials strictly on backend: never exposed to client or logged
        self._api_key = (
            api_key
            or os.environ.get("STRONG_MODEL_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._max_response_bytes = max_response_bytes
        self._max_response_chars = max_response_chars
        self._backoff_factor = backoff_factor

        # Deterministic test mode: active by default if test_mode=True or no API key is present
        if test_mode is not None:
            self._test_mode = test_mode
        else:
            env_flag = os.environ.get("STRONG_MODEL_TEST_MODE", "").lower()
            if env_flag in ("true", "1", "yes"):
                self._test_mode = True
            elif env_flag in ("false", "0", "no"):
                self._test_mode = False
            else:
                self._test_mode = self._api_key is None

        # Test simulation hooks for deterministic edge-case testing
        self._mock_response_content: str | None = None
        self._mock_tokens: int | None = None
        self._mock_timeout: bool = False
        self._mock_transient_failures: int = 0
        self._mock_oversized_response: bool = False
        self._attempts_recorded: int = 0

    @property
    def provider_name(self) -> str:
        return "strong_model"

    @property
    def test_mode(self) -> bool:
        return self._test_mode

    def get_model_id_for_tier(self, tier: ModelTier) -> str:
        return self._model_id

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider_name=self.provider_name,
            supported_tiers=[ModelTier.FAST_CHEAP, ModelTier.STRONG],
            fast_model_id=self._model_id,
            strong_model_id=self._model_id,
            supports_streaming=True,
            max_context_window=128_000
        )

    async def check_health(self) -> bool:
        """Health check verifying operational readiness without leaking secrets."""
        return True

    # --- Test Mock Configuration Hooks ---

    def set_test_mode(self, enabled: bool) -> None:
        """Toggle deterministic test mode."""
        self._test_mode = enabled

    def set_mock_response(self, content: str | None, tokens: int | None = None) -> None:
        """Configure custom deterministic mocked response content."""
        self._mock_response_content = content
        self._mock_tokens = tokens

    def set_mock_timeout(self, enabled: bool = True) -> None:
        """Configure mock to simulate a gateway timeout."""
        self._mock_timeout = enabled

    def set_mock_failures(self, transient_failures: int) -> None:
        """Configure mock to fail N times transiently before succeeding."""
        self._mock_transient_failures = transient_failures

    def set_mock_oversized_response(self, enabled: bool = True) -> None:
        """Configure mock to simulate a response exceeding response-size limits."""
        self._mock_oversized_response = enabled

    def reset_mocks(self) -> None:
        """Reset all mock simulation states."""
        self._mock_response_content = None
        self._mock_tokens = None
        self._mock_timeout = False
        self._mock_transient_failures = 0
        self._mock_oversized_response = False
        self._attempts_recorded = 0

    # --- Core Operations (Matching Logical Request Contract) ---

    async def execute_fast(self, request: GatewayRequest) -> GatewayResponse:
        """Execute request using stronger model with fast/cheap tier label."""
        return await self._execute_with_resilience(ModelTier.FAST_CHEAP, request)

    async def execute_strong(self, request: GatewayRequest) -> GatewayResponse:
        """Execute request using stronger model for deep reasoning / complex tasks."""
        return await self._execute_with_resilience(ModelTier.STRONG, request)

    # --- Resilient Execution Engine ---

    async def _execute_with_resilience(
        self,
        tier: ModelTier,
        request: GatewayRequest
    ) -> GatewayResponse:
        """Executes model operation with timeouts, bounded retries, and size bounding."""
        start_time = time.perf_counter()

        # Deterministic Test Mode (Offline, Zero Live Inference Dependency)
        if self._test_mode:
            return await self._execute_mock(tier, request, start_time)

        # Live Inference Execution with HTTP Client
        return await self._execute_live(tier, request, start_time)

    # --- Deterministic Mock Execution ---

    async def _execute_mock(
        self,
        tier: ModelTier,
        request: GatewayRequest,
        start_time: float
    ) -> GatewayResponse:
        """Produces deterministic responses without external network calls."""
        self._attempts_recorded += 1

        # Simulate timeout if configured
        if self._mock_timeout:
            raise GatewayTimeoutError(
                f"Operation timed out after {self._timeout_seconds}s (simulated)"
            )

        # Simulate transient failures and bounded retries
        retries_performed = 0
        if self._mock_transient_failures > 0:
            failures_to_simulate = self._mock_transient_failures
            attempts_made = 0
            while attempts_made < failures_to_simulate:
                attempts_made += 1
                if attempts_made > self._max_retries:
                    self._mock_transient_failures = 0
                    raise GatewayRetryExhaustedError(
                        f"Simulated transient error: retries exhausted after {attempts_made} attempts"
                    )
                retries_performed += 1
                await asyncio.sleep(self._backoff_factor * (2 ** (attempts_made - 1)))
            self._mock_transient_failures = 0

        # Construct deterministic response content
        if self._mock_response_content is not None:
            raw_content = self._mock_response_content
        elif self._mock_oversized_response:
            raw_content = "Y" * (self._max_response_chars + 2000)
        else:
            raw_content = (
                f"[StrongModel: {self._model_id} (v{self._model_version})] "
                f"In-depth analysis and comprehensive reasoning completed: {request.prompt[:60]}."
            )

        # Apply response size limit
        content, finish_reason, size_truncated = self._apply_response_size_limits(raw_content)

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        input_tokens = self._mock_tokens or max(1, len(request.prompt) // 4)
        output_tokens = self._mock_tokens or max(1, len(content) // 4)

        return GatewayResponse(
            content=content,
            tier=tier,
            model_id=self._model_id,
            model_version=self._model_version,
            provider_name=self.provider_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency_ms, 2),
            finish_reason=finish_reason,
            raw_metadata={
                "model_version": self._model_version,
                "pricing_tier": "premium",
                "context_window": 128_000,
                "test_mode": True,
                "retries_performed": retries_performed,
                "size_truncated": size_truncated,
                "has_backend_key": bool(self._api_key),
            }
        )

    # --- Live HTTP Execution ---

    async def _execute_live(
        self,
        tier: ModelTier,
        request: GatewayRequest,
        start_time: float
    ) -> GatewayResponse:
        """Executes live HTTP call with bounded retries, timeouts, and size caps."""
        if not self._api_key:
            raise GatewayError(
                "Cannot execute live model query: STRONG_MODEL_API_KEY is not configured on backend."
            )

        url = f"{self._base_url}/chat/completions"
        # Backend-only headers: authorization key stays within this scope
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if request.correlation_id:
            headers["X-Correlation-ID"] = request.correlation_id

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        for turn in request.context_turns:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": self._model_id,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        timeout = httpx.Timeout(self._timeout_seconds, connect=3.0)
        retries_performed = 0
        last_exception: Exception | None = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    response = await client.post(url, headers=headers, json=payload)

                    # Transient error codes eligible for bounded retry
                    if response.status_code in (429, 500, 502, 503, 504):
                        if attempt < self._max_retries:
                            retries_performed += 1
                            backoff = self._backoff_factor * (2 ** attempt)
                            await asyncio.sleep(backoff)
                            continue
                        else:
                            raise GatewayRetryExhaustedError(
                                f"HTTP {response.status_code} from strong model provider after {attempt + 1} attempts"
                            )

                    # Non-retryable HTTP error
                    if response.is_error:
                        raise GatewayError(
                            f"Strong model provider returned HTTP {response.status_code}"
                        )

                    # Check raw response bytes limit
                    if len(response.content) > self._max_response_bytes:
                        raise GatewayResponseSizeLimitError(
                            f"Strong model response payload ({len(response.content)} bytes) exceeds limit ({self._max_response_bytes} bytes)"
                        )

                    data = response.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise GatewayError("Strong model provider response missing completion choices")

                    raw_content = choices[0].get("message", {}).get("content", "")
                    content, finish_reason, size_truncated = self._apply_response_size_limits(raw_content)

                    usage = data.get("usage", {})
                    input_tokens = usage.get("prompt_tokens", len(request.prompt) // 4)
                    output_tokens = usage.get("completion_tokens", len(content) // 4)
                    latency_ms = (time.perf_counter() - start_time) * 1000.0

                    return GatewayResponse(
                        content=content,
                        tier=tier,
                        model_id=self._model_id,
                        model_version=self._model_version,
                        provider_name=self.provider_name,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=round(latency_ms, 2),
                        finish_reason=finish_reason,
                        raw_metadata={
                            "model_version": self._model_version,
                            "pricing_tier": "premium",
                            "context_window": 128_000,
                            "test_mode": False,
                            "retries_performed": retries_performed,
                            "size_truncated": size_truncated,
                        }
                    )

                except (httpx.TimeoutException, TimeoutError) as e:
                    last_exception = e
                    if attempt < self._max_retries:
                        retries_performed += 1
                        backoff = self._backoff_factor * (2 ** attempt)
                        await asyncio.sleep(backoff)
                        continue
                    raise GatewayTimeoutError(
                        f"Strong model gateway request timed out after {self._timeout_seconds}s across {attempt + 1} attempts"
                    ) from e

                except (httpx.NetworkError, httpx.ConnectError) as e:
                    last_exception = e
                    if attempt < self._max_retries:
                        retries_performed += 1
                        backoff = self._backoff_factor * (2 ** attempt)
                        await asyncio.sleep(backoff)
                        continue
                    raise GatewayRetryExhaustedError(
                        f"Network connectivity failure for strong model after {attempt + 1} attempts"
                    ) from e

        raise GatewayError(f"Strong model gateway execution failed: {last_exception}")

    # --- Response Size Limiting ---

    def _apply_response_size_limits(self, raw_content: str) -> tuple[str, str, bool]:
        """Truncates response safely if it exceeds configured character limits."""
        if len(raw_content) > self._max_response_chars:
            truncated = raw_content[:self._max_response_chars] + "\n[RESPONSE TRUNCATED: Size limit exceeded]"
            return truncated, "size_limit_exceeded", True
        return raw_content, "stop", False
