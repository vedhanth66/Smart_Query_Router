"""Smart Query Router - Backend API Service.

Provides:
- GET /health: Simple liveness and version probe.
- POST /api/v1/optimize: Validates NormalizedQueryPackage and returns an
  OptimizationDecisionResponse following the minimal contract without ML routing.
"""

import os
import re
import time
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from fastapi import FastAPI, Header, Response, status, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .schemas.contract import (
    ClientMetadata,
    NormalizedQueryPackage,
    OptimizationDecisionResponse,
    DecisionType,
    OptimizationInstructions,
    CoarseRoute,
    TaskCategory,
    ComplexityLevel,
    UserRoutingOverride,
    ModelTier,
    RouteExecutionMetadata,
    CacheOutcome,
    LocalFeatures,
    ExperimentalCompressionResult,
    CompressionABComparisonResult,
    RichContentHandling,
    OutcomeFeedbackEvent,
)
from .schemas.dataset import (
    CandidateSourceType,
    ReviewStatus,
    CandidateRejectionCategory,
    DatasetSplit,
    TrainingCandidate,
    ReviewDecisionRequest,
    DatasetStatsResponse,
    CandidateIngestFeedbackRequest,
    CandidateIngestEscalationRequest,
)
from .dataset import (
    default_dataset_pipeline,
    default_candidate_repository,
    CandidateNotFoundError,
    InvalidReviewStateTransitionError,
)
from .schemas.benchmark import (
    StrongModelBaselineReport,
    SmartRoutingEvaluationReport,
    ComparativeEvaluationReport,
    RouterFailureAnalysisReport,
    PricingConfig,
    BenchmarkDataset,
)
from .dataset.benchmark_validator import (
    load_benchmark_dataset,
)
from .dataset.baseline_runner import (
    StrongModelBaselineRunner,
    save_baseline_report,
    load_baseline_report,
)
from .dataset.smart_routing_runner import (
    SmartRoutingBenchmarkRunner,
    save_smart_routing_report,
    load_smart_routing_report,
)
from .dataset.comparative_evaluator import (
    ComparativeBenchmarkEvaluator,
    save_comparative_report,
    load_comparative_report,
)
from .dataset.failure_analyzer import (
    RouterFailureAnalyzer,
    save_failure_report,
    load_failure_report,
)
from .schemas.ml_dataset import (
    MLFeatureDataset,
)
from .schemas.ml_model import (
    ModelTrainingMetadata,
    MLPredictionRequest,
    MLPredictionResponse,
    ComparativeEmbeddingTrainingReport,
    GuardedRoutingDecision,
    GuardedPredictionRequest,
    MLCompareEmbeddingsRequest,
)
from .schemas.shadow_routing import (
    ShadowEvaluationStatusReport,
    ShadowActivationRequest,
    ShadowBatchSimulateRequest,
    ShadowActivationThresholds,
)
from .dataset.ml_dataset_generator import (
    MLFeatureDatasetGenerator,
    save_ml_dataset_json,
    save_ml_dataset_jsonl,
    save_ml_dataset_csv,
    load_ml_dataset_json,
)
from .ml.router_classifier import (
    MLRouterClassifier,
)
from .ml.shadow_router import (
    default_shadow_router,
)
from .schemas.rollout_routing import (
    RolloutCohort,
    RolloutConfig,
    RollbackTriggerThresholds,
    RolloutStatusReport,
    RolloutConfigureRequest,
    KillSwitchRequest,
    RollbackResetRequest,
    RolloutSimulationRequest,
)
from .ml.rollout_manager import (
    RolloutManager,
    default_rollout_manager,
)
from .gateway import (
    default_gateway,
    GatewayRequest,
    GatewayResponse,
    GatewayError,
    GatewayTimeoutError,
    GatewayRetryExhaustedError,
    GatewayResponseSizeLimitError,
    ModelExecutionComparison,
)
from .evaluator import (
    default_evaluator_registry,
    EvaluationRequest,
    EvaluationResult,
    EscalationRecommendation,
)
from .cache import (
    default_response_cache,
    default_deduplicator,
    default_semantic_cache,
    DeduplicationTimeoutError,
)
from .optimizer import (
    default_query_optimizer,
    default_experimental_compressor,
    compare_ab_compression_routing,
    is_experimental_compression_allowed,
    ExperimentalCompressionConfig,
)
from .config import get_settings
from .metrics import production_metrics

APP_START_TIME = time.time()

DEFAULT_EVAL_CONFIDENCE_THRESHOLD = 0.70
DEFAULT_EVAL_COMPLETENESS_THRESHOLD = 0.70

def get_eval_confidence_threshold() -> float:
    try:
        val = float(os.environ.get("ROUTER_EVAL_CONFIDENCE_THRESHOLD") or os.environ.get("EVALUATION_CONFIDENCE_THRESHOLD", DEFAULT_EVAL_CONFIDENCE_THRESHOLD))
        if 0.0 <= val <= 1.0:
            return val
        return DEFAULT_EVAL_CONFIDENCE_THRESHOLD
    except (ValueError, TypeError):
        return DEFAULT_EVAL_CONFIDENCE_THRESHOLD

def get_eval_completeness_threshold() -> float:
    try:
        val = float(os.environ.get("ROUTER_EVAL_COMPLETENESS_THRESHOLD") or os.environ.get("EVALUATION_COMPLETENESS_THRESHOLD", DEFAULT_EVAL_COMPLETENESS_THRESHOLD))
        if 0.0 <= val <= 1.0:
            return val
        return DEFAULT_EVAL_COMPLETENESS_THRESHOLD
    except (ValueError, TypeError):
        return DEFAULT_EVAL_COMPLETENESS_THRESHOLD

app = FastAPI(
    title="Smart Query Router Backend",
    version="0.1.0",
    description="Minimal backend contract and routing service for Claude extension."
)

# CORS configuration allowing Chrome extensions and local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permits chrome-extension://* callers
    allow_credentials=False,  # Strict: no credentials/cookies
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-ID"],
)

# Concurrency and In-Flight Metrics Tracking
IN_FLIGHT_REQUESTS: int = 0
TOTAL_REQUESTS_SERVED: int = 0
RECENT_REQUEST_LATENCIES_MS: list[float] = []


@app.middleware("http")
async def track_in_flight_concurrency(request, call_next):
    """Tracks active in-flight requests and request duration for queue-aware scaling."""
    global IN_FLIGHT_REQUESTS, TOTAL_REQUESTS_SERVED
    IN_FLIGHT_REQUESTS += 1
    production_metrics.increment_in_flight()
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        IN_FLIGHT_REQUESTS = max(0, IN_FLIGHT_REQUESTS - 1)
        TOTAL_REQUESTS_SERVED += 1
        production_metrics.decrement_in_flight()
        if len(RECENT_REQUEST_LATENCIES_MS) >= 1000:
            RECENT_REQUEST_LATENCIES_MS.pop(0)
        RECENT_REQUEST_LATENCIES_MS.append(duration_ms)


@app.get("/metrics", response_class=Response, summary="Prometheus metrics for queue-aware autoscaling and production observability")
def metrics_endpoint() -> Response:
    """Prometheus exposition metrics endpoint for queue-aware autoscaling and monitoring."""
    return Response(content=production_metrics.export_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/api/v1/metrics/summary", summary="Production metrics summary answering 'Are we saving work without degrading user experience?'")
def metrics_summary_endpoint() -> dict[str, Any]:
    """Summary report answering: 'Are we saving work without degrading user experience?'"""
    return production_metrics.get_summary_report()


@app.get("/health", status_code=status.HTTP_200_OK)
def health_check() -> dict[str, Any]:
    """Health and liveness endpoint."""
    return {
        "status": "ok",
        "service": "smart-query-router-backend",
        "version": "0.1.0",
        "uptime_seconds": round(time.time() - APP_START_TIME, 2),
    }


@app.get("/healthz", status_code=status.HTTP_200_OK)
def liveness_probe() -> dict[str, Any]:
    """Kubernetes / container orchestration liveness probe."""
    return {
        "status": "ok",
        "service": "smart-query-router-backend",
        "version": "0.1.0",
        "timestamp": int(time.time()),
        "uptime_seconds": round(time.time() - APP_START_TIME, 2),
    }


@app.get("/readyz", status_code=status.HTTP_200_OK)
def readiness_probe(response: Response) -> dict[str, Any]:
    """Kubernetes / container orchestration readiness probe."""
    components: dict[str, str] = {}
    is_ready = True

    # 1. Cache readiness check
    try:
        if default_response_cache is not None and default_semantic_cache is not None:
            components["cache"] = "ready"
        else:
            components["cache"] = "unavailable"
            is_ready = False
    except Exception as exc:
        components["cache"] = f"error: {str(exc)}"
        is_ready = False

    # 2. Gateway readiness check
    try:
        if default_gateway is not None:
            components["gateway"] = "ready"
        else:
            components["gateway"] = "unavailable"
            is_ready = False
    except Exception as exc:
        components["gateway"] = f"error: {str(exc)}"
        is_ready = False

    # 3. Rollout manager readiness check
    try:
        if default_rollout_manager is not None:
            components["rollout"] = "ready"
        else:
            components["rollout"] = "unavailable"
            is_ready = False
    except Exception as exc:
        components["rollout"] = f"error: {str(exc)}"
        is_ready = False

    # 4. Settings readiness check
    try:
        _ = get_settings()
        components["config"] = "ready"
    except Exception as exc:
        components["config"] = f"error: {str(exc)}"
        is_ready = False

    production_metrics.set_subsystem_health("cache", components.get("cache") == "ready")
    production_metrics.set_subsystem_health("semantic_cache", default_semantic_cache is not None)
    production_metrics.set_subsystem_health("gateway", components.get("gateway") == "ready")
    production_metrics.set_subsystem_health("rollout_manager", components.get("rollout") == "ready")
    production_metrics.set_subsystem_health("router", is_ready)

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "uptime_seconds": round(time.time() - APP_START_TIME, 2),
            "components": components,
        }

    return {
        "status": "ready",
        "uptime_seconds": round(time.time() - APP_START_TIME, 2),
        "components": components,
    }


@app.get("/startupz", status_code=status.HTTP_200_OK)
def startup_probe() -> dict[str, Any]:
    """Kubernetes / container orchestration startup probe."""
    return {
        "status": "started",
        "service": "smart-query-router-backend",
        "version": "0.1.0",
        "timestamp": int(time.time()),
        "uptime_seconds": round(time.time() - APP_START_TIME, 2),
    }


