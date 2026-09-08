"""Schemas for ML Router Limited Rollout, Emergency Kill Switch, and Measurable Regression Rollback Triggers.

Defines:
- Rollout configuration (canary percentage, allow/denylists, kill switch).
- Multi-dimensional monitoring models (routes, escalations, errors, latency, cache, quality).
- Measurable regression rollback trigger criteria and check results.
- Request payloads for configuration, kill switch, rollback reset, and batch simulation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class RolloutCohort(str, Enum):
    """Cohort category of an evaluated query."""
    ML = "ML"
    CONTROL_RULE = "CONTROL_RULE"
    FALLBACK_DETERMINISTIC = "FALLBACK_DETERMINISTIC"


class RolloutTriggerState(str, Enum):
    """Lifecycle status of the automated rollback trigger engine."""
    HEALTHY = "HEALTHY"
    TRIGGERED = "TRIGGERED"
    MANUALLY_OVERRIDDEN = "MANUALLY_OVERRIDDEN"


class RolloutConfig(BaseModel):
    """Dynamic rollout settings and kill switch state."""
    model_config = ConfigDict(extra="forbid")

    kill_switch_engaged: bool = Field(
        default=False,
        description="Global emergency kill switch. When true, 100% of queries divert to deterministic router."
    )
    rollout_percentage: float = Field(
        default=10.0,
        ge=0.0,
        le=100.0,
        description="Percentage of traffic allocated to ML router (0.0% to 100.0%)."
    )
    canary_salt: str = Field(
        default="smart_query_router_v1",
        min_length=1,
        max_length=64,
        description="Consistent hashing salt for deterministic cohort partitioning."
    )
    allowlist_user_ids: list[str] = Field(
        default_factory=list,
        description="User IDs explicitly included in ML cohort regardless of percentage."
    )
    denylist_user_ids: list[str] = Field(
        default_factory=list,
        description="User IDs explicitly excluded from ML cohort."
    )
    allowlist_tenant_ids: list[str] = Field(
        default_factory=list,
        description="Tenant IDs explicitly included in ML cohort."
    )
    denylist_tenant_ids: list[str] = Field(
        default_factory=list,
        description="Tenant IDs explicitly excluded from ML cohort."
    )


class RollbackTriggerThresholds(BaseModel):
    """Quantifiable, measurable regression boundaries for automated rollback."""
    model_config = ConfigDict(extra="forbid")

    max_error_rate_pct: float = Field(
        default=2.0,
        ge=0.0,
        le=100.0,
        description="Maximum allowed error rate percentage in ML cohort before tripping rollback."
    )
    max_escalation_rate_pct: float = Field(
        default=15.0,
        ge=0.0,
        le=100.0,
        description="Maximum allowed small-model escalation rate percentage in ML cohort."
    )
    max_p95_latency_ms: float = Field(
        default=2500.0,
        ge=10.0,
        description="Maximum tolerable 95th percentile latency in ms for ML cohort."
    )
    min_quality_completeness: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Minimum acceptable average completeness score from heuristic evaluator."
    )
    max_consecutive_errors: int = Field(
        default=3,
        ge=1,
        description="Consecutive ML routing failures required to trip immediate emergency rollback."
    )
    min_eval_samples: int = Field(
        default=10,
        ge=1,
        description="Minimum queries in sliding ML window required before rate thresholds evaluate."
    )


class LatencySummary(BaseModel):
    """Summary of query execution latency in milliseconds."""
    model_config = ConfigDict(extra="forbid")

    sample_count: int = Field(default=0, ge=0)
    mean_ms: float = Field(default=0.0, ge=0.0)
    p50_ms: float = Field(default=0.0, ge=0.0)
    p90_ms: float = Field(default=0.0, ge=0.0)
    p95_ms: float = Field(default=0.0, ge=0.0)
    p99_ms: float = Field(default=0.0, ge=0.0)


class RouteDistributionMetrics(BaseModel):
    """Route selection counts and percentages for a cohort."""
    model_config = ConfigDict(extra="forbid")

    total_queries: int = Field(default=0, ge=0)
    local_eligible_count: int = Field(default=0, ge=0)
    local_eligible_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    simple_model_count: int = Field(default=0, ge=0)
    simple_model_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    complex_model_count: int = Field(default=0, ge=0)
    complex_model_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    needs_evaluation_count: int = Field(default=0, ge=0)
    needs_evaluation_pct: float = Field(default=0.0, ge=0.0, le=100.0)


class EscalationMetrics(BaseModel):
    """Small-model completion escalation metrics."""
    model_config = ConfigDict(extra="forbid")

    simple_model_attempts: int = Field(default=0, ge=0)
    escalation_count: int = Field(default=0, ge=0)
    escalation_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    escalation_reasons: dict[str, int] = Field(default_factory=dict)


class ErrorMetrics(BaseModel):
    """Gateway, provider, and routing failure metrics."""
    model_config = ConfigDict(extra="forbid")

    total_requests: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    error_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    failure_categories: dict[str, int] = Field(default_factory=dict)


class CacheMetrics(BaseModel):
    """Cache outcome distribution and overall hit rates."""
    model_config = ConfigDict(extra="forbid")

    total_queries: int = Field(default=0, ge=0)
    exact_hit_count: int = Field(default=0, ge=0)
    semantic_hit_count: int = Field(default=0, ge=0)
    miss_count: int = Field(default=0, ge=0)
    bypass_count: int = Field(default=0, ge=0)
    cache_hit_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0)


class QualityMetrics(BaseModel):
    """Heuristic evaluator quality and completeness signals."""
    model_config = ConfigDict(extra="forbid")

    evaluated_queries: int = Field(default=0, ge=0)
    mean_completeness: float = Field(default=1.0, ge=0.0, le=1.0)
    mean_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    detected_issue_counts: dict[str, int] = Field(default_factory=dict)


class CohortMetrics(BaseModel):
    """Multi-dimensional monitoring telemetry aggregated for a specific cohort."""
    model_config = ConfigDict(extra="forbid")

    cohort_name: str = Field(..., description="Cohort name (e.g. ML, CONTROL_RULE)")
    total_queries: int = Field(default=0, ge=0)
    route_distribution: RouteDistributionMetrics = Field(default_factory=RouteDistributionMetrics)
    escalations: EscalationMetrics = Field(default_factory=EscalationMetrics)
    errors: ErrorMetrics = Field(default_factory=ErrorMetrics)
    latency: LatencySummary = Field(default_factory=LatencySummary)
    cache: CacheMetrics = Field(default_factory=CacheMetrics)
    quality: QualityMetrics = Field(default_factory=QualityMetrics)


class RollbackTriggerCheckResult(BaseModel):
    """Status of a single measurable rollback regression check."""
    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(..., description="Quantifiable metric evaluated")
    threshold: Any = Field(..., description="Allowed threshold boundary")
    observed: Any = Field(..., description="Observed value from ML cohort")
    triggered: bool = Field(..., description="Whether this criterion breached safety boundaries")
    details: str = Field(..., description="Contextual diagnostic explanation")


class RollbackStatus(BaseModel):
    """Real-time status of the automated rollback trigger engine."""
    model_config = ConfigDict(extra="forbid")

    state: RolloutTriggerState = Field(default=RolloutTriggerState.HEALTHY)
    is_rollback_active: bool = Field(default=False)
    triggered_at: str | None = Field(default=None)
    trigger_reason: str | None = Field(default=None)
    trigger_metric: str | None = Field(default=None)
    observed_value: float | None = Field(default=None)
    threshold_value: float | None = Field(default=None)
    consecutive_errors: int = Field(default=0, ge=0)
    trigger_checks: list[RollbackTriggerCheckResult] = Field(default_factory=list)


class RolloutStatusReport(BaseModel):
    """Comprehensive monitoring report across all 6 dimensions and rollback status."""
    model_config = ConfigDict(extra="forbid")

    total_queries_observed: int = Field(default=0, ge=0)
    active_routing_mode: str = Field(..., description="Currently active routing strategy")
    config: RolloutConfig = Field(..., description="Active rollout parameters")
    rollback_status: RollbackStatus = Field(..., description="Automated rollback engine state")
    ml_cohort: CohortMetrics = Field(..., description="Telemetry for ML-routed queries")
    control_cohort: CohortMetrics = Field(..., description="Telemetry for deterministic rule queries")
    blended_totals: CohortMetrics = Field(..., description="Overall blended traffic telemetry")


# =========================================================================
# Request Payloads
# =========================================================================

class RolloutConfigureRequest(BaseModel):
    """Payload to update rollout percentage, kill switch, or cohort rules."""
    model_config = ConfigDict(extra="forbid")

    kill_switch_engaged: bool | None = Field(default=None)
    rollout_percentage: float | None = Field(default=None, ge=0.0, le=100.0)
    canary_salt: str | None = Field(default=None, min_length=1, max_length=64)
    allowlist_user_ids: list[str] | None = Field(default=None)
    denylist_user_ids: list[str] | None = Field(default=None)
    allowlist_tenant_ids: list[str] | None = Field(default=None)
    denylist_tenant_ids: list[str] | None = Field(default=None)
    thresholds: RollbackTriggerThresholds | None = Field(default=None)


class KillSwitchRequest(BaseModel):
    """Payload to engage or disengage the global kill switch."""
    model_config = ConfigDict(extra="forbid")

    engage: bool = Field(..., description="True to engage emergency kill switch, False to disengage")
    reason: str = Field(..., min_length=3, max_length=512, description="Audit justification")


class RollbackResetRequest(BaseModel):
    """Payload to reset an automated rollback after operator inspection."""
    model_config = ConfigDict(extra="forbid")

    justification: str = Field(..., min_length=5, max_length=512, description="Operator justification for reset")
    restore_rollout_percentage: float = Field(
        default=5.0,
        ge=0.0,
        le=100.0,
        description="Cautious rollout percentage to restore after reset (defaults to 5.0%)"
    )


class RolloutSimulationRequest(BaseModel):
    """Payload to simulate benchmark traffic across canary cohorts."""
    model_config = ConfigDict(extra="forbid")

    iterations: int = Field(default=1, ge=1, le=20)
    dataset_path: str | None = Field(default=None)
    simulate_ml_error: bool = Field(default=False, description="Simulate sporadic ML failure to test fallback")
