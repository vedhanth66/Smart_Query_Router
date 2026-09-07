"""Smart Query Router - Backend API Service.

Provides:
- GET /health: Simple liveness and version probe.
- POST /api/v1/optimize: Validates NormalizedQueryPackage and returns an
  OptimizationDecisionResponse following the minimal contract without ML routing.
"""

from fastapi import FastAPI, Header, Response, status
from fastapi.middleware.cors import CORSMiddleware
from .schemas.contract import (
    NormalizedQueryPackage,
    OptimizationDecisionResponse,
    DecisionType,
    OptimizationInstructions,
    CoarseRoute,
    TaskCategory,
    ComplexityLevel,
)

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
def evaluate_optimization(
    package: NormalizedQueryPackage,
    response: Response,
    x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID")
) -> OptimizationDecisionResponse:
    """Evaluates an incoming normalized query package against the baseline contract.
    
    Tracks correlation identifier across client and backend, returning it
    in the response body and X-Correlation-ID header.
    
    Guarantees:
    - Rejects malformed or oversized payloads with HTTP 422.
    - Strictly never includes provider secrets or model credentials.
    - Preserves correlation identifier.
    """
    correlation_id = x_correlation_id or package.correlation_id or package.request_id
    response.headers["X-Correlation-ID"] = correlation_id

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

    return OptimizationDecisionResponse(
        request_id=package.request_id,
        correlation_id=correlation_id,
        decision_type=decision_type,
        coarse_route=coarse_route,
        task_category=package.task_category,
        complexity_score=package.complexity_score,
        complexity_level=package.complexity_level,
        confidence=confidence,
        reason_code=reason_code,
        optimization_instructions=instructions
    )
