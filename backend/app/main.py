"""Smart Query Router - Backend API Service.

Provides:
- GET /health: Simple liveness and version probe.
- POST /api/v1/optimize: Validates NormalizedQueryPackage and returns an
  OptimizationDecisionResponse following the minimal contract without ML routing.
"""

import os
import re
import time
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from fastapi import FastAPI, Header, Response, status, HTTPException, Query
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

    # Deterministic coarse route determination
    if package.coarse_route:
        coarse_route = package.coarse_route
    elif has_rich:
        # Queries depending on attachments, images, files, tables, or rich inputs
        # strictly default to conservative complex-model candidate
        coarse_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE
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






