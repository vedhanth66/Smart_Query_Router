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
    timestamp: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        description="Epoch timestamp in milliseconds"
    )
    server_version: str = Field(
        default="0.1.0",
        max_length=32,
        description="Router backend version"
    )


class CacheOutcome(str, Enum):
    """Performance evaluation cache outcome."""
    HIT = "HIT"
    MISS = "MISS"
    BYPASS = "BYPASS"
    NOT_CHECKED = "NOT_CHECKED"


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
    model_route: str | None = Field(default=None, max_length=64)
    cache_outcome: CacheOutcome = CacheOutcome.NOT_CHECKED
    latency_ms: float = Field(..., ge=0.0)
    error_category: ErrorCategory = ErrorCategory.NONE
    feature_identifiers: dict[str, Any] = Field(default_factory=dict)
    version_identifiers: dict[str, str] = Field(default_factory=dict)
    debug_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Development-only debug metadata. Never populated in production mode."
    )
