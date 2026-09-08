"""Schemas for ML Router Shadow Mode and Evidence-Gated Activation.

GOVERNANCE & SAFETY GUARANTEES:
1. Zero Production Interference:
   Shadow mode evaluates ML recommendations asynchronously or passively alongside
   the production rule router without altering the user-visible route or executed content.
2. Comprehensive Disagreement & Quality Tracking:
   Explicitly tracks agreement status, disagreement classification, expected dollar savings,
   and projected quality impacts (e.g. degradation risk vs quality preservation).
3. Evidence-Gated Activation Criteria:
   ML routing cannot be activated until statistical, agreement, quality risk, financial,
   and latency thresholds are satisfied.
"""

from __future__ import annotations

from enum import Enum
import time
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.benchmark import PricingConfig
from app.schemas.ml_model import EmbeddingTelemetry


class ShadowDisagreementType(str, Enum):
    """Classification of route divergence between production rule router and shadow ML router."""
    AGREEMENT = "AGREEMENT"
    ML_CHEAPER = "ML_CHEAPER"
    ML_MORE_EXPENSIVE = "ML_MORE_EXPENSIVE"
    ML_LOCAL_INSTEAD_OF_MODEL = "ML_LOCAL_INSTEAD_OF_MODEL"


class LikelyQualityImpact(str, Enum):
    """Assessed quality risk or benefit of the ML recommendation relative to the production rule."""
    NEUTRAL = "NEUTRAL"
    QUALITY_PRESERVED_COST_OPTIMIZED = "QUALITY_PRESERVED_COST_OPTIMIZED"
    POTENTIAL_DEGRADATION_RISK = "POTENTIAL_DEGRADATION_RISK"
    QUALITY_ENHANCEMENT = "QUALITY_ENHANCEMENT"


class ShadowRoutingEvent(BaseModel):
    """Single observed shadow routing decision and comparative telemetry."""
    model_config = ConfigDict(extra="forbid")

    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000), description="Event timestamp in ms")
    request_id: str = Field(..., description="Unique request identifier")
    correlation_id: str = Field(..., description="Correlation ID tracing the request")
    query_text_snippet: str | None = Field(default=None, description="Sanitized/bounded query snippet")
    task_category: str | None = Field(default=None, description="Task category of the query")
    production_route: str = Field(..., description="Route selected by authoritative production rule router")
    production_tier: str = Field(..., description="Model tier selected by production rule router")
    ml_route: str = Field(..., description="Route recommended by shadow ML router")
    ml_tier: str = Field(..., description="Model tier recommended by shadow ML router")
    ml_confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score from shadow ML router")
    ml_decision_gate: str = Field(..., description="Gate that generated recommendation (DETERMINISTIC_SAFETY, EXPLICIT_CONTEXT_GUARD, ML_CLASSIFIER_AUXILIARY)")
    is_agreement: bool = Field(..., description="Whether production route and ML recommendation agree")
    disagreement_type: ShadowDisagreementType = Field(..., description="Classification of disagreement")
    expected_savings_usd: float = Field(..., description="Expected dollar delta if ML route were executed instead of rule")
    likely_quality_impact: LikelyQualityImpact = Field(..., description="Projected quality impact of ML recommendation")
    latency_overhead_ms: float = Field(..., ge=0.0, description="Execution latency of the shadow ML evaluation in ms")
    embedding_telemetry: EmbeddingTelemetry | None = Field(default=None, description="Embedding generation telemetry if applicable")


class ShadowActivationThresholds(BaseModel):
    """Configurable project criteria required to authorize activating ML routing."""
    model_config = ConfigDict(extra="forbid")

    min_sample_size: int = Field(default=50, ge=5, description="Minimum shadow evaluation queries required for statistical confidence")
    min_agreement_rate_pct: float = Field(default=70.0, ge=0.0, le=100.0, description="Minimum agreement percentage with production baseline")
    max_quality_risk_pct: float = Field(default=5.0, ge=0.0, le=100.0, description="Maximum allowed percentage of queries with degradation risk")
    min_net_savings_usd: float = Field(default=0.0, description="Minimum net dollar savings required (must not lose money)")
    max_p95_latency_ms: float = Field(default=5.0, ge=0.5, description="Maximum allowed 95th percentile latency overhead in ms")
    min_disagreement_confidence: float = Field(default=0.70, ge=0.0, le=1.0, description="Minimum average ML confidence on diverging recommendations")