@app.post(
    "/api/v1/optimize",
    response_model=OptimizationDecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate query optimization decision contract"
)
async def evaluate_optimization(
    package: NormalizedQueryPackage,
    response: Response,
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> OptimizationDecisionResponse:
    """Evaluates an incoming normalized query package against the baseline contract.
    
    Tracks correlation identifier across client and backend, returning it
    in the response body and X-Correlation-ID header.
    
    Guarantees:
    - Rejects malformed or oversized payloads with HTTP 422.
    - Strictly never includes provider secrets or model credentials.
    - Preserves correlation identifier.
    - Executes only the selected route unless the policy explicitly says evaluation is needed.
    - Safe non-blocking fallback on execution errors without repeated retry loops.
    - Records route, model_version, latency, and failure_category as metadata.
    - Isolated deduplication and exact-match cache by tenant and user scope.
    """
    corr_val = x_correlation_id if isinstance(x_correlation_id, str) else None
    correlation_id = corr_val or package.correlation_id or package.request_id
    response.headers["X-Correlation-ID"] = correlation_id

    effective_user_id = (
        (x_user_id if isinstance(x_user_id, str) else None)
        or package.user_id
        or (package.client_metadata.user_id if package.client_metadata else None)
        or "default_user"
    )
    effective_tenant_id = (
        (x_tenant_id if isinstance(x_tenant_id, str) else None)
        or package.tenant_id
        or (package.client_metadata.tenant_id if package.client_metadata else None)
        or "default_tenant"
    )

    # Semantics-preserving backend query optimization stage
    query_optimization = default_query_optimizer.optimize(package.query_text)
    query = query_optimization.optimized_query if query_optimization.is_transformed else package.query_text.strip()

    # Disabled-by-default experimental stronger compression path (locked in production)
    experimental_compression: ExperimentalCompressionResult | None = None
    if is_experimental_compression_allowed(default_experimental_compressor.config):
        experimental_compression = default_experimental_compressor.compress(
            package.query_text,
            features=package.local_features,
        )

    # Detect rich content and attachment dependencies
    has_rich = False
    rich_types: list[str] = []
    if package.local_features:
        lf = package.local_features
        if lf.has_rich_input:
            has_rich = True
        if lf.has_attachments:
            has_rich = True
            rich_types.append("ATTACHMENT")
        if lf.has_images:
            has_rich = True
            rich_types.append("IMAGE")
        if lf.has_files:
            has_rich = True
            rich_types.append("FILE")
        if lf.has_tables:
            has_rich = True
            rich_types.append("TABLE")
        if lf.has_code_blocks:
            has_rich = True
            rich_types.append("CODE_BLOCK")
        if lf.attachment_types:
            for at in lf.attachment_types:
                if at not in rich_types:
                    rich_types.append(at)

    # Check query text for attachment markers or tables if not already captured
    if re.search(r"\[(?:Attachment|Image|File|Upload)(?:\s*#?\d*)?(?:\s*:\s*[^\]]+)?\]", package.query_text, re.I):
        has_rich = True
        if "ATTACHMENT" not in rich_types:
            rich_types.append("ATTACHMENT")
    if re.search(r"^\s*\|.+?\|\s*$", package.query_text, re.M) or re.search(r"^\s*[\+\|][-+=]+[\+\|]\s*$", package.query_text, re.M):
        has_rich = True
        if "TABLE" not in rich_types:
            rich_types.append("TABLE")

    rich_content_handling = RichContentHandling(
        has_rich_input=has_rich,
        detected_types=rich_types,
        preservation_strategy="CONSERVATIVE_PRESERVATION" if has_rich else "NONE",
        notes="Conservative routing to complex model with full content preservation" if has_rich else "No rich content detected"
    )

    # Minimal deterministic baseline evaluation (no ML yet)
    if has_rich:
        decision_type = DecisionType.BACKEND_CANDIDATE
        confidence = 0.90
        reason_code = "RICH_CONTENT_CANDIDATE"
        instructions = OptimizationInstructions(
            suggested_model=None,
            notes=f"Query depends on rich content ({', '.join(rich_types) if rich_types else 'rich inputs'}); content preservation required"
        )
    elif len(query) < 15 and not package.context_candidates:
        decision_type = DecisionType.NO_OPTIMIZATION
        confidence = 0.95
        reason_code = "QUERY_SHORT_PASSTHROUGH"
        instructions = None
    elif package.local_features and package.local_features.has_code:
        # Code-containing queries are marked as backend evaluation candidates
        decision_type = DecisionType.BACKEND_CANDIDATE
        confidence = 0.80
        reason_code = "CODE_ANALYSIS_CANDIDATE"
        instructions = OptimizationInstructions(
            suggested_model=None,  # No ML routing yet
            notes="Identified code syntax in query package"
        )
    else:
        # Default baseline fallthrough
        decision_type = DecisionType.NO_OPTIMIZATION
        confidence = 0.75
        reason_code = "DEFAULT_BASELINE_FALLTHROUGH"
        instructions = None

    # Deterministic router as immediate fallback & baseline
    rule_coarse_route: CoarseRoute
    if package.coarse_route:
        rule_coarse_route = package.coarse_route
    elif has_rich:
        rule_coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.task_category in [
        TaskCategory.CODING,
        TaskCategory.DEBUGGING,
        TaskCategory.REASONING,
        TaskCategory.COMPARISON,
        TaskCategory.ANALYSIS,
    ]:
        rule_coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.task_category in [TaskCategory.GREETING, TaskCategory.ARITHMETIC]:
        rule_coarse_route = CoarseRoute.LOCAL_ELIGIBLE
    elif package.task_category in [
        TaskCategory.FACTUAL_QUESTION,
        TaskCategory.SUMMARIZATION,
        TaskCategory.REWRITING,
        TaskCategory.TRANSLATION,
        TaskCategory.CREATIVE_WRITING,
    ]:
        rule_coarse_route = CoarseRoute.SIMPLE_MODEL_CANDIDATE
    elif package.local_features and package.local_features.has_code:
        rule_coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.local_features and package.local_features.has_math:
        rule_coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.local_features and package.local_features.detected_cues:
        rule_coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.context_candidates:
        rule_coarse_route = CoarseRoute.NEEDS_EVALUATION
    elif len(query) < 15 and any(query.lower().startswith(g) for g in ["hi", "hello", "hey", "thanks"]):
        rule_coarse_route = CoarseRoute.LOCAL_ELIGIBLE
    else:
        rule_coarse_route = CoarseRoute.SIMPLE_MODEL_CANDIDATE

    rule_model_tier = default_gateway.resolve_tier_from_route(rule_coarse_route.value if rule_coarse_route else None)

    # Rollout and Fallback Cohort Allocation
    assigned_cohort: RolloutCohort = RolloutCohort.CONTROL_RULE
    coarse_route = rule_coarse_route
    model_tier = rule_model_tier

    # Track quality and escalation signals across execution
    observed_completeness: float | None = None
    observed_confidence: float | None = None
    observed_issues: list[str] = []
    escalation_reason: str | None = None

    if package.coarse_route:
        # Client explicitly forced route: honor it
        coarse_route = package.coarse_route
        model_tier = default_gateway.resolve_tier_from_route(coarse_route.value if coarse_route else None)
    else:
        should_route_ml, rollout_reason = default_rollout_manager.should_route_to_ml(
            package=package,
            user_id=effective_user_id,
            tenant_id=effective_tenant_id,
        )
        if should_route_ml:
            try:
                guarded_router = default_shadow_router._ensure_guarded_router()

                has_context_dep = bool(package.context_candidates)
                prior_turns = [
                    {"role": t.role, "content": t.content} for t in (package.context_candidates or [])
                ]
                task_cat_str = package.task_category.value if package.task_category else None
                complexity_str = package.complexity_level.value if package.complexity_level else None

                local_sigs = None
                if package.local_features:
                    lf = package.local_features
                    local_sigs = {
                        "char_count": lf.char_count,
                        "word_count": lf.word_count,
                        "estimated_tokens": lf.estimated_tokens,
                        "has_code": lf.has_code,
                        "has_math": lf.has_math,
                        "has_questions": lf.has_questions,
                        "has_urls": lf.has_urls,
                        "has_tables": lf.has_tables,
                        "has_code_blocks": lf.has_code_blocks,
                        "has_rich_input": lf.has_rich_input,
                        "uppercase_ratio": lf.uppercase_ratio,
                        "numeric_ratio": lf.numeric_ratio,
                        "special_char_ratio": lf.special_char_ratio,
                    }

                ml_decision = guarded_router.route_query(
                    query_text=package.query_text,
                    task_type=task_cat_str,
                    complexity_label=complexity_str,
                    has_context_dependency=has_context_dep,
                    prior_conversation_turns=prior_turns,
                    local_signals=local_sigs,
                )
                if ml_decision.final_route in [r.value for r in CoarseRoute]:
                    coarse_route = CoarseRoute(ml_decision.final_route)
                    model_tier = default_gateway.resolve_tier_from_route(coarse_route.value)
                    assigned_cohort = RolloutCohort.ML
                    confidence = ml_decision.ml_confidence or 0.85
                    reason_code = f"ML_ROUTING_{ml_decision.decision_gate}"
                    if instructions and model_tier:
                        instructions.suggested_model = default_gateway.get_model_recommendation(model_tier)
                        instructions.notes = f"Routed via active ML rollout ({ml_decision.decision_gate})"
                else:
                    # Unrecognized route fallback
                    coarse_route = rule_coarse_route
                    model_tier = rule_model_tier
                    assigned_cohort = RolloutCohort.FALLBACK_DETERMINISTIC
                    reason_code = "FALLBACK_DETERMINISTIC_UNRECOGNIZED_ML_ROUTE"
            except Exception:
                # Immediate fallback to deterministic router on ML error
                coarse_route = rule_coarse_route
                model_tier = rule_model_tier
                assigned_cohort = RolloutCohort.FALLBACK_DETERMINISTIC
                reason_code = "FALLBACK_DETERMINISTIC_ON_ML_ERROR"
        else:
            coarse_route = rule_coarse_route
            model_tier = rule_model_tier
            assigned_cohort = RolloutCohort.CONTROL_RULE

    if instructions and model_tier and not instructions.suggested_model:
        instructions.suggested_model = default_gateway.get_model_recommendation(model_tier)

    # Execute only the selected route via Model Gateway (unless evaluation is needed)
    execution_metadata: RouteExecutionMetadata | None = None
    if package.execute_route and coarse_route:
        exec_start = time.perf_counter()
        try:
            if coarse_route == CoarseRoute.LOCAL_ELIGIBLE:
                # Local-eligible route bypasses remote model gateway execution
                execution_metadata = RouteExecutionMetadata(
                    route=coarse_route.value,
                    model_id=None,
                    model_version=None,
                    latency_ms=0.0,
                    failure_category="NONE",
                    fallback_applied=False,
                    executed_content=None,
                    cache_outcome=CacheOutcome.NOT_CHECKED,
                    cache_key=None,
                    semantic_cache_outcome="SEMANTIC_BYPASS",
                    semantic_validation_reason="LOCAL_ELIGIBLE_ROUTE_BYPASS",
                    evaluation_metadata=None,
                )
            elif coarse_route == CoarseRoute.SIMPLE_MODEL_CANDIDATE:
                small_model_id = default_gateway.get_model_recommendation(ModelTier.FAST_CHEAP, "small_model")
                small_model_version = default_gateway.get_model_version_recommendation(ModelTier.FAST_CHEAP, "small_model")

                # 1. Exact-match cache lookup (scoped by tenant and user)
                cached_res, cache_outcome, bypass_reason, cache_key = await default_response_cache.check_cache(
                    package=package,
                    tier=ModelTier.FAST_CHEAP,
                    model_id=small_model_id,
                    model_version=small_model_version,
                    provider="small_model",
                    tenant_id=effective_tenant_id,
                    user_id=effective_user_id,
                )

                if cache_outcome == CacheOutcome.HIT and cached_res is not None:
                    # Exact Cache HIT: return cached completion directly without remote gateway execution
                    elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
                    hit_eval_metadata = dict(cached_res.metadata or {})
                    hit_eval_metadata.update({
                        "cached": True,
                        "hit_count": cached_res.hit_count,
                        "ttl_remaining_s": max(0.0, round(cached_res.expires_at - time.time(), 1)),
                    })
                    execution_metadata = RouteExecutionMetadata(
                        route=coarse_route.value,
                        model_id=cached_res.model_id,
                        model_version=cached_res.model_version,
                        latency_ms=elapsed_ms,
                        failure_category="NONE",
                        fallback_applied=False,
                        executed_content=cached_res.content,
                        escalation_occurred=False,
                        escalation_reason=None,
                        cache_outcome=CacheOutcome.HIT,
                        cache_key=cache_key,
                        is_deduplicated=False,
                        deduplication_role=None,
                        semantic_cache_outcome=None,
                        semantic_validation_reason="EXACT_CACHE_HIT",
                        evaluation_metadata=hit_eval_metadata,
                    )
                else:
                    # 2. Semantic cache lookup (only for eligible static informational requests)
                    sem_match, sem_val, sem_dec = await default_semantic_cache.lookup_candidate(
                        package=package,
                        expected_tier=ModelTier.FAST_CHEAP,
                        expected_model_id=small_model_id,
                        expected_model_version=small_model_version,
                        tenant_id=effective_tenant_id,
                        user_id=effective_user_id,
                    )

                    if sem_match is not None and sem_val is not None and sem_val.is_valid:
                        # Semantic Cache HIT: return cached completion directly without remote execution
                        elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
                        hit_eval_metadata = dict(sem_match.metadata or {})
                        hit_eval_metadata.update({
                            "cached": True,
                            "semantic_hit": True,
                            "similarity_score": sem_match.similarity_score,
                            "matched_query": sem_match.query_text,
                            "hit_count": sem_match.hit_count,
                        })
                        execution_metadata = RouteExecutionMetadata(
                            route=coarse_route.value,
                            model_id=sem_match.metadata.get("model_id") or small_model_id,
                            model_version=sem_match.metadata.get("model_version") or small_model_version,
                            latency_ms=elapsed_ms,
                            failure_category="NONE",
                            fallback_applied=False,
                            executed_content=sem_match.content,
                            escalation_occurred=False,
                            escalation_reason=None,
                            cache_outcome=CacheOutcome.HIT,
                            cache_key=cache_key,
                            is_deduplicated=False,
                            deduplication_role=None,
                            semantic_cache_outcome="SEMANTIC_HIT",
                            semantic_validation_reason=sem_val.primary_reason(),
                            evaluation_metadata=hit_eval_metadata,
                        )
                    else:
                        # Semantic Cache MISS or BYPASS: determine outcome and reason
                        if not sem_dec.is_eligible:
                            sem_outcome = "SEMANTIC_BYPASS"
                            sem_reason = sem_dec.bypass_reason
                        elif sem_val is not None and not sem_val.is_valid:
                            sem_outcome = "SEMANTIC_MISS"
                            sem_reason = sem_val.primary_reason()
                        else:
                            sem_outcome = "SEMANTIC_MISS"
                            sem_reason = "NO_SIMILAR_CANDIDATE_ABOVE_THRESHOLD"

                        # Cache MISS or BYPASS: in-flight request deduplication
                        effective_cache_key = cache_key or default_response_cache.generate_cache_key(
                            package=package,
                            tier=ModelTier.FAST_CHEAP,
                            model_id=small_model_id,
                            model_version=small_model_version,
                            provider="small_model",
                            tenant_id=effective_tenant_id,
                            user_id=effective_user_id,
                        ).compute_key()
                        dedup_key = default_deduplicator.generate_dedup_key(
                            tenant_id=effective_tenant_id,
                            user_id=effective_user_id,
                            cache_key=effective_cache_key,
                        )

                        async def _run_simple_route() -> RouteExecutionMetadata:
                            nonlocal observed_completeness, observed_confidence, observed_issues, escalation_reason
                            gw_req = GatewayRequest(
                                prompt=query,
                                tier=ModelTier.FAST_CHEAP,
                                provider="small_model",
                                correlation_id=correlation_id,
                                metadata={"task_category": package.task_category.value if package.task_category else None},
                            )
                            gw_res = await default_gateway.execute(gw_req)

                            # Evaluate small-model completion via pluggable evaluator
                            eval_req = EvaluationRequest(
                                prompt=query,
                                candidate_response=gw_res.content,
                                task_category=package.task_category.value if package.task_category else None,
                                correlation_id=correlation_id,
                            )
                            eval_res = await default_evaluator_registry.evaluate(
                                eval_req,
                                evaluator_id="heuristic-incompleteness-v1"
                            )

                            observed_completeness = eval_res.completeness
                            observed_confidence = eval_res.confidence
                            observed_issues = [i.issue_code for i in eval_res.detected_issues]

                            conf_threshold = get_eval_confidence_threshold()
                            comp_threshold = get_eval_completeness_threshold()

                            is_incomplete = (
                                eval_res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
                                or eval_res.completeness < comp_threshold
                            )
                            is_low_confidence = eval_res.confidence < conf_threshold

                            if is_incomplete or is_low_confidence:
                                reasons = []
                                if is_incomplete:
                                    issue_codes = [i.issue_code for i in eval_res.detected_issues]
                                    reasons.append(f"INCOMPLETE ({', '.join(issue_codes) if issue_codes else f'completeness={eval_res.completeness} < {comp_threshold}'})")
                                if is_low_confidence:
                                    reasons.append(f"LOW_CONFIDENCE ({eval_res.confidence} < {conf_threshold})")
                                escalation_reason = "; ".join(reasons)

                                # Re-run the same logical request through the stronger model
                                gw_req_strong = GatewayRequest(
                                    prompt=query,
                                    tier=ModelTier.STRONG,
                                    provider="strong_model",
                                    correlation_id=correlation_id,
                                    metadata={"task_category": package.task_category.value if package.task_category else None},
                                )
                                try:
                                    strong_res = await default_gateway.execute(gw_req_strong)
                                    total_latency = round(gw_res.latency_ms + strong_res.latency_ms, 2)
                                    return RouteExecutionMetadata(
                                        route=coarse_route.value,
                                        model_id=strong_res.model_id,
                                        model_version=strong_res.model_version,
                                        latency_ms=total_latency,
                                        failure_category="NONE",
                                        fallback_applied=False,
                                        executed_content=strong_res.content,
                                        escalation_occurred=True,
                                        escalation_reason=escalation_reason,
                                        cache_outcome=cache_outcome,
                                        cache_key=effective_cache_key,
                                        semantic_cache_outcome=sem_outcome,
                                        semantic_validation_reason=sem_reason,
                                        evaluation_metadata={
                                            "evaluator_id": eval_res.evaluator_id,
                                            "completeness": eval_res.completeness,
                                            "confidence": eval_res.confidence,
                                            "escalation_recommendation": eval_res.escalation_recommendation.value,
                                            "detected_issues": [i.model_dump() for i in eval_res.detected_issues],
                                            "original_model_id": gw_res.model_id,
                                            "original_model_version": gw_res.model_version,
                                            "original_latency_ms": gw_res.latency_ms,
                                            "length_guidance": strong_res.raw_metadata.get("length_guidance"),
                                        },
                                    )
                                except (GatewayTimeoutError, GatewayRetryExhaustedError, GatewayResponseSizeLimitError, GatewayError) as strong_err:
                                    return RouteExecutionMetadata(
                                        route=coarse_route.value,
                                        model_id=gw_res.model_id,
                                        model_version=gw_res.model_version,
                                        latency_ms=gw_res.latency_ms,
                                        failure_category="ESCALATION_FAILED_RETAINED_ORIGINAL",
                                        fallback_applied=True,
                                        executed_content=gw_res.content,
                                        escalation_occurred=True,
                                        escalation_reason=f"{escalation_reason} (Strong model failed: {type(strong_err).__name__})",
                                        cache_outcome=cache_outcome,
                                        cache_key=effective_cache_key,
                                        semantic_cache_outcome=sem_outcome,
                                        semantic_validation_reason=sem_reason,
                                        evaluation_metadata={
                                            "evaluator_id": eval_res.evaluator_id,
                                            "completeness": eval_res.completeness,
                                            "confidence": eval_res.confidence,
                                            "escalation_recommendation": eval_res.escalation_recommendation.value,
                                            "detected_issues": [i.model_dump() for i in eval_res.detected_issues],
                                            "escalation_error": str(strong_err),
                                            "length_guidance": gw_res.raw_metadata.get("length_guidance"),
                                        },
                                    )
                            else:
                                # Evaluation passed! Store successful small-model response in cache if eligible
                                if cache_outcome == CacheOutcome.MISS:
                                    await default_response_cache.store_response(
                                        package=package,
                                        tier=ModelTier.FAST_CHEAP,
                                        model_id=gw_res.model_id,
                                        model_version=gw_res.model_version,
                                        provider="small_model",
                                        content=gw_res.content,
                                        tenant_id=effective_tenant_id,
                                        user_id=effective_user_id,
                                        metadata={
                                            "evaluator_id": eval_res.evaluator_id,
                                            "completeness": eval_res.completeness,
                                            "confidence": eval_res.confidence,
                                            "escalation_recommendation": eval_res.escalation_recommendation.value,
                                            "detected_issues": [],
                                            "passed": True,
                                        }
                                    )

                                # Index in semantic cache if eligible
                                if sem_dec.is_eligible:
                                    await default_semantic_cache.index_candidate(
                                        entry_id=f"sem-{correlation_id}",
                                        query_text=query,
                                        content=gw_res.content,
                                        package=package,
                                        tier=ModelTier.FAST_CHEAP,
                                        model_id=gw_res.model_id,
                                        model_version=gw_res.model_version,
                                        tenant_id=effective_tenant_id,
                                        user_id=effective_user_id,
                                    )

                                return RouteExecutionMetadata(
                                    route=coarse_route.value,
                                    model_id=gw_res.model_id,
                                    model_version=gw_res.model_version,
                                    latency_ms=gw_res.latency_ms,
                                    failure_category="NONE",
                                    fallback_applied=False,
                                    executed_content=gw_res.content,
                                    escalation_occurred=False,
                                    escalation_reason=None,
                                    cache_outcome=cache_outcome,
                                    cache_key=effective_cache_key,
                                    semantic_cache_outcome=sem_outcome,
                                    semantic_validation_reason=sem_reason,
                                    evaluation_metadata={
                                        "evaluator_id": eval_res.evaluator_id,
                                        "completeness": eval_res.completeness,
                                        "confidence": eval_res.confidence,
                                        "escalation_recommendation": eval_res.escalation_recommendation.value,
                                        "detected_issues": [],
                                        "passed": True,
                                        "length_guidance": gw_res.raw_metadata.get("length_guidance"),
                                    },
                                )

                        coalesced = await default_deduplicator.execute_or_join(
                            key=dedup_key,
                            coro_fn=_run_simple_route,
                            timeout_seconds=15.0,
                        )
                        base_meta = coalesced.data
                        elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
                        execution_metadata = base_meta.model_copy(update={
                            "latency_ms": elapsed_ms if not coalesced.is_leader else base_meta.latency_ms,
                            "is_deduplicated": coalesced.is_deduplicated,
                            "deduplication_role": "leader" if coalesced.is_leader else "follower",
                        })
            elif coarse_route == CoarseRoute.COMPLEX_MODEL_CANDIDATE:
                strong_model_id = default_gateway.get_model_recommendation(ModelTier.STRONG, "strong_model")
                strong_model_version = default_gateway.get_model_version_recommendation(ModelTier.STRONG, "strong_model")

                # 1. Exact-match cache lookup (scoped by tenant and user)
                cached_res, cache_outcome, bypass_reason, cache_key = await default_response_cache.check_cache(
                    package=package,
                    tier=ModelTier.STRONG,
                    model_id=strong_model_id,
                    model_version=strong_model_version,
                    provider="strong_model",
                    tenant_id=effective_tenant_id,
                    user_id=effective_user_id,
                )

                if cache_outcome == CacheOutcome.HIT and cached_res is not None:
                    # Exact Cache HIT: return cached completion directly without remote gateway execution
                    elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
                    hit_eval_metadata = dict(cached_res.metadata or {})
                    hit_eval_metadata.update({
                        "cached": True,
                        "hit_count": cached_res.hit_count,
                        "ttl_remaining_s": max(0.0, round(cached_res.expires_at - time.time(), 1)),
                    })
                    execution_metadata = RouteExecutionMetadata(
                        route=coarse_route.value,
                        model_id=cached_res.model_id,
                        model_version=cached_res.model_version,
                        latency_ms=elapsed_ms,
                        failure_category="NONE",
                        fallback_applied=False,
                        executed_content=cached_res.content,
                        cache_outcome=CacheOutcome.HIT,
                        cache_key=cache_key,
                        is_deduplicated=False,
                        deduplication_role=None,
                        semantic_cache_outcome=None,
                        semantic_validation_reason="EXACT_CACHE_HIT",
                        evaluation_metadata=hit_eval_metadata,
                    )
                else:
                    # 2. Semantic cache lookup (only for eligible static informational requests)
                    sem_match, sem_val, sem_dec = await default_semantic_cache.lookup_candidate(
                        package=package,
                        expected_tier=ModelTier.STRONG,
                        expected_model_id=strong_model_id,
                        expected_model_version=strong_model_version,
                        tenant_id=effective_tenant_id,
                        user_id=effective_user_id,
                    )

                    if sem_match is not None and sem_val is not None and sem_val.is_valid:
                        # Semantic Cache HIT
                        elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
                        hit_eval_metadata = dict(sem_match.metadata or {})
                        hit_eval_metadata.update({
                            "cached": True,
                            "semantic_hit": True,
                            "similarity_score": sem_match.similarity_score,
                            "matched_query": sem_match.query_text,
                            "hit_count": sem_match.hit_count,
                        })
                        execution_metadata = RouteExecutionMetadata(
                            route=coarse_route.value,
                            model_id=sem_match.metadata.get("model_id") or strong_model_id,
                            model_version=sem_match.metadata.get("model_version") or strong_model_version,
                            latency_ms=elapsed_ms,
                            failure_category="NONE",
                            fallback_applied=False,
                            executed_content=sem_match.content,
                            cache_outcome=CacheOutcome.HIT,
                            cache_key=cache_key,
                            is_deduplicated=False,
                            deduplication_role=None,
                            semantic_cache_outcome="SEMANTIC_HIT",
                            semantic_validation_reason=sem_val.primary_reason(),
                            evaluation_metadata=hit_eval_metadata,
                        )
                    else:
                        # Semantic Cache MISS or BYPASS
                        if not sem_dec.is_eligible:
                            sem_outcome = "SEMANTIC_BYPASS"
                            sem_reason = sem_dec.bypass_reason
                        elif sem_val is not None and not sem_val.is_valid:
                            sem_outcome = "SEMANTIC_MISS"
                            sem_reason = sem_val.primary_reason()
                        else:
                            sem_outcome = "SEMANTIC_MISS"
                            sem_reason = "NO_SIMILAR_CANDIDATE_ABOVE_THRESHOLD"

                        # Cache MISS or BYPASS: in-flight request deduplication
                        effective_cache_key = cache_key or default_response_cache.generate_cache_key(
                            package=package,
                            tier=ModelTier.STRONG,
                            model_id=strong_model_id,
                            model_version=strong_model_version,
                            provider="strong_model",
                            tenant_id=effective_tenant_id,
                            user_id=effective_user_id,
                        ).compute_key()
                        dedup_key = default_deduplicator.generate_dedup_key(
                            tenant_id=effective_tenant_id,
                            user_id=effective_user_id,
                            cache_key=effective_cache_key,
                        )

                        async def _run_complex_route() -> RouteExecutionMetadata:
                            gw_req = GatewayRequest(
                                prompt=query,
                                tier=ModelTier.STRONG,
                                provider="strong_model",
                                correlation_id=correlation_id,
                                metadata={"task_category": package.task_category.value if package.task_category else None},
                            )
                            gw_res = await default_gateway.execute(gw_req)

                            # Store successful strong-model response in cache if eligible
                            if cache_outcome == CacheOutcome.MISS:
                                await default_response_cache.store_response(
                                    package=package,
                                    tier=ModelTier.STRONG,
                                    model_id=gw_res.model_id,
                                    model_version=gw_res.model_version,
                                    provider="strong_model",
                                    content=gw_res.content,
                                    tenant_id=effective_tenant_id,
                                    user_id=effective_user_id,
                                )

                            # Index in semantic cache if eligible
                            if sem_dec.is_eligible:
                                await default_semantic_cache.index_candidate(
                                    entry_id=f"sem-{correlation_id}",
                                    query_text=query,
                                    content=gw_res.content,
                                    package=package,
                                    tier=ModelTier.STRONG,
                                    model_id=gw_res.model_id,
                                    model_version=gw_res.model_version,
                                    tenant_id=effective_tenant_id,
                                    user_id=effective_user_id,
                                )

                            return RouteExecutionMetadata(
                                route=coarse_route.value,
                                model_id=gw_res.model_id,
                                model_version=gw_res.model_version,
                                latency_ms=gw_res.latency_ms,
                                failure_category="NONE",
                                fallback_applied=False,
                                executed_content=gw_res.content,
                                cache_outcome=cache_outcome,
                                cache_key=effective_cache_key,
                                semantic_cache_outcome=sem_outcome,
                                semantic_validation_reason=sem_reason,
                                evaluation_metadata=None,
                            )

                        coalesced = await default_deduplicator.execute_or_join(
                            key=dedup_key,
                            coro_fn=_run_complex_route,
                            timeout_seconds=15.0,
                        )
                        base_meta = coalesced.data
                        elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
                        execution_metadata = base_meta.model_copy(update={
                            "latency_ms": elapsed_ms if not coalesced.is_leader else base_meta.latency_ms,
                            "is_deduplicated": coalesced.is_deduplicated,
                            "deduplication_role": "leader" if coalesced.is_leader else "follower",
                        })
            elif coarse_route == CoarseRoute.NEEDS_EVALUATION:
                # Policy explicitly says evaluation is needed: compare both models
                gw_req = GatewayRequest(
                    prompt=query,
                    correlation_id=correlation_id,
                    metadata={"task_category": package.task_category.value if package.task_category else None},
                )
                comp = await default_gateway.compare_execution(
                    request=gw_req,
                    small_provider="small_model",
                    strong_provider="strong_model",
                    comparison_id=correlation_id,
                )
                max_lat = max(comp.small_response.latency_ms, comp.strong_response.latency_ms)
                comp_dump = comp.model_dump()
                if comp.strong_response.raw_metadata.get("length_guidance"):
                    comp_dump["length_guidance"] = comp.strong_response.raw_metadata["length_guidance"]
                execution_metadata = RouteExecutionMetadata(
                    route=coarse_route.value,
                    model_id=comp.strong_response.model_id,
                    model_version=comp.strong_response.model_version,
                    latency_ms=max_lat,
                    failure_category="NONE",
                    fallback_applied=False,
                    executed_content=comp.strong_response.content,
                    cache_outcome=CacheOutcome.BYPASS,
                    semantic_cache_outcome="SEMANTIC_BYPASS",
                    semantic_validation_reason="EVALUATION_POLICY_REQUIRED",
                    evaluation_metadata=comp_dump,
                )
        except DeduplicationTimeoutError:
            # Safe fallback on deduplication wait timeout
            elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
            decision_type = DecisionType.NO_OPTIMIZATION
            confidence = 0.0
            reason_code = "DEDUPLICATION_TIMEOUT_FALLBACK"
            instructions = None
            execution_metadata = RouteExecutionMetadata(
                route=coarse_route.value,
                latency_ms=elapsed_ms,
                failure_category="TIMEOUT",
                fallback_applied=True,
                is_deduplicated=True,
                deduplication_role="follower",
                semantic_cache_outcome="SEMANTIC_BYPASS",
                semantic_validation_reason="DEDUPLICATION_TIMEOUT_FALLBACK",
            )
        except GatewayTimeoutError:
            # Safe fallback: do not repeatedly retry expensive failures or block user workflow
            elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
            decision_type = DecisionType.NO_OPTIMIZATION
            confidence = 0.0
            reason_code = "GATEWAY_TIMEOUT_FALLBACK"
            instructions = None
            execution_metadata = RouteExecutionMetadata(
                route=coarse_route.value,
                latency_ms=elapsed_ms,
                failure_category="TIMEOUT",
                fallback_applied=True,
                semantic_cache_outcome="SEMANTIC_BYPASS",
                semantic_validation_reason="GATEWAY_TIMEOUT_FALLBACK",
            )
        except (GatewayRetryExhaustedError, GatewayResponseSizeLimitError, GatewayError) as e:
            # Safe fallback: do not block user workflow or repeatedly retry expensive failures
            elapsed_ms = round((time.perf_counter() - exec_start) * 1000.0, 2)
            fail_cat = "RETRY_EXHAUSTED" if isinstance(e, GatewayRetryExhaustedError) else "PROVIDER_ERROR"
            decision_type = DecisionType.NO_OPTIMIZATION
            confidence = 0.0
            reason_code = "GATEWAY_FAILURE_FALLBACK"
            instructions = None
            execution_metadata = RouteExecutionMetadata(
                route=coarse_route.value,
                latency_ms=elapsed_ms,
                failure_category=fail_cat,
                fallback_applied=True,
                semantic_cache_outcome="SEMANTIC_BYPASS",
                semantic_validation_reason="GATEWAY_FAILURE_FALLBACK",
            )

    # -------------------------------------------------------------
    # ML Rollout Telemetry Recording & Automated Rollback Evaluation
    # -------------------------------------------------------------
    try:
        default_rollout_manager.record_query_telemetry(
            cohort=assigned_cohort,
            route=coarse_route.value if coarse_route else "unknown",
            latency_ms=execution_metadata.latency_ms if execution_metadata else 0.0,
            cache_outcome=execution_metadata.cache_outcome.value if (execution_metadata and execution_metadata.cache_outcome) else "NOT_CHECKED",
            is_escalated=bool(escalation_reason),
            escalation_reason=escalation_reason,
            has_error=bool(execution_metadata and execution_metadata.failure_category not in (None, "NONE")),
            failure_category=execution_metadata.failure_category if execution_metadata else "NONE",
            completeness_score=observed_completeness,
            confidence_score=observed_confidence,
            detected_issues=observed_issues,
        )
    except Exception:
        pass

    # -------------------------------------------------------------
    # Production Telemetry & Savings Recording (Strictly Zero Query Content Stored)
    # -------------------------------------------------------------
    try:
        in_toks = (
            query_optimization.original_token_estimate
            if query_optimization and query_optimization.original_token_estimate
            else max(1, len(package.query_text) // 4)
        )
        comp_toks = None
        if query_optimization and query_optimization.is_transformed and query_optimization.optimized_token_estimate:
            comp_toks = query_optimization.optimized_token_estimate
        elif experimental_compression and experimental_compression.is_compressed:
            comp_toks = experimental_compression.compressed_token_estimate

        production_metrics.record_query_execution(
            route=coarse_route.value if coarse_route else "unknown",
            status_code=500 if (execution_metadata and execution_metadata.failure_category not in (None, "NONE")) else 200,
            cohort=assigned_cohort.value if hasattr(assigned_cohort, "value") else str(assigned_cohort),
            latency_ms=execution_metadata.latency_ms if execution_metadata else 0.0,
            cache_outcome=execution_metadata.cache_outcome.value if (execution_metadata and execution_metadata.cache_outcome) else "NOT_CHECKED",
            is_escalated=bool(escalation_reason),
            escalation_reason=escalation_reason,
            has_error=bool(execution_metadata and execution_metadata.failure_category not in (None, "NONE")),
            error_category=execution_metadata.failure_category if execution_metadata else None,
            input_tokens=in_toks,
            output_tokens=250,
            compressed_input_tokens=comp_toks,
            confidence_score=observed_confidence,
            completeness_score=observed_completeness,
        )
    except Exception:
        pass

    # -------------------------------------------------------------
    # ML Router Shadow Mode Evaluation (Zero production interference by default)
    # -------------------------------------------------------------
    try:
        shadow_event = default_shadow_router.evaluate_shadow(
            package=package,
            production_route=rule_coarse_route.value if rule_coarse_route else "complex-model candidate",
            production_tier=rule_model_tier.value if rule_model_tier else "strong",
        )

        # Controlled Activation: Only if project thresholds are satisfied and ML routing is active
        if default_shadow_router.is_active:
            if shadow_event.ml_route in [r.value for r in CoarseRoute]:
                coarse_route = CoarseRoute(shadow_event.ml_route)
            model_tier = default_gateway.resolve_tier_from_route(shadow_event.ml_route)
            confidence = shadow_event.ml_confidence
            reason_code = f"ML_ROUTING_ACTIVE_{shadow_event.ml_decision_gate}"
            if instructions and model_tier:
                instructions.suggested_model = default_gateway.get_model_recommendation(model_tier)
                instructions.notes = f"Routed via activated ML model ({shadow_event.ml_decision_gate})"
    except Exception:
        # Passive shadow evaluation failure MUST NOT block or fail production routing
        pass

    return OptimizationDecisionResponse(
        request_id=package.request_id,
        correlation_id=correlation_id,
        decision_type=decision_type,
        coarse_route=coarse_route,
        task_category=package.task_category,
        complexity_score=package.complexity_score,
        complexity_level=package.complexity_level,
        user_override=package.user_override,
        model_tier=model_tier,
        confidence=confidence,
        reason_code=reason_code,
        optimization_instructions=instructions,
        query_optimization=query_optimization,
        experimental_compression=experimental_compression,
        rich_content=rich_content_handling,
        execution_metadata=execution_metadata,
    )


@app.get("/api/v1/gateway/health", status_code=status.HTTP_200_OK)
async def gateway_health() -> dict:
    """Gateway health probe and active adapter capabilities."""
    return await default_gateway.health_check()


@app.post(
    "/api/v1/gateway/execute",
    response_model=GatewayResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute model operation via decoupled gateway"
)
async def gateway_execute(
    request: GatewayRequest,
    task_category: TaskCategory | None = None,
) -> GatewayResponse:
    """Execute model operation through the active adapter."""
    try:
        return await default_gateway.execute(request, task_category=task_category)
    except GatewayTimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Model gateway timeout: {e}"
        )
    except GatewayRetryExhaustedError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Model gateway retries exhausted: {e}"
        )
    except GatewayResponseSizeLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Model gateway response size limit exceeded: {e}"
        )
    except GatewayError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Model gateway error: {e}"
        )


