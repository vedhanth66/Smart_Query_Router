"""ML Router Limited Rollout Manager, Emergency Kill Switch, and Measurable Regression Rollback Triggers.

Provides:
- Deterministic hash-based canary bucketing (0.0% - 100.0%).
- Global emergency kill switch for immediate 100% traffic diversion.
- Strict deterministic router fallback on kill switch, bucket exclusion, or ML failure.
- Multi-dimensional monitoring across 6 key metrics:
  1. Route distribution (local, simple, complex, needs-evaluation).
  2. Escalation rate (% of simple-model queries escalated to strong model).
  3. Error rate (% gateway errors, timeouts, provider failures).
  4. Latency profile (mean, P50, P90, P95, P99).
  5. Cache behavior (exact hits, semantic hits, misses, bypasses).
  6. Quality signals (completeness, confidence, detected issue codes).
- Automated regression rollback trigger engine that trips the kill switch based on measurable data.
"""

from __future__ import annotations

import collections
from datetime import datetime, timezone
import hashlib
import threading
import time
from typing import Any

import numpy as np

from app.schemas.contract import NormalizedQueryPackage
from app.schemas.rollout_routing import (
    CacheMetrics,
    CohortMetrics,
    ErrorMetrics,
    EscalationMetrics,
    LatencySummary,
    QualityMetrics,
    RollbackStatus,
    RollbackTriggerCheckResult,
    RollbackTriggerThresholds,
    RolloutCohort,
    RolloutConfig,
    RolloutConfigureRequest,
    RolloutStatusReport,
    RolloutTriggerState,
    RouteDistributionMetrics,
)


