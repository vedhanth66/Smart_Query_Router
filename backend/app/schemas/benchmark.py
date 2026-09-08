"""Schemas for the standardized Benchmark Dataset Format.

Guarantees:
1. Comprehensive 13-category task representation:
   greetings, arithmetic, factual questions, summarization, rewriting,
   translation, creative writing, coding, debugging, comparison, analysis,
   reasoning, and context-dependent follow-ups.
2. Explicit routing & model targets:
   coarse route ('local-eligible', 'simple-model candidate', 'complex-model candidate', 'needs-evaluation')
   and model tier ('fast_cheap', 'strong', 'local').
3. Structured quality criteria:
   correctness, completeness, conciseness, context adherence, format compliance,
   and hallucination tolerance.
4. Granular sensitivity flags:
   PII risk levels, credentials presence, proprietary code, safety sensitivity,
   and redaction mandates.
5. Strict Label Provenance Guardrail:
   Prevents hard-coding labels from intuition alone by requiring explicit
   verification method attribution ('DETERMINISTIC_VERIFIED', 'REFERENCE_MODEL_EVALUATED',
   'HUMAN_REVIEW_REQUIRED', 'PENDING_VERIFICATION'), confidence scores,
   evaluator IDs, and review rationale notes.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contract import (
    CoarseRoute,
    ComplexityLevel,
)


class BenchmarkModelTier(str, Enum):
    """Recommended model capability tier for benchmark evaluation."""
    FAST_CHEAP = "fast_cheap"
    STRONG = "strong"
    LOCAL = "local"



class BenchmarkTaskType(str, Enum):
    """Stable task categories recognized across the router benchmark suite."""
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
    CONTEXT_DEPENDENT = "context-dependent follow-up"


class LabelVerificationMethod(str, Enum):
    """Provenance and verification method for ground truth and routing labels.
    
    CRITICAL GUARDRAIL:
    Labels must NEVER be assigned from raw human intuition without marking provenance.
    - DETERMINISTIC_VERIFIED: Rule-based ground truth (e.g. arithmetic, simple greetings).
    - REFERENCE_MODEL_EVALUATED: Evaluated against reference strong model judge consensus.
    - HUMAN_REVIEW_REQUIRED: Nuanced, subjective, or ambiguous; requires human annotator consensus.
    - PENDING_VERIFICATION: Provisional/staged candidate awaiting review.
    """
    DETERMINISTIC_VERIFIED = "DETERMINISTIC_VERIFIED"
    REFERENCE_MODEL_EVALUATED = "REFERENCE_MODEL_EVALUATED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"


class CorrectnessRequirement(str, Enum):
    """Correctness expectation for evaluating query outputs."""
    EXACT_MATCH = "EXACT_MATCH"
    FUNCTIONAL_EQUIVALENCE = "FUNCTIONAL_EQUIVALENCE"
    SEMANTIC_TRUTH = "SEMANTIC_TRUTH"
    SUBJECTIVE_QUALITY = "SUBJECTIVE_QUALITY"
    NONE = "NONE"


class HallucinationTolerance(str, Enum):
    """Tolerance level for unsupported statements or hallucinations."""
    ZERO_TOLERANCE = "ZERO_TOLERANCE"
    LOW_TOLERANCE = "LOW_TOLERANCE"
    STANDARD = "STANDARD"
    CREATIVE_FREEDOM = "CREATIVE_FREEDOM"


class PiiRiskLevel(str, Enum):
    """Potential presence of Personally Identifiable Information."""
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class LabelProvenance(BaseModel):
    """Explicit attribution and verification metadata for labels."""
    model_config = ConfigDict(extra="forbid")

    method: LabelVerificationMethod = Field(
        ...,
        description="Method by which this label was verified or assigned"
    )
    evaluator_id: str | None = Field(
        default=None,
        max_length=128,
        description="Identifier of the rule engine, reference judge model, or human reviewer"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score in the label assignment (0.0 - 1.0)"
    )
    verified_at: int | None = Field(
        default=None,
        description="Timestamp in milliseconds when verification occurred"
    )
    review_notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Mandatory or recommended justification explaining why this method was chosen"
    )


class QualityCriteria(BaseModel):
    """Structured evaluation rubrics and quality criteria for grading responses."""
    model_config = ConfigDict(extra="forbid")

    correctness: CorrectnessRequirement = Field(
        default=CorrectnessRequirement.SEMANTIC_TRUTH,
        description="Standard of correctness expected for this task"
    )
    completeness: str = Field(
        default="FULL_COVERAGE",
        max_length=64,
        description="Coverage expectation (e.g. 'FULL_COVERAGE', 'KEY_POINTS_ONLY', 'MINIMAL_ANSWER')"
    )
    conciseness: str = Field(
        default="BALANCED",
        max_length=64,
        description="Brevity expectation (e.g. 'STRICT_BREVITY', 'BALANCED', 'DETAILED_EXPLANATION')"
    )
    context_adherence: bool = Field(
        default=False,
        description="Whether the response must strictly respect conversational context and constraints"
    )
    format_compliance: str | None = Field(
        default=None,
        max_length=64,
        description="Required formatting structure if any (e.g. 'JSON', 'MARKDOWN_TABLE', 'CODE_SNIPPET')"
    )
    hallucination_tolerance: HallucinationTolerance = Field(
        default=HallucinationTolerance.STANDARD,
        description="Tolerance for ungrounded claims or extraneous fabrications"
    )
    rubric_description: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Clear, actionable grading rubric instructions for evaluators"
    )


class SensitivityFlags(BaseModel):
    """Privacy, security, and content safety risk classifications."""
    model_config = ConfigDict(extra="forbid")

    pii_level: PiiRiskLevel = Field(
        default=PiiRiskLevel.NONE,
        description="Assessed risk level of Personally Identifiable Information"
    )
    contains_credentials: bool = Field(
        default=False,
        description="Whether query contains secrets, API keys, or credentials"
    )
    contains_proprietary_code: bool = Field(
        default=False,
        description="Whether query contains confidential source code"
    )
    safety_sensitive: bool = Field(
        default=False,
        description="Whether query involves high-stakes domains (medical advice, legal counsel, financial decisions)"
    )
    requires_redaction: bool = Field(
        default=False,
        description="Whether the item must be sanitized or redacted prior to public benchmark export"
    )
    authorized_for_benchmark: bool = Field(
        default=True,
        description="Whether this query has been authorized for offline benchmark evaluation"
    )


class ConversationTurn(BaseModel):
    """Preceding conversational turn for context-dependent multi-turn evaluations."""
    model_config = ConfigDict(extra="forbid")

    role: str = Field(
        ...,
        pattern=r"^(user|assistant|system)$",
        description="Speaker role in the conversational exchange"
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Turn utterance or message body"
    )


class BenchmarkItem(BaseModel):
    """Individual benchmark test case covering input, routing targets, and quality rubrics."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        description="Unique identifier for the benchmark instance (e.g. 'bench_arith_001')"
    )
    query: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="The primary user prompt or query to be routed and evaluated"
    )
    context_turns: list[ConversationTurn] = Field(
        default_factory=list,
        description="Preceding turns for multi-turn evaluations; empty for standalone queries"
    )
    has_context_dependency: bool = Field(
        default=False,
        description="Explicit flag indicating whether interpreting the query requires context_turns"
    )
    task_type: BenchmarkTaskType = Field(
        ...,
        description="One of the 13 stable benchmark task types"
    )
    expected_route: CoarseRoute = Field(
        ...,
        description="Target coarse routing decision ('local-eligible', 'simple-model candidate', 'complex-model candidate', 'needs-evaluation')"
    )
    complexity_label: ComplexityLevel = Field(
        ...,
        description="Expected categorical complexity tier ('VERY_LOW', 'LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH')"
    )
    target_model_tier: BenchmarkModelTier = Field(
        ...,
        description="Recommended model capability tier ('fast_cheap', 'strong', 'local')"
    )
    quality_criteria: QualityCriteria = Field(
        ...,
        description="Concrete rubrics for evaluating output completeness and correctness"
    )
    sensitivity_flags: SensitivityFlags = Field(
        ...,
        description="Privacy and safety classification flags"
    )
    route_provenance: LabelProvenance = Field(
        ...,
        description="Provenance and verification metadata for the expected_route label"
    )
    complexity_provenance: LabelProvenance = Field(
        ...,
        description="Provenance and verification metadata for the complexity_label"
    )
    task_provenance: LabelProvenance = Field(
        ...,
        description="Provenance and verification metadata for the task_type label"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Optional descriptive tags for filtering and grouping"
    )
    created_at: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        description="Creation timestamp in milliseconds"
    )


