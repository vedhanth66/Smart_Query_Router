# Smart Query Router: Production Observability & Alerting Guide

This directory contains the production-grade monitoring, alerting, and dashboard configuration for the Smart Query Router backend.

## Core Operational Question: *"Are we saving work without degrading user experience?"*

A smart router is only beneficial if the computational and financial work it saves exceeds its routing overhead, while never compromising answer quality, latency, or system availability.

This observability suite structures telemetry directly into two balancing pillars:
1. **Work Saved (Work & Cost Reduction)**:
   - Cumulative net dollar savings vs an unoptimized, always-strong model baseline ($2.50 / $10.00 per 1M input/output tokens).
   - Total tokens saved via semantic query optimization, on-device local execution, and exact/semantic cache hits.
   - Route distribution shift (proportion of requests kept local or on fast/cheap tiers).
   - Cache hit rate and LLM inference offload.
2. **User Experience (Latency, Availability, and Quality Guardrails)**:
   - P95 and P99 request latency percentiles (must remain under SLA threshold of 2500ms).
   - Model escalation rate (percentage of fast/cheap model executions rejected by quality evaluators; must remain $\le 15\%$).
   - System error rate (gateway failures, timeouts, provider outages; must remain $\le 2\%$).
   - Observed response quality signals (completeness and confidence metrics $\ge 0.70$).

---

## Metric Reference & Prometheus Exposition

The backend exposes metrics at `GET /metrics` in Prometheus text format (`version=0.0.4`):

| Metric Name | Type | Description |
| :--- | :--- | :--- |
| `smart_query_router_cost_savings_usd_total` | Counter | Net cumulative financial savings in USD ($) |
| `smart_query_router_baseline_cost_usd_total` | Counter | Cumulative cost if 100% of queries went to strong model |
| `smart_query_router_actual_cost_usd_total` | Counter | Cumulative cost of actually executed routes |
| `smart_query_router_cost_savings_ratio` | Gauge | Ratio of savings to baseline cost |
| `smart_query_router_tokens_saved_total{type=...}` | Counter | Disaggregated token savings (`input_compression`, `cache_avoided`, `local_avoided`, `total`) |
| `smart_query_router_request_duration_ms{quantile=...}` | Gauge | Rolling latency percentiles (P50, P90, P95, P99, mean) |
| `smart_query_router_route_p95_latency_ms{route=...}` | Gauge | P95 latency disaggregated by candidate route |
| `smart_query_router_error_rate_ratio` | Gauge | Rolling error rate ratio over recent requests |
| `smart_query_router_errors_total{category=...}` | Counter | Total errors by category (`TIMEOUT`, `GATEWAY_ERROR`, etc.) |
| `smart_query_router_cache_hit_rate_ratio` | Gauge | Ratio of cache hits (exact + semantic) over total lookups |
| `smart_query_router_cache_operations_total{outcome=...}` | Counter | Cache operations (`EXACT_HIT`, `SEMANTIC_HIT`, `MISS`, `BYPASS`) |
| `smart_query_router_escalation_rate_ratio` | Gauge | Ratio of simple-model queries escalated to strong model |
| `smart_query_router_escalations_total{reason=...}` | Counter | Escalations disaggregated by trigger reason |
| `smart_query_router_route_distribution_total{route=...}` | Counter | Request count per chosen route |
| `smart_query_router_in_flight_requests` | Gauge | Active requests in-flight or waiting in event loop |
| `http_requests_in_flight` | Gauge | Generic custom metric for Kubernetes HPA |
| `smart_query_router_subsystem_health{subsystem=...}` | Gauge | Readiness state per subsystem (1=ready, 0=degraded) |
| `smart_query_router_uptime_seconds` | Gauge | Backend process uptime in seconds |

---

## Strict Privacy Guarantee

