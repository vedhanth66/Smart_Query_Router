"""Smart Query Router - Backend API Contract Schemas.

Defines the typed Pydantic v2 schemas for:
- NormalizedQueryPackage (Request payload from client/extension)
- OptimizationDecisionResponse (Response payload returned to client)

DESIGN CONSTRAINTS:
- Strict input validation with bounded size limits.
- Rejects malformed requests (empty query, oversized texts, excessive candidates).
- Extra fields forbidden to prevent schema drift or injection.
- Zero credential exposure: Never accepts or returns provider keys or model secrets.
"""

from enum import Enum
from typing import Any, Literal
import time
from pydantic import BaseModel, ConfigDict, Field, field_validator


class DecisionType(str, Enum):
    """Supported optimization decision outcomes."""
    NO_OPTIMIZATION = "NO_OPTIMIZATION"
    LOCAL_ANSWER_CANDIDATE = "LOCAL_ANSWER_CANDIDATE"
    CACHE_CANDIDATE = "CACHE_CANDIDATE"
    BACKEND_CANDIDATE = "BACKEND_CANDIDATE"
    MODEL_RECOMMENDATION = "MODEL_RECOMMENDATION"
    CONTEXT_PRUNING = "CONTEXT_PRUNING"


class CoarseRoute(str, Enum):
    """Deterministic coarse routing classification."""
    LOCAL_ELIGIBLE = "local-eligible"
    SIMPLE_MODEL_CANDIDATE = "simple-model candidate"
    COMPLEX_MODEL_CANDIDATE = "complex-model candidate"
    NEEDS_EVALUATION = "needs-evaluation"


class TaskCategory(str, Enum):
    """Stable task categories recognized by the router and classifier."""
    GREETING = "greeting"
    ARITHMETIC = "arithmetic"
    FACTUAL_QUESTION = "factual question"
    SUMMARIZATION = "summarization"
    REWRITING = "rewriting"
    TRANSLATION = "translation"
    CREATIVE_WRITING = "creative writing"
    CODING = "coding"
    DEBUGGING = "debugging"
    COMPARISON = "comparison"
    ANALYSIS = "analysis"
    REASONING = "reasoning"
    UNKNOWN = "unknown"


class ComplexityLevel(str, Enum):
    """Categorical complexity level bands."""
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class UserRoutingOverride(str, Enum):
    """User routing preference override."""
    AUTOMATIC = "automatic"
    PREFER_SIMPLE = "prefer-simple"
    PREFER_STRONG = "prefer-strong"


class ModelTier(str, Enum):
    """Abstract model capability tier."""
    FAST_CHEAP = "fast_cheap"
    STRONG = "strong"


class CacheOutcome(str, Enum):
    """Performance evaluation cache outcome."""
    HIT = "HIT"
    MISS = "MISS"
    BYPASS = "BYPASS"
    NOT_CHECKED = "NOT_CHECKED"


class ClientMetadata(BaseModel):
    """Metadata regarding client and extension environment."""
    model_config = ConfigDict(extra="forbid")

    extension_version: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Version string of the calling extension (e.g. '0.1.0')"
    )
    client_type: str = Field(
        default="chrome_extension",
        min_length=1,
        max_length=64,
        description="Type of client invoking the router"
    )
    schema_version: str = Field(
        default="1.0",
        min_length=1,
        max_length=16,
        description="Protocol schema version"
    )
    hostname: str | None = Field(
        default=None,
        max_length=128,
        description="Target origin hostname (e.g. 'claude.ai')"
    )
    user_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional caller user identifier"
    )
    tenant_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional tenant/organization identifier"
    )


class LocalFeatures(BaseModel):
    """Inexpensive, non-generative local features extracted client-side."""
    model_config = ConfigDict(extra="forbid")

    character_count: int | None = Field(default=None, ge=0)
    word_count: int | None = Field(default=None, ge=0)
    has_code: bool = False
    has_math: bool = False
    has_questions: bool = False
    has_urls: bool = False
    is_normalized: bool = False
    detected_cues: list[str] = Field(default_factory=list, max_length=30)


class ContextCandidateTurn(BaseModel):
    """A single bounded conversational context turn from client turn tracker."""
    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(..., min_length=1, max_length=128)
    role: Literal["user", "assistant"]
    content: str = Field(
        ...,
        max_length=20_000,
        description="Turn snippet or content, capped at 20k characters"
    )
    original_index: int = Field(default=0, ge=0)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    timestamp: int | None = Field(default=None, ge=0)