@app.post(
    "/api/v1/gateway/compare",
    response_model=ModelExecutionComparison,
    status_code=status.HTTP_200_OK,
    summary="Compare execution outcomes and characteristics between small and strong models"
)
async def gateway_compare(
    request: GatewayRequest,
    small_provider: str = "small_model",
    strong_provider: str = "strong_model",
    task_category: TaskCategory | None = None,
) -> ModelExecutionComparison:
    """Executes small and strong models concurrently and returns comparative metrics."""
    try:
        return await default_gateway.compare_execution(
            request=request,
            small_provider=small_provider,
            strong_provider=strong_provider,
            task_category=task_category,
        )
    except GatewayTimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Model comparison timed out: {e}"
        )
    except GatewayError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Model comparison failed: {e}"
        )


@app.get("/api/v1/cache/stats", status_code=status.HTTP_200_OK, summary="Get cache statistics")
async def get_cache_stats() -> dict[str, Any]:
    """Returns operational statistics for the response cache."""
    return await default_response_cache.get_stats()


@app.post("/api/v1/cache/clear", status_code=status.HTTP_200_OK, summary="Clear response cache")
async def clear_cache() -> dict[str, str]:
    """Clears all entries from the response cache."""
    await default_response_cache.clear()
    return {"status": "ok", "message": "Response cache cleared"}


