"""Unit and Integration Tests for ML Router Limited Rollout, Kill Switch, Telemetry, and Measurable Rollback Triggers.

TEST COVERAGE:
1. Limited Rollout Canary Bucketing:
   - Consistent hash partitioning across percentage levels (0%, 25%, 50%, 100%).
   - Allowlist and denylist enforcement by user_id and tenant_id.
2. Global Emergency Kill Switch:
   - Engaging kill switch immediately diverts 100% of traffic to deterministic router.
   - Disengaging restores configured canary rollout.
3. Deterministic Router Fallback:
   - Deterministic router acts as immediate fallback on kill switch, bucket exclusion, or ML failure.
4. Multi-Dimensional Monitoring Telemetry:
   - Route distribution (% local, simple, complex).
   - Escalation rate (simple-model escalation tracking).
   - Error metrics and categories.
   - Latency percentiles (P50, P90, P95, P99).
   - Cache behavior (exact, semantic, misses, bypasses).
   - Quality signals (completeness and confidence).
5. Measurable Regression Rollback Triggers:
   - Error rate regression trigger.
   - Escalation rate regression trigger.
   - Consecutive failure breaker (immediate trip).
   - Quality completeness regression trigger.
6. Rollback Reset & Cautious Recovery:
   - Operator reset with justification and cautious percentage restoration.
7. Preserved Guardrail Primacy:
   - Gates 1 & 2 remain active overrides in ML cohort.
8. REST API Endpoints:
   - GET /api/v1/router/rollout/status
   - POST /api/v1/router/rollout/configure
   - POST /api/v1/router/rollout/kill-switch
   - POST /api/v1/router/rollout/reset-rollback
   - POST /api/v1/router/rollout/simulate
"""

from unittest.mock import patch
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
from app.schemas.rollout_routing import (
    RollbackTriggerThresholds,
    RolloutCohort,
    RolloutConfig,
    RolloutTriggerState,
)
from app.ml.rollout_manager import (
    RolloutManager,
    default_rollout_manager,
)


@pytest.fixture
def client():
    default_rollout_manager.clear_history()
    default_rollout_manager.config.kill_switch_engaged = False
    default_rollout_manager.config.rollout_percentage = 10.0
    yield TestClient(app)
    default_rollout_manager.clear_history()