class RolloutManager:
    """Manages canary rollout, emergency kill switch, cohort telemetry, and automated rollback."""

    def __init__(
        self,
        config: RolloutConfig | None = None,
        thresholds: RollbackTriggerThresholds | None = None,
        max_history: int = 2000,
    ) -> None:
        self._lock = threading.Lock()
        self.config = config or RolloutConfig()
        self.thresholds = thresholds or RollbackTriggerThresholds()
        self.max_history = max_history

        self.rollback_status = RollbackStatus()
        self._consecutive_errors: int = 0
        self._events: collections.deque[dict[str, Any]] = collections.deque(maxlen=max_history)

    # -------------------------------------------------------------------------
    # Canary Cohort Determination & Fallback
    # -------------------------------------------------------------------------

    def should_route_to_ml(
        self,
        package: NormalizedQueryPackage,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> tuple[bool, str]:
        """Determines whether a query is allocated to the ML router or the deterministic router."""
        with self._lock:
            # 1. Emergency kill switch
            if self.config.kill_switch_engaged:
                return False, "KILL_SWITCH_ENGAGED"

            # 2. Automated rollback active
            if self.rollback_status.is_rollback_active:
                metric = self.rollback_status.trigger_metric or "REGRESSION"
                return False, f"ROLLBACK_ACTIVE_{metric}"

            eff_user = user_id or package.user_id or "default_user"
            eff_tenant = tenant_id or package.tenant_id or "default_tenant"

            # 3. Explicit denylists
            if eff_user in self.config.denylist_user_ids:
                return False, "DENYLIST_EXCLUDED_USER"
            if eff_tenant in self.config.denylist_tenant_ids:
                return False, "DENYLIST_EXCLUDED_TENANT"

            # 4. Explicit allowlists
            if eff_user in self.config.allowlist_user_ids:
                return True, "ALLOWLIST_INCLUDED_USER"
            if eff_tenant in self.config.allowlist_tenant_ids:
                return True, "ALLOWLIST_INCLUDED_TENANT"

            # 5. Rollout boundary conditions
            if self.config.rollout_percentage <= 0.0:
                return False, "ROLLOUT_ZERO_PERCENT"
            if self.config.rollout_percentage >= 100.0:
                return True, "ROLLOUT_FULL"

            # 6. Consistent deterministic hash partitioning
            partition_key = f"{self.config.canary_salt}:{eff_user}:{package.request_id}"
            digest = hashlib.sha256(partition_key.encode("utf-8")).hexdigest()
            bucket = int(digest[:8], 16) % 100

            if bucket < self.config.rollout_percentage:
                return True, f"CANARY_BUCKET_{bucket}"
            return False, f"CANARY_EXCLUDED_{bucket}"

    # -------------------------------------------------------------------------
    # Telemetry Recording
    # -------------------------------------------------------------------------

    def record_query_telemetry(
        self,
        cohort: RolloutCohort,
        route: str,
        latency_ms: float,
        cache_outcome: str | None = None,
        is_escalated: bool = False,
        escalation_reason: str | None = None,
        has_error: bool = False,
        failure_category: str | None = None,
        completeness_score: float | None = None,
        confidence_score: float | None = None,
        detected_issues: list[str] | None = None,
    ) -> None:
        """Records telemetry for an executed query across cohorts."""
        event = {
            "cohort": cohort.value if isinstance(cohort, RolloutCohort) else str(cohort),
            "route": route,
            "latency_ms": max(0.0, float(latency_ms)),
            "cache_outcome": cache_outcome or "NOT_CHECKED",
            "is_escalated": bool(is_escalated),
            "escalation_reason": escalation_reason,
            "has_error": bool(has_error),
            "failure_category": failure_category or "NONE",
            "completeness_score": completeness_score,
            "confidence_score": confidence_score,
            "detected_issues": detected_issues or [],
            "timestamp": time.time(),
        }

        with self._lock:
            self._events.append(event)

            # Track consecutive errors in ML cohort
            if cohort == RolloutCohort.ML:
                if has_error:
                    self._consecutive_errors += 1
                else:
                    self._consecutive_errors = 0
                self.rollback_status.consecutive_errors = self._consecutive_errors

        # Check automated rollback triggers immediately after recording
        self.evaluate_rollback_triggers()

    # -------------------------------------------------------------------------
    # Automated Measurable Regression Rollback Triggers
    # -------------------------------------------------------------------------

    def evaluate_rollback_triggers(self) -> RollbackStatus:
        """Evaluates quantifiable regression boundaries over recent ML traffic."""
        with self._lock:
            if self.rollback_status.is_rollback_active:
                return self.rollback_status.model_copy()

            # Filter recent ML events
            ml_events = [e for e in self._events if e["cohort"] == RolloutCohort.ML.value]
            sample_count = len(ml_events)

            checks: list[RollbackTriggerCheckResult] = []
            breached_metric: str | None = None
            observed_val: float | None = None
            threshold_val: float | None = None
            breach_reason: str | None = None

            # 1. Consecutive error breaker (evaluates immediately, even on low samples)
            consec_breached = self._consecutive_errors >= self.thresholds.max_consecutive_errors
            checks.append(
                RollbackTriggerCheckResult(
                    metric_name="consecutive_errors",
                    threshold=self.thresholds.max_consecutive_errors,
                    observed=self._consecutive_errors,
                    triggered=consec_breached,
                    details=f"Observed {self._consecutive_errors} consecutive failures (limit: {self.thresholds.max_consecutive_errors})",
                )
            )
            if consec_breached:
                breached_metric = "consecutive_errors"
                observed_val = float(self._consecutive_errors)
                threshold_val = float(self.thresholds.max_consecutive_errors)
                breach_reason = (
                    f"Consecutive failure breaker tripped: observed {self._consecutive_errors} "
                    f"sequential errors in ML cohort (threshold: {self.thresholds.max_consecutive_errors})"
                )

            # 2. Rate and performance checks (evaluate once min_eval_samples reached)
            if not breached_metric and sample_count >= self.thresholds.min_eval_samples:
                # A. Error rate regression
                error_count = sum(1 for e in ml_events if e["has_error"])
                error_rate_pct = round((error_count / sample_count) * 100.0, 2)
                err_breached = error_rate_pct > self.thresholds.max_error_rate_pct
                checks.append(
                    RollbackTriggerCheckResult(
                        metric_name="error_rate_pct",
                        threshold=self.thresholds.max_error_rate_pct,
                        observed=error_rate_pct,
                        triggered=err_breached,
                        details=f"ML error rate {error_rate_pct:.2f}% (max: {self.thresholds.max_error_rate_pct:.2f}%)",
                    )
                )
                if err_breached and not breached_metric:
                    breached_metric = "error_rate_pct"
                    observed_val = error_rate_pct
                    threshold_val = self.thresholds.max_error_rate_pct
                    breach_reason = (
                        f"Error rate regression: observed {error_rate_pct:.2f}% error rate "
                        f"exceeds safety threshold of {self.thresholds.max_error_rate_pct:.2f}% "
                        f"over {sample_count} queries"
                    )

                # B. Escalation rate regression
                simple_model_attempts = [e for e in ml_events if e["route"] == "simple-model candidate"]
                if simple_model_attempts:
                    escalated_count = sum(1 for e in simple_model_attempts if e["is_escalated"])
                    escalation_rate_pct = round((escalated_count / len(simple_model_attempts)) * 100.0, 2)
                else:
                    escalation_rate_pct = 0.0

                esc_breached = escalation_rate_pct > self.thresholds.max_escalation_rate_pct
                checks.append(
                    RollbackTriggerCheckResult(
                        metric_name="escalation_rate_pct",
                        threshold=self.thresholds.max_escalation_rate_pct,
                        observed=escalation_rate_pct,
                        triggered=esc_breached,
                        details=f"ML escalation rate {escalation_rate_pct:.2f}% (max: {self.thresholds.max_escalation_rate_pct:.2f}%)",
                    )
                )
                if esc_breached and not breached_metric:
                    breached_metric = "escalation_rate_pct"
                    observed_val = escalation_rate_pct
                    threshold_val = self.thresholds.max_escalation_rate_pct
                    breach_reason = (
                        f"Escalation rate regression: observed {escalation_rate_pct:.2f}% escalation rate "
                        f"exceeds safety threshold of {self.thresholds.max_escalation_rate_pct:.2f}%"
                    )

                # C. P95 Latency regression
                latencies = [e["latency_ms"] for e in ml_events]
                p95_lat = float(np.percentile(latencies, 95)) if latencies else 0.0
                lat_breached = p95_lat > self.thresholds.max_p95_latency_ms
                checks.append(
                    RollbackTriggerCheckResult(
                        metric_name="p95_latency_ms",
                        threshold=self.thresholds.max_p95_latency_ms,
                        observed=round(p95_lat, 2),
                        triggered=lat_breached,
                        details=f"ML P95 latency {p95_lat:.2f}ms (max: {self.thresholds.max_p95_latency_ms:.2f}ms)",
                    )
                )
                if lat_breached and not breached_metric:
                    breached_metric = "p95_latency_ms"
                    observed_val = round(p95_lat, 2)
                    threshold_val = self.thresholds.max_p95_latency_ms
                    breach_reason = (
                        f"P95 latency regression: observed {p95_lat:.2f}ms exceeds "
                        f"runtime SLA threshold of {self.thresholds.max_p95_latency_ms:.2f}ms"
                    )

                # D. Quality completeness regression
                evaluated_quality = [e["completeness_score"] for e in ml_events if e["completeness_score"] is not None]
                if evaluated_quality:
                    mean_quality = float(np.mean(evaluated_quality))
                    qual_breached = mean_quality < self.thresholds.min_quality_completeness
                    checks.append(
                        RollbackTriggerCheckResult(
                            metric_name="min_quality_completeness",
                            threshold=self.thresholds.min_quality_completeness,
                            observed=round(mean_quality, 3),
                            triggered=qual_breached,
                            details=f"Mean completeness {mean_quality:.3f} (min: {self.thresholds.min_quality_completeness:.3f})",
                        )
                    )
                    if qual_breached and not breached_metric:
                        breached_metric = "min_quality_completeness"
                        observed_val = round(mean_quality, 3)
                        threshold_val = self.thresholds.min_quality_completeness
                        breach_reason = (
                            f"Quality regression: mean completeness {mean_quality:.3f} falls below "
                            f"acceptable minimum of {self.thresholds.min_quality_completeness:.3f}"
                        )

            self.rollback_status.trigger_checks = checks

            # Trip automatic rollback if any measurable safety breach detected
            if breached_metric:
                self.config.kill_switch_engaged = True
                self.config.rollout_percentage = 0.0
                self.rollback_status.state = RolloutTriggerState.TRIGGERED
                self.rollback_status.is_rollback_active = True
                self.rollback_status.triggered_at = datetime.now(timezone.utc).isoformat()
                self.rollback_status.trigger_metric = breached_metric
                self.rollback_status.trigger_reason = breach_reason
                self.rollback_status.observed_value = observed_val
                self.rollback_status.threshold_value = threshold_val

            return self.rollback_status.model_copy()

    # -------------------------------------------------------------------------
    # Configuration, Kill Switch & Rollback Reset
    # -------------------------------------------------------------------------

    def set_kill_switch(self, engage: bool, reason: str) -> RolloutStatusReport:
        """Engages or disengages the global emergency kill switch."""
        with self._lock:
            self.config.kill_switch_engaged = engage
            if not engage and self.rollback_status.is_rollback_active:
                # Disengaging after an automated rollback sets status to manually overridden
                self.rollback_status.state = RolloutTriggerState.MANUALLY_OVERRIDDEN
                self.rollback_status.is_rollback_active = False

        return self.get_status_report()

    def update_config(self, req: RolloutConfigureRequest) -> RolloutStatusReport:
        """Updates rollout settings, canary percentage, or thresholds."""
        with self._lock:
            if req.kill_switch_engaged is not None:
                self.config.kill_switch_engaged = req.kill_switch_engaged
            if req.rollout_percentage is not None:
                self.config.rollout_percentage = req.rollout_percentage
            if req.canary_salt is not None:
                self.config.canary_salt = req.canary_salt
            if req.allowlist_user_ids is not None:
                self.config.allowlist_user_ids = req.allowlist_user_ids
            if req.denylist_user_ids is not None:
                self.config.denylist_user_ids = req.denylist_user_ids
            if req.allowlist_tenant_ids is not None:
                self.config.allowlist_tenant_ids = req.allowlist_tenant_ids
            if req.denylist_tenant_ids is not None:
                self.config.denylist_tenant_ids = req.denylist_tenant_ids
            if req.thresholds is not None:
                self.thresholds = req.thresholds

        return self.get_status_report()

    def reset_rollback(self, justification: str, restore_percentage: float = 5.0) -> RolloutStatusReport:
        """Safely resets an automated rollback after operator review."""
        with self._lock:
            self.rollback_status = RollbackStatus(
                state=RolloutTriggerState.HEALTHY,
                is_rollback_active=False,
                triggered_at=None,
                trigger_reason=None,
                trigger_metric=None,
                observed_value=None,
                threshold_value=None,
                consecutive_errors=0,
            )
            self._consecutive_errors = 0
            self.config.kill_switch_engaged = False
            self.config.rollout_percentage = min(100.0, max(0.0, float(restore_percentage)))

        return self.get_status_report()

    def clear_history(self) -> None:
        """Clears in-memory history and resets state for tests."""
        with self._lock:
            self._events.clear()
            self._consecutive_errors = 0
            self.rollback_status = RollbackStatus()
            self.config.kill_switch_engaged = False
            self.config.rollout_percentage = 10.0

    # -------------------------------------------------------------------------
    # Comprehensive Status Report & Aggregations
    # -------------------------------------------------------------------------

    def get_status_report(self) -> RolloutStatusReport:
        """Generates a complete multi-dimensional status report."""
        with self._lock:
            all_events = list(self._events)
            cfg = self.config.model_copy()
            rb_stat = self.rollback_status.model_copy()

        ml_events = [e for e in all_events if e["cohort"] == RolloutCohort.ML.value]
        control_events = [
            e for e in all_events
            if e["cohort"] in (RolloutCohort.CONTROL_RULE.value, RolloutCohort.FALLBACK_DETERMINISTIC.value)
        ]

        active_mode = "EMERGENCY_KILL_SWITCH" if cfg.kill_switch_engaged else (
            f"CANARY_ROLLOUT_{cfg.rollout_percentage:.1f}%" if cfg.rollout_percentage > 0 else "DETERMINISTIC_100%"
        )
        if rb_stat.is_rollback_active:
            active_mode = f"ROLLED_BACK_TO_DETERMINISTIC ({rb_stat.trigger_metric})"

        return RolloutStatusReport(
            total_queries_observed=len(all_events),
            active_routing_mode=active_mode,
            config=cfg,
            rollback_status=rb_stat,
            ml_cohort=self._aggregate_cohort("ML", ml_events),
            control_cohort=self._aggregate_cohort("CONTROL_RULE", control_events),
            blended_totals=self._aggregate_cohort("BLENDED", all_events),
        )

    def _aggregate_cohort(self, cohort_name: str, events: list[dict[str, Any]]) -> CohortMetrics:
        """Aggregates telemetry for a slice of events across all 6 dimensions."""
        n = len(events)
        if n == 0:
            return CohortMetrics(cohort_name=cohort_name, total_queries=0)

        # 1. Route distribution
        local_c = sum(1 for e in events if e["route"] == "local-eligible")
        simple_c = sum(1 for e in events if e["route"] == "simple-model candidate")
        complex_c = sum(1 for e in events if e["route"] == "complex-model candidate")
        eval_c = sum(1 for e in events if e["route"] == "needs-evaluation")

        route_dist = RouteDistributionMetrics(
            total_queries=n,
            local_eligible_count=local_c,
            local_eligible_pct=round((local_c / n) * 100.0, 2),
            simple_model_count=simple_c,
            simple_model_pct=round((simple_c / n) * 100.0, 2),
            complex_model_count=complex_c,
            complex_model_pct=round((complex_c / n) * 100.0, 2),
            needs_evaluation_count=eval_c,
            needs_evaluation_pct=round((eval_c / n) * 100.0, 2),
        )

        # 2. Escalation metrics
        simple_events = [e for e in events if e["route"] == "simple-model candidate"]
        n_simple = len(simple_events)
        escalation_c = sum(1 for e in simple_events if e["is_escalated"])
        reasons_dist: dict[str, int] = collections.defaultdict(int)
        for e in simple_events:
            if e["escalation_reason"]:
                reasons_dist[e["escalation_reason"][:64]] += 1

        escalations = EscalationMetrics(
            simple_model_attempts=n_simple,
            escalation_count=escalation_c,
            escalation_rate_pct=round((escalation_c / n_simple * 100.0), 2) if n_simple > 0 else 0.0,
            escalation_reasons=dict(reasons_dist),
        )

        # 3. Error metrics
        err_c = sum(1 for e in events if e["has_error"])
        cats_dist: dict[str, int] = collections.defaultdict(int)
        for e in events:
            if e["has_error"]:
                cats_dist[e["failure_category"]] += 1

        errors = ErrorMetrics(
            total_requests=n,
            error_count=err_c,
            error_rate_pct=round((err_c / n) * 100.0, 2),
            failure_categories=dict(cats_dist),
        )

        # 4. Latency summary
        latencies = [e["latency_ms"] for e in events]
        lat_summary = LatencySummary(
            sample_count=n,
            mean_ms=round(float(np.mean(latencies)), 2),
            p50_ms=round(float(np.percentile(latencies, 50)), 2),
            p90_ms=round(float(np.percentile(latencies, 90)), 2),
            p95_ms=round(float(np.percentile(latencies, 95)), 2),
            p99_ms=round(float(np.percentile(latencies, 99)), 2),
        )

        # 5. Cache metrics
        exact_hits = sum(1 for e in events if e["cache_outcome"] == "HIT")
        sem_hits = sum(1 for e in events if e.get("semantic_cache_outcome") == "SEMANTIC_HIT")
        misses = sum(1 for e in events if e["cache_outcome"] == "MISS")
        bypasses = sum(1 for e in events if e["cache_outcome"] in ("BYPASS", "NOT_CHECKED"))
        total_hits = exact_hits + sem_hits

        cache = CacheMetrics(
            total_queries=n,
            exact_hit_count=exact_hits,
            semantic_hit_count=sem_hits,
            miss_count=misses,
            bypass_count=bypasses,
            cache_hit_rate_pct=round((total_hits / n) * 100.0, 2),
        )

        # 6. Quality signals
        quality_scores = [e["completeness_score"] for e in events if e["completeness_score"] is not None]
        confidence_scores = [e["confidence_score"] for e in events if e["confidence_score"] is not None]
        issues_dist: dict[str, int] = collections.defaultdict(int)
        for e in events:
            for issue in e.get("detected_issues", []):
                issues_dist[issue] += 1

        quality = QualityMetrics(
            evaluated_queries=len(quality_scores),
            mean_completeness=round(float(np.mean(quality_scores)), 3) if quality_scores else 1.0,
            mean_confidence=round(float(np.mean(confidence_scores)), 3) if confidence_scores else 1.0,
            detected_issue_counts=dict(issues_dist),
        )

        return CohortMetrics(
            cohort_name=cohort_name,
            total_queries=n,
            route_distribution=route_dist,
            escalations=escalations,
            errors=errors,
            latency=lat_summary,
            cache=cache,
            quality=quality,
        )


# Global singleton rollout manager instance
default_rollout_manager = RolloutManager()