class BenchmarkDataset(BaseModel):
    """Collection of benchmark items with dataset-level metadata and versioning."""
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        description="Identifier for the dataset collection (e.g. 'sqr_canonical_benchmark_v1')"
    )
    version: str = Field(
        default="1.0.0",
        min_length=1,
        max_length=32,
        description="Semver version string of the benchmark format and items"
    )
    description: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Overview of the dataset contents, target distribution, and curation guidelines"
    )
    created_at: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        description="Dataset release timestamp in milliseconds"
    )
    items: list[BenchmarkItem] = Field(
        default_factory=list,
        description="Curated list of benchmark evaluation items"
    )


class TokenUsageEstimate(BaseModel):
    """Estimated and measured token usage metrics for a query execution."""
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated or measured input prompt tokens"
    )
    output_tokens: int = Field(
        default=0,
        ge=0,
        description="Estimated or measured output completion tokens"
    )
    total_tokens: int = Field(
        default=0,
        ge=0,
        description="Total tokens consumed (input + output)"
    )


class AnswerQualityReference(BaseModel):
    """Reference answer generated by the stronger model for benchmarking quality."""
    model_config = ConfigDict(extra="forbid")

    content: str = Field(
        ...,
        description="Reference response text generated by the stronger model"
    )
    finish_reason: str = Field(
        default="stop",
        description="Completion finish reason ('stop', 'length', etc.)"
    )
    model_id: str = Field(
        ...,
        description="Concrete model identifier (e.g. 'gpt-4o-2024-08-06')"
    )
    model_version: str | None = Field(
        default=None,
        description="Model version tag if available"
    )
    provider_name: str = Field(
        ...,
        description="Provider name of the stronger model adapter"
    )
    content_length_chars: int = Field(
        default=0,
        ge=0,
        description="Character count of generated answer"
    )
    meets_format_compliance: bool | None = Field(
        default=None,
        description="Whether the response satisfies any explicit formatting requirements"
    )
    quality_notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Notes on answer quality, rubric evaluation, or detected nuances"
    )


