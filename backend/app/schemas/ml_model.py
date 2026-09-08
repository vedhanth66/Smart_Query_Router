"""Schemas for Machine Learning Router Model Training, Evaluation, and Metadata.

ISOLATION & REPRODUCIBILITY GUARANTEES:
1. Strict Offline Isolation:
   These schemas manage training telemetry and evaluation comparisons strictly
   outside the production inference path.
2. Comprehensive Reproducibility:
   Captures framework versions, random seeds, hyperparameters, exact feature names,
   in-sample metrics, cross-validation metrics, baseline comparisons, and cost/quality tradeoffs.
3. Production Safety:
   Explicitly declares `is_production_active=False` and `deployment_status='EXPERIMENTAL_OFFLINE'`.
"""

from __future__ import annotations

import time
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.benchmark import PricingConfig


class PerClassMetric(BaseModel):
    """Detailed evaluation metrics for a single routing class."""
    model_config = ConfigDict(extra="forbid")

    class_name: str = Field(..., description="Target routing class label")
    precision: float = Field(..., ge=0.0, le=1.0, description="Precision score")
    recall: float = Field(..., ge=0.0, le=1.0, description="Recall score")
    f1: float = Field(..., ge=0.0, le=1.0, description="Harmonic mean F1 score")
    support: int = Field(..., ge=0, description="Number of true instances in class")


class EvaluationMetrics(BaseModel):
    """Aggregated classification performance metrics and confusion matrix."""
    model_config = ConfigDict(extra="forbid")

    accuracy: float = Field(..., ge=0.0, le=1.0, description="Overall classification accuracy")
    precision_macro: float = Field(..., ge=0.0, le=1.0, description="Unweighted macro precision")
    recall_macro: float = Field(..., ge=0.0, le=1.0, description="Unweighted macro recall")
    f1_macro: float = Field(..., ge=0.0, le=1.0, description="Unweighted macro F1 score")
    per_class: dict[str, PerClassMetric] = Field(default_factory=dict, description="Per-class breakdown")
    confusion_matrix: list[list[int]] = Field(..., description="2D confusion matrix [true_idx][pred_idx]")
    class_labels: list[str] = Field(..., description="Ordered class labels corresponding to matrix axes")


class CostQualityTradeoff(BaseModel):
    """Financial spend and quality parity tradeoffs relative to baseline router."""
    model_config = ConfigDict(extra="forbid")

    always_strong_cost_usd: float = Field(..., ge=0.0, description="Hypothetical spend if all queries went to frontier model")
    rule_baseline_cost_usd: float = Field(..., ge=0.0, description="Observed spend under deterministic rule router")
    ml_model_cost_usd: float = Field(..., ge=0.0, description="Simulated spend under ML router predictions")
    ml_savings_vs_strong_usd: float = Field(..., description="Dollar savings vs always-strong baseline")
    ml_savings_vs_strong_pct: float = Field(..., description="Percentage savings vs always-strong baseline")
    ml_savings_vs_rule_usd: float = Field(..., description="Dollar delta vs deterministic rule router")
    ml_savings_vs_rule_pct: float = Field(..., description="Percentage delta vs deterministic rule router")
    under_routed_count: int = Field(default=0, ge=0, description="Queries routed to a cheaper tier than optimal (quality risk)")
    over_routed_count: int = Field(default=0, ge=0, description="Queries routed to a more expensive tier than optimal (cost inefficiency)")
    optimal_routed_count: int = Field(default=0, ge=0, description="Queries routed to exact optimal target tier")
    expected_quality_parity_pct: float = Field(..., ge=0.0, le=100.0, description="Expected percentage maintaining quality parity")
    pricing_assumptions: dict[str, Any] = Field(default_factory=dict, description="Pricing parameters used in simulation")


class BaselineComparisonSummary(BaseModel):
    """Head-to-head comparison between Deterministic Rule Baseline and ML Classifier."""
    model_config = ConfigDict(extra="forbid")

    rule_accuracy: float = Field(..., description="Deterministic rule router accuracy")
    ml_accuracy: float = Field(..., description="ML classifier accuracy")
    accuracy_delta: float = Field(..., description="ML accuracy minus rule accuracy")
    rule_macro_f1: float = Field(..., description="Deterministic rule router macro F1")
    ml_macro_f1: float = Field(..., description="ML classifier macro F1")
    macro_f1_delta: float = Field(..., description="ML macro F1 minus rule macro F1")
    rule_confusion_matrix: list[list[int]] = Field(..., description="Rule router confusion matrix")
    ml_confusion_matrix: list[list[int]] = Field(..., description="ML classifier confusion matrix")
    class_labels: list[str] = Field(..., description="Ordered class labels corresponding to matrices")