class ThresholdCheckResult(BaseModel):
    """Status of a single activation threshold criterion."""
    model_config = ConfigDict(extra="forbid")

    criterion_name: str = Field(..., description="Descriptive name of the gating requirement")
    threshold_value: Any = Field(..., description="Required threshold value")
    actual_value: Any = Field(..., description="Observed value from shadow evaluation")
    passed: bool = Field(..., description="Whether the actual value satisfies the threshold")
    details: str = Field(..., description="Explanatory context or failure justification")


class ShadowEvaluationStatusReport(BaseModel):
    """Aggregated shadow mode analysis, disagreement breakdown, and activation readiness."""
    model_config = ConfigDict(extra="forbid")

    total_shadow_queries: int = Field(..., ge=0, description="Total queries observed in shadow mode")
    is_ml_routing_active: bool = Field(..., description="Whether ML routing is currently active in production")
    agreement_count: int = Field(..., ge=0, description="Queries where production and ML agreed")
    agreement_rate_pct: float = Field(..., ge=0.0, le=100.0, description="Percentage of agreement")
    disagreement_count: int = Field(..., ge=0, description="Queries where production and ML differed")
    disagreement_rate_pct: float = Field(..., ge=0.0, le=100.0, description="Percentage of disagreement")
    disagreement_breakdown: dict[str, int] = Field(default_factory=dict, description="Count by ShadowDisagreementType")
    quality_impact_breakdown: dict[str, int] = Field(default_factory=dict, description="Count by LikelyQualityImpact")
    quality_risk_count: int = Field(default=0, ge=0, description="Queries flagged with POTENTIAL_DEGRADATION_RISK")
    quality_risk_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of queries with quality risk")
    net_expected_savings_usd: float = Field(..., description="Total projected dollar savings across shadow queries")
    avg_expected_savings_per_query_usd: float = Field(..., description="Average dollar savings per shadow query")
    avg_ml_confidence: float = Field(..., ge=0.0, le=1.0, description="Average confidence score of ML recommendations")
    avg_disagreement_confidence: float = Field(..., ge=0.0, le=1.0, description="Average confidence score on diverging queries")
    p95_latency_ms: float = Field(..., ge=0.0, description="95th percentile shadow evaluation latency in ms")
    threshold_criteria: list[ThresholdCheckResult] = Field(..., description="Detailed verification checklist for all gating criteria")
    all_thresholds_satisfied: bool = Field(..., description="True if every threshold criterion is satisfied")
    activation_status: str = Field(..., description="Status: 'BLOCKED_BY_THRESHOLDS', 'ELIGIBLE_FOR_ACTIVATION', or 'ACTIVE'")
    gating_rationale: str = Field(..., description="Governance justification for current activation status")
    recent_events: list[ShadowRoutingEvent] = Field(default_factory=list, description="Most recent shadow events (up to 20)")


class ShadowActivationRequest(BaseModel):
    """Administrative request to activate or reconfigure ML routing."""
    model_config = ConfigDict(extra="forbid")

    force_activation: bool = Field(default=False, description="Whether to bypass threshold checks (requires explicit justification)")
    bypass_justification: str | None = Field(default=None, description="Audit justification if force_activation is requested")
    custom_thresholds: ShadowActivationThresholds | None = Field(default=None, description="Optional custom activation thresholds")


class ShadowBatchSimulateRequest(BaseModel):
    """Request payload to simulate a batch of queries through shadow evaluation."""
    model_config = ConfigDict(extra="forbid")

    dataset_path: str | None = Field(default=None, description="Optional path to custom benchmark or ML dataset JSON")
    iterations: int = Field(default=1, ge=1, le=10, description="Number of times to run the dataset through shadow mode")
    pricing_config: PricingConfig | None = Field(default=None, description="Optional pricing parameters")