@app.get("/api/v1/dedup/stats", status_code=status.HTTP_200_OK, summary="Get deduplication statistics")
async def get_dedup_stats() -> dict[str, Any]:
    """Returns operational statistics for in-flight request deduplication."""
    return await default_deduplicator.get_stats()


@app.post("/api/v1/dedup/clear", status_code=status.HTTP_200_OK, summary="Clear in-flight deduplicator")
async def clear_dedup() -> dict[str, str]:
    """Cancels all active in-flight requests and resets deduplication state."""
    await default_deduplicator.clear()
    return {"status": "ok", "message": "In-flight deduplicator cleared"}


@app.get("/api/v1/semantic-cache/stats", status_code=status.HTTP_200_OK, summary="Get semantic cache statistics")
async def get_semantic_cache_stats() -> dict[str, Any]:
    """Returns operational statistics for the semantic response cache."""
    return await default_semantic_cache.get_stats()


@app.post("/api/v1/semantic-cache/clear", status_code=status.HTTP_200_OK, summary="Clear semantic response cache")
async def clear_semantic_cache() -> dict[str, str]:
    """Clears all entries from the semantic response cache."""
    await default_semantic_cache.clear()
    return {"status": "ok", "message": "Semantic response cache cleared"}


class ExperimentalCompressRequest(BaseModel):
    """Request payload for evaluating experimental prompt compression."""
    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(..., min_length=1, max_length=100_000)
    features: LocalFeatures | None = None
    enabled: bool | None = None
    environment: str | None = None
    allow_high_sensitivity: bool | None = None