class NormalizedQueryPackage(BaseModel):
    """Incoming request payload representing a query event from the extension."""
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique request identifier",
        pattern=r"^[a-zA-Z0-9_\-\.:]+$"
    )
    correlation_id: str | None = Field(
        default=None,
        max_length=128,
        description="Correlation identifier spanning client observation to backend",
        pattern=r"^[a-zA-Z0-9_\-\.:]+$"
    )
    coarse_route: CoarseRoute | None = Field(
        default=None,
        description="Optional client-classified deterministic coarse route"
    )
    task_category: TaskCategory | None = Field(
        default=None,
        description="Optional client-classified stable task category signal"
    )
    complexity_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional client-estimated initial complexity score"
    )
    complexity_level: ComplexityLevel | None = Field(
        default=None,
        description="Optional categorical complexity level"
    )
    user_override: UserRoutingOverride | None = Field(
        default=None,
        description="Optional user routing preference override"
    )
    query_text: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="User query text, bounded to maximum 100,000 characters"
    )
    context_candidates: list[ContextCandidateTurn] = Field(
        default_factory=list,
        max_length=10,
        description="Bounded context candidate turns, maximum 10 turns allowed"
    )
    local_features: LocalFeatures | None = Field(
        default=None,
        description="Client-extracted non-generative query features"
    )
    client_metadata: ClientMetadata = Field(
        ...,
        description="Client and extension version metadata"
    )
    execute_route: bool = Field(
        default=True,
        description="Whether to execute the selected model route on the gateway"
    )
    user_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional caller user identifier"
    )
    tenant_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional tenant/organization identifier"
    )

    @field_validator("query_text")
    @classmethod
    def validate_non_whitespace_query(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query_text cannot be empty or whitespace-only")
        return v


class OptimizationInstructions(BaseModel):
    """Optional actionable optimization instructions returned to the extension."""
    model_config = ConfigDict(extra="forbid")

    suggested_model: str | None = Field(
        default=None,
        max_length=64,
        description="Suggested Claude model identifier (e.g. 'claude-3-5-haiku')"
    )
    pruned_turn_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="List of turn IDs that can be safely excluded from context"
    )
    notes: str | None = Field(
        default=None,
        max_length=256,
        description="Non-sensitive operational note"
    )


class RouteExecutionMetadata(BaseModel):
    """Metadata recorded for route execution via the model gateway."""
    model_config = ConfigDict(extra="forbid")

    route: str = Field(..., description="Selected coarse route")
    model_id: str | None = Field(default=None, description="Executed model identifier")
    model_version: str | None = Field(default=None, description="Executed model version metadata")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Model execution latency in milliseconds")
    failure_category: str = Field(default="NONE", description="Failure category ('NONE', 'TIMEOUT', 'PROVIDER_ERROR', 'RETRY_EXHAUSTED', etc.)")
    fallback_applied: bool = Field(default=False, description="Whether safe fallback was triggered on execution failure")
    executed_content: str | None = Field(default=None, description="Model output completion if executed")
    escalation_occurred: bool = Field(default=False, description="Whether small-model response failed evaluation and was escalated to stronger model")
    escalation_reason: str | None = Field(default=None, description="Reason code or explanation for escalation")
    cache_outcome: CacheOutcome = Field(
        default=CacheOutcome.NOT_CHECKED,
        description="Response cache outcome ('HIT', 'MISS', 'BYPASS', 'NOT_CHECKED')"
    )
    cache_key: str | None = Field(
        default=None,
        max_length=256,
        description="Computed cache key if cache was evaluated"
    )
    is_deduplicated: bool = Field(
        default=False,
        description="Whether this request shared an in-flight execution with other callers"
    )
    deduplication_role: str | None = Field(
        default=None,
        description="Role in deduplication flight ('leader', 'follower', or None)"
    )
    semantic_cache_outcome: str | None = Field(
        default=None,
        max_length=64,
        description="Semantic cache outcome ('SEMANTIC_HIT', 'SEMANTIC_MISS', 'SEMANTIC_BYPASS', or None)"
    )
    semantic_validation_reason: str | None = Field(
        default=None,
        max_length=256,
        description="Detailed reason for semantic cache match validation or rejection"
    )
    evaluation_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Evaluation or comparative evaluation details"
    )


