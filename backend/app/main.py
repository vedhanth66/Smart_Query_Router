"""Smart Query Router - Backend API Service.

Provides:
- GET /health: Simple liveness and version probe.
- POST /api/v1/optimize: Validates NormalizedQueryPackage and returns an
  OptimizationDecisionResponse following the minimal contract without ML routing.
"""

import os
import time
from typing import Any
from fastapi import FastAPI, Header, Response, status, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .schemas.contract import (
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


@app.get("/health", status_code=status.HTTP_200_OK)
def health_check() -> dict[str, str]:
    """Health and liveness endpoint."""
    return {
        "status": "ok",
        "service": "smart-query-router-backend",
        "version": "0.1.0"
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
    correlation_id = x_correlation_id or package.correlation_id or package.request_id
    response.headers["X-Correlation-ID"] = correlation_id

    effective_user_id = (
        x_user_id
        or package.user_id
        or (package.client_metadata.user_id if package.client_metadata else None)
        or "default_user"
    )
    effective_tenant_id = (
        x_tenant_id
        or package.tenant_id
        or (package.client_metadata.tenant_id if package.client_metadata else None)
        or "default_tenant"
    )

    query = package.query_text.strip()

    # Minimal deterministic baseline evaluation (no ML yet)
    # Check if query is trivial/short pass-through
    if len(query) < 15 and not package.context_candidates:
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

    # Deterministic coarse route determination
    if package.coarse_route:
        coarse_route = package.coarse_route
    elif package.task_category in [
        TaskCategory.CODING,
        TaskCategory.DEBUGGING,
        TaskCategory.REASONING,
        TaskCategory.COMPARISON,
        TaskCategory.ANALYSIS,
    ]:
        coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.task_category in [TaskCategory.GREETING, TaskCategory.ARITHMETIC]:
        coarse_route = CoarseRoute.LOCAL_ELIGIBLE
    elif package.task_category in [
        TaskCategory.FACTUAL_QUESTION,
        TaskCategory.SUMMARIZATION,
        TaskCategory.REWRITING,
        TaskCategory.TRANSLATION,
        TaskCategory.CREATIVE_WRITING,
    ]:
        coarse_route = CoarseRoute.SIMPLE_MODEL_CANDIDATE
    elif package.local_features and package.local_features.has_code:
        coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.local_features and package.local_features.has_math:
        coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.local_features and package.local_features.detected_cues:
        coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
    elif package.context_candidates:
        coarse_route = CoarseRoute.NEEDS_EVALUATION
    elif len(query) < 15 and any(query.lower().startswith(g) for g in ["hi", "hello", "hey", "thanks"]):
        coarse_route = CoarseRoute.LOCAL_ELIGIBLE
    else:
        coarse_route = CoarseRoute.SIMPLE_MODEL_CANDIDATE

    # Resolve model tier via decoupled gateway abstraction
    model_tier = default_gateway.resolve_tier_from_route(coarse_route.value if coarse_route else None)
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
                            gw_req = GatewayRequest(
                                prompt=query,
                                tier=ModelTier.FAST_CHEAP,
                                provider="small_model",
                                correlation_id=correlation_id,
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
                )
                comp = await default_gateway.compare_execution(
                    request=gw_req,
                    small_provider="small_model",
                    strong_provider="strong_model",
                    comparison_id=correlation_id,
                )
                max_lat = max(comp.small_response.latency_ms, comp.strong_response.latency_ms)
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
                    evaluation_metadata=comp.model_dump(),
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
async def gateway_execute(request: GatewayRequest) -> GatewayResponse:
    """Execute model operation through the active adapter."""
    try:
        return await default_gateway.execute(request)
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
) -> ModelExecutionComparison:
    """Executes small and strong models concurrently and returns comparative metrics."""
    try:
        return await default_gateway.compare_execution(
            request=request,
            small_provider=small_provider,
            strong_provider=strong_provider,
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