class BaselineItemResult(BaseModel):
    """Evaluation metrics recorded for a single benchmark query against the stronger model."""
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(
        ...,
        description="Benchmark item unique identifier"
    )
    task_type: BenchmarkTaskType = Field(
        ...,
        description="Task category of the benchmark query"
    )
    expected_route: CoarseRoute = Field(
        ...,
        description="Benchmark item target route"
    )
    complexity_label: ComplexityLevel = Field(
        ...,
        description="Benchmark item target complexity"
    )
    target_model_tier: BenchmarkModelTier = Field(
        ...,
        description="Benchmark item target model tier"
    )
    latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Measured execution latency in milliseconds"
    )
    token_usage: TokenUsageEstimate = Field(
        ...,
        description="Measured/estimated token usage"
    )
    answer_quality: AnswerQualityReference = Field(
        ...,
        description="Reference response from the stronger model"
    )
    status: str = Field(
        default="SUCCESS",
        description="Execution status ('SUCCESS', 'ERROR', 'TIMEOUT')"
    )
    error_message: str | None = Field(
        default=None,
        description="Error details if execution failed"
    )
    timestamp: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        description="Timestamp in milliseconds when evaluation occurred"
    )


class StrongModelBaselineReport(BaseModel):
    """Aggregated benchmark baseline report establishing reference performance on the stronger model."""
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(
        ...,
        description="Unique run identifier"
    )
    dataset_id: str = Field(
        ...,
        description="Dataset identifier that was evaluated"
    )
    dataset_version: str = Field(
        default="1.0.0",
        description="Dataset version evaluated"
    )
    model_id: str = Field(
        ...,
        description="Stronger model identifier evaluated"
    )
    model_version: str | None = Field(
        default=None,
        description="Stronger model version tag"
    )
    total_queries: int = Field(
        ...,
        ge=0,
        description="Total queries in the benchmark run"
    )
    successful_queries: int = Field(
        ...,
        ge=0,
        description="Queries successfully evaluated"
    )
    failed_queries: int = Field(
        default=0,
        ge=0,
        description="Queries that encountered errors"
    )
    total_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Total latency summed across queries"
    )
    mean_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Mean latency per query in milliseconds"
    )
    p50_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Median (50th percentile) latency in milliseconds"
    )
    p95_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="95th percentile latency in milliseconds"
    )
    total_input_tokens: int = Field(
        ...,
        ge=0,
        description="Total input tokens across all queries"
    )
    total_output_tokens: int = Field(
        ...,
        ge=0,
        description="Total output tokens across all queries"
    )
    total_tokens: int = Field(
        ...,
        ge=0,
        description="Total tokens consumed across all queries"
    )
    mean_tokens_per_query: float = Field(
        ...,
        ge=0.0,
        description="Average total tokens per query"
    )
    by_task_type: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-task-type breakdown (count, mean latency, total tokens)"
    )
    items: list[BaselineItemResult] = Field(
        default_factory=list,
        description="Individual evaluation records for each query"
    )
    created_at: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        description="Timestamp when report was generated"
    )


