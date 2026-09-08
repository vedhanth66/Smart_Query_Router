"""Production-Grade Metrics Collector for Smart Query Router.

Provides:
- Thread-safe aggregation of production telemetry:
  1. Request counts by route, HTTP status, and canary cohort.
  2. Latency percentiles (P50, P90, P95, P99) overall and disaggregated by route.
  3. Categorized errors (timeouts, gateway, client, rate limits) and error rate ratio.
  4. Cache behavior (exact hits, semantic hits, misses, bypasses) and hit rate ratio.
  5. Route distribution counts and percentages (local, simple, complex, needs-evaluation).
  6. Escalation telemetry by trigger reason and escalation rate ratio.
  7. Estimated tokens saved (input compression, cache avoided, local avoided) vs baseline.
  8. Estimated cost proxy savings ($ USD) against canonical PricingConfig.
  9. Subsystem service health status gauges and active in-flight concurrency.
- Strict Privacy Guarantee:
  Strictly never stores, logs, or exports raw query text, prompts, or sensitive strings.
- Prometheus exposition format generator (`export_prometheus`).
- Structured JSON summary report answering:
  "Are we saving work without degrading user experience?"
"""

from __future__ import annotations

import collections
from datetime import datetime, timezone
import math
import threading
import time
from typing import Any

from app.schemas.benchmark import PricingConfig