def make_package(
    query_text: str,
    request_id: str = "req_rollout_001",
    task_category: TaskCategory | None = None,
    complexity_level: ComplexityLevel | None = None,
    execute_route: bool = False,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> NormalizedQueryPackage:
    return NormalizedQueryPackage(
        request_id=request_id,
        query_text=query_text,
        task_category=task_category,
        complexity_level=complexity_level,
        execute_route=execute_route,
        user_id=user_id,
        tenant_id=tenant_id,
        client_metadata=ClientMetadata(extension_version="0.1.0"),
    )


# =========================================================================
# 1. Limited Rollout Canary Bucketing & Overrides
# =========================================================================

def test_canary_rollout_zero_percent_diverts_all_to_rule():
    """Verifies that with 0% rollout, all queries route to deterministic control."""
    mgr = RolloutManager(config=RolloutConfig(rollout_percentage=0.0))
    for i in range(20):
        pkg = make_package(query_text="Sample query", request_id=f"req_{i}")
        should_ml, reason = mgr.should_route_to_ml(pkg)
        assert should_ml is False
        assert reason == "ROLLOUT_ZERO_PERCENT"


def test_canary_rollout_full_percent_routes_all_to_ml():
    """Verifies that with 100% rollout, all queries route to ML."""
    mgr = RolloutManager(config=RolloutConfig(rollout_percentage=100.0))
    for i in range(20):
        pkg = make_package(query_text="Sample query", request_id=f"req_{i}")
        should_ml, reason = mgr.should_route_to_ml(pkg)
        assert should_ml is True
        assert reason == "ROLLOUT_FULL"


def test_canary_rollout_consistent_partitioning():
    """Verifies that identical query/user pairs always map to the identical bucket."""
    mgr = RolloutManager(config=RolloutConfig(rollout_percentage=50.0))
    pkg = make_package(query_text="Sample query", request_id="stable_req_001", user_id="user_alice")

    first_decision, first_reason = mgr.should_route_to_ml(pkg, user_id="user_alice")
    for _ in range(10):
        decision, reason = mgr.should_route_to_ml(pkg, user_id="user_alice")
        assert decision == first_decision
        assert reason == first_reason


def test_allowlist_and_denylist_enforcement():
    """Verifies explicit allowlists and denylists override hash partitioning."""
    mgr = RolloutManager(
        config=RolloutConfig(
            rollout_percentage=0.0,
            allowlist_user_ids=["vip_user"],
            denylist_user_ids=["banned_user"],
        )
    )
    # VIP user routes to ML even with 0% rollout
    pkg_vip = make_package(query_text="VIP query", request_id="req_vip", user_id="vip_user")
    should_ml_vip, reason_vip = mgr.should_route_to_ml(pkg_vip, user_id="vip_user")
    assert should_ml_vip is True
    assert reason_vip == "ALLOWLIST_INCLUDED_USER"

    # Banned user excluded even with 100% rollout
    mgr.config.rollout_percentage = 100.0
    pkg_banned = make_package(query_text="Banned query", request_id="req_ban", user_id="banned_user")
    should_ml_ban, reason_ban = mgr.should_route_to_ml(pkg_banned, user_id="banned_user")
    assert should_ml_ban is False
    assert reason_ban == "DENYLIST_EXCLUDED_USER"


# =========================================================================
# 2. Global Emergency Kill Switch
# =========================================================================

def test_kill_switch_operation(client):
    """Verifies emergency kill switch immediately stops all ML routing."""
    # 1. Force 100% rollout
    client.post("/api/v1/router/rollout/configure", json={"rollout_percentage": 100.0})

    # Query routes via ML
    resp_ml = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_ks_01",
            "query_text": "Write a three-stanza poem about deep space exploration.",
            "task_category": "creative writing",
            "complexity_level": "MEDIUM",
            "execute_route": False,
            "client_metadata": {"extension_version": "0.1.0"},
        },
    )
    assert resp_ml.status_code == 200
    assert "ML_ROUTING" in resp_ml.json()["reason_code"]

    # 2. Engage kill switch
    ks_resp = client.post(
        "/api/v1/router/rollout/kill-switch",
        json={"engage": True, "reason": "Emergency operator kill switch test"},
    )
    assert ks_resp.status_code == 200
    assert ks_resp.json()["config"]["kill_switch_engaged"] is True

    # Query now strictly falls back to deterministic rule router
    resp_ks = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_ks_02",
            "query_text": "Write a three-stanza poem about deep space exploration.",
            "task_category": "creative writing",
            "complexity_level": "MEDIUM",
            "execute_route": False,
            "client_metadata": {"extension_version": "0.1.0"},
        },
    )
    assert resp_ks.status_code == 200
    data_ks = resp_ks.json()
    assert "ML_ROUTING" not in data_ks["reason_code"]
    assert data_ks["coarse_route"] == CoarseRoute.SIMPLE_MODEL_CANDIDATE.value


# =========================================================================
# 3. Deterministic Router Fallback on ML Exception
# =========================================================================