class SmartRoutingQualityMetrics(BaseModel):
    """Quality metrics observed during smart routing evaluation."""
    model_config = ConfigDict(extra="forbid")

    format_compliance_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Percentage of applicable responses that adhered to required format"
    )
    non_empty_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Percentage of responses that produced non-empty output"
    )
    mean_completeness_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Average completeness score from candidate evaluations where applicable"
    )
    mean_lexical_overlap: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Average lexical Jaccard overlap compared against strong-model reference answers"
    )


class SmartRoutingItemResult(BaseModel):
    """Execution outcome for a single benchmark query routed through the smart-routing pipeline."""
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(..., description="Benchmark item identifier")
    task_type: BenchmarkTaskType = Field(..., description="Benchmark task category")
    expected_route: CoarseRoute = Field(..., description="Expected target coarse route")
    actual_route: str = Field(..., description="Route selected by the optimizer pipeline")
    decision_type: str = Field(..., description="Primary optimization decision outcome")
    reason_code: str = Field(..., description="Reason code justifying the routing decision")
    is_local_handled: bool = Field(default=False, description="Whether query was handled locally without cloud model execution")
    is_cache_hit: bool = Field(default=False, description="Whether query was resolved via exact or semantic cache")
    is_small_model: bool = Field(default=False, description="Whether query was executed on the fast/cheap model")
    is_strong_model: bool = Field(default=False, description="Whether query was executed on the stronger model")
    is_escalated: bool = Field(default=False, description="Whether small-model response was escalated to strong model")
    escalation_reason: str | None = Field(default=None, description="Reason for escalation if escalated")
    executed_model_id: str | None = Field(default=None, description="Executed model ID if a model was called")
    executed_model_version: str | None = Field(default=None, description="Executed model version tag")
    latency_ms: float = Field(..., ge=0.0, description="Total pipeline latency in milliseconds")
    token_usage: TokenUsageEstimate = Field(..., description="Measured or estimated token usage")
    content: str | None = Field(default=None, description="Output response content")
    format_compliant: bool | None = Field(default=None, description="Whether response met format requirements")
    completeness_score: float | None = Field(default=None, description="Completeness score if evaluated")
    lexical_overlap: float | None = Field(default=None, description="Lexical similarity to strong baseline answer")
    has_context_dependency: bool = Field(default=False, description="Whether query relies on conversational history")
    query_text: str | None = Field(default=None, description="Raw query prompt text")
    status: str = Field(default="SUCCESS", description="Execution status ('SUCCESS', 'ERROR')")
    error_message: str | None = Field(default=None, description="Error message if execution failed")
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))