> [!IMPORTANT]
> **Zero Raw Query Content Logging**:
> In accordance with zero-trust and enterprise privacy standards:
> - User prompt text, query text, conversation history, and model responses are **strictly forbidden** from being stored in metrics memory, exported in Prometheus labels, or printed to metric log channels.
> - Metric labels are restricted to low-cardinality, strictly bounded enums (`route`, `status`, `category`, `reason`, `quantile`, `subsystem`).
> - An automated regression test asserts that private tokens or sample user queries never appear in Prometheus exports or JSON summaries.

---

## Actionable Alerts & Runbooks

Alerts are designed strictly for **actionable operational conditions** to eliminate alert fatigue. Transient blips and normal traffic variations do not alert.

### 1. `HighErrorRate`
- **Condition**: Error rate > 2% sustained for 5 minutes (`smart_query_router_error_rate_ratio > 0.02`).
- **Severity**: Critical.
- **Runbook**:
  1. Inspect `smart_query_router_errors_total{category=...}` for the dominant failure type (`TIMEOUT`, `GATEWAY_ERROR`, etc.).
  2. Verify external LLM provider status dashboards and API quotas.
  3. Verify provider credentials in Kubernetes Secret `/run/secrets/api-keys/`.

### 2. `HighEscalationRate`
- **Condition**: Escalation rate > 15% sustained for 10 minutes (`smart_query_router_escalation_rate_ratio > 0.15`).
- **Severity**: Warning.
- **Runbook**:
  1. Inspect `smart_query_router_escalations_total{reason=...}` (`LOW_CONFIDENCE`, `LOW_COMPLETENESS`, `HEURISTIC_TRIGGER`).
  2. Check for task type drift (e.g. sudden surge of complex coding or deep reasoning queries).
  3. If persistent, trip the emergency kill switch via `POST /api/v1/rollout/kill-switch` or trigger automated rollback to revert 100% traffic to deterministic routing.

### 3. `HighP95Latency`
- **Condition**: P95 latency > 2500ms sustained for 5 minutes (`smart_query_router_request_duration_ms{quantile="0.95"} > 2500`).
- **Severity**: Warning.
- **Runbook**:
  1. Check `smart_query_router_in_flight_requests` to see if requests are queuing.
  2. Check HPA pod replicas to verify autoscaling is scaling out to meet concurrency demand.
  3. Check upstream model provider streaming latency.

### 4. `ServiceReadinessDegraded`
- **Condition**: Any subsystem unready for > 1 minute (`smart_query_router_subsystem_health == 0`).
- **Severity**: Critical.
- **Runbook**:
  1. Query `GET /readyz` to identify the degraded subsystem (`cache`, `gateway`, `semantic_cache`, or `rollout_manager`).
  2. Check container memory utilization to rule out OOM.
  3. Restart the container if in-memory cache structures have corrupted.

### 5. `CostSavingsInversion`
- **Condition**: Net savings $\le \$0.00$ after 50+ requests sustained for 15 minutes (`smart_query_router_cost_savings_usd_total <= 0 and smart_query_router_requests_total > 50`).
- **Severity**: Warning.
- **Runbook**:
  1. Check route distribution: are all queries going to the complex tier or escalating?
  2. Compare small model execution cost plus strong model re-run cost against baseline.
  3. Tune evaluator confidence/completeness thresholds or reduce small model allocation.

---

## Programmatic Summary API (`GET /api/v1/metrics/summary`)

Provides an automated JSON response summarizing:
- `status_verdict`: `SAVING_WORK_HEALTHY` | `SAVING_WORK_UX_DEGRADED` | `NEUTRAL_UX_HEALTHY` | `DEGRADED_NO_SAVINGS`
- `verdict_statement`: Human-readable summary answering *"Are we saving work without degrading user experience?"*
- `work_saved`: Net dollars saved, total tokens saved, cache hit rate, and route distribution.
- `user_experience`: Latency percentiles, escalation rate, error rate, and average quality scores.
- `service_health`: In-flight requests, total requests, and subsystem health states.