class QueryOptimizationComparison(BaseModel):
    """Comparison metrics between original query and semantics-preserving optimized query."""
    model_config = ConfigDict(extra="forbid")

    original_query: str = Field(..., description="Original normalized query, strictly preserved")
    optimized_query: str = Field(..., description="Optimized query after semantics-preserving transformations")
    is_transformed: bool = Field(..., description="Whether any transformations altered the query")
    original_char_count: int = Field(..., ge=0, description="Character count of original query")
    optimized_char_count: int = Field(..., ge=0, description="Character count of optimized query")
    char_savings: int = Field(..., description="Character count reduction (original - optimized)")
    char_savings_pct: float = Field(..., ge=0.0, description="Percentage of characters saved")
    original_token_estimate: int = Field(..., ge=0, description="Estimated token count of original query")
    optimized_token_estimate: int = Field(..., ge=0, description="Estimated token count of optimized query")
    token_savings: int = Field(..., description="Estimated token count reduction")
    token_savings_pct: float = Field(..., ge=0.0, description="Percentage of tokens saved")
    compression_ratio: float = Field(..., ge=0.0, description="Ratio of optimized to original characters")
    transformations_applied: list[str] = Field(default_factory=list, description="List of transformation rules applied")


class QualityEvaluationStatus(str, Enum):
    """Evaluation status regarding model output quality parity.
    
    MANDATE: Never make claims about quality parity, degradation, or improvement
    until empirical evaluation data from task benchmarks exists.
    """
    UNVALIDATED = "UNVALIDATED_PENDING_EMPIRICAL_EVALUATION"
    EMPIRICALLY_EVALUATED = "EMPIRICALLY_EVALUATED"


class ExperimentalCompressionResult(BaseModel):
    """Experimental stronger prompt compression result.
    
    Disabled by default and strictly locked in production.
    High-sensitivity structures (code, math, quoted legal text, URLs) bypass compression.
    Quality parity is strictly unvalidated pending empirical evaluation.
    """
    model_config = ConfigDict(extra="forbid")

    original_input: str = Field(..., description="Original input query, strictly preserved")
    compressed_input: str = Field(..., description="Compressed query text")
    is_compressed: bool = Field(..., description="Whether stronger compression was applied")
    bypass_reason: str | None = Field(default=None, max_length=128, description="Reason if compression was bypassed")
    detected_sensitivities: list[str] = Field(default_factory=list, description="High-sensitivity structures detected")
    original_char_count: int = Field(..., ge=0, description="Character count of original query")
    compressed_char_count: int = Field(..., ge=0, description="Character count of compressed query")
    char_savings: int = Field(..., description="Characters saved")
    compression_ratio: float = Field(..., ge=0.0, description="Ratio of compressed to original characters")
    original_token_estimate: int = Field(..., ge=0, description="Estimated tokens of original query")
    compressed_token_estimate: int = Field(..., ge=0, description="Estimated tokens of compressed query")
    token_savings: int = Field(..., description="Estimated tokens saved")
    applied_transformations: list[str] = Field(default_factory=list, description="Specific compression rules applied")
    quality_evaluation_status: QualityEvaluationStatus = Field(
        default=QualityEvaluationStatus.UNVALIDATED,
        description="Quality claim status; unvalidated pending empirical data"
    )


class CompressionABComparisonResult(BaseModel):
    """A/B comparison between uncompressed routing (Variant A) and compressed routing (Variant B)."""
    model_config = ConfigDict(extra="forbid")

    comparison_id: str = Field(..., description="Comparison execution identifier")
    variant_a_prompt: str = Field(..., description="Uncompressed original prompt (Variant A)")
    variant_b_prompt: str = Field(..., description="Compressed prompt (Variant B)")
    is_compressed: bool = Field(..., description="Whether Variant B was successfully compressed")
    compression_ratio: float = Field(..., ge=0.0, description="Compression ratio of Variant B vs Variant A")
    estimated_token_savings: int = Field(..., description="Estimated token difference (A - B)")
    variant_a_latency_ms: float = Field(default=0.0, ge=0.0, description="Execution latency for Variant A")
    variant_b_latency_ms: float = Field(default=0.0, ge=0.0, description="Execution latency for Variant B")
    latency_delta_ms: float = Field(default=0.0, description="Latency delta (A - B)")
    variant_a_content: str | None = Field(default=None, description="Model completion for Variant A")
    variant_b_content: str | None = Field(default=None, description="Model completion for Variant B")
    lexical_overlap: float = Field(default=0.0, ge=0.0, le=1.0, description="Lexical Jaccard overlap between outputs")
    quality_evaluation_status: QualityEvaluationStatus = Field(
        default=QualityEvaluationStatus.UNVALIDATED,
        description="Quality claim status; unvalidated pending empirical data"
    )