class SmartRoutingEvaluationReport(BaseModel):
    """Comprehensive evaluation report for benchmark run through the full smart-routing pipeline."""
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="Unique run identifier")
    dataset_id: str = Field(..., description="Dataset identifier evaluated")
    dataset_version: str = Field(default="1.0.0", description="Dataset version evaluated")
    total_queries: int = Field(..., ge=0, description="Total queries in the benchmark run")
    successful_queries: int = Field(..., ge=0, description="Queries successfully executed")
    failed_queries: int = Field(default=0, ge=0, description="Queries resulting in error")
    local_handled_count: int = Field(default=0, ge=0, description="Count of queries handled locally")
    local_handled_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of queries handled locally")
    cache_hit_count: int = Field(default=0, ge=0, description="Count of queries resolved via cache")
    cache_hit_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of queries resolved via cache")
    small_model_count: int = Field(default=0, ge=0, description="Count of queries executed on small model")
    small_model_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of queries executed on small model")
    strong_model_count: int = Field(default=0, ge=0, description="Count of queries executed on strong model")
    strong_model_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of queries executed on strong model")
    escalation_count: int = Field(default=0, ge=0, description="Count of queries escalated from small to strong")
    escalation_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of queries escalated")
    error_count: int = Field(default=0, ge=0, description="Count of queries that encountered errors")
    error_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Error rate percentage")
    total_input_tokens: int = Field(..., ge=0, description="Total input tokens across all queries")
    total_output_tokens: int = Field(..., ge=0, description="Total output tokens across all queries")
    total_tokens: int = Field(..., ge=0, description="Total tokens consumed across all queries")
    mean_tokens_per_query: float = Field(..., ge=0.0, description="Average tokens consumed per query")
    total_latency_ms: float = Field(..., ge=0.0, description="Total latency summed across queries")
    mean_latency_ms: float = Field(..., ge=0.0, description="Mean latency per query in milliseconds")
    p50_latency_ms: float = Field(..., ge=0.0, description="Median latency in milliseconds")
    p95_latency_ms: float = Field(..., ge=0.0, description="95th percentile latency in milliseconds")
    quality_metrics: SmartRoutingQualityMetrics = Field(..., description="Aggregated quality metrics")
    by_task_type: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Per-task-category breakdown")
    items: list[SmartRoutingItemResult] = Field(default_factory=list, description="Individual query execution results")
    comparative_analysis: ComparativeEvaluationReport | None = Field(
        default=None,
        description="Comparative evaluation relative to strong baseline if evaluated"
    )
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))


# -----------------------------------------------------------------------------
# Disaggregated Comparative Benchmark Evaluation Schemas
# -----------------------------------------------------------------------------

class MetricWithDenominator(BaseModel):
    """Standardized metric representation guaranteeing an explicit denominator, formula, and interpretation."""
    model_config = ConfigDict(extra="forbid")

    numerator: float = Field(
        ...,
        description="Calculated absolute measure or difference (e.g. baseline - routing for savings; routing - baseline for quality)"
    )
    denominator: float = Field(
        ...,
        description="Baseline reference value acting as denominator"
    )
    relative_change_pct: float | None = Field(
        default=None,
        description="Percentage change calculated as (numerator / denominator) * 100. None if denominator is 0."
    )
    unit: str = Field(
        default="tokens",
        description="Measurement unit (e.g. 'tokens', 'USD', 'ms', 'queries', 'ratio')"
    )
    formula: str = Field(
        ...,
        description="Explicit mathematical formula defining the metric calculation"
    )
    interpretation: str = Field(
        ...,
        description="Human-readable statement clarifying directionality (e.g. positive = savings/reduction)"
    )


class PricingConfig(BaseModel):
    """Explicit and configurable pricing assumptions for cost evaluation."""
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(
        default="USD",
        description="Currency identifier"
    )
    strong_input_price_per_million: float = Field(
        default=2.50,
        ge=0.0,
        description="Strong model input price in USD per 1M tokens (e.g. GPT-4o: $2.50)"
    )
    strong_output_price_per_million: float = Field(
        default=10.00,
        ge=0.0,
        description="Strong model output price in USD per 1M tokens (e.g. GPT-4o: $10.00)"
    )
    small_input_price_per_million: float = Field(
        default=0.15,
        ge=0.0,
        description="Small model input price in USD per 1M tokens (e.g. GPT-4o mini: $0.15)"
    )
    small_output_price_per_million: float = Field(
        default=0.60,
        ge=0.0,
        description="Small model output price in USD per 1M tokens (e.g. GPT-4o mini: $0.60)"
    )
    local_cost_per_query: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost in USD per locally handled on-device query"
    )
    cache_lookup_cost_per_query: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost in USD per cached query lookup proxy"
    )
    pricing_source: str = Field(
        default="Standard list pricing (USD per 1M tokens) for GPT-4o and GPT-4o-mini as of 2024-08",
        max_length=500,
        description="Source or rationale for pricing assumptions"
    )


class TokenSavingsBreakdown(BaseModel):
    """Disaggregated token savings comparing smart routing against always-strong baseline."""
    model_config = ConfigDict(extra="forbid")

    input_token_savings: MetricWithDenominator = Field(..., description="Input tokens saved relative to baseline input tokens")
    output_token_savings: MetricWithDenominator = Field(..., description="Output tokens saved relative to baseline output tokens")
    total_token_savings: MetricWithDenominator = Field(..., description="Total tokens saved relative to baseline total tokens")
    cache_token_savings: MetricWithDenominator = Field(..., description="Tokens saved specifically from cache hits")
    avoided_call_token_savings: MetricWithDenominator = Field(..., description="Tokens saved from queries completely avoiding cloud model execution")