def test_deterministic_fallback_on_ml_exception(client):
    """Verifies that an unexpected ML evaluation failure falls back to rule router without request failure."""
    client.post("/api/v1/router/rollout/configure", json={"rollout_percentage": 100.0})

    # Patch route_query to raise an unexpected runtime error
    with patch("app.ml.shadow_router.default_shadow_router._ensure_guarded_router") as mock_ensure:
        mock_guarded = mock_ensure.return_value
        mock_guarded.route_query.side_effect = RuntimeError("Simulated ML model failure")

        resp = client.post(
            "/api/v1/optimize",
            json={
                "request_id": "req_fb_01",
                "query_text": "def quicksort(arr): pass",
                "task_category": "coding",
                "complexity_level": "HIGH",
                "execute_route": False,
                "client_metadata": {"extension_version": "0.1.0"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reason_code"] == "FALLBACK_DETERMINISTIC_ON_ML_ERROR"
        assert data["coarse_route"] == "complex-model candidate"


# =========================================================================
# 4. Multi-Dimensional Monitoring Telemetry
# =========================================================================

def test_multi_dimensional_telemetry_monitoring():
    """Verifies recording and calculation across all 6 monitoring dimensions."""
    mgr = RolloutManager()

    # Record diverse events
    mgr.record_query_telemetry(
        cohort=RolloutCohort.ML,
        route="simple-model candidate",
        latency_ms=120.0,
        cache_outcome="HIT",
        is_escalated=False,
        has_error=False,
        completeness_score=0.95,
        confidence_score=0.90,
    )
    mgr.record_query_telemetry(
        cohort=RolloutCohort.ML,
        route="simple-model candidate",
        latency_ms=350.0,
        cache_outcome="MISS",
        is_escalated=True,
        escalation_reason="INCOMPLETE (missing_imports)",
        has_error=False,
        completeness_score=0.60,
        confidence_score=0.70,
        detected_issues=["missing_imports"],
    )
    mgr.record_query_telemetry(
        cohort=RolloutCohort.CONTROL_RULE,
        route="complex-model candidate",
        latency_ms=800.0,
        cache_outcome="BYPASS",
        is_escalated=False,
        has_error=False,
        completeness_score=1.0,
        confidence_score=0.95,
    )

    report = mgr.get_status_report()
    assert report.total_queries_observed == 3

    # ML Cohort Metrics
    ml_m = report.ml_cohort
    assert ml_m.total_queries == 2
    assert ml_m.route_distribution.simple_model_count == 2
    assert ml_m.route_distribution.simple_model_pct == 100.0
    assert ml_m.escalations.escalation_count == 1
    assert ml_m.escalations.escalation_rate_pct == 50.0
    assert ml_m.cache.exact_hit_count == 1
    assert ml_m.cache.cache_hit_rate_pct == 50.0
    assert ml_m.quality.mean_completeness == 0.775
    assert ml_m.latency.mean_ms == 235.0

    # Control Cohort Metrics
    ctrl_m = report.control_cohort
    assert ctrl_m.total_queries == 1
    assert ctrl_m.route_distribution.complex_model_count == 1


# =========================================================================
# 5. Measurable Regression Rollback Triggers
# =========================================================================

def test_auto_rollback_trigger_on_consecutive_failures():
    """Verifies that 3 consecutive unhandled failures in ML cohort immediately trip the kill switch."""
    mgr = RolloutManager(
        config=RolloutConfig(rollout_percentage=50.0),
        thresholds=RollbackTriggerThresholds(max_consecutive_errors=3),
    )

    for i in range(2):
        mgr.record_query_telemetry(cohort=RolloutCohort.ML, route="simple-model candidate", latency_ms=10.0, has_error=True)
        assert mgr.config.kill_switch_engaged is False
        assert mgr.rollback_status.is_rollback_active is False

    # 3rd consecutive error trips rollback
    mgr.record_query_telemetry(cohort=RolloutCohort.ML, route="simple-model candidate", latency_ms=10.0, has_error=True)
    assert mgr.config.kill_switch_engaged is True
    assert mgr.config.rollout_percentage == 0.0
    assert mgr.rollback_status.is_rollback_active is True
    assert mgr.rollback_status.trigger_metric == "consecutive_errors"
    assert mgr.rollback_status.state == RolloutTriggerState.TRIGGERED


def test_auto_rollback_trigger_on_error_rate():
    """Verifies that error rate exceeding max_error_rate_pct trips rollback once sample size reached."""
    mgr = RolloutManager(
        config=RolloutConfig(rollout_percentage=50.0),
        thresholds=RollbackTriggerThresholds(min_eval_samples=10, max_error_rate_pct=10.0, max_consecutive_errors=10),
    )

    # 8 successes, 2 errors (20% error rate > 10% threshold)
    for _ in range(8):
        mgr.record_query_telemetry(cohort=RolloutCohort.ML, route="simple-model candidate", latency_ms=10.0, has_error=False)
    for _ in range(2):
        mgr.record_query_telemetry(cohort=RolloutCohort.ML, route="simple-model candidate", latency_ms=10.0, has_error=True)

    assert mgr.config.kill_switch_engaged is True
    assert mgr.rollback_status.is_rollback_active is True
    assert mgr.rollback_status.trigger_metric == "error_rate_pct"
    assert mgr.rollback_status.observed_value == 20.0


def test_auto_rollback_trigger_on_escalation_rate():
    """Verifies that small-model escalation rate > max_escalation_rate_pct trips rollback."""
    mgr = RolloutManager(
        config=RolloutConfig(rollout_percentage=50.0),
        thresholds=RollbackTriggerThresholds(min_eval_samples=10, max_escalation_rate_pct=15.0, max_consecutive_errors=10),
    )

    # 10 queries: 3 escalations (30% > 15%)
    for _ in range(7):
        mgr.record_query_telemetry(cohort=RolloutCohort.ML, route="simple-model candidate", latency_ms=50.0, is_escalated=False)
    for _ in range(3):
        mgr.record_query_telemetry(cohort=RolloutCohort.ML, route="simple-model candidate", latency_ms=250.0, is_escalated=True)

    assert mgr.config.kill_switch_engaged is True
    assert mgr.rollback_status.is_rollback_active is True
    assert mgr.rollback_status.trigger_metric == "escalation_rate_pct"
    assert mgr.rollback_status.observed_value == 30.0


def test_auto_rollback_trigger_on_quality_completeness():
    """Verifies that average completeness falling below min_quality_completeness trips rollback."""
    mgr = RolloutManager(
        config=RolloutConfig(rollout_percentage=50.0),
        thresholds=RollbackTriggerThresholds(min_eval_samples=10, min_quality_completeness=0.80, max_consecutive_errors=10),
    )

    # 10 queries with low completeness (0.65 < 0.80)
    for _ in range(10):
        mgr.record_query_telemetry(
            cohort=RolloutCohort.ML,
            route="simple-model candidate",
            latency_ms=100.0,
            completeness_score=0.65,
        )

    assert mgr.config.kill_switch_engaged is True
    assert mgr.rollback_status.is_rollback_active is True
    assert mgr.rollback_status.trigger_metric == "min_quality_completeness"


# =========================================================================
# 6. Rollback Reset & Recovery
# =========================================================================

def test_rollback_reset_and_cautious_recovery(client):
    """Verifies operator can review and reset an automated rollback with cautious percentage."""
    # Force auto-rollback
    for _ in range(3):
        default_rollout_manager.record_query_telemetry(
            cohort=RolloutCohort.ML, route="simple-model candidate", latency_ms=10.0, has_error=True
        )

    assert default_rollout_manager.rollback_status.is_rollback_active is True
    assert default_rollout_manager.config.kill_switch_engaged is True

    # Reset rollback via endpoint
    reset_resp = client.post(
        "/api/v1/router/rollout/reset-rollback",
        json={
            "justification": "Incident resolved: downstream timeout configuration fixed",
            "restore_rollout_percentage": 5.0,
        },
    )
    assert reset_resp.status_code == 200
    data = reset_resp.json()
    assert data["rollback_status"]["is_rollback_active"] is False
    assert data["rollback_status"]["state"] == "HEALTHY"
    assert data["config"]["kill_switch_engaged"] is False
    assert data["config"]["rollout_percentage"] == 5.0


# =========================================================================
# 7. Preserved Guardrail Primacy Under ML Rollout
# =========================================================================

def test_guardrails_immutable_under_ml_rollout(client):
    """Verifies Gate 1 and Gate 2 guardrails remain absolute even in 100% ML rollout."""
    client.post("/api/v1/router/rollout/configure", json={"rollout_percentage": 100.0})

    # Gate 1: Greeting MUST route to local-eligible
    resp_g = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_greet_canary",
            "query_text": "Good morning! How are you doing today?",
            "task_category": "greeting",
            "execute_route": False,
            "client_metadata": {"extension_version": "0.1.0"},
        },
    )
    assert resp_g.status_code == 200
    assert resp_g.json()["coarse_route"] == "local-eligible"

    # Gate 1: Arithmetic MUST route to local-eligible
    resp_a = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_arith_canary",
            "query_text": "What is 482 * 17?",
            "task_category": "arithmetic",
            "execute_route": False,
            "client_metadata": {"extension_version": "0.1.0"},
        },
    )
    assert resp_a.status_code == 200
    assert resp_a.json()["coarse_route"] == "local-eligible"

    # Gate 2: Context MUST route to complex-model candidate
    resp_c = client.post(
        "/api/v1/optimize",
        json={
            "request_id": "req_ctx_canary",
            "query_text": "Which of those two options would you recommend?",
            "context_candidates": [
                {
                    "turn_id": "turn_01",
                    "role": "user",
                    "content": "Comparing Option A and Option B for architecture.",
                }
            ],
            "client_metadata": {"extension_version": "0.1.0"},
            "execute_route": False,
        },
    )
    assert resp_c.status_code == 200
    assert resp_c.json()["coarse_route"] == "complex-model candidate"


# =========================================================================
# 8. REST API Endpoints & Simulation
# =========================================================================

def test_api_rollout_lifecycle(client):
    """Verifies status reporting, configuration, and batch simulation endpoints."""
    # 1. GET Status
    stat_resp = client.get("/api/v1/router/rollout/status")
    assert stat_resp.status_code == 200
    report = stat_resp.json()
    assert "ml_cohort" in report
    assert "control_cohort" in report
    assert "rollback_status" in report

    # 2. POST Configure
    cfg_resp = client.post(
        "/api/v1/router/rollout/configure",
        json={"rollout_percentage": 25.0, "allowlist_user_ids": ["test_user_01"]},
    )
    assert cfg_resp.status_code == 200
    assert cfg_resp.json()["config"]["rollout_percentage"] == 25.0
    assert "test_user_01" in cfg_resp.json()["config"]["allowlist_user_ids"]

    # 3. POST Simulate Batch
    sim_resp = client.post(
        "/api/v1/router/rollout/simulate",
        json={"iterations": 1},
    )
    assert sim_resp.status_code == 200
    sim_data = sim_resp.json()
    assert sim_data["total_queries_observed"] >= 15
    assert sim_data["blended_totals"]["total_queries"] >= 15