class CompressionABRequest(BaseModel):
    """Request payload for A/B comparison routing between uncompressed and compressed prompts."""
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(..., min_length=1, max_length=100_000)
    tier: ModelTier = ModelTier.FAST_CHEAP
    provider: str | None = None
    enabled: bool | None = None
    environment: str | None = None
    allow_high_sensitivity: bool | None = None
    correlation_id: str | None = None


@app.post(
    "/api/v1/experimental/compress",
    response_model=ExperimentalCompressionResult,
    status_code=status.HTTP_200_OK,
    summary="Evaluate experimental stronger prompt compression"
)
async def evaluate_experimental_compress(req: ExperimentalCompressRequest) -> ExperimentalCompressionResult:
    """Evaluates experimental stronger prompt compression with safety guardrails and production locking."""
    cfg = None
    if req.enabled is not None or req.environment is not None or req.allow_high_sensitivity is not None:
        cfg = ExperimentalCompressionConfig(
            enabled=req.enabled if req.enabled is not None else default_experimental_compressor.config.enabled,
            environment=req.environment if req.environment is not None else default_experimental_compressor.config.environment,
            allow_high_sensitivity=req.allow_high_sensitivity if req.allow_high_sensitivity is not None else False,
        )
    return default_experimental_compressor.compress(
        req.query_text,
        features=req.features,
        override_config=cfg,
    )


@app.post(
    "/api/v1/experimental/compare-ab",
    response_model=CompressionABComparisonResult,
    status_code=status.HTTP_200_OK,
    summary="Execute A/B comparison routing between uncompressed and compressed prompts"
)
async def evaluate_compression_ab_comparison(req: CompressionABRequest) -> CompressionABComparisonResult:
    """Executes concurrent A/B comparison routing between uncompressed Variant A and compressed Variant B."""
    cfg = None
    if req.enabled is not None or req.environment is not None or req.allow_high_sensitivity is not None:
        cfg = ExperimentalCompressionConfig(
            enabled=req.enabled if req.enabled is not None else default_experimental_compressor.config.enabled,
            environment=req.environment if req.environment is not None else default_experimental_compressor.config.environment,
            allow_high_sensitivity=req.allow_high_sensitivity if req.allow_high_sensitivity is not None else False,
        )
    return await compare_ab_compression_routing(
        gateway=default_gateway,
        prompt=req.prompt,
        tier=req.tier,
        provider=req.provider,
        override_config=cfg,
        correlation_id=req.correlation_id,
    )


class FeedbackResponse(BaseModel):
    """Acknowledgement response for ingested feedback."""
    model_config = ConfigDict(extra="forbid")

    received: bool = True
    feedback_id: str
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))


@app.post(
    "/api/v1/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest internal outcome feedback referencing correlation ID and routing metadata"
)
async def ingest_outcome_feedback(
    event: OutcomeFeedbackEvent,
    response: Response,
    x_correlation_id: str | None = Header(None, alias="X-Correlation-ID")
) -> FeedbackResponse:
    """Ingests outcome feedback (completion, user rejection, bypass, escalation, error).
    
    GUARANTEE: Telemetry and quality monitoring only. Never stores or logs raw conversational text.
    Selected high-signal events (rejections, errors, negative ratings) are staged into the
    curation pipeline for human review before any evaluation use.
    """
    corr = event.correlation_id or x_correlation_id or "unknown"
    response.headers["X-Correlation-ID"] = corr

    # Staging hook: if this feedback meets selection criteria, stage it for human review
    if default_dataset_pipeline.should_select_feedback(event):
        default_dataset_pipeline.convert_feedback_event(
            event=event,
            raw_text=None,
            authorized=False,
        )

    return FeedbackResponse(
        received=True,
        feedback_id=event.feedback_id
    )


# -----------------------------------------------------------------------------
# Dataset Curation & Offline Evaluation Endpoints
# -----------------------------------------------------------------------------

@app.post(
    "/api/v1/dataset/candidates/from-feedback",
    response_model=TrainingCandidate | None,
    status_code=status.HTTP_200_OK,
    summary="Stage a candidate from an outcome feedback event"
)
async def stage_candidate_from_feedback(
    req: CandidateIngestFeedbackRequest,
) -> TrainingCandidate | None:
    """Selects, sanitizes, and stages feedback into the candidate pool for human review.
    
    GUARANTEE: Text snippets are discarded unless authorized_for_eval is True.
    """
    return default_dataset_pipeline.convert_feedback_event(
        event=req.event,
        raw_text=req.raw_text,
        authorized=req.authorized_for_eval,
        force_select=False,
    )


@app.post(
    "/api/v1/dataset/candidates/from-escalation",
    response_model=TrainingCandidate | None,
    status_code=status.HTTP_200_OK,
    summary="Stage a candidate from an escalation execution record"
)
async def stage_candidate_from_escalation(
    req: CandidateIngestEscalationRequest,
) -> TrainingCandidate | None:
    """Selects, sanitizes, and stages escalation record into candidate pool for human review."""
    return default_dataset_pipeline.convert_escalation_record(
        correlation_id=req.correlation_id,
        route_metadata=req.route_metadata,
        query_text=req.query_text,
        task_category=req.task_category,
        authorized=req.authorized_for_eval,
        force_select=False,
    )


@app.get(
    "/api/v1/dataset/candidates",
    response_model=list[TrainingCandidate],
    summary="List candidates staged in the curation pipeline"
)
async def list_candidates(
    review_status: ReviewStatus | None = Query(None, alias="status"),
    source_type: CandidateSourceType | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[TrainingCandidate]:
    """Lists candidate records with optional status and source filtering."""
    return default_candidate_repository.list_candidates(
        status=review_status,
        source_type=source_type,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/v1/dataset/candidates/{candidate_id}",
    response_model=TrainingCandidate,
    summary="Get candidate details by ID"
)
async def get_candidate(candidate_id: str) -> TrainingCandidate:
    """Retrieves candidate record by ID."""
    candidate = default_candidate_repository.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate '{candidate_id}' not found"
        )
    return candidate


@app.post(
    "/api/v1/dataset/candidates/{candidate_id}/review",
    response_model=TrainingCandidate,
    summary="Apply human review decision to approve or reject candidate"
)
async def review_candidate(
    candidate_id: str,
    decision: ReviewDecisionRequest,
) -> TrainingCandidate:
    """Manual review boundary: Approves or rejects a staged candidate.
    
    Rejections allow categorizing items as PRIVACY_RISK, LOW_QUALITY, etc.
    Only approved candidates can be exported into training or evaluation sets.
    """
    try:
        return default_candidate_repository.review_candidate(candidate_id, decision)
    except CandidateNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate '{candidate_id}' not found"
        )
    except InvalidReviewStateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@app.get(
    "/api/v1/dataset/export",
    summary="Export approved dataset records for authorized offline evaluation"
)
async def export_approved_dataset(
    split: DatasetSplit | None = Query(None),
    format: str = Query("jsonl", pattern="^(jsonl|json)$"),
) -> Response:
    """Exports ONLY candidates that have passed human review (review_status=APPROVED).
    
    GUARANTEES:
    1. Strictly excludes rejected and pending review items.
    2. Decoupled from online training: never triggers automated model training.
    """
    import json
    exported = default_candidate_repository.export_approved(split=split, format=format)
    if format == "json":
        return Response(
            content=json.dumps(exported),
            media_type="application/json"
        )
    return Response(
        content=str(exported),
        media_type="application/x-ndjson"
    )