class ModelTrainingMetadata(BaseModel):
    """Reproducible training run metadata and artifact manifest."""
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(default="router_classifier_v1", description="Model architecture identifier")
    model_type: str = Field(
        default="Multinomial Logistic Regression with Standardized & Encoded Feature Pipeline",
        description="Model algorithm and preprocessing description"
    )
    version: str = Field(default="1.0.0", description="Model version")
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000), description="Training timestamp ms")
    framework_versions: dict[str, str] = Field(..., description="Exact versions of scikit-learn, joblib, numpy, pandas")
    feature_names: dict[str, list[str]] = Field(..., description="Feature columns separated by numeric, bool, and cat")
    target_classes: list[str] = Field(..., description="Supported output class labels")
    hyperparameters: dict[str, Any] = Field(..., description="Exact model hyperparameters and random seeds")
    in_sample_metrics: EvaluationMetrics = Field(..., description="Evaluation metrics on full dataset")
    cross_validation_metrics: EvaluationMetrics = Field(..., description="Evaluation metrics via Leave-One-Out CV")
    rule_baseline_metrics: EvaluationMetrics = Field(..., description="Performance of existing deterministic rule router")
    baseline_comparison: BaselineComparisonSummary = Field(..., description="Head-to-head comparison summary")
    cost_quality_tradeoffs: CostQualityTradeoff = Field(..., description="Simulated cost and quality tradeoff analysis")
    is_production_active: bool = Field(default=False, description="Whether this model is active in production inference")
    deployment_status: str = Field(default="EXPERIMENTAL_OFFLINE", description="Current deployment governance status")
    recommendation: str = Field(..., description="Engineering recommendation regarding production deployment readiness")


class MLPredictionRequest(BaseModel):
    """Payload for evaluating an offline prediction with the trained ML router."""
    model_config = ConfigDict(extra="forbid")

    task_type: str = Field(..., description="Task category name")
    complexity_label: str | None = Field(default=None, description="Optional complexity level")
    local_signals: dict[str, Any] = Field(..., description="Local feature signals dictionary")
    context_signals: dict[str, Any] = Field(default_factory=dict, description="Context dependency indicators dictionary")


class MLPredictionResponse(BaseModel):
    """Output prediction and confidence scores from the ML router classifier."""
    model_config = ConfigDict(extra="forbid")

    predicted_route: str = Field(..., description="Predicted optimal route (local-eligible, simple, complex)")
    recommended_tier: str = Field(..., description="Model tier (local, fast_cheap, strong)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Predicted class probability score")
    class_probabilities: dict[str, float] = Field(..., description="Full probability distribution over classes")
    model_id: str = Field(default="router_classifier_v1", description="Model identifier")
    model_version: str = Field(default="1.0.0", description="Model version")


class EmbeddingTelemetry(BaseModel):
    """Timing and operational telemetry for embedding feature generation."""
    model_config = ConfigDict(extra="forbid")

    extraction_latency_ms: float = Field(..., ge=0.0, description="Time in milliseconds to generate embedding")
    dimension: int = Field(..., gt=0, description="Embedding vector dimension")
    model_name: str = Field(..., description="Identifier of embedding generator")
    within_budget: bool = Field(..., description="Whether generation completed within the latency budget")
    circuit_breaker_triggered: bool = Field(default=False, description="Whether fallback zero-vector was used due to timeout/error")


class ModelPerformanceComparison(BaseModel):
    """Comparative metric entry between with-embedding and without-embedding models."""
    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(..., description="Name of performance metric")
    without_embeddings: float = Field(..., description="Score without embedding features")
    with_embeddings: float = Field(..., description="Score with embedding features")
    delta: float = Field(..., description="Difference: with_embeddings - without_embeddings")
    pct_change: float | None = Field(default=None, description="Percentage change relative to baseline")


class LatencyResourceCostReport(BaseModel):
    """Evaluation of added latency and compute resource costs vs routing savings."""
    model_config = ConfigDict(extra="forbid")

    embedding_latency_ms: float = Field(..., ge=0.0, description="Average time in ms to generate embeddings")
    inference_latency_without_ms: float = Field(..., ge=0.0, description="Inference latency without embeddings in ms")
    inference_latency_with_ms: float = Field(..., ge=0.0, description="Inference latency with embeddings in ms")
    total_overhead_ms: float = Field(..., ge=0.0, description="Total router overhead: embedding + inference with ms")
    latency_budget_ms: float = Field(default=5.0, ge=0.0, description="Maximum allowed latency budget in ms")
    latency_budget_passed: bool = Field(..., description="True if total overhead is within budget")
    compute_cost_per_query_usd: float = Field(..., ge=0.0, description="Estimated compute resource cost per query in USD")
    token_savings_per_query_usd: float = Field(..., ge=0.0, description="Estimated token cost saved per query in USD")
    net_benefit_per_query_usd: float = Field(..., description="Net dollar benefit: savings minus compute cost")
    roi_multiplier: float = Field(..., ge=0.0, description="ROI ratio: savings / compute cost")
    cost_justification_verdict: str = Field(..., description="Analytical verdict on cost justification")


