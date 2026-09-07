"""Base models and definitions for the Model Gateway abstraction."""

from typing import Any
import time
from pydantic import BaseModel, Field, ConfigDict
from ..schemas.contract import ModelTier


class GatewayRequest(BaseModel):
    """Standardized request for model gateway operations."""
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="Prompt text to be processed by the model"
    )
    tier: ModelTier = Field(
        default=ModelTier.FAST_CHEAP,
        description="Required model capability tier"
    )
    provider: str | None = Field(
        default=None,
        max_length=64,
        description="Optional explicit provider identifier to route to"
    )
    system_prompt: str | None = Field(
        default=None,
        max_length=20_000,
        description="Optional system prompt or persona instructions"
    )
    max_tokens: int = Field(
        default=1024,
        ge=1,
        le=8192,
        description="Maximum tokens to generate"
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Sampling temperature"
    )
    context_turns: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=20,
        description="Optional structured conversational context turns"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-sensitive request metadata"
    )
    correlation_id: str | None = Field(
        default=None,
        max_length=128,
        description="Correlation identifier for tracking and telemetry"
    )


class GatewayResponse(BaseModel):
    """Standardized response from model gateway operations."""
    model_config = ConfigDict(extra="forbid")

    content: str = Field(
        ...,
        description="Model output completion or response text"
    )
    tier: ModelTier = Field(
        ...,
        description="Model tier used for the operation"
    )
    model_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Concrete model identifier from the provider (e.g. claude-3-5-haiku-20241022)"
    )
    model_version: str | None = Field(
        default=None,
        max_length=64,
        description="Exact provider model release or version tag (e.g. '2024-07-18')"
    )
    provider_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Name of the model provider adapter (e.g. 'claude', 'small_model')"
    )
    input_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated or measured input tokens"
    )
    output_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated or measured output tokens"
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Execution latency in milliseconds"
    )
    finish_reason: str = Field(
        default="stop",
        description="Reason for completion termination ('stop', 'length', 'size_limit_exceeded', etc.)"
    )
    raw_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-sensitive provider-specific metadata"
    )


class ModelCapabilities(BaseModel):
    """Capabilities and metadata of an adapter."""
    model_config = ConfigDict(extra="forbid")

    provider_name: str = Field(..., min_length=1, max_length=64)
    supported_tiers: list[ModelTier] = Field(
        default_factory=lambda: [ModelTier.FAST_CHEAP, ModelTier.STRONG]
    )
    fast_model_id: str = Field(..., min_length=1, max_length=128)
    strong_model_id: str = Field(..., min_length=1, max_length=128)
    supports_streaming: bool = True
    max_context_window: int = 200_000


# --- Gateway Exceptions ---

class GatewayError(Exception):
    """Base exception for model gateway operations."""
    pass


class GatewayTimeoutError(GatewayError):
    """Raised when a model gateway operation times out."""
    pass


class GatewayRetryExhaustedError(GatewayError):
    """Raised when bounded retries for a model operation are exhausted."""
    pass


class GatewayResponseSizeLimitError(GatewayError):
    """Raised when a model response exceeds the configured maximum size limit."""
    pass


# --- Provider-Agnostic Model Comparison Models & Functions ---

class ModelExecutionComparison(BaseModel):
    """Provider-agnostic comparative analysis between two model execution outcomes."""
    model_config = ConfigDict(extra="forbid")

    comparison_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique identifier tracking this comparative evaluation"
    )
    small_response: GatewayResponse = Field(
        ...,
        description="Execution outcome from fast/inexpensive model"
    )
    strong_response: GatewayResponse = Field(
        ...,
        description="Execution outcome from stronger model"
    )

    # Latency differential metrics
    latency_delta_ms: float = Field(
        ...,
        description="Difference in latency (strong_latency - small_latency) in ms"
    )
    latency_ratio: float = Field(
        ...,
        ge=0.0,
        description="Ratio of strong latency to small latency"
    )

    # Token consumption differential metrics
    token_delta: int = Field(
        ...,
        description="Difference in total tokens (strong_tokens - small_tokens)"
    )
    token_ratio: float = Field(
        ...,
        ge=0.0,
        description="Ratio of strong output tokens to small output tokens"
    )
    content_length_delta: int = Field(
        ...,
        description="Difference in content character length (strong_len - small_len)"
    )

    # Structural & Quality Consensus Signals
    both_successful: bool = Field(
        ...,
        description="Whether both model invocations terminated successfully"
    )
    agreement_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Normalized lexical/structural similarity score [0.0 - 1.0]"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-sensitive comparative metadata"
    )


def compute_lexical_overlap(text_a: str, text_b: str) -> float:
    """Computes Jaccard word-level overlap as a provider-agnostic agreement signal."""
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a.intersection(tokens_b)
    union = tokens_a.union(tokens_b)
    return round(len(intersection) / len(union), 4)


def compare_executions(
    small_response: GatewayResponse,
    strong_response: GatewayResponse,
    comparison_id: str | None = None
) -> ModelExecutionComparison:
    """Computes provider-agnostic comparative metrics between two gateway responses.
    
    Operates strictly on abstract GatewayResponse objects without leaking provider-specific
    assumptions into the router core.
    """
    comp_id = comparison_id or f"comp-{int(time.time() * 1000)}"

    small_lat = max(0.01, small_response.latency_ms)
    strong_lat = strong_response.latency_ms
    latency_delta_ms = round(strong_lat - small_response.latency_ms, 2)
    latency_ratio = round(strong_lat / small_lat, 3)

    small_total_tokens = small_response.input_tokens + small_response.output_tokens
    strong_total_tokens = strong_response.input_tokens + strong_response.output_tokens
    token_delta = strong_total_tokens - small_total_tokens
    token_ratio = round(strong_response.output_tokens / max(1, small_response.output_tokens), 3)

    content_length_delta = len(strong_response.content) - len(small_response.content)
    both_successful = (
        small_response.finish_reason in ("stop", "length")
        and strong_response.finish_reason in ("stop", "length")
    )
    agreement_score = compute_lexical_overlap(small_response.content, strong_response.content)

    return ModelExecutionComparison(
        comparison_id=comp_id,
        small_response=small_response,
        strong_response=strong_response,
        latency_delta_ms=latency_delta_ms,
        latency_ratio=latency_ratio,
        token_delta=token_delta,
        token_ratio=token_ratio,
        content_length_delta=content_length_delta,
        both_successful=both_successful,
        agreement_score=agreement_score,
        metadata={
            "small_model_id": small_response.model_id,
            "small_model_version": small_response.model_version,
            "strong_model_id": strong_response.model_id,
            "strong_model_version": strong_response.model_version,
        }
    )