@app.get(
    "/api/v1/dataset/stats",
    response_model=DatasetStatsResponse,
    summary="Get candidate curation pipeline statistics"
)
async def get_dataset_stats() -> DatasetStatsResponse:
    """Returns candidate counts across review statuses, sources, and dataset splits."""
    return default_candidate_repository.get_stats()


# -----------------------------------------------------------------------------
# Benchmark Baseline Evaluation Endpoints
# -----------------------------------------------------------------------------

_latest_baseline_report: StrongModelBaselineReport | None = None


class BaselineEvaluationRequest(BaseModel):
    """Request payload for triggering a stronger model baseline evaluation."""
    model_config = ConfigDict(extra="forbid")

    dataset_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to benchmark dataset (.json or .jsonl). Defaults to canonical benchmark."
    )
    concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Maximum concurrent executions on the stronger model"
    )
    save_to_disk: bool = Field(
        default=False,
        description="Whether to persist the generated baseline report to disk"
    )
    output_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional output file path if save_to_disk is true"
    )


@app.post(
    "/api/v1/benchmark/baseline/evaluate",
    response_model=StrongModelBaselineReport,
    status_code=status.HTTP_200_OK,
    summary="Execute benchmark queries against the stronger model to establish a stable reference baseline"
)
async def evaluate_strong_model_baseline(
    request: BaselineEvaluationRequest | None = None,
) -> StrongModelBaselineReport:
    """Sends every benchmark query to the stronger model and records latency, tokens, and answer-quality reference.
    
    Strictly decoupled from router logic to establish an unadulterated baseline for future experiments.
    """
    global _latest_baseline_report

    req = request or BaselineEvaluationRequest()

    if req.dataset_path:
        target_path = Path(req.dataset_path)
    else:
        target_path = Path(__file__).parent / "dataset" / "canonical_benchmark.json"

    if not target_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Benchmark dataset file not found: {target_path}",
        )

    try:
        dataset = load_benchmark_dataset(target_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to load benchmark dataset: {exc}",
        )

    runner = StrongModelBaselineRunner(gateway=default_gateway, max_concurrency=req.concurrency)
    report = await runner.run_baseline_evaluation(dataset, concurrency=req.concurrency)

    _latest_baseline_report = report

    if req.save_to_disk:
        out = Path(req.output_path) if req.output_path else (target_path.parent / f"{report.run_id}.json")
        save_baseline_report(report, out)

    return report


@app.get(
    "/api/v1/benchmark/baseline/latest",
    response_model=StrongModelBaselineReport,
    summary="Retrieve the most recent strong-model baseline evaluation report"
)
async def get_latest_baseline_report() -> StrongModelBaselineReport:
    """Returns the latest in-memory evaluated baseline report, or 404 if none has been run."""
    global _latest_baseline_report
    if _latest_baseline_report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No baseline evaluation report has been generated in this session. Run POST /api/v1/benchmark/baseline/evaluate first.",
        )
    return _latest_baseline_report


# -----------------------------------------------------------------------------
# Smart-Routing Benchmark Evaluation Endpoints
# -----------------------------------------------------------------------------

_latest_smart_routing_report: SmartRoutingEvaluationReport | None = None
_latest_comparative_report: ComparativeEvaluationReport | None = None


class SmartRoutingEvaluationRequest(BaseModel):
    """Request payload for evaluating benchmark queries through the smart-routing pipeline."""
    model_config = ConfigDict(extra="forbid")

    dataset_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to benchmark dataset (.json or .jsonl). Defaults to canonical benchmark."
    )
    concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Maximum concurrent executions through the router"
    )
    compare_with_baseline: bool = Field(
        default=True,
        description="Whether to compare outputs against the latest strong-model baseline for lexical overlap"
    )
    pricing_config: PricingConfig | None = Field(
        default=None,
        description="Optional custom token pricing and proxy cost configuration"
    )
    save_to_disk: bool = Field(
        default=False,
        description="Whether to persist the generated report to disk"
    )
    output_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional output file path if save_to_disk is true"
    )


@app.post(
    "/api/v1/benchmark/smart-routing/evaluate",
    response_model=SmartRoutingEvaluationReport,
    status_code=status.HTTP_200_OK,
    summary="Execute benchmark queries through the full smart-routing pipeline"
)
async def evaluate_smart_routing_benchmark(
    request: SmartRoutingEvaluationRequest | None = None,
) -> SmartRoutingEvaluationReport:
    """Runs the benchmark through the full smart-routing pipeline and captures routing distributions.
    
    Captures:
    - Local-handled percentage
    - Cache hit rate
    - Small-model percentage
    - Strong-model percentage
    - Escalation rate
    - Token estimates
    - Latency (mean, p50, p95)
    - Error rate and quality measures
    - Disaggregated comparative analysis (if baseline report is available)
    """
    global _latest_smart_routing_report, _latest_comparative_report

    req = request or SmartRoutingEvaluationRequest()

    if req.dataset_path:
        target_path = Path(req.dataset_path)
    else:
        target_path = Path(__file__).parent / "dataset" / "canonical_benchmark.json"

    if not target_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Benchmark dataset file not found: {target_path}",
        )

    try:
        dataset = load_benchmark_dataset(target_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to load benchmark dataset: {exc}",
        )

    base_report = _latest_baseline_report if req.compare_with_baseline else None
    runner = SmartRoutingBenchmarkRunner(concurrency=req.concurrency)
    report = await runner.run_smart_routing_evaluation(
        dataset=dataset,
        baseline_report=base_report,
        concurrency=req.concurrency,
        pricing_config=req.pricing_config,
    )

    _latest_smart_routing_report = report
    if report.comparative_analysis:
        _latest_comparative_report = report.comparative_analysis

    if req.save_to_disk:
        out = Path(req.output_path) if req.output_path else (target_path.parent / f"{report.run_id}.json")
        save_smart_routing_report(report, out)

    return report


@app.get(
    "/api/v1/benchmark/smart-routing/latest",
    response_model=SmartRoutingEvaluationReport,
    summary="Retrieve the most recent smart-routing evaluation report"
)
async def get_latest_smart_routing_report() -> SmartRoutingEvaluationReport:
    """Returns the latest evaluated smart-routing report, or 404 if none has been run."""
    global _latest_smart_routing_report
    if _latest_smart_routing_report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No smart-routing evaluation report has been generated in this session. Run POST /api/v1/benchmark/smart-routing/evaluate first.",
        )
    return _latest_smart_routing_report


# -----------------------------------------------------------------------------
# Disaggregated Comparative Benchmark Evaluation Endpoints
# -----------------------------------------------------------------------------

class ComparativeEvaluationRequest(BaseModel):
    """Request payload for comparing baseline and smart-routing reports."""
    model_config = ConfigDict(extra="forbid")

    baseline_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to baseline report JSON file"
    )
    smart_routing_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to smart routing report JSON file"
    )
    pricing_config: PricingConfig | None = Field(
        default=None,
        description="Optional custom token pricing and proxy cost configuration"
    )
    save_to_disk: bool = Field(
        default=False,
        description="Whether to persist the generated comparative report to disk"
    )
    output_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional output file path if save_to_disk is true"
    )


@app.post(
    "/api/v1/benchmark/compare",
    response_model=ComparativeEvaluationReport,
    status_code=status.HTTP_200_OK,
    summary="Compute disaggregated savings and quality deltas relative to baseline"
)
async def compare_benchmark_evaluations(
    request: ComparativeEvaluationRequest | None = None,
) -> ComparativeEvaluationReport:
    """Calculates disaggregated token savings, dollar savings, avoided calls, and quality deltas.
    
    Guarantees:
    - Every savings number includes its explicit denominator and formula.
    - Token pricing is explicit and configurable via PricingConfig.
    - Avoided calls and quality changes are disaggregated into discrete dimensions.
    """
    global _latest_baseline_report, _latest_smart_routing_report, _latest_comparative_report
    req = request or ComparativeEvaluationRequest()

    base_rep: StrongModelBaselineReport | None = None
    if req.baseline_path:
        p = Path(req.baseline_path)
        if not p.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Baseline report file not found: {p}",
            )
        try:
            base_rep = load_baseline_report(p)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to load baseline report: {exc}",
            )
    else:
        base_rep = _latest_baseline_report

    sr_rep: SmartRoutingEvaluationReport | None = None
    if req.smart_routing_path:
        p = Path(req.smart_routing_path)
        if not p.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Smart routing report file not found: {p}",
            )
        try:
            sr_rep = load_smart_routing_report(p)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to load smart-routing report: {exc}",
            )
    else:
        sr_rep = _latest_smart_routing_report

    if base_rep is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No baseline report available. Provide baseline_path or run POST /api/v1/benchmark/baseline/evaluate first.",
        )
    if sr_rep is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No smart-routing report available. Provide smart_routing_path or run POST /api/v1/benchmark/smart-routing/evaluate first.",
        )

    evaluator = ComparativeBenchmarkEvaluator(default_pricing=req.pricing_config)
    comp_report = evaluator.compare(
        baseline_report=base_rep,
        smart_routing_report=sr_rep,
        pricing=req.pricing_config,
    )

    _latest_comparative_report = comp_report

    if req.save_to_disk:
        out = Path(req.output_path) if req.output_path else (Path(__file__).parent / "dataset" / f"{comp_report.run_id}.json")
        save_comparative_report(comp_report, out)

    return comp_report


@app.get(
    "/api/v1/benchmark/compare/latest",
    response_model=ComparativeEvaluationReport,
    summary="Retrieve the most recent comparative evaluation report"
)
async def get_latest_comparative_report() -> ComparativeEvaluationReport:
    """Returns the latest evaluated comparative report, or 404 if none has been run."""
    global _latest_comparative_report
    if _latest_comparative_report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No comparative evaluation report has been generated in this session. Run POST /api/v1/benchmark/compare first.",
        )
    return _latest_comparative_report


# -----------------------------------------------------------------------------
# Router Failure Analysis & Misrouting Breakdown Endpoints
# -----------------------------------------------------------------------------

_latest_failure_analysis_report: RouterFailureAnalysisReport | None = None


class FailureAnalysisRequest(BaseModel):
    """Request payload for diagnosing router choices across categories and context."""
    model_config = ConfigDict(extra="forbid")

    smart_routing_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to smart routing report JSON file"
    )
    dataset_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to benchmark dataset JSON file"
    )
    pricing_config: PricingConfig | None = Field(
        default=None,
        description="Optional pricing parameters for estimating misrouting dollar impact"
    )
    save_to_disk: bool = Field(
        default=False,
        description="Whether to persist the generated failure analysis report to disk"
    )
    output_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional output file path if save_to_disk is true"
    )


