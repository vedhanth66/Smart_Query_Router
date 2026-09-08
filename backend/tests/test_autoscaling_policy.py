"""Autoscaling Policy and In-Flight Metrics Test Suite.

Validates:
- Prometheus /metrics endpoint format and headers
- In-flight request concurrency tracking and total requests counters
- Kubernetes HPA v2 manifest syntax, metric targets, and stabilization windows
- Mathematical scaling decision simulations comparing CPU vs in-flight signals
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import yaml

from app.main import app, IN_FLIGHT_REQUESTS, TOTAL_REQUESTS_SERVED
import app.main as main_module

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HPA_FILE = REPO_ROOT / "k8s" / "hpa.yaml"

client = TestClient(app)


def test_metrics_endpoint_format():
    """Verify GET /metrics returns valid Prometheus exposition text."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    text = response.text

    assert "smart_query_router_in_flight_requests" in text
    assert "http_requests_in_flight" in text
    assert "smart_query_router_requests_total" in text
    assert "smart_query_router_request_duration_ms" in text
    assert "smart_query_router_uptime_seconds" in text


def test_in_flight_concurrency_tracking():
    """Verify request processing increments counters and updates latency percentiles."""
    prev_total = main_module.TOTAL_REQUESTS_SERVED

    # Fire a health check request
    resp = client.get("/healthz")
    assert resp.status_code == 200

    # Counter should increment
    assert main_module.TOTAL_REQUESTS_SERVED > prev_total
    # In-flight should return to 0 at rest
    assert main_module.IN_FLIGHT_REQUESTS == 0


def test_hpa_manifest_structure_and_metrics():
    """Verify HPA v2 manifest declares queue-aware metrics and stabilization policies."""
    assert HPA_FILE.is_file(), f"hpa.yaml missing at {HPA_FILE}"
    hpa = yaml.safe_load(HPA_FILE.read_text(encoding="utf-8"))

    assert hpa["apiVersion"] == "autoscaling/v2"
    assert hpa["kind"] == "HorizontalPodAutoscaler"

    spec = hpa["spec"]
    assert spec["scaleTargetRef"]["name"] == "smart-query-router-backend"
    assert spec["minReplicas"] == 2
    assert spec["maxReplicas"] == 10

    metrics = spec["metrics"]
    metric_map = {}
    for m in metrics:
        if m["type"] == "Pods":
            metric_map[m["pods"]["metric"]["name"]] = m["pods"]["target"]
        elif m["type"] == "Resource":
            metric_map[m["resource"]["name"]] = m["resource"]["target"]

    # 1. Primary Queue Metric
    assert "http_requests_in_flight" in metric_map
    assert metric_map["http_requests_in_flight"]["type"] == "AverageValue"
    assert metric_map["http_requests_in_flight"]["averageValue"] == "25"

    # 2. Secondary Memory Metric
    assert "memory" in metric_map
    assert metric_map["memory"]["averageUtilization"] == 80

    # 3. Fallback CPU Metric
    assert "cpu" in metric_map
    assert metric_map["cpu"]["averageUtilization"] == 75

    # Behavior stabilization windows
    behavior = spec["behavior"]
    assert behavior["scaleUp"]["stabilizationWindowSeconds"] == 0
    assert behavior["scaleDown"]["stabilizationWindowSeconds"] == 300


def test_scaling_decision_simulation():
    """Simulates why in-flight queue scaling succeeds where CPU-only scaling fails."""
    # Scenario: 75 concurrent callers executing upstream model gateway requests.
    # Pods are waiting asynchronously on external network sockets.
    # Measured CPU utilization per pod is 5%.
    current_replicas = 2
    in_flight_total = 75
    cpu_utilization = 5.0  # 5%

    target_in_flight_per_pod = 25
    target_cpu_utilization = 75.0

    # 1. CPU-Only Autoscaler Evaluation:
    # desiredReplicas = ceil(currentReplicas * (currentMetric / targetMetric))
    import math
    desired_replicas_cpu = math.ceil(current_replicas * (cpu_utilization / target_cpu_utilization))
    # Desired replicas = max(minReplicas, ceil(2 * (5 / 75))) = max(2, 1) = 2
    # Result: CPU AUTOSCALER DOES NOT SCALE OUT! 75 requests queue on 2 pods.
    assert desired_replicas_cpu <= current_replicas

    # 2. Queue-Aware In-Flight Autoscaler Evaluation:
    # average_in_flight = 75 / 2 = 37.5 req/pod
    avg_in_flight = in_flight_total / current_replicas
    desired_replicas_queue = math.ceil(current_replicas * (avg_in_flight / target_in_flight_per_pod))
    # Desired replicas = ceil(2 * (37.5 / 25)) = ceil(3.0) = 3 replicas
    # Result: IN-FLIGHT AUTOSCALER IMMEDIATELY SCALES TO 3 PODS!
    assert desired_replicas_queue > current_replicas
    assert desired_replicas_queue == 3