class CostSavingsBreakdown(BaseModel):
    """Disaggregated cost savings calculated under explicit pricing assumptions."""
    model_config = ConfigDict(extra="forbid")

    pricing_assumptions: PricingConfig = Field(..., description="Configurable pricing parameters used for calculation")
    baseline_total_cost_usd: float = Field(..., ge=0.0, description="Total baseline cost in USD (always-strong)")
    smart_routing_total_cost_usd: float = Field(..., ge=0.0, description="Total smart routing cost in USD")
    net_cost_savings: MetricWithDenominator = Field(..., description="Net dollar savings relative to baseline cost")
    small_model_substitution_savings: MetricWithDenominator = Field(..., description="Savings achieved by routing simple queries to small model")
    local_handled_savings: MetricWithDenominator = Field(..., description="Savings achieved by resolving queries on-device without cloud call")
    cache_hit_savings: MetricWithDenominator = Field(..., description="Savings achieved from cache hits avoiding re-computation")
    escalation_cost_overhead: MetricWithDenominator = Field(..., description="Overhead cost incurred when small model attempts escalated to strong model")


class AvoidedCallsBreakdown(BaseModel):
    """Breakdown of queries that completely avoided invoking cloud model tiers."""
    model_config = ConfigDict(extra="forbid")

    total_queries: int = Field(..., ge=0, description="Total benchmark queries evaluated")
    avoided_calls_count: int = Field(..., ge=0, description="Number of queries completely avoiding cloud execution")
    avoided_calls_ratio: MetricWithDenominator = Field(..., description="Proportion of total queries handled without cloud call")
    local_handled_calls: MetricWithDenominator = Field(..., description="Queries handled locally on-device")
    cache_hit_calls: MetricWithDenominator = Field(..., description="Queries resolved via cache")
    avoided_call_token_savings: MetricWithDenominator = Field(..., description="Tokens spared by avoiding cloud execution")
    avoided_call_cost_savings_usd: MetricWithDenominator = Field(..., description="Dollars saved by avoiding cloud execution")


class LatencyDeltaBreakdown(BaseModel):
    """Disaggregated latency changes comparing smart routing to baseline."""
    model_config = ConfigDict(extra="forbid")

    mean_latency_delta_ms: MetricWithDenominator = Field(..., description="Mean latency difference (baseline_mean - routing_mean)")
    p50_latency_delta_ms: MetricWithDenominator = Field(..., description="P50 latency difference (baseline_p50 - routing_p50)")
    p95_latency_delta_ms: MetricWithDenominator = Field(..., description="P95 latency difference (baseline_p95 - routing_p95)")
    speedup_factor: float | None = Field(default=None, description="Ratio of baseline mean latency to smart routing mean latency")


class QualityDeltaBreakdown(BaseModel):
    """Quality changes and parity metrics relative to the reference strong baseline."""
    model_config = ConfigDict(extra="forbid")

    format_compliance_delta: MetricWithDenominator = Field(..., description="Smart routing compliance rate minus baseline compliance rate")
    non_empty_rate_delta: MetricWithDenominator = Field(..., description="Smart routing non-empty rate minus baseline non-empty rate")
    mean_completeness_delta: MetricWithDenominator | None = Field(default=None, description="Completeness score delta if available")
    mean_lexical_overlap: MetricWithDenominator | None = Field(default=None, description="Average token Jaccard similarity across evaluated items")
    items_evaluated: int = Field(..., ge=0, description="Total items compared")
    quality_parity_count: int = Field(..., ge=0, description="Items with format compliance and non-empty status equal to or exceeding baseline")
    quality_degraded_count: int = Field(..., ge=0, description="Items where smart routing failed quality rubrics satisfied by baseline")
    quality_improved_count: int = Field(..., ge=0, description="Items where smart routing satisfied rubrics not met by baseline")