@app.post(
    "/api/v1/benchmark/failures/analyze",
    response_model=RouterFailureAnalysisReport,
    status_code=status.HTTP_200_OK,
    summary="Identify failure classes: queries routed too cheaply, too expensively, or missed local/cache"
)
async def analyze_router_failures(
    request: FailureAnalysisRequest | None = None,
) -> RouterFailureAnalysisReport:
    """Isolates and diagnoses router misclassifications.
    
    Identifies:
    - Queries routed too cheaply (risky down-routing)
    - Queries routed too expensively (over-routing cost inefficiency)
    - Missed local handling (greetings, arithmetic dispatched to cloud)
    - Missed cache opportunities
    - Unexpected escalations
    - Disaggregated breakdown by task category and context dependency
    """
    global _latest_smart_routing_report, _latest_failure_analysis_report
    req = request or FailureAnalysisRequest()

    sr_rep: SmartRoutingEvaluationReport | None = None
    if req.smart_routing_path:
        p = Path(req.smart_routing_path)
        if not p.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Smart-routing report file not found: {p}",
            )
        try:
            sr_rep = load_smart_routing_report(p)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to load smart-routing report: {exc}",
            )
    else:
        sr_rep = _latest_smart_routing_report

    if sr_rep is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No smart-routing report available. Provide smart_routing_path or run POST /api/v1/benchmark/smart-routing/evaluate first.",
        )

    dataset: BenchmarkDataset | None = None
    if req.dataset_path:
        dp = Path(req.dataset_path)
        if dp.exists():
            try:
                dataset = load_benchmark_dataset(dp)
            except Exception:
                pass
    else:
        canonical_p = Path(__file__).parent / "dataset" / "canonical_benchmark.json"
        if canonical_p.exists():
            try:
                dataset = load_benchmark_dataset(canonical_p)
            except Exception:
                pass

    analyzer = RouterFailureAnalyzer(pricing=req.pricing_config)
    fail_report = analyzer.analyze(
        report=sr_rep,
        dataset=dataset,
        pricing=req.pricing_config,
    )

    _latest_failure_analysis_report = fail_report

    if req.save_to_disk:
        out = Path(req.output_path) if req.output_path else (Path(__file__).parent / "dataset" / f"{fail_report.run_id}.json")
        save_failure_report(fail_report, out)

    return fail_report


@app.get(
    "/api/v1/benchmark/failures/latest",
    response_model=RouterFailureAnalysisReport,
    summary="Retrieve the most recent router failure analysis report"
)
async def get_latest_failure_report() -> RouterFailureAnalysisReport:
    """Returns the latest router failure analysis report, or 404 if none has been run."""
    global _latest_failure_analysis_report
    if _latest_failure_analysis_report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No failure analysis report has been generated in this session. Run POST /api/v1/benchmark/failures/analyze first.",
        )
    return _latest_failure_analysis_report


# -----------------------------------------------------------------------------
# ML Feature Dataset Generation Endpoints
# -----------------------------------------------------------------------------

_latest_ml_feature_dataset: MLFeatureDataset | None = None


class MLDatasetGenerateRequest(BaseModel):
    """Request payload for generating an offline ML routing feature dataset."""
    model_config = ConfigDict(extra="forbid")

    dataset_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to benchmark dataset JSON file"
    )
    baseline_report_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to precomputed strong baseline report JSON"
    )
    smart_routing_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to precomputed smart-routing report JSON"
    )
    include_raw_query: bool = Field(
        default=False,
        description="Whether to retain sanitized query text for research-authorized items"
    )
    pricing_config: PricingConfig | None = Field(
        default=None,
        description="Optional pricing parameters for evaluating token cost deltas"
    )
    export_format: str = Field(
        default="json",
        description="Serialization format: 'json', 'jsonl', or 'csv'"
    )
    save_to_disk: bool = Field(
        default=False,
        description="Whether to persist the generated ML dataset to disk"
    )
    output_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional output file path if save_to_disk is true"
    )


@app.post(
    "/api/v1/benchmark/ml-dataset/generate",
    response_model=MLFeatureDataset,
    status_code=status.HTTP_200_OK,
    summary="Generate offline feature dataset for ML routing experiments"
)
async def generate_ml_feature_dataset(
    request: MLDatasetGenerateRequest | None = None,
) -> MLFeatureDataset:
    """Generates an offline tabular feature dataset for ML routing model training.
    
    Captures:
    - Local non-generative signals (char/word count, estimated tokens, code/math/table cues, ratios)
    - Task category (13 canonical categories) and complexity label
    - Context dependency signals (turns count, history chars, conversational cues)
    - Observed routing outcome (route, tier, decision type, cache hit, escalation, tokens, latency)
    - Quality deltas relative to strong baseline (verdict, format delta, lexical overlap, cost savings)
    - Target optimal routing label (local-eligible, simple-model candidate, complex-model candidate)
    
    Guarantees:
    - Offline isolation: Online production inference is completely decoupled.
    - Privacy protection: Private user content scrubbed unless explicitly authorized and redacted.
    """
    global _latest_baseline_report, _latest_smart_routing_report, _latest_comparative_report, _latest_ml_feature_dataset
    req = request or MLDatasetGenerateRequest()

    # Load dataset
    dataset: BenchmarkDataset
    if req.dataset_path:
        p = Path(req.dataset_path)
        if not p.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Benchmark dataset file not found: {p}",
            )
        try:
            dataset = load_benchmark_dataset(p)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to load benchmark dataset: {exc}",
            )
    else:
        canonical_p = Path(__file__).parent / "dataset" / "canonical_benchmark.json"
        if not canonical_p.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Canonical benchmark dataset not found: {canonical_p}",
            )
        dataset = load_benchmark_dataset(canonical_p)

    # Resolve or execute baseline report
    base_rep: StrongModelBaselineReport | None = None
    if req.baseline_report_path:
        bp = Path(req.baseline_report_path)
        if not bp.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Baseline report file not found: {bp}",
            )
        base_rep = load_baseline_report(bp)
    elif _latest_baseline_report is not None:
        base_rep = _latest_baseline_report
    else:
        base_runner = StrongModelBaselineRunner()
        base_rep = await base_runner.run_baseline_evaluation(dataset)
        _latest_baseline_report = base_rep

    # Resolve or execute smart-routing report
    sr_rep: SmartRoutingEvaluationReport | None = None
    if req.smart_routing_path:
        sp = Path(req.smart_routing_path)
        if not sp.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Smart-routing report file not found: {sp}",
            )
        sr_rep = load_smart_routing_report(sp)
    elif _latest_smart_routing_report is not None:
        sr_rep = _latest_smart_routing_report
    else:
        sr_runner = SmartRoutingBenchmarkRunner()
        sr_rep = await sr_runner.run_smart_routing_evaluation(dataset)
        _latest_smart_routing_report = sr_rep

    generator = MLFeatureDatasetGenerator(pricing=req.pricing_config)
    ml_dataset = generator.generate_from_reports(
        dataset=dataset,
        baseline_report=base_rep,
        smart_routing_report=sr_rep,
        comparative_report=_latest_comparative_report,
        include_raw_query=req.include_raw_query,
    )

    _latest_ml_feature_dataset = ml_dataset

    if req.save_to_disk:
        out = Path(req.output_path) if req.output_path else (Path(__file__).parent / "dataset" / f"{ml_dataset.dataset_id}.{req.export_format.lower()}")
        fmt = req.export_format.lower()
        if fmt == "csv":
            save_ml_dataset_csv(ml_dataset, out)
        elif fmt == "jsonl":
            save_ml_dataset_jsonl(ml_dataset, out)
        else:
            save_ml_dataset_json(ml_dataset, out)

    return ml_dataset


@app.get(
    "/api/v1/benchmark/ml-dataset/latest",
    response_model=MLFeatureDataset,
    summary="Retrieve the most recent generated ML routing feature dataset"
)
async def get_latest_ml_feature_dataset() -> MLFeatureDataset:
    """Returns the latest generated ML routing feature dataset, or 404 if none has been generated."""
    global _latest_ml_feature_dataset
    if _latest_ml_feature_dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No ML feature dataset has been generated in this session. Run POST /api/v1/benchmark/ml-dataset/generate first.",
        )
    return _latest_ml_feature_dataset


# -----------------------------------------------------------------------------
# Offline ML Router Classification Endpoints
# -----------------------------------------------------------------------------

_trained_router_classifier: MLRouterClassifier | None = None


class MLModelTrainRequest(BaseModel):
    """Request parameters for training the offline ML routing classifier."""
    model_config = ConfigDict(extra="forbid")

    dataset_path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional path to ML feature dataset JSON file. Defaults to canonical dataset."
    )
    hyperparameters: dict[str, Any] | None = Field(
        default=None,
        description="Optional hyperparameter overrides for Logistic Regression"
    )
    pricing_config: PricingConfig | None = Field(
        default=None,
        description="Optional pricing parameters for cost/quality tradeoff simulation"
    )
    save_to_disk: bool = Field(
        default=True,
        description="Whether to persist the model binary and metadata JSON to disk"
    )
    output_dir: str | None = Field(
        default=None,
        max_length=512,
        description="Optional output directory for saving artifacts (defaults to backend/app/models)"
    )


@app.post(
    "/api/v1/models/router-classifier/train",
    response_model=ModelTrainingMetadata,
    status_code=status.HTTP_200_OK,
    summary="Train lightweight ML classification router and compare against rule baseline"
)
async def train_ml_router_classifier(
    request: MLModelTrainRequest | None = None,
) -> ModelTrainingMetadata:
    """Trains a lightweight Multinomial Logistic Regression router.
    
    Evaluates:
    - Precision, Recall, Macro F1, and 3x3 Confusion Matrix
    - Leave-One-Out Cross-Validation
    - Direct head-to-head comparison against the Deterministic Rule Baseline
    - Cost and quality tradeoff simulation
    - Saves reproducible model (.joblib) and metadata (.json)
    
    Safety Guardrail:
    - Does NOT replace the production router (/api/v1/optimize remains rule-based).
    """
    global _trained_router_classifier
    req = request or MLModelTrainRequest()

    # Resolve dataset
    dataset_p = (
        Path(req.dataset_path)
        if req.dataset_path
        else (Path(__file__).parent / "dataset" / "canonical_ml_features_v1.json")
    )

    if not dataset_p.exists():
        # Fallback to generating from canonical benchmark
        gen = MLFeatureDatasetGenerator(pricing=req.pricing_config)
        dataset = await gen.generate_from_benchmark(
            save_to_disk=True,
            output_path=dataset_p,
            export_format="json",
        )
    else:
        dataset = load_ml_dataset_json(dataset_p)

    classifier = MLRouterClassifier(
        hyperparameters=req.hyperparameters,
        pricing=req.pricing_config,
    )
    _, metadata = classifier.train(dataset, pricing=req.pricing_config)
    _trained_router_classifier = classifier

    if req.save_to_disk:
        out_dir = Path(req.output_dir) if req.output_dir else (Path(__file__).parent / "models")
        classifier.save_artifacts(out_dir)

    return metadata


@app.get(
    "/api/v1/models/router-classifier/metadata",
    response_model=ModelTrainingMetadata,
    summary="Retrieve the latest reproducible training metadata for the ML router"
)
async def get_router_classifier_metadata() -> ModelTrainingMetadata:
    """Retrieves the latest training metadata, metrics, and baseline comparisons."""
    global _trained_router_classifier
    if _trained_router_classifier is not None and _trained_router_classifier.metadata is not None:
        return _trained_router_classifier.metadata

    # Attempt to load from disk
    models_dir = Path(__file__).parent / "models"
    meta_path = models_dir / "router_classifier_v1_metadata.json"
    if meta_path.exists():
        try:
            _trained_router_classifier = MLRouterClassifier.load_artifacts(models_dir)
            if _trained_router_classifier.metadata:
                return _trained_router_classifier.metadata
        except Exception:
            pass

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No trained ML router metadata available. Run POST /api/v1/models/router-classifier/train first.",
    )