class ComparativeEmbeddingTrainingReport(BaseModel):
    """Complete comparative evaluation report for router classifier with vs without embeddings."""
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(..., description="Benchmark/training dataset ID")
    total_samples: int = Field(..., ge=1, description="Number of samples evaluated")
    embedding_dimension: int = Field(..., gt=0, description="Embedding vector dimensionality")
    embedding_model: str = Field(..., description="Embedding model identifier")
    without_embeddings_in_sample: EvaluationMetrics = Field(..., description="In-sample metrics without embeddings")
    without_embeddings_cv: EvaluationMetrics = Field(..., description="LOOCV metrics without embeddings")
    with_embeddings_in_sample: EvaluationMetrics = Field(..., description="In-sample metrics with embeddings")
    with_embeddings_cv: EvaluationMetrics = Field(..., description="LOOCV metrics with embeddings")
    metric_comparisons: list[ModelPerformanceComparison] = Field(..., description="Side-by-side metric comparison table")
    latency_resource_cost: LatencyResourceCostReport = Field(..., description="Latency and compute cost accounting")
    summary_verdict: str = Field(..., description="Overall executive summary and recommendation")


class GuardedRoutingDecision(BaseModel):
    """Guarded routing decision enforcing deterministic safety and context primacy."""
    model_config = ConfigDict(extra="forbid")

    final_route: str = Field(..., description="Final selected route")
    recommended_tier: str = Field(..., description="Final recommended model tier (local, fast_cheap, strong)")
    decision_gate: str = Field(..., description="Gate that decided the route: DETERMINISTIC_SAFETY, EXPLICIT_CONTEXT_GUARD, ML_CLASSIFIER_AUXILIARY")
    safety_override_applied: bool = Field(default=False, description="True if greeting/arithmetic/PII safety check overrode ML")
    context_guard_applied: bool = Field(default=False, description="True if explicit context dependency overrode ML")
    ml_predicted_route: str | None = Field(default=None, description="Raw prediction from ML model if evaluated")
    ml_confidence: float | None = Field(default=None, description="ML confidence score if evaluated")
    embedding_telemetry: EmbeddingTelemetry | None = Field(default=None, description="Embedding telemetry if extracted")
    reason: str = Field(..., description="Explanation of routing decision and guardrail enforcement")


class GuardedPredictionRequest(BaseModel):
    """Request payload for guarded query routing inference."""
    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(..., description="Raw query string")
    task_type: str | None = Field(default=None, description="Optional task category")
    complexity_label: str | None = Field(default=None, description="Optional complexity label")
    has_context_dependency: bool = Field(default=False, description="Explicit conversational context dependency flag")
    prior_conversation_turns: list[dict[str, Any]] = Field(default_factory=list, description="Prior conversation history turns")
    local_signals: dict[str, Any] | None = Field(default=None, description="Pre-computed local signals if available")
    context_signals: dict[str, Any] | None = Field(default=None, description="Pre-computed context signals if available")


class MLCompareEmbeddingsRequest(BaseModel):
    """Payload for triggering comparative training with and without embeddings."""
    model_config = ConfigDict(extra="forbid")

    dataset_path: str | None = Field(default=None, description="Optional path to custom ML feature dataset JSON")
    embedding_dimension: int = Field(default=16, ge=4, le=512, description="Dimension of generated embedding vectors")
    latency_budget_ms: float = Field(default=5.0, ge=0.5, le=100.0, description="Max acceptable latency budget in ms")
    benchmark_runs: int = Field(default=50, ge=10, le=1000, description="Inference benchmark iterations for latency measurement")
    hyperparameters: dict[str, Any] | None = Field(default=None, description="Model hyperparameters")
    pricing_config: PricingConfig | None = Field(default=None, description="Custom pricing parameters")
    save_to_disk: bool = Field(default=True, description="Whether to serialize model and metadata to disk")
    output_dir: str | None = Field(default=None, description="Output directory for serialized artifacts")