class ProductionMetricsCollector:
    """Thread-safe, privacy-preserving production telemetry collector."""

    def __init__(self, pricing_config: PricingConfig | None = None) -> None:
        self._lock = threading.Lock()
        self.pricing = pricing_config or PricingConfig()
        self.start_time = time.time()

        # In-flight & basic counts
        self._in_flight_requests: int = 0
        self._total_requests_received: int = 0
        self._total_requests_served: int = 0

        # Request Counters: (route, status, cohort) -> count
        self._requests_counter: collections.Counter[tuple[str, str, str]] = collections.Counter()

        # Route Distribution Counter: route -> count
        self._route_distribution: collections.Counter[str] = collections.Counter()

        # Error Counters: error_category -> count
        self._errors_counter: collections.Counter[str] = collections.Counter()

        # Cache Operation Counters: outcome -> count
        self._cache_counter: collections.Counter[str] = collections.Counter()

        # Escalation Counters: reason -> count
        self._escalation_counter: collections.Counter[str] = collections.Counter()
        self._total_escalations: int = 0
        self._total_simple_model_attempts: int = 0

        # Token Accounting
        self._tokens_saved_input_compression: int = 0
        self._tokens_saved_cache_avoided: int = 0
        self._tokens_saved_local_avoided: int = 0
        self._tokens_saved_total: int = 0
        self._tokens_consumed_strong: int = 0
        self._tokens_consumed_small: int = 0

        # Cost Accounting (USD)
        self._baseline_cost_usd: float = 0.0
        self._actual_cost_usd: float = 0.0

        # Latency Tracking: rolling buffer of last 2000 execution latencies (ms)
        self._recent_latencies: collections.deque[float] = collections.deque(maxlen=2000)
        self._recent_latencies_by_route: dict[str, collections.deque[float]] = {
            "local-eligible": collections.deque(maxlen=1000),
            "simple-model candidate": collections.deque(maxlen=1000),
            "complex-model candidate": collections.deque(maxlen=1000),
            "needs-evaluation": collections.deque(maxlen=1000),
        }

        # Rolling window for error rate (last 1000 requests: 1 for error, 0 for success)
        self._recent_errors: collections.deque[int] = collections.deque(maxlen=1000)

        # Quality Scores (rolling window of last 1000 observed scores)
        self._recent_confidence_scores: collections.deque[float] = collections.deque(maxlen=1000)
        self._recent_completeness_scores: collections.deque[float] = collections.deque(maxlen=1000)

        # Subsystem Health Status (1 = healthy/ready, 0 = degraded/unavailable)
        self._subsystem_health: dict[str, int] = {
            "gateway": 1,
            "cache": 1,
            "semantic_cache": 1,
            "rollout_manager": 1,
            "router": 1,
        }

    # -------------------------------------------------------------------------
    # Concurrency & In-Flight Tracking
    # -------------------------------------------------------------------------

    def increment_in_flight(self) -> int:
        with self._lock:
            self._in_flight_requests += 1
            self._total_requests_received += 1
            return self._in_flight_requests

    def decrement_in_flight(self) -> int:
        with self._lock:
            self._in_flight_requests = max(0, self._in_flight_requests - 1)
            self._total_requests_served += 1
            return self._in_flight_requests

    @property
    def in_flight_count(self) -> int:
        with self._lock:
            return self._in_flight_requests

    # -------------------------------------------------------------------------
    # Subsystem Health Updates
    # -------------------------------------------------------------------------

    def set_subsystem_health(self, subsystem: str, is_healthy: bool) -> None:
        with self._lock:
            self._subsystem_health[subsystem] = 1 if is_healthy else 0

    # -------------------------------------------------------------------------
    # Primary Telemetry Recording (Zero Raw Query Logging)
    # -------------------------------------------------------------------------

    def record_query_execution(
        self,
        *,
        route: str,
        status_code: int = 200,
        cohort: str = "unassigned",
        latency_ms: float = 0.0,
        cache_outcome: str = "NOT_CHECKED",
        is_escalated: bool = False,
        escalation_reason: str | None = None,
        has_error: bool = False,
        error_category: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        compressed_input_tokens: int | None = None,
        confidence_score: float | None = None,
        completeness_score: float | None = None,
    ) -> None:
        """Records telemetry for an executed query.
        
        PRIVACY GUARANTEE:
        Strictly no query text, prompt, or conversational turns accepted or stored.
        All inputs are categorized enums, numbers, or normalized keys.
        """
        # Normalize keys to low-cardinality values
        norm_route = str(route).strip().lower() if route else "unknown"
        norm_cohort = str(cohort).strip().lower() if cohort else "unassigned"
        norm_status = f"{status_code // 100}xx" if status_code >= 400 else "200"
        norm_cache = str(cache_outcome).strip().upper() if cache_outcome else "NOT_CHECKED"

        with self._lock:
            # 1. Increment request counter
            self._requests_counter[(norm_route, norm_status, norm_cohort)] += 1
            self._route_distribution[norm_route] += 1

            # 2. Latency profile
            safe_lat = max(0.0, float(latency_ms))
            self._recent_latencies.append(safe_lat)
            if norm_route in self._recent_latencies_by_route:
                self._recent_latencies_by_route[norm_route].append(safe_lat)

            # 3. Errors
            if has_error or status_code >= 400:
                self._recent_errors.append(1)
                err_cat = (error_category or "SERVER_ERROR").strip().upper()
                self._errors_counter[err_cat] += 1
            else:
                self._recent_errors.append(0)

            # 4. Cache Behavior
            self._cache_counter[norm_cache] += 1

            # 5. Escalation Telemetry
            if "simple" in norm_route or is_escalated:
                self._total_simple_model_attempts += 1
            if is_escalated:
                self._total_escalations += 1
                reason_key = (escalation_reason or "UNSPECIFIED").strip().upper()
                self._escalation_counter[reason_key] += 1

            # 6. Quality Scores
            if confidence_score is not None:
                self._recent_confidence_scores.append(max(0.0, min(1.0, float(confidence_score))))
            if completeness_score is not None:
                self._recent_completeness_scores.append(max(0.0, min(1.0, float(completeness_score))))

            # 7. Token & Cost Accounting vs Strong Baseline
            self._record_savings_and_costs_locked(
                norm_route=norm_route,
                norm_cache=norm_cache,
                is_escalated=is_escalated,
                input_tokens=max(0, input_tokens),
                output_tokens=max(0, output_tokens),
                compressed_input_tokens=max(0, compressed_input_tokens) if compressed_input_tokens is not None else None,
            )

    def _record_savings_and_costs_locked(
        self,
        *,
        norm_route: str,
        norm_cache: str,
        is_escalated: bool,
        input_tokens: int,
        output_tokens: int,
        compressed_input_tokens: int | None,
    ) -> None:
        """Internal helper calculating tokens and dollars saved under lock."""
        strong_in_rate = self.pricing.strong_input_price_per_million / 1_000_000.0
        strong_out_rate = self.pricing.strong_output_price_per_million / 1_000_000.0
        small_in_rate = self.pricing.small_input_price_per_million / 1_000_000.0
        small_out_rate = self.pricing.small_output_price_per_million / 1_000_000.0

        # Baseline cost (what strong-only would cost)
        base_cost = (input_tokens * strong_in_rate) + (output_tokens * strong_out_rate)
        self._baseline_cost_usd += base_cost

        # Case A: Cache Hit (Exact or Semantic)
        if norm_cache in ("EXACT_HIT", "SEMANTIC_HIT"):
            self._tokens_saved_cache_avoided += (input_tokens + output_tokens)
            self._tokens_saved_total += (input_tokens + output_tokens)
            self._actual_cost_usd += self.pricing.cache_lookup_cost_per_query
            return

        # Case B: Local Route (On-device execution)
        if "local" in norm_route:
            self._tokens_saved_local_avoided += (input_tokens + output_tokens)
            self._tokens_saved_total += (input_tokens + output_tokens)
            self._actual_cost_usd += self.pricing.local_cost_per_query
            return

        # Case C: Prompt Compression Savings (Input token reduction)
        effective_in_tokens = input_tokens
        if compressed_input_tokens is not None and compressed_input_tokens < input_tokens:
            input_saved = input_tokens - compressed_input_tokens
            self._tokens_saved_input_compression += input_saved
            self._tokens_saved_total += input_saved
            effective_in_tokens = compressed_input_tokens

        # Case D: Small Model Execution with Escalation Handled
        if is_escalated:
            small_attempt_cost = (effective_in_tokens * small_in_rate) + (output_tokens * small_out_rate)
            strong_escalated_cost = (effective_in_tokens * strong_in_rate) + (output_tokens * strong_out_rate)
            self._actual_cost_usd += (small_attempt_cost + strong_escalated_cost)
            self._tokens_consumed_small += (effective_in_tokens + output_tokens)
            self._tokens_consumed_strong += (effective_in_tokens + output_tokens)
            return

        # Case E: Simple Model Execution (No escalation)
        if "simple" in norm_route:
            actual_cost = (effective_in_tokens * small_in_rate) + (output_tokens * small_out_rate)
            self._actual_cost_usd += actual_cost
            self._tokens_consumed_small += (effective_in_tokens + output_tokens)
            return

        # Case F: Complex / Strong Model Execution
        actual_cost = (effective_in_tokens * strong_in_rate) + (output_tokens * strong_out_rate)
        self._actual_cost_usd += actual_cost
        self._tokens_consumed_strong += (effective_in_tokens + output_tokens)

    # -------------------------------------------------------------------------
    # Mathematical Percentile Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _compute_percentiles(latencies: list[float] | collections.deque[float]) -> dict[str, float]:
        if not latencies:
            return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0}
        sorted_vals = sorted(latencies)
        n = len(sorted_vals)
        mean_val = sum(sorted_vals) / n

        def _quantile(q: float) -> float:
            idx = min(int(n * q), n - 1)
            return sorted_vals[idx]

        return {
            "p50": round(_quantile(0.50), 3),
            "p90": round(_quantile(0.90), 3),
            "p95": round(_quantile(0.95), 3),
            "p99": round(_quantile(0.99), 3),
            "mean": round(mean_val, 3),
        }

    # -------------------------------------------------------------------------
    # Aggregated Summary Report Generator
    # -------------------------------------------------------------------------

    def get_summary_report(self) -> dict[str, Any]:
        """Generates a structured report answering:
        'Are we saving work without degrading user experience?'
        """
        with self._lock:
            uptime = round(time.time() - self.start_time, 2)
            total_reqs = self._total_requests_served

            # Latencies
            overall_lat = self._compute_percentiles(self._recent_latencies)
            route_latencies = {
                r: self._compute_percentiles(buf)
                for r, buf in self._recent_latencies_by_route.items()
            }

            # Error rate
            err_count = sum(self._recent_errors)
            err_denom = max(1, len(self._recent_errors))
            error_rate_ratio = round(err_count / err_denom, 4)

            # Cache hit rate
            exact_hits = self._cache_counter.get("EXACT_HIT", 0)
            semantic_hits = self._cache_counter.get("SEMANTIC_HIT", 0)
            misses = self._cache_counter.get("MISS", 0)
            cache_lookups = exact_hits + semantic_hits + misses
            cache_hit_rate = round((exact_hits + semantic_hits) / max(1, cache_lookups), 4)

            # Escalation rate
            esc_denom = max(1, self._total_simple_model_attempts)
            escalation_rate = round(self._total_escalations / esc_denom, 4)

            # Route distribution percentages
            route_dist_counts = dict(self._route_distribution)
            dist_denom = max(1, sum(route_dist_counts.values()))
            route_dist_pct = {
                r: round((cnt / dist_denom) * 100.0, 2)
                for r, cnt in route_dist_counts.items()
            }

            # Cost & Work savings
            cost_saved_usd = max(0.0, round(self._baseline_cost_usd - self._actual_cost_usd, 6))
            cost_savings_pct = (
                round((cost_saved_usd / self._baseline_cost_usd) * 100.0, 2)
                if self._baseline_cost_usd > 0.0
                else 0.0
            )

            # Quality metrics
            avg_conf = (
                round(sum(self._recent_confidence_scores) / len(self._recent_confidence_scores), 3)
                if self._recent_confidence_scores
                else 1.0
            )
            avg_comp = (
                round(sum(self._recent_completeness_scores) / len(self._recent_completeness_scores), 3)
                if self._recent_completeness_scores
                else 1.0
            )

            # High-level Verdict on "Are we saving work without degrading user experience?"
            is_saving_work = (cost_saved_usd > 0.0 or self._tokens_saved_total > 0 or cache_hit_rate > 0.05)
            is_ux_healthy = (
                error_rate_ratio <= 0.02
                and escalation_rate <= 0.15
                and overall_lat["p95"] <= 2500.0
                and avg_comp >= 0.70
            )

            if is_saving_work and is_ux_healthy:
                verdict = "SAVING_WORK_HEALTHY"
                verdict_statement = "Yes: Routing delivers measurable token and cost savings while maintaining latency, error rate, and quality thresholds."
            elif is_saving_work and not is_ux_healthy:
                verdict = "SAVING_WORK_UX_DEGRADED"
                verdict_statement = "Warning: Router achieves work reduction but exceeds user experience latency/error/escalation thresholds."
            elif not is_saving_work and is_ux_healthy:
                verdict = "NEUTRAL_UX_HEALTHY"
                verdict_statement = "Neutral: System is performing reliably, but traffic mix or routing policies have not yielded significant savings yet."
            else:
                verdict = "DEGRADED_NO_SAVINGS"
                verdict_statement = "Critical: System is experiencing regressions and failing to produce work savings."

            return {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": uptime,
                "status_verdict": verdict,
                "verdict_statement": verdict_statement,
                "work_saved": {
                    "net_cost_saved_usd": cost_saved_usd,
                    "cost_savings_pct": cost_savings_pct,
                    "baseline_cost_usd": round(self._baseline_cost_usd, 6),
                    "actual_cost_usd": round(self._actual_cost_usd, 6),
                    "total_tokens_saved": self._tokens_saved_total,
                    "tokens_saved_breakdown": {
                        "input_compression": self._tokens_saved_input_compression,
                        "cache_avoided": self._tokens_saved_cache_avoided,
                        "local_avoided": self._tokens_saved_local_avoided,
                    },
                    "cache_hit_rate_ratio": cache_hit_rate,
                    "cache_operations": dict(self._cache_counter),
                    "route_distribution_counts": route_dist_counts,
                    "route_distribution_pct": route_dist_pct,
                },
                "user_experience": {
                    "overall_latency_ms": overall_lat,
                    "route_latencies_ms": route_latencies,
                    "error_rate_ratio": error_rate_ratio,
                    "errors_by_category": dict(self._errors_counter),
                    "escalation_rate_ratio": escalation_rate,
                    "total_escalations": self._total_escalations,
                    "escalations_by_reason": dict(self._escalation_counter),
                    "average_confidence_score": avg_conf,
                    "average_completeness_score": avg_comp,
                },
                "service_health": {
                    "in_flight_requests": self._in_flight_requests,
                    "total_requests_received": self._total_requests_received,
                    "total_requests_served": total_reqs,
                    "subsystems": dict(self._subsystem_health),
                    "is_all_subsystems_healthy": all(v == 1 for v in self._subsystem_health.values()),
                },
            }

    # -------------------------------------------------------------------------
    # Prometheus Exposition Format Generator
    # -------------------------------------------------------------------------

    def export_prometheus(self) -> str:
        """Exports all metrics in Prometheus text exposition format (version 0.0.4)."""
        with self._lock:
            uptime = round(time.time() - self.start_time, 2)
            overall_lat = self._compute_percentiles(self._recent_latencies)

            # Calculated ratios
            err_count = sum(self._recent_errors)
            err_denom = max(1, len(self._recent_errors))
            error_rate = err_count / err_denom

            exact_hits = self._cache_counter.get("EXACT_HIT", 0)
            semantic_hits = self._cache_counter.get("SEMANTIC_HIT", 0)
            misses = self._cache_counter.get("MISS", 0)
            cache_lookups = exact_hits + semantic_hits + misses
            cache_hit_rate = (exact_hits + semantic_hits) / max(1, cache_lookups)

            esc_denom = max(1, self._total_simple_model_attempts)
            escalation_rate = self._total_escalations / esc_denom

            cost_saved_usd = max(0.0, self._baseline_cost_usd - self._actual_cost_usd)
            cost_savings_ratio = (
                cost_saved_usd / self._baseline_cost_usd if self._baseline_cost_usd > 0.0 else 0.0
            )

            lines: list[str] = []

            # 1. In-flight requests (HPA queue-aware autoscaling signals)
            lines.extend([
                "# HELP smart_query_router_in_flight_requests Current active in-flight requests serving or waiting.",
                "# TYPE smart_query_router_in_flight_requests gauge",
                f"smart_query_router_in_flight_requests {self._in_flight_requests}",
                "",
                "# HELP http_requests_in_flight Generic in-flight request gauge for Kubernetes custom metrics HPA adapter.",
                "# TYPE http_requests_in_flight gauge",
                f"http_requests_in_flight {self._in_flight_requests}",
                "",
            ])

            # 2. Total requests served
            lines.extend([
                "# HELP smart_query_router_requests_total Total number of HTTP requests received.",
                "# TYPE smart_query_router_requests_total counter",
                f"smart_query_router_requests_total {self._total_requests_served}",
                "",
            ])

            # 3. Disaggregated Request Counters
            lines.extend([
                "# HELP smart_query_router_requests_disaggregated_total Requests categorized by route, HTTP status class, and rollout cohort.",
                "# TYPE smart_query_router_requests_disaggregated_total counter",
            ])
            for (route, status, cohort), count in sorted(self._requests_counter.items()):
                lines.append(f'smart_query_router_requests_disaggregated_total{{route="{route}",status="{status}",cohort="{cohort}"}} {count}')
            lines.append("")

            # 4. Latency Percentiles (Overall)
            lines.extend([
                "# HELP smart_query_router_request_duration_ms Request duration percentiles in milliseconds.",
                "# TYPE smart_query_router_request_duration_ms gauge",
                f'smart_query_router_request_duration_ms{{quantile="0.5"}} {overall_lat["p50"]:.3f}',
                f'smart_query_router_request_duration_ms{{quantile="0.9"}} {overall_lat["p90"]:.3f}',
                f'smart_query_router_request_duration_ms{{quantile="0.95"}} {overall_lat["p95"]:.3f}',
                f'smart_query_router_request_duration_ms{{quantile="0.99"}} {overall_lat["p99"]:.3f}',
                f'smart_query_router_request_duration_ms{{quantile="mean"}} {overall_lat["mean"]:.3f}',
                "",
                "# HELP smart_query_router_request_duration_seconds Request duration percentiles in seconds.",
                "# TYPE smart_query_router_request_duration_seconds gauge",
                f'smart_query_router_request_duration_seconds{{quantile="0.5"}} {overall_lat["p50"] / 1000.0:.4f}',
                f'smart_query_router_request_duration_seconds{{quantile="0.9"}} {overall_lat["p90"] / 1000.0:.4f}',
                f'smart_query_router_request_duration_seconds{{quantile="0.95"}} {overall_lat["p95"] / 1000.0:.4f}',
                f'smart_query_router_request_duration_seconds{{quantile="0.99"}} {overall_lat["p99"] / 1000.0:.4f}',
                "",
            ])

            # 5. Route Latency Percentiles (P95 by Route)
            lines.extend([
                "# HELP smart_query_router_route_p95_latency_ms P95 latency in milliseconds disaggregated by route.",
                "# TYPE smart_query_router_route_p95_latency_ms gauge",
            ])
            for route_name, q in sorted(self._recent_latencies_by_route.items()):
                p95_val = self._compute_percentiles(q)["p95"]
                lines.append(f'smart_query_router_route_p95_latency_ms{{route="{route_name}"}} {p95_val:.3f}')
            lines.append("")

            # 6. Errors & Error Rate
            lines.extend([
                "# HELP smart_query_router_errors_total Total errors disaggregated by error category.",
                "# TYPE smart_query_router_errors_total counter",
            ])
            for cat, cnt in sorted(self._errors_counter.items()):
                lines.append(f'smart_query_router_errors_total{{category="{cat}"}} {cnt}')
            if not self._errors_counter:
                lines.append('smart_query_router_errors_total{category="NONE"} 0')
            lines.extend([
                "",
                "# HELP smart_query_router_error_rate_ratio Rolling error rate ratio over recent requests.",
                "# TYPE smart_query_router_error_rate_ratio gauge",
                f"smart_query_router_error_rate_ratio {error_rate:.4f}",
                "",
            ])

            # 7. Cache Behavior & Hit Rate
            lines.extend([
                "# HELP smart_query_router_cache_operations_total Cache operations disaggregated by outcome.",
                "# TYPE smart_query_router_cache_operations_total counter",
            ])
            for outcome, cnt in sorted(self._cache_counter.items()):
                lines.append(f'smart_query_router_cache_operations_total{{outcome="{outcome}"}} {cnt}')
            if not self._cache_counter:
                lines.append('smart_query_router_cache_operations_total{outcome="NOT_CHECKED"} 0')
            lines.extend([
                "",
                "# HELP smart_query_router_cache_hit_rate_ratio Ratio of exact and semantic cache hits over total lookups.",
                "# TYPE smart_query_router_cache_hit_rate_ratio gauge",
                f"smart_query_router_cache_hit_rate_ratio {cache_hit_rate:.4f}",
                "",
            ])

            # 8. Route Distribution
            lines.extend([
                "# HELP smart_query_router_route_distribution_total Total requests routed to each candidate route.",
                "# TYPE smart_query_router_route_distribution_total counter",
            ])
            for r_name, cnt in sorted(self._route_distribution.items()):
                lines.append(f'smart_query_router_route_distribution_total{{route="{r_name}"}} {cnt}')
            lines.append("")

            # 9. Escalations & Escalation Rate
            lines.extend([
                "# HELP smart_query_router_escalations_total Total simple-model queries escalated to strong model by trigger reason.",
                "# TYPE smart_query_router_escalations_total counter",
            ])
            for reason, cnt in sorted(self._escalation_counter.items()):
                lines.append(f'smart_query_router_escalations_total{{reason="{reason}"}} {cnt}')
            if not self._escalation_counter:
                lines.append('smart_query_router_escalations_total{reason="NONE"} 0')
            lines.extend([
                "",
                "# HELP smart_query_router_escalation_rate_ratio Ratio of simple-model queries requiring strong model escalation.",
                "# TYPE smart_query_router_escalation_rate_ratio gauge",
                f"smart_query_router_escalation_rate_ratio {escalation_rate:.4f}",
                "",
            ])

            # 10. Estimated Tokens Saved
            lines.extend([
                "# HELP smart_query_router_tokens_saved_total Total estimated tokens saved relative to always-strong baseline.",
                "# TYPE smart_query_router_tokens_saved_total counter",
                f'smart_query_router_tokens_saved_total{{type="input_compression"}} {self._tokens_saved_input_compression}',
                f'smart_query_router_tokens_saved_total{{type="cache_avoided"}} {self._tokens_saved_cache_avoided}',
                f'smart_query_router_tokens_saved_total{{type="local_avoided"}} {self._tokens_saved_local_avoided}',
                f'smart_query_router_tokens_saved_total{{type="total"}} {self._tokens_saved_total}',
                "",
            ])

            # 11. Estimated Cost Proxy ($ USD)
            lines.extend([
                "# HELP smart_query_router_baseline_cost_usd_total Estimated cumulative cost in USD if all queries used strong model.",
                "# TYPE smart_query_router_baseline_cost_usd_total counter",
                f"smart_query_router_baseline_cost_usd_total {self._baseline_cost_usd:.6f}",
                "",
                "# HELP smart_query_router_actual_cost_usd_total Estimated cumulative cost in USD of executed model routes.",
                "# TYPE smart_query_router_actual_cost_usd_total counter",
                f"smart_query_router_actual_cost_usd_total {self._actual_cost_usd:.6f}",
                "",
                "# HELP smart_query_router_cost_savings_usd_total Estimated net financial savings in USD (baseline - actual).",
                "# TYPE smart_query_router_cost_savings_usd_total counter",
                f"smart_query_router_cost_savings_usd_total {cost_saved_usd:.6f}",
                "",
                "# HELP smart_query_router_cost_savings_ratio Relative financial savings ratio (savings / baseline).",
                "# TYPE smart_query_router_cost_savings_ratio gauge",
                f"smart_query_router_cost_savings_ratio {cost_savings_ratio:.4f}",
                "",
            ])

            # 12. Service Health & Uptime
            lines.extend([
                "# HELP smart_query_router_subsystem_health Subsystem readiness probe state (1=ready, 0=degraded).",
                "# TYPE smart_query_router_subsystem_health gauge",
            ])
            for sub, val in sorted(self._subsystem_health.items()):
                lines.append(f'smart_query_router_subsystem_health{{subsystem="{sub}"}} {val}')
            lines.extend([
                "",
                "# HELP smart_query_router_uptime_seconds Process uptime in seconds.",
                "# TYPE smart_query_router_uptime_seconds gauge",
                f"smart_query_router_uptime_seconds {uptime:.2f}",
                "",
            ])

            return "\n".join(lines) + "\n"

    def reset_metrics_for_testing(self) -> None:
        """Resets all metrics state for deterministic unit test execution."""
        with self._lock:
            self.start_time = time.time()
            self._in_flight_requests = 0
            self._total_requests_received = 0
            self._total_requests_served = 0
            self._requests_counter.clear()
            self._route_distribution.clear()
            self._errors_counter.clear()
            self._cache_counter.clear()
            self._escalation_counter.clear()
            self._total_escalations = 0
            self._total_simple_model_attempts = 0
            self._tokens_saved_input_compression = 0
            self._tokens_saved_cache_avoided = 0
            self._tokens_saved_local_avoided = 0
            self._tokens_saved_total = 0
            self._tokens_consumed_strong = 0
            self._tokens_consumed_small = 0
            self._baseline_cost_usd = 0.0
            self._actual_cost_usd = 0.0
            self._recent_latencies.clear()
            for buf in self._recent_latencies_by_route.values():
                buf.clear()
            self._recent_errors.clear()
            self._recent_confidence_scores.clear()
            self._recent_completeness_scores.clear()
            for k in self._subsystem_health:
                self._subsystem_health[k] = 1


# Default production singleton
production_metrics = ProductionMetricsCollector()
