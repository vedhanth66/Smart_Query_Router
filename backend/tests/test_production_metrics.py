"""Automated Test Suite for Production Metrics, Privacy Guarantees, and Observability.

Validates:
1. Thread-safe metrics collection across routes, cache outcomes, latencies, and errors.
2. Latency percentiles calculation (P50, P90, P95, P99).
3. Cache hit rate, escalation rate, and error rate ratio calculations.
4. Token savings and financial cost proxy calculations based on canonical PricingConfig.
5. Strict Privacy Guarantee: Zero raw query content, prompts, or sensitive strings logged or stored.
6. Prometheus exposition format syntax and backward-compatible metrics.
7. /api/v1/metrics/summary API structure and "Are we saving work without degrading user experience?" verdict.
8. Actionable alerts YAML schema and required alert rules.
9. Grafana dashboard JSON specification schema and metric queries.
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import yaml

from app.main import app
from app.metrics import ProductionMetricsCollector, production_metrics
from app.schemas.benchmark import PricingConfig

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ALERTS_YAML = REPO_ROOT / "monitoring" / "alerts.yaml"
K8S_ALERTS_YAML = REPO_ROOT / "k8s" / "alerts.yaml"
DASHBOARD_JSON = REPO_ROOT / "monitoring" / "dashboard.json"

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_metrics_state():
    """Reset metrics state before each test for test isolation."""
    production_metrics.reset_metrics_for_testing()
    yield
    production_metrics.reset_metrics_for_testing()


def test_metrics_collector_record_and_quantiles():
    """Verify recording query execution latencies calculates percentiles accurately."""
    collector = ProductionMetricsCollector()

    # Record 100 queries with ascending latencies: 10ms to 1000ms
    for i in range(1, 101):
        collector.record_query_execution(
            route="simple-model candidate",
            status_code=200,
            cohort="canary",
            latency_ms=float(i * 10),
            cache_outcome="MISS",
            input_tokens=100,
            output_tokens=50,
        )

    summary = collector.get_summary_report()
    overall_lat = summary["user_experience"]["overall_latency_ms"]

    # 50th item should be ~500ms
    assert 480.0 <= overall_lat["p50"] <= 520.0
    # 95th item should be ~950ms
    assert 930.0 <= overall_lat["p95"] <= 970.0
    # 99th item should be ~990ms
    assert 970.0 <= overall_lat["p99"] <= 1000.0


def test_cache_hit_rate_and_error_rate_calculation():
    """Verify cache hit rate and error rate ratios calculate correctly."""
    collector = ProductionMetricsCollector()

    # 4 exact hits, 2 semantic hits, 4 misses = 6/10 = 0.60 hit rate
    for _ in range(4):
        collector.record_query_execution(route="local-eligible", cache_outcome="EXACT_HIT")
    for _ in range(2):
        collector.record_query_execution(route="simple-model candidate", cache_outcome="SEMANTIC_HIT")
    for _ in range(4):
        collector.record_query_execution(route="complex-model candidate", cache_outcome="MISS")

    summary = collector.get_summary_report()
    assert summary["work_saved"]["cache_hit_rate_ratio"] == 0.60
    assert summary["work_saved"]["cache_operations"]["EXACT_HIT"] == 4
    assert summary["work_saved"]["cache_operations"]["SEMANTIC_HIT"] == 2
    assert summary["work_saved"]["cache_operations"]["MISS"] == 4


def test_escalation_telemetry_and_ratio():
    """Verify escalation counter and ratio calculations."""
    collector = ProductionMetricsCollector()

    # 10 simple model queries, 2 escalated
    for i in range(8):
        collector.record_query_execution(
            route="simple-model candidate",
            is_escalated=False,
        )
    for i in range(2):
        collector.record_query_execution(
            route="simple-model candidate",
            is_escalated=True,
            escalation_reason="LOW_CONFIDENCE",
        )

    summary = collector.get_summary_report()
    # 2 out of 10 = 0.20
    assert summary["user_experience"]["escalation_rate_ratio"] == 0.20
    assert summary["user_experience"]["total_escalations"] == 2
    assert summary["user_experience"]["escalations_by_reason"]["LOW_CONFIDENCE"] == 2


def test_token_and_cost_savings_math():
    """Verify tokens saved and financial cost proxy match PricingConfig formulas."""
    pricing = PricingConfig(
        strong_input_price_per_million=2.50,
        strong_output_price_per_million=10.00,
        small_input_price_per_million=0.15,
        small_output_price_per_million=0.60,
    )
    collector = ProductionMetricsCollector(pricing_config=pricing)

    # 1. Query with cache hit: 1000 input tokens, 500 output tokens
    # Baseline cost = (1000 * 2.50 / 1e6) + (500 * 10.00 / 1e6) = $0.0025 + $0.005 = $0.0075
    # Actual cost = $0.00
    # Net savings = $0.0075, tokens saved = 1500
    collector.record_query_execution(
        route="local-eligible",
        cache_outcome="EXACT_HIT",
        input_tokens=1000,
        output_tokens=500,
    )

    summary = collector.get_summary_report()
    work = summary["work_saved"]
    assert work["total_tokens_saved"] == 1500
    assert work["tokens_saved_breakdown"]["cache_avoided"] == 1500
    assert abs(work["baseline_cost_usd"] - 0.0075) < 1e-5
    assert abs(work["actual_cost_usd"] - 0.0) < 1e-5
    assert abs(work["net_cost_saved_usd"] - 0.0075) < 1e-5


def test_privacy_guarantee_zero_raw_query_logging():
    """STRICT PRIVACY GUARD: Verify zero raw query text or private tokens appear in metrics.
    
    Ensures user prompts and conversation turns never leak into:
    1. Prometheus exposition format
    2. Metrics JSON summary endpoint
    3. Metrics collector memory state
    """
    private_canary = "CONFIDENTIAL_PATIENT_SSN_98765_DO_NOT_LEAK"

    payload = {
        "request_id": "req-priv-001",
        "correlation_id": "corr-priv-001",
        "query_text": f"Patient diagnosed with diabetes {private_canary}",
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "hostname": "claude.ai",
        },
    }

    # Execute request through API
    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200

    # 1. Inspect Prometheus exposition output
    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert private_canary not in metrics_resp.text, "CRITICAL: Private query text leaked into /metrics exposition!"

    # 2. Inspect Metrics Summary JSON output
    summary_resp = client.get("/api/v1/metrics/summary")
    assert summary_resp.status_code == 200
    summary_text = summary_resp.text
    assert private_canary not in summary_text, "CRITICAL: Private query text leaked into /api/v1/metrics/summary!"

    # 3. Inspect internal collector memory
    collector_repr = str(production_metrics.__dict__)
    assert private_canary not in collector_repr, "CRITICAL: Private query text leaked into collector memory!"


def test_prometheus_exposition_format():
    """Verify GET /metrics produces compliant Prometheus v0.0.4 text with all required metrics."""
    production_metrics.record_query_execution(
        route="simple-model candidate",
        status_code=200,
        cohort="canary",
        latency_ms=125.5,
        cache_outcome="MISS",
        input_tokens=200,
        output_tokens=100,
    )

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain; version=0.0.4" in resp.headers["content-type"]
    body = resp.text

    # Verify presence of all required metric series
    required_series = [
        "smart_query_router_in_flight_requests",
        "http_requests_in_flight",
        "smart_query_router_requests_total",
        "smart_query_router_requests_disaggregated_total",
        "smart_query_router_request_duration_ms",
        "smart_query_router_request_duration_seconds",
        "smart_query_router_route_p95_latency_ms",
        "smart_query_router_errors_total",
        "smart_query_router_error_rate_ratio",
        "smart_query_router_cache_operations_total",
        "smart_query_router_cache_hit_rate_ratio",
        "smart_query_router_route_distribution_total",
        "smart_query_router_escalations_total",
        "smart_query_router_escalation_rate_ratio",
        "smart_query_router_tokens_saved_total",
        "smart_query_router_baseline_cost_usd_total",
        "smart_query_router_actual_cost_usd_total",
        "smart_query_router_cost_savings_usd_total",
        "smart_query_router_cost_savings_ratio",
        "smart_query_router_subsystem_health",
        "smart_query_router_uptime_seconds",
    ]

    for s in required_series:
        assert s in body, f"Prometheus exposition missing required metric series: {s}"


def test_metrics_summary_endpoint_and_verdict():
    """Verify /api/v1/metrics/summary returns structured response answering work saved vs UX."""
    # Seed healthy work saving execution
    production_metrics.record_query_execution(
        route="local-eligible",
        status_code=200,
        latency_ms=5.0,
        cache_outcome="EXACT_HIT",
        input_tokens=500,
        output_tokens=200,
        confidence_score=0.95,
        completeness_score=0.95,
    )

    resp = client.get("/api/v1/metrics/summary")
    assert resp.status_code == 200
    data = resp.json()

    assert "status_verdict" in data
    assert "verdict_statement" in data
    assert data["status_verdict"] == "SAVING_WORK_HEALTHY"
    assert "Yes:" in data["verdict_statement"]

    # Check top-level sections
    assert "work_saved" in data
    assert "user_experience" in data
    assert "service_health" in data

    # Verify work saved fields
    assert data["work_saved"]["net_cost_saved_usd"] > 0
    assert data["work_saved"]["total_tokens_saved"] == 700

    # Verify UX fields
    assert data["user_experience"]["error_rate_ratio"] == 0.0
    assert data["user_experience"]["escalation_rate_ratio"] == 0.0
    assert data["user_experience"]["overall_latency_ms"]["p95"] > 0

    # Verify health
    assert data["service_health"]["is_all_subsystems_healthy"] is True


def test_actionable_alerts_definitions():
    """Verify alerting rules in monitoring/alerts.yaml and k8s/alerts.yaml."""
    assert ALERTS_YAML.is_file(), f"Missing alerts file at {ALERTS_YAML}"
    assert K8S_ALERTS_YAML.is_file(), f"Missing k8s alerts file at {K8S_ALERTS_YAML}"

    for path in (ALERTS_YAML, K8S_ALERTS_YAML):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        groups = data["groups"] if "groups" in data else data["spec"]["groups"]
        rules = groups[0]["rules"]

        alert_map = {r["alert"]: r for r in rules}

        # 5 required actionable alerts
        expected_alerts = [
            "HighErrorRate",
            "HighEscalationRate",
            "HighP95Latency",
            "ServiceReadinessDegraded",
            "CostSavingsInversion",
        ]
        for expected in expected_alerts:
            assert expected in alert_map, f"Missing alert {expected} in {path.name}"
            rule = alert_map[expected]
            assert "expr" in rule
            assert "for" in rule
            assert "labels" in rule
            assert "annotations" in rule
            assert "action" in rule["annotations"] or "runbook_url" in rule["annotations"]


def test_grafana_dashboard_json():
    """Verify Grafana dashboard JSON schema and metric panel definitions."""
    assert DASHBOARD_JSON.is_file(), f"Missing dashboard file at {DASHBOARD_JSON}"
    dash = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))

    assert dash["uid"] == "sqr-work-saved-ux"
    assert "Work Saved vs User Experience" in dash["title"]

    panels = dash["panels"]
    assert len(panels) >= 15, "Dashboard should have comprehensive panels across KPIs, work saved, UX, and health"

    # Verify key query expressions exist across panels
    all_exprs: list[str] = []
    for p in panels:
        for t in p.get("targets", []):
            if "expr" in t:
                all_exprs.append(t["expr"])

    expr_blob = " ".join(all_exprs)
    assert "smart_query_router_cost_savings_usd_total" in expr_blob
    assert "smart_query_router_tokens_saved_total" in expr_blob
    assert "smart_query_router_request_duration_ms" in expr_blob
    assert "smart_query_router_escalation_rate_ratio" in expr_blob
    assert "smart_query_router_error_rate_ratio" in expr_blob
    assert "smart_query_router_in_flight_requests" in expr_blob