class OptimizationDecisionResponse(BaseModel):
    """Outgoing response contract returned by the backend router to the extension."""
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identifier matching incoming request"
    )
    correlation_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Correlation identifier spanning client and backend"
    )
    decision_type: DecisionType = Field(
        ...,
        description="Primary optimization decision outcome"
    )
    coarse_route: CoarseRoute | None = Field(
        default=None,
        description="Deterministic coarse route decision"
    )
    task_category: TaskCategory | None = Field(
        default=None,
        description="Stable task category associated with the decision"
    )
    complexity_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Indicative complexity score associated with decision"
    )
    complexity_level: ComplexityLevel | None = Field(
        default=None,
        description="Categorical complexity level associated with decision"
    )
    user_override: UserRoutingOverride | None = Field(
        default=None,
        description="User routing preference override associated with decision"
    )
    model_tier: ModelTier | None = Field(
        default=None,
        description="Abstract model capability tier recommended by gateway"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for the decision from 0.0 to 1.0"
    )
    reason_code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Machine-readable decision reason identifier"
    )
    optimization_instructions: OptimizationInstructions | None = Field(
        default=None,
        description="Optional instructions if optimization action is recommended"
    )
    query_optimization: QueryOptimizationComparison | None = Field(
        default=None,
        description="Backend query optimization comparison and measured savings metrics"
    )
    experimental_compression: ExperimentalCompressionResult | None = Field(
        default=None,
        description="Optional experimental stronger compression result when enabled"
    )
    execution_metadata: RouteExecutionMetadata | None = Field(
        default=None,
        description="Execution and performance metadata recorded during gateway routing"
    )
    timestamp: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        description="Epoch timestamp in milliseconds"
    )
    server_version: str = Field(
        default="0.1.0",
        max_length=32,
        description="Router backend version"
    )


class ErrorCategory(str, Enum):
    """Performance evaluation error category."""
    NONE = "NONE"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    CLIENT_ERROR = "CLIENT_ERROR"


class PerformanceTelemetryRecord(BaseModel):
    """Performance evaluation telemetry schema.
    
    GUARANTEE: Strictly omits raw query text and sensitive conversation content.
    """
    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(..., max_length=128)
    client_timestamp: int = Field(..., description="Epoch ms when attempt started")
    backend_timestamp: int | None = Field(default=None, description="Epoch ms when backend processed")
    decision_type: DecisionType
    coarse_route: CoarseRoute | None = None
    task_category: TaskCategory | None = None
    complexity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    complexity_level: ComplexityLevel | None = None
    user_override: UserRoutingOverride | None = None
    model_tier: ModelTier | None = None
    model_route: str | None = Field(default=None, max_length=64)
    model_version: str | None = Field(default=None, max_length=64)
    cache_outcome: CacheOutcome = CacheOutcome.NOT_CHECKED
    latency_ms: float = Field(..., ge=0.0)
    execution_latency_ms: float | None = Field(default=None, ge=0.0)
    error_category: ErrorCategory = ErrorCategory.NONE
    failure_category: str = Field(default="NONE", max_length=64)
    escalation_occurred: bool = Field(default=False, description="Whether escalation occurred")
    escalation_reason: str | None = Field(default=None, max_length=256, description="Reason for escalation")
    semantic_cache_outcome: str | None = Field(default=None, max_length=64)
    semantic_validation_reason: str | None = Field(default=None, max_length=256)
    feature_identifiers: dict[str, Any] = Field(default_factory=dict)
    version_identifiers: dict[str, str] = Field(default_factory=dict)
    debug_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Development-only debug metadata. Never populated in production mode."
    )