@app.post(
    "/api/v1/models/router-classifier/predict",
    response_model=MLPredictionResponse,
    status_code=status.HTTP_200_OK,
    summary="Offline inference probe using the lightweight ML routing classifier"
)
async def predict_ml_route(
    request: MLPredictionRequest,
) -> MLPredictionResponse:
    """Scores an incoming query package using the lightweight ML router classifier.
    
    Returns predicted route, recommended tier, confidence score, and class probabilities.
    NOTE: For offline evaluation only; production routing remains on /api/v1/optimize.
    """
    global _trained_router_classifier
    if _trained_router_classifier is None or _trained_router_classifier.pipeline is None:
        models_dir = Path(__file__).parent / "models"
        if (models_dir / "router_classifier_v1.joblib").exists():
            _trained_router_classifier = MLRouterClassifier.load_artifacts(models_dir)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ML router model is not trained or loaded. Run POST /api/v1/models/router-classifier/train first.",
            )

    route, tier, conf, probs = _trained_router_classifier.predict(
        local_signals=request.local_signals,
        task_type=request.task_type,
        complexity_label=request.complexity_label,
        context_signals=request.context_signals,
    )

    return MLPredictionResponse(
        predicted_route=route,
        recommended_tier=tier,
        confidence=conf,
        class_probabilities=probs,
        model_id=_trained_router_classifier.metadata.model_id if _trained_router_classifier.metadata else "router_classifier_v1",
        model_version=_trained_router_classifier.metadata.version if _trained_router_classifier.metadata else "1.0.0",
    )


@app.post(
    "/api/v1/models/router-classifier/compare-embeddings",
    response_model=ComparativeEmbeddingTrainingReport,
    status_code=status.HTTP_200_OK,
    summary="Train and compare router classifier performance with vs without sentence embeddings"
)
async def compare_embeddings_router(
    request: MLCompareEmbeddingsRequest | None = None,
) -> ComparativeEmbeddingTrainingReport:
    """Trains Model A (without embeddings) and Model B (with embeddings) on identical data.

    Returns:
    - Side-by-side Accuracy, Macro F1, LOOCV metrics, and per-class metrics
    - Added latency breakdown (embedding generation vs inference overhead)
    - Hardware compute resource cost vs LLM token cost savings ROI calculation
    - Cost justification verdict proving the router does not spend more than it saves
    """
    global _trained_router_classifier
    req = request or MLCompareEmbeddingsRequest()

    dataset_p = (
        Path(req.dataset_path)
        if req.dataset_path
        else (Path(__file__).parent / "dataset" / "canonical_ml_features_v1.json")
    )

    if not dataset_p.exists():
        gen = MLFeatureDatasetGenerator(pricing=req.pricing_config)
        dataset = await gen.generate_from_benchmark(
            save_to_disk=True,
            output_path=dataset_p,
            export_format="json",
        )
    else:
        dataset = load_ml_dataset_json(dataset_p)

    classifier = MLRouterClassifier(
        hyperparameters=req.hyperparameters,
        pricing=req.pricing_config,
    )

    _, report = classifier.train_comparative(
        dataset=dataset,
        embedding_dimension=req.embedding_dimension,
        pricing=req.pricing_config,
        latency_budget_ms=req.latency_budget_ms,
        benchmark_runs=req.benchmark_runs,
    )
    _trained_router_classifier = classifier

    if req.save_to_disk:
        out_dir = Path(req.output_dir) if req.output_dir else (Path(__file__).parent / "models")
        classifier.save_artifacts(out_dir)

    return report


@app.post(
    "/api/v1/models/router-classifier/predict-guarded",
    response_model=GuardedRoutingDecision,
    status_code=status.HTTP_200_OK,
    summary="Guarded query routing inference enforcing deterministic safety and context primacy"
)
async def predict_guarded_route(
    request: GuardedPredictionRequest,
) -> GuardedRoutingDecision:
    """Evaluates an incoming query through the 3-gate guarded routing pipeline:

    1. Gate 1: Deterministic Safety (greetings, arithmetic) -> on-device local resolution
    2. Gate 2: Explicit Context Handling (multi-turn conversation) -> context-aware strong tier
    3. Gate 3: Auxiliary ML Classification -> predicts optimal tier using sentence embeddings
    """
    global _trained_router_classifier
    if _trained_router_classifier is None or _trained_router_classifier.pipeline is None:
        models_dir = Path(__file__).parent / "models"
        if (models_dir / "router_classifier_v1.joblib").exists():
            _trained_router_classifier = MLRouterClassifier.load_artifacts(models_dir)
        else:
            _trained_router_classifier = MLRouterClassifier()

    return _trained_router_classifier.predict_guarded(
        query_text=request.query_text,
        task_type=request.task_type,
        complexity_label=request.complexity_label,
        has_context_dependency=request.has_context_dependency,
        prior_conversation_turns=request.prior_conversation_turns,
        local_signals=request.local_signals,
        context_signals=request.context_signals,
    )


@app.get(
    "/api/v1/models/router-classifier/shadow-mode/report",
    response_model=ShadowEvaluationStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Retrieve shadow routing evaluation report, disagreements, savings, and threshold checklist"
)
async def get_shadow_mode_report(
    min_sample_size: int | None = Query(default=None, ge=1),
    min_agreement_rate: float | None = Query(default=None, ge=0.0, le=100.0),
    max_quality_risk: float | None = Query(default=None, ge=0.0, le=100.0),
) -> ShadowEvaluationStatusReport:
    """Retrieves observed shadow routing analytics, disagreements, expected savings, and threshold status."""
    custom_th = None
    if min_sample_size is not None or min_agreement_rate is not None or max_quality_risk is not None:
        custom_th = ShadowActivationThresholds(
            min_sample_size=min_sample_size if min_sample_size is not None else default_shadow_router.default_thresholds.min_sample_size,
            min_agreement_rate_pct=min_agreement_rate if min_agreement_rate is not None else default_shadow_router.default_thresholds.min_agreement_rate_pct,
            max_quality_risk_pct=max_quality_risk if max_quality_risk is not None else default_shadow_router.default_thresholds.max_quality_risk_pct,
        )
    return default_shadow_router.get_status_report(thresholds=custom_th)


@app.post(
    "/api/v1/models/router-classifier/shadow-mode/activate",
    response_model=ShadowEvaluationStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Activate ML routing if project evidence and threshold criteria are satisfied"
)
async def activate_ml_routing(
    request: ShadowActivationRequest | None = None,
) -> ShadowEvaluationStatusReport:
    """Evaluates activation threshold checklist and enables ML routing if evidence criteria are met."""
    req = request or ShadowActivationRequest()
    try:
        return default_shadow_router.try_activate(
            force=req.force_activation,
            justification=req.bypass_justification,
            thresholds=req.custom_thresholds,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@app.post(
    "/api/v1/models/router-classifier/shadow-mode/deactivate",
    response_model=ShadowEvaluationStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Deactivate ML routing and return to passive shadow evaluation mode"
)
async def deactivate_ml_routing() -> ShadowEvaluationStatusReport:
    """Disables ML routing in production and restores authoritative rule router with shadow tracking."""
    return default_shadow_router.deactivate()


@app.post(
    "/api/v1/models/router-classifier/shadow-mode/simulate-batch",
    response_model=ShadowEvaluationStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Simulate a batch of benchmark queries through shadow routing"
)
async def simulate_shadow_batch(
    request: ShadowBatchSimulateRequest | None = None,
) -> ShadowEvaluationStatusReport:
    """Loads benchmark queries and simulates them through shadow evaluation to build evidence."""
    req = request or ShadowBatchSimulateRequest()

    dataset_path = (
        Path(req.dataset_path)
        if req.dataset_path
        else (Path(__file__).parent / "dataset" / "canonical_benchmark.json")
    )

    if not dataset_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Benchmark dataset not found at: {dataset_path}",
        )

    b_data = load_benchmark_dataset(dataset_path)

    for _ in range(req.iterations):
        for item in b_data.items:
            # Map item task type to contract TaskCategory if possible
            t_cat: TaskCategory | None = None
            try:
                t_cat = TaskCategory(item.task_type.value)
            except Exception:
                pass

            comp_lvl: ComplexityLevel | None = None
            try:
                if item.complexity_label:
                    comp_lvl = ComplexityLevel(item.complexity_label.value)
            except Exception:
                pass

            pkg = NormalizedQueryPackage(
                request_id=f"sim_{item.id}_{int(time.time() * 1000)}",
                query_text=item.query,
                task_category=t_cat,
                complexity_level=comp_lvl,
                execute_route=False,
                client_metadata=ClientMetadata(
                    extension_version="0.1.0",
                    client_type="benchmark_simulator",
                ),
            )

            # Route through evaluate_optimization
            resp_dummy = Response()
            await evaluate_optimization(
                package=pkg,
                response=resp_dummy,
                x_correlation_id=pkg.request_id,
                x_user_id="benchmark_sim",
                x_tenant_id="benchmark_sim",
            )

    return default_shadow_router.get_status_report()


# =========================================================================
# ML Router Limited Rollout, Kill Switch & Rollback Endpoints
# =========================================================================

@app.get(
    "/api/v1/router/rollout/status",
    response_model=RolloutStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Retrieve rollout status, multi-dimensional cohort metrics, and rollback state"
)
async def get_rollout_status() -> RolloutStatusReport:
    """Returns real-time telemetry across all 6 monitoring dimensions and automated rollback trigger status."""
    return default_rollout_manager.get_status_report()


@app.post(
    "/api/v1/router/rollout/configure",
    response_model=RolloutStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Update rollout configuration, canary percentage, allow/denylists, or thresholds"
)
async def configure_rollout(request: RolloutConfigureRequest) -> RolloutStatusReport:
    """Configures canary rollout percentage, consistent hashing rules, or rollback trigger thresholds."""
    return default_rollout_manager.update_config(request)


@app.post(
    "/api/v1/router/rollout/kill-switch",
    response_model=RolloutStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Engage or disengage the global emergency kill switch"
)
async def toggle_kill_switch(request: KillSwitchRequest) -> RolloutStatusReport:
    """Immediately diverts 100% of traffic back to the deterministic router when engaged."""
    return default_rollout_manager.set_kill_switch(engage=request.engage, reason=request.reason)


@app.post(
    "/api/v1/router/rollout/reset-rollback",
    response_model=RolloutStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Reset an automated rollback after operator inspection"
)
async def reset_rollback(request: RollbackResetRequest) -> RolloutStatusReport:
    """Resets the automated rollback status and cautiously restores canary traffic."""
    return default_rollout_manager.reset_rollback(
        justification=request.justification,
        restore_percentage=request.restore_rollout_percentage,
    )


@app.post(
    "/api/v1/router/rollout/simulate",
    response_model=RolloutStatusReport,
    status_code=status.HTTP_200_OK,
    summary="Simulate a batch of benchmark queries across canary rollout cohorts"
)
async def simulate_rollout_batch(
    request: RolloutSimulationRequest | None = None,
) -> RolloutStatusReport:
    """Loads benchmark queries and processes them through the full rollout pipeline."""
    req = request or RolloutSimulationRequest()

    dataset_path = (
        Path(req.dataset_path)
        if req.dataset_path
        else (Path(__file__).parent / "dataset" / "canonical_benchmark.json")
    )

    if not dataset_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Benchmark dataset not found at: {dataset_path}",
        )

    b_data = load_benchmark_dataset(dataset_path)

    for _ in range(req.iterations):
        for item in b_data.items:
            t_cat: TaskCategory | None = None
            try:
                t_cat = TaskCategory(item.task_type.value)
            except Exception:
                pass

            comp_lvl: ComplexityLevel | None = None
            try:
                if item.complexity_label:
                    comp_lvl = ComplexityLevel(item.complexity_label.value)
            except Exception:
                pass

            pkg = NormalizedQueryPackage(
                request_id=f"rollout_sim_{item.id}_{int(time.time() * 1000)}",
                query_text=item.query,
                task_category=t_cat,
                complexity_level=comp_lvl,
                execute_route=False,
                client_metadata=ClientMetadata(
                    extension_version="0.1.0",
                    client_type="rollout_simulator",
                ),
            )

            resp_dummy = Response()
            await evaluate_optimization(
                package=pkg,
                response=resp_dummy,
                x_correlation_id=pkg.request_id,
                x_user_id="rollout_sim_user",
                x_tenant_id="rollout_sim_tenant",
            )

    return default_rollout_manager.get_status_report()