class ItemComparativeDetail(BaseModel):
    """Side-by-side comparative record for a single benchmark query."""
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(..., description="Benchmark query ID")
    task_type: BenchmarkTaskType = Field(..., description="Task category")
    route: str = Field(..., description="Optimizer selected route")
    baseline_tokens: TokenUsageEstimate = Field(..., description="Baseline token usage")
    routing_tokens: TokenUsageEstimate = Field(..., description="Smart routing token usage")
    token_delta: int = Field(..., description="Baseline total tokens minus routing total tokens")
    baseline_cost_usd: float = Field(..., ge=0.0, description="Baseline execution cost in USD")
    routing_cost_usd: float = Field(..., ge=0.0, description="Smart routing execution cost in USD")
    cost_savings_usd: float = Field(..., description="Baseline cost minus smart routing cost")
    baseline_latency_ms: float = Field(..., ge=0.0, description="Baseline latency in ms")
    routing_latency_ms: float = Field(..., ge=0.0, description="Smart routing latency in ms")
    latency_delta_ms: float = Field(..., description="Baseline latency minus routing latency")
    baseline_format_compliant: bool | None = Field(default=None, description="Baseline format compliance")
    routing_format_compliant: bool | None = Field(default=None, description="Smart routing format compliance")
    lexical_overlap: float | None = Field(default=None, description="Jaccard token similarity to baseline answer")
    quality_verdict: str = Field(..., description="Comparative verdict: 'PARITY', 'DEGRADED', 'IMPROVED', 'NEUTRAL'")


class ComparativeEvaluationReport(BaseModel):
    """Comprehensive disaggregated comparative evaluation report relative to always-strong baseline."""
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="Unique comparative run identifier")
    baseline_run_id: str = Field(..., description="Evaluated baseline run identifier")
    smart_routing_run_id: str = Field(..., description="Evaluated smart routing run identifier")
    dataset_id: str = Field(..., description="Benchmark dataset evaluated")
    dataset_version: str = Field(default="1.0.0", description="Benchmark dataset version")
    pricing_assumptions: PricingConfig = Field(..., description="Pricing parameters and assumptions")
    token_savings: TokenSavingsBreakdown = Field(..., description="Disaggregated token savings with denominators")
    cost_savings: CostSavingsBreakdown = Field(..., description="Disaggregated dollar cost savings with denominators")
    avoided_calls: AvoidedCallsBreakdown = Field(..., description="Avoided cloud model call savings with denominators")
    latency_changes: LatencyDeltaBreakdown = Field(..., description="Latency changes with denominators")
    quality_changes: QualityDeltaBreakdown = Field(..., description="Quality deltas and parity analysis with denominators")
    item_details: list[ItemComparativeDetail] = Field(default_factory=list, description="Per-item granular comparison")
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))


# -----------------------------------------------------------------------------
# Router Failure Analysis & Misrouting Breakdown Schemas
# -----------------------------------------------------------------------------

class RouterFailureClass(str, Enum):
    """Discrete failure classes characterizing suboptimal or incorrect routing decisions."""
    NONE = "NONE"
    ROUTED_TOO_CHEAPLY = "ROUTED_TOO_CHEAPLY"
    ROUTED_TOO_EXPENSIVELY = "ROUTED_TOO_EXPENSIVELY"
    MISSED_LOCAL_HANDLING = "MISSED_LOCAL_HANDLING"
    MISSED_CACHE_OPPORTUNITY = "MISSED_CACHE_OPPORTUNITY"
    UNEXPECTED_ESCALATION = "UNEXPECTED_ESCALATION"


class QueryFailureItem(BaseModel):
    """Detailed diagnostic record for a single misrouted benchmark query."""
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(..., description="Benchmark item unique identifier")
    query_preview: str = Field(..., description="Truncated preview of query text")
    task_type: BenchmarkTaskType = Field(..., description="Benchmark task category")
    has_context_dependency: bool = Field(..., description="Whether query relies on conversation history")
    expected_route: CoarseRoute = Field(..., description="Target route designated in ground truth")
    actual_route: str = Field(..., description="Route selected by router")
    failure_class: RouterFailureClass = Field(..., description="Assigned failure category")
    reason_code: str = Field(..., description="Internal reason code assigned during routing")
    executed_model_id: str | None = Field(default=None, description="Actual model executed if any")
    is_escalated: bool = Field(default=False, description="Whether escalation was triggered")
    token_impact: int = Field(default=0, description="Token deficit or excess vs expectation")
    cost_impact_usd: float = Field(default=0.0, description="Dollar excess or deficit vs expectation")
    quality_impact: str | None = Field(default=None, description="Impact on format or correctness")
    diagnosis: str = Field(..., description="Root-cause diagnostic explanation and heuristic recommendation")


