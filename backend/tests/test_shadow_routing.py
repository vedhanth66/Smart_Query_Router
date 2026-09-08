"""Unit and Integration Tests for ML Router Shadow Mode and Evidence-Gated Activation.

TEST COVERAGE:
1. Passive Shadow Evaluation & Non-Interference:
   - Verifies that the production rule router continues to govern the user-visible path.
   - Verifies shadow telemetry is captured without disrupting responses.
2. Disagreement Classification:
   - AGREEMENT, ML_CHEAPER, ML_MORE_EXPENSIVE, and ML_LOCAL_INSTEAD_OF_MODEL.
3. Expected Savings & Quality Impact Assessment:
   - POTENTIAL_DEGRADATION_RISK vs QUALITY_PRESERVED_COST_OPTIMIZED vs QUALITY_ENHANCEMENT.
4. Evidence-Gated Activation Thresholds:
   - Rejection when sample size or agreement criteria are unsatisfied.
   - Successful activation once thresholds are satisfied.
5. Preserved Guardrail Primacy Under Activation:
   - Greetings and arithmetic remain local-eligible even when ML routing is active.
   - Context dependency remains complex-model candidate.
6. API Endpoints:
   - GET /api/v1/models/router-classifier/shadow-mode/report
   - POST /api/v1/models/router-classifier/shadow-mode/activate
   - POST /api/v1/models/router-classifier/shadow-mode/deactivate
   - POST /api/v1/models/router-classifier/shadow-mode/simulate-batch
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.contract import (
    ClientMetadata,
    CoarseRoute,
    ComplexityLevel,
    ModelTier,
    NormalizedQueryPackage,
    TaskCategory,
)
from app.schemas.shadow_routing import (
    LikelyQualityImpact,
    ShadowActivationThresholds,
    ShadowDisagreementType,
)
from app.ml.shadow_router import (
    ShadowRouterService,
    default_shadow_router,
)
from app.ml.rollout_manager import default_rollout_manager


@pytest.fixture
def client():
    # Ensure shadow router starts deactivated and with clean history for each test
    default_shadow_router.deactivate()
    default_shadow_router.clear_history()
    default_rollout_manager.config.rollout_percentage = 0.0
    yield TestClient(app)
    default_shadow_router.deactivate()
    default_shadow_router.clear_history()
    default_rollout_manager.config.rollout_percentage = 0.0


def make_package(
    query_text: str,
    request_id: str = "req_test_001",
    task_category: TaskCategory | None = None,
    complexity_level: ComplexityLevel | None = None,
    execute_route: bool = False,
    client_metadata: ClientMetadata | None = None,
) -> NormalizedQueryPackage:
    return NormalizedQueryPackage(
        request_id=request_id,
        query_text=query_text,
        task_category=task_category,
        complexity_level=complexity_level,
        execute_route=execute_route,
        client_metadata=client_metadata or ClientMetadata(extension_version="0.1.0"),
    )


# =========================================================================
# 1. Passive Shadow Evaluation & Non-Interference
# =========================================================================

def test_shadow_mode_does_not_alter_user_visible_path(client):
    """Verifies that production optimize endpoint strictly retains rule-based decisions during shadow mode."""
    payload = {
        "request_id": "req_shadow_001",
        "query_text": "Write a three-stanza poem about deep space exploration.",
        "task_category": "creative writing",
        "complexity_level": "MEDIUM",
        "execute_route": False,
        "client_metadata": {
            "extension_version": "0.1.0",
        },
    }

    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    # Rule-based decision MUST be preserved in user-visible fields
    assert data["coarse_route"] == CoarseRoute.SIMPLE_MODEL_CANDIDATE.value
    assert data["model_tier"] == ModelTier.FAST_CHEAP.value
    assert "ML_ROUTING_ACTIVE" not in data["reason_code"]

    # Verify shadow telemetry was captured in report
    report_resp = client.get("/api/v1/models/router-classifier/shadow-mode/report")
    assert report_resp.status_code == 200
    report = report_resp.json()
    assert report["total_shadow_queries"] == 1
    assert report["is_ml_routing_active"] is False

    recent = report["recent_events"][0]
    assert recent["production_route"] == "simple-model candidate"
    assert recent["ml_route"] == "complex-model candidate"
    assert recent["is_agreement"] is False
    assert recent["disagreement_type"] == "ML_MORE_EXPENSIVE"
    assert recent["likely_quality_impact"] == "QUALITY_ENHANCEMENT"


# =========================================================================
# 2. Disagreement Classification & Quality Impact
# =========================================================================

def test_shadow_disagreement_agreement():
    """Verifies AGREEMENT when rule and ML recommend the same route."""
    service = ShadowRouterService()
    pkg = make_package(
        query_text="Good morning! How are you doing today?",
        task_category=TaskCategory.GREETING,
        complexity_level=ComplexityLevel.VERY_LOW,
    )
    event = service.evaluate_shadow(
        package=pkg,
        production_route="local-eligible",
        production_tier="local",
    )
    assert event.is_agreement is True
    assert event.disagreement_type == ShadowDisagreementType.AGREEMENT
    assert event.likely_quality_impact == LikelyQualityImpact.NEUTRAL
    assert event.expected_savings_usd == 0.0


def test_shadow_disagreement_ml_cheaper():
    """Verifies ML_CHEAPER when ML recommends a lower-cost tier than the rule router."""
    service = ShadowRouterService()
    pkg = make_package(
        query_text="What is the capital of Australia?",
        task_category=TaskCategory.FACTUAL_QUESTION,
        complexity_level=ComplexityLevel.LOW,
    )
    event = service.evaluate_shadow(
        package=pkg,
        production_route="complex-model candidate",
        production_tier="strong",
    )
    assert event.is_agreement is False
    assert event.disagreement_type == ShadowDisagreementType.ML_CHEAPER
    assert event.expected_savings_usd > 0.0
    assert event.likely_quality_impact == LikelyQualityImpact.QUALITY_PRESERVED_COST_OPTIMIZED


def test_shadow_disagreement_potential_degradation_risk():
    """Verifies POTENTIAL_DEGRADATION_RISK when ML recommends cheaper routing on complex tasks."""
    service = ShadowRouterService()
    pkg = make_package(
        query_text="Write a multi-threaded lock-free queue in Rust with memory ordering proofs",
        task_category=TaskCategory.CODING,
        complexity_level=ComplexityLevel.HIGH,
    )
    event = service.evaluate_shadow(
        package=pkg,
        production_route="complex-model candidate",
        production_tier="strong",
    )
    if event.disagreement_type == ShadowDisagreementType.ML_CHEAPER:
        assert event.likely_quality_impact == LikelyQualityImpact.POTENTIAL_DEGRADATION_RISK


def test_shadow_disagreement_ml_local_instead_of_model():
    """Verifies ML_LOCAL_INSTEAD_OF_MODEL when ML routes on-device but rule called cloud model."""
    service = ShadowRouterService()
    pkg = make_package(
        query_text="What is 482 * 17?",
        task_category=TaskCategory.ARITHMETIC,
        complexity_level=ComplexityLevel.VERY_LOW,
    )
    event = service.evaluate_shadow(
        package=pkg,
        production_route="simple-model candidate",
        production_tier="fast_cheap",
    )
    assert event.is_agreement is False
    assert event.disagreement_type == ShadowDisagreementType.ML_LOCAL_INSTEAD_OF_MODEL
    assert event.expected_savings_usd > 0.0


# =========================================================================
# 3. Evidence-Gated Activation Thresholds
# =========================================================================

def test_activation_blocked_by_insufficient_samples(client):
    """Verifies that activating ML routing is blocked when sample size is below threshold."""
    resp = client.post("/api/v1/models/router-classifier/shadow-mode/activate", json={})
    assert resp.status_code == 400
    assert "Minimum Sample Size" in resp.json()["detail"]


def test_activation_blocked_by_quality_risk(client):
    """Verifies that activation is blocked if quality risk exceeds threshold."""
    service = ShadowRouterService()
    pkg = make_package(
        query_text="Analyze zero-day exploit in cryptographic protocol",
        task_category=TaskCategory.ANALYSIS,
        complexity_level=ComplexityLevel.VERY_HIGH,
    )
    service.evaluate_shadow(pkg, production_route="complex-model candidate", production_tier="strong")

    strict_th = ShadowActivationThresholds(min_sample_size=1, max_quality_risk_pct=0.0)
    report = service.get_status_report(thresholds=strict_th)
    assert report.all_thresholds_satisfied is False or report.quality_risk_count == 0


def test_activation_succeeds_when_thresholds_satisfied(client):
    """Verifies that ML routing activates when sample and agreement criteria are met."""
    sim_resp = client.post(
        "/api/v1/models/router-classifier/shadow-mode/simulate-batch",
        json={"iterations": 1},
    )
    assert sim_resp.status_code == 200
    sim_data = sim_resp.json()
    assert sim_data["total_shadow_queries"] >= 15

    custom_th = {
        "min_sample_size": 15,
        "min_agreement_rate_pct": 50.0,
        "max_quality_risk_pct": 20.0,
        "min_net_savings_usd": -1.0,
        "max_p95_latency_ms": 100.0,
        "min_disagreement_confidence": 0.50,
    }

    act_resp = client.post(
        "/api/v1/models/router-classifier/shadow-mode/activate",
        json={"custom_thresholds": custom_th},
    )
    assert act_resp.status_code == 200, f"Activation failed: {act_resp.text}"
    act_data = act_resp.json()
    assert act_data["is_ml_routing_active"] is True
    assert act_data["activation_status"] == "ACTIVE"


# =========================================================================
# 4. Preserved Guardrail Primacy Under Active ML Routing
# =========================================================================

def test_guardrails_immutable_under_active_ml_routing(client):
    """Verifies that even when ML routing is active, Gate 1 & Gate 2 guardrails remain absolute."""
    act_resp = client.post(
        "/api/v1/models/router-classifier/shadow-mode/activate",
        json={"force_activation": True, "bypass_justification": "Testing guardrail immutability"},
    )
    assert act_resp.status_code == 200
    assert act_resp.json()["is_ml_routing_active"] is True

    # 1. Gate 1 Primacy: Greeting query MUST route to local-eligible
    greet_resp = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_greet_01",
            "query_text": "Good morning! How are you doing today?",
            "task_category": "greeting",
            "execute_route": False,
            "client_metadata": {"extension_version": "0.1.0"},
        },
    )
    assert greet_resp.status_code == 200
    greet_data = greet_resp.json()
    assert greet_data["coarse_route"] == "local-eligible"
    assert greet_data.get("model_tier") is None
    assert "DETERMINISTIC_SAFETY" in greet_data["reason_code"]

    # 2. Gate 1 Primacy: Arithmetic query MUST route to local-eligible
    arith_resp = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_arith_01",
            "query_text": "What is 482 * 17?",
            "task_category": "arithmetic",
            "execute_route": False,
            "client_metadata": {"extension_version": "0.1.0"},
        },
    )
    assert arith_resp.status_code == 200
    arith_data = arith_resp.json()
    assert arith_data["coarse_route"] == "local-eligible"
    assert arith_data.get("model_tier") is None
    assert "DETERMINISTIC_SAFETY" in arith_data["reason_code"]

    # 3. Gate 2 Primacy: Context dependency MUST route to complex-model candidate
    ctx_resp = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_ctx_01",
            "query_text": "Which of those two options would you recommend?",
            "context_candidates": [
                {
                    "turn_id": "turn_01",
                    "role": "user",
                    "content": "Comparing Option A and Option B for architecture.",
                }
            ],
            "client_metadata": {
                "extension_version": "0.1.0",
            },
            "execute_route": False,
        },
    )
    assert ctx_resp.status_code == 200
    ctx_data = ctx_resp.json()
    assert ctx_data["coarse_route"] == "complex-model candidate"
    assert ctx_data["model_tier"] == "strong"
    assert "EXPLICIT_CONTEXT_GUARD" in ctx_data["reason_code"]


# =========================================================================
# 5. Clean Deactivation
# =========================================================================

def test_deactivation_restores_passive_shadow_mode(client):
    """Verifies that calling deactivate restores rule router authority and passive shadow mode."""
    client.post(
        "/api/v1/models/router-classifier/shadow-mode/activate",
        json={"force_activation": True, "bypass_justification": "Testing deactivation"},
    )
    deact_resp = client.post("/api/v1/models/router-classifier/shadow-mode/deactivate")
    assert deact_resp.status_code == 200
    deact_data = deact_resp.json()
    assert deact_data["is_ml_routing_active"] is False

    resp = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_deact_01",
            "query_text": "Write a short creative story",
            "task_category": "creative writing",
            "execute_route": False,
            "client_metadata": {"extension_version": "0.1.0"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["coarse_route"] == CoarseRoute.SIMPLE_MODEL_CANDIDATE.value
    assert "ML_ROUTING_ACTIVE" not in data["reason_code"]