class TaskCategoryFailureBreakdown(BaseModel):
    """Aggregated failure metrics for a specific task category."""
    model_config = ConfigDict(extra="forbid")

    task_type: BenchmarkTaskType = Field(..., description="Task category evaluated")
    total_queries: int = Field(..., ge=0, description="Total queries in this category")
    correct_count: int = Field(..., ge=0, description="Queries routed optimally")
    accuracy_rate: float = Field(..., ge=0.0, le=100.0, description="Routing accuracy percentage")
    too_cheap_count: int = Field(default=0, ge=0, description="Count routed too cheaply")
    too_cheap_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage routed too cheaply")
    too_expensive_count: int = Field(default=0, ge=0, description="Count routed too expensively")
    too_expensive_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage routed too expensively")
    missed_local_count: int = Field(default=0, ge=0, description="Count that should have been local")
    missed_local_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage that should have been local")
    missed_cache_count: int = Field(default=0, ge=0, description="Count that should have been cached")
    missed_cache_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage that should have been cached")
    escalation_count: int = Field(default=0, ge=0, description="Count requiring escalation")
    primary_failure_class: RouterFailureClass = Field(
        default=RouterFailureClass.NONE,
        description="Most frequent failure class in this category"
    )


class ContextDependencyFailureBreakdown(BaseModel):
    """Aggregated failure metrics partitioned by conversational context dependency."""
    model_config = ConfigDict(extra="forbid")

    has_context_dependency: bool = Field(..., description="Context dependency partition")
    total_queries: int = Field(..., ge=0, description="Total queries in this partition")
    correct_count: int = Field(..., ge=0, description="Queries routed optimally")
    accuracy_rate: float = Field(..., ge=0.0, le=100.0, description="Routing accuracy percentage")
    too_cheap_count: int = Field(default=0, ge=0, description="Count routed too cheaply")
    too_cheap_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage routed too cheaply")
    too_expensive_count: int = Field(default=0, ge=0, description="Count routed too expensively")
    too_expensive_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage routed too expensively")
    missed_local_count: int = Field(default=0, ge=0, description="Count that should have been local")
    missed_local_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage that should have been local")
    missed_cache_count: int = Field(default=0, ge=0, description="Count that should have been cached")
    missed_cache_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage that should have been cached")
    escalation_count: int = Field(default=0, ge=0, description="Count requiring escalation")


class RouterFailureAnalysisReport(BaseModel):
    """Diagnostic report isolating router misclassifications across categories and context."""
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="Unique analysis run identifier")
    dataset_id: str = Field(..., description="Benchmark dataset evaluated")
    dataset_version: str = Field(default="1.0.0", description="Benchmark dataset version")
    total_queries_analyzed: int = Field(..., ge=0, description="Total queries analyzed")
    correct_choices_count: int = Field(..., ge=0, description="Total correctly routed queries")
    overall_accuracy_rate: float = Field(..., ge=0.0, le=100.0, description="Overall accuracy percentage")
    total_misrouted_queries: int = Field(..., ge=0, description="Total misrouted queries")
    overall_misrouting_rate: float = Field(..., ge=0.0, le=100.0, description="Overall misrouting percentage")
    too_cheap_count: int = Field(default=0, ge=0, description="Queries routed too cheaply")
    too_cheap_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Rate routed too cheaply")
    too_expensive_count: int = Field(default=0, ge=0, description="Queries routed too expensively")
    too_expensive_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Rate routed too expensively")
    missed_local_count: int = Field(default=0, ge=0, description="Queries missing local handling")
    missed_local_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Rate missing local handling")
    missed_cache_count: int = Field(default=0, ge=0, description="Queries missing cache hit")
    missed_cache_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Rate missing cache hit")
    escalation_count: int = Field(default=0, ge=0, description="Queries triggering escalation")
    escalation_rate: float = Field(default=0.0, ge=0.0, le=100.0, description="Rate triggering escalation")
    by_task_category: dict[str, TaskCategoryFailureBreakdown] = Field(
        default_factory=dict,
        description="Misrouting breakdown by canonical task category"
    )
    by_context_dependency: dict[str, ContextDependencyFailureBreakdown] = Field(
        default_factory=dict,
        description="Misrouting breakdown by context dependency (True vs False)"
    )
    failure_items: list[QueryFailureItem] = Field(
        default_factory=list,
        description="Itemized diagnoses of all misrouted queries"
    )
    key_recommendations: list[str] = Field(
        default_factory=list,
        description="Systemic heuristic and threshold tuning recommendations"
    )
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))


