"""ML Router Shadow Mode and Evidence-Gated Activation Engine.

GOVERNANCE & SAFETY GUARANTEES:
1. Passive Shadow Evaluation:
   Executes ML router recommendations concurrently with the production rule router.
   Strictly never modifies the user-visible route, tier, instructions, or executed content.
2. Comprehensive Disagreement & Quality Tracking:
   Analyzes disagreements, calculates expected token cost savings, and flags
   potential quality degradation risks.
3. Strict Evidence-Gated Activation:
   Blocks activating ML routing until explicit sample size, agreement, quality risk,
   financial, and latency SLA thresholds are satisfied.
4. Immutable Guardrails:
   Even if activated, Gate 1 Deterministic Safety (greetings, arithmetic) and
   Gate 2 Context Primacy (conversational history) strictly override ML recommendations.
"""

from __future__ import annotations

import collections
from pathlib import Path
import threading
import time
from typing import Any
import numpy as np

from app.schemas.contract import NormalizedQueryPackage
from app.schemas.benchmark import PricingConfig
from app.schemas.shadow_routing import (
    LikelyQualityImpact,
    ShadowActivationThresholds,
    ShadowDisagreementType,
    ShadowEvaluationStatusReport,
    ShadowRoutingEvent,
    ThresholdCheckResult,
)
from app.ml.guarded_router import GuardedRouter
from app.ml.router_classifier import MLRouterClassifier
from app.optimizer.query_optimizer import estimate_token_count
from app.dataset.sanitizer import sanitize_text_snippet


# Canonical tier rank for cost ordering: local (0) < fast_cheap (1) < strong (2)
TIER_RANK: dict[str, int] = {
    "local": 0,
    "fast_cheap": 1,
    "strong": 2,
}

ROUTE_RANK: dict[str, int] = {
    "local-eligible": 0,
    "simple-model candidate": 1,
    "complex-model candidate": 2,
}

# High-complexity task categories with inherent quality sensitivity
QUALITY_SENSITIVE_TASKS = {
    "coding",
    "debugging",
    "reasoning",
    "analysis",
    "comparison",
    "creative writing",
}


class ShadowRouterService:
    """Manages passive shadow evaluation, telemetry aggregation, and gated activation."""

    def __init__(
        self,
        classifier: MLRouterClassifier | None = None,
        pricing: PricingConfig | None = None,
        default_thresholds: ShadowActivationThresholds | None = None,
        max_history: int = 1000,
    ) -> None:
        self._lock = threading.Lock()
        self.pricing = pricing or PricingConfig()
        self.default_thresholds = default_thresholds or ShadowActivationThresholds()
        self.max_history = max_history
        self.is_active: bool = False  # Production routing default: FALSE (shadow only)

        # Lazy-load or assign classifier
        self._classifier = classifier
        self._guarded_router: GuardedRouter | None = None
        self._events: collections.deque[ShadowRoutingEvent] = collections.deque(
            maxlen=max_history
        )

    def _ensure_guarded_router(self) -> GuardedRouter:
        """Lazily initializes the GuardedRouter and loads ML artifacts from disk if needed."""
        if self._guarded_router is None:
            if self._classifier is None:
                models_dir = Path(__file__).parent.parent / "models"
                if (models_dir / "router_classifier_v1.joblib").exists():
                    try:
                        self._classifier = MLRouterClassifier.load_artifacts(models_dir)
                    except Exception:
                        self._classifier = MLRouterClassifier()
                else:
                    self._classifier = MLRouterClassifier()

            self._guarded_router = GuardedRouter(ml_classifier=self._classifier)
        return self._guarded_router

    def evaluate_shadow(
        self,
        package: NormalizedQueryPackage,
        production_route: str,
        production_tier: str,
        production_cost_usd: float | None = None,
    ) -> ShadowRoutingEvent:
        """Evaluates ML recommendation in shadow mode without modifying production behavior."""
        start_t = time.perf_counter()
        guarded = self._ensure_guarded_router()

        # Extract context markers from package.context_candidates
        has_context_dep = bool(package.context_candidates)
        prior_turns: list[dict[str, Any]] = [
            {"role": t.role, "content": t.content} for t in (package.context_candidates or [])
        ]

        task_cat_str = package.task_category.value if package.task_category else None
        complexity_str = package.complexity_level.value if package.complexity_level else None

        # Build local signals dict from package if available
        local_sigs: dict[str, Any] | None = None
        if package.local_features:
            lf = package.local_features
            local_sigs = {
                "char_count": lf.char_count,
                "word_count": lf.word_count,
                "estimated_tokens": lf.estimated_tokens,
                "has_code": lf.has_code,
                "has_math": lf.has_math,
                "has_questions": lf.has_questions,
                "has_urls": lf.has_urls,
                "has_tables": lf.has_tables,
                "has_code_blocks": lf.has_code_blocks,
                "has_rich_input": lf.has_rich_input,
                "uppercase_ratio": lf.uppercase_ratio,
                "numeric_ratio": lf.numeric_ratio,
                "special_char_ratio": lf.special_char_ratio,
            }

        # Run guarded shadow routing
        decision = guarded.route_query(
            query_text=package.query_text,
            task_type=task_cat_str,
            complexity_label=complexity_str,
            has_context_dependency=has_context_dep,
            prior_conversation_turns=prior_turns,
            local_signals=local_sigs,
        )

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        ml_route = decision.final_route
        ml_tier = decision.recommended_tier
        ml_conf = decision.ml_confidence if decision.ml_confidence is not None else 1.0
        ml_gate = decision.decision_gate

        # Disagreement Classification
        is_agree = (production_route == ml_route)
        prod_rank = ROUTE_RANK.get(production_route, 2)
        ml_rank = ROUTE_RANK.get(ml_route, 2)

        if is_agree:
            disagreement_type = ShadowDisagreementType.AGREEMENT
        elif ml_route == "local-eligible" and production_route != "local-eligible":
            disagreement_type = ShadowDisagreementType.ML_LOCAL_INSTEAD_OF_MODEL
        elif ml_rank < prod_rank:
            disagreement_type = ShadowDisagreementType.ML_CHEAPER
        else:
            disagreement_type = ShadowDisagreementType.ML_MORE_EXPENSIVE

        # Cost & Expected Savings Simulation
        inp_tok = estimate_token_count(package.query_text)
        out_tok = 50  # Conservative estimate of average response tokens

        def _compute_tier_cost(tier_str: str) -> float:
            if tier_str == "local":
                return self.pricing.local_cost_per_query
            elif tier_str == "fast_cheap":
                return (
                    (inp_tok * self.pricing.small_input_price_per_million / 1_000_000.0)
                    + (out_tok * self.pricing.small_output_price_per_million / 1_000_000.0)
                )
            else:
                return (
                    (inp_tok * self.pricing.strong_input_price_per_million / 1_000_000.0)
                    + (out_tok * self.pricing.strong_output_price_per_million / 1_000_000.0)
                )

        cost_prod = (
            production_cost_usd
            if production_cost_usd is not None
            else _compute_tier_cost(production_tier)
        )
        cost_ml = _compute_tier_cost(ml_tier)
        expected_savings = round(cost_prod - cost_ml, 6)

        # Likely Quality Impact Assessment
        clean_task = (task_cat_str or "").lower()
        if is_agree:
            quality_impact = LikelyQualityImpact.NEUTRAL
        elif disagreement_type == ShadowDisagreementType.ML_MORE_EXPENSIVE:
            quality_impact = LikelyQualityImpact.QUALITY_ENHANCEMENT
        elif clean_task in QUALITY_SENSITIVE_TASKS or (complexity_str and complexity_str in ("HIGH", "VERY_HIGH")):
            # High-complexity query routed cheaper carries degradation risk
            quality_impact = LikelyQualityImpact.POTENTIAL_DEGRADATION_RISK
        elif ml_conf < 0.70:
            quality_impact = LikelyQualityImpact.POTENTIAL_DEGRADATION_RISK
        else:
            quality_impact = LikelyQualityImpact.QUALITY_PRESERVED_COST_OPTIMIZED

        # Sanitize query snippet for privacy
        sanitized_snippet, _ = sanitize_text_snippet(
            package.query_text, max_chars=120, authorized=True
        )

        event = ShadowRoutingEvent(
            timestamp_ms=int(time.time() * 1000),
            request_id=package.request_id,
            correlation_id=package.correlation_id or package.request_id,
            query_text_snippet=sanitized_snippet,
            task_category=task_cat_str,
            production_route=production_route,
            production_tier=production_tier,
            ml_route=ml_route,
            ml_tier=ml_tier,
            ml_confidence=round(ml_conf, 4),
            ml_decision_gate=ml_gate,
            is_agreement=is_agree,
            disagreement_type=disagreement_type,
            expected_savings_usd=expected_savings,
            likely_quality_impact=quality_impact,
            latency_overhead_ms=round(elapsed_ms, 4),
            embedding_telemetry=decision.embedding_telemetry,
        )

        with self._lock:
            self._events.append(event)

        return event

    def get_status_report(
        self,
        thresholds: ShadowActivationThresholds | None = None,
    ) -> ShadowEvaluationStatusReport:
        """Aggregates observed shadow metrics and verifies against activation threshold criteria."""
        th = thresholds or self.default_thresholds

        with self._lock:
            events = list(self._events)

        total = len(events)
        if total == 0:
            return ShadowEvaluationStatusReport(
                total_shadow_queries=0,
                is_ml_routing_active=self.is_active,
                agreement_count=0,
                agreement_rate_pct=0.0,
                disagreement_count=0,
                disagreement_rate_pct=0.0,
                disagreement_breakdown={},
                quality_impact_breakdown={},
                quality_risk_count=0,
                quality_risk_rate_pct=0.0,
                net_expected_savings_usd=0.0,
                avg_expected_savings_per_query_usd=0.0,
                avg_ml_confidence=0.0,
                avg_disagreement_confidence=0.0,
                p95_latency_ms=0.0,
                threshold_criteria=[
                    ThresholdCheckResult(
                        criterion_name="Minimum Sample Size",
                        threshold_value=th.min_sample_size,
                        actual_value=0,
                        passed=False,
                        details=f"No shadow queries recorded yet (requires {th.min_sample_size})",
                    )
                ],
                all_thresholds_satisfied=False,
                activation_status="ACTIVE" if self.is_active else "BLOCKED_BY_THRESHOLDS",
                gating_rationale="Insufficient evidence: 0 shadow queries recorded.",
                recent_events=[],
            )

        # 1. Agreement & Disagreement Breakdown
        agreement_count = sum(1 for e in events if e.is_agreement)
        agreement_rate_pct = round((agreement_count / total) * 100.0, 2)
        disagreement_count = total - agreement_count
        disagreement_rate_pct = round(100.0 - agreement_rate_pct, 2)

        disagree_dist: dict[str, int] = {}
        for dt in ShadowDisagreementType:
            cnt = sum(1 for e in events if e.disagreement_type == dt)
            if cnt > 0:
                disagree_dist[dt.value] = cnt

        # 2. Quality Impact Breakdown
        quality_dist: dict[str, int] = {}
        for qi in LikelyQualityImpact:
            cnt = sum(1 for e in events if e.likely_quality_impact == qi)
            if cnt > 0:
                quality_dist[qi.value] = cnt

        quality_risk_count = sum(
            1 for e in events if e.likely_quality_impact == LikelyQualityImpact.POTENTIAL_DEGRADATION_RISK
        )
        quality_risk_rate_pct = round((quality_risk_count / total) * 100.0, 2)

        # 3. Savings & Financial Accounting
        net_savings = round(sum(e.expected_savings_usd for e in events), 6)
        avg_savings = round(net_savings / total, 6)

        # 4. Confidence & Latency Accounting
        avg_conf = round(float(np.mean([e.ml_confidence for e in events])), 4)
        disagree_confs = [e.ml_confidence for e in events if not e.is_agreement]
        avg_disagree_conf = (
            round(float(np.mean(disagree_confs)), 4) if disagree_confs else 1.0
        )
        p95_lat = round(float(np.percentile([e.latency_overhead_ms for e in events], 95)), 4)

        # 5. Threshold Criteria Checklist
        criteria: list[ThresholdCheckResult] = [
            ThresholdCheckResult(
                criterion_name="Minimum Sample Size",
                threshold_value=th.min_sample_size,
                actual_value=total,
                passed=(total >= th.min_sample_size),
                details=f"{total}/{th.min_sample_size} queries observed",
            ),
            ThresholdCheckResult(
                criterion_name="Baseline Agreement Rate",
                threshold_value=f">={th.min_agreement_rate_pct}%",
                actual_value=f"{agreement_rate_pct}%",
                passed=(agreement_rate_pct >= th.min_agreement_rate_pct),
                details=f"Agreement is {agreement_rate_pct}% (minimum: {th.min_agreement_rate_pct}%)",
            ),
            ThresholdCheckResult(
                criterion_name="Maximum Quality Degradation Risk",
                threshold_value=f"<={th.max_quality_risk_pct}%",
                actual_value=f"{quality_risk_rate_pct}%",
                passed=(quality_risk_rate_pct <= th.max_quality_risk_pct),
                details=f"Quality risk is {quality_risk_rate_pct}% (maximum allowed: {th.max_quality_risk_pct}%)",
            ),
            ThresholdCheckResult(
                criterion_name="Net Cost Savings",
                threshold_value=f">=${th.min_net_savings_usd:.4f}",
                actual_value=f"${net_savings:.4f}",
                passed=(net_savings >= th.min_net_savings_usd),
                details=f"Total projected savings: ${net_savings:.4f}",
            ),
            ThresholdCheckResult(
                criterion_name="P95 Latency SLA",
                threshold_value=f"<={th.max_p95_latency_ms}ms",
                actual_value=f"{p95_lat}ms",
                passed=(p95_lat <= th.max_p95_latency_ms),
                details=f"P95 latency overhead: {p95_lat}ms (budget: {th.max_p95_latency_ms}ms)",
            ),
            ThresholdCheckResult(
                criterion_name="Disagreement Confidence",
                threshold_value=f">={th.min_disagreement_confidence}",
                actual_value=avg_disagree_conf,
                passed=(avg_disagree_conf >= th.min_disagreement_confidence),
                details=f"Average confidence on divergent recommendations: {avg_disagree_conf}",
            ),
        ]

        all_passed = all(c.passed for c in criteria)

        if self.is_active:
            activation_status = "ACTIVE"
            rationale = "ML routing is actively governing production queries with guardrails."
        elif all_passed:
            activation_status = "ELIGIBLE_FOR_ACTIVATION"
            rationale = (
                f"All {len(criteria)} threshold criteria are satisfied across {total} shadow queries. "
                "Evidence supports activating ML routing."
            )
        else:
            activation_status = "BLOCKED_BY_THRESHOLDS"
            failing = [c.criterion_name for c in criteria if not c.passed]
            rationale = (
                f"Activation blocked by unsatisfied threshold criteria: {', '.join(failing)}."
            )

        return ShadowEvaluationStatusReport(
            total_shadow_queries=total,
            is_ml_routing_active=self.is_active,
            agreement_count=agreement_count,
            agreement_rate_pct=agreement_rate_pct,
            disagreement_count=disagreement_count,
            disagreement_rate_pct=disagreement_rate_pct,
            disagreement_breakdown=disagree_dist,
            quality_impact_breakdown=quality_dist,
            quality_risk_count=quality_risk_count,
            quality_risk_rate_pct=quality_risk_rate_pct,
            net_expected_savings_usd=net_savings,
            avg_expected_savings_per_query_usd=avg_savings,
            avg_ml_confidence=avg_conf,
            avg_disagreement_confidence=avg_disagree_conf,
            p95_latency_ms=p95_lat,
            threshold_criteria=criteria,
            all_thresholds_satisfied=all_passed,
            activation_status=activation_status,
            gating_rationale=rationale,
            recent_events=events[-20:],
        )

    def try_activate(
        self,
        force: bool = False,
        justification: str | None = None,
        thresholds: ShadowActivationThresholds | None = None,
    ) -> ShadowEvaluationStatusReport:
        """Attempts to activate ML routing, requiring all threshold criteria to be satisfied."""
        report = self.get_status_report(thresholds=thresholds)

        if not report.all_thresholds_satisfied and not force:
            failing = [c.criterion_name for c in report.threshold_criteria if not c.passed]
            raise ValueError(
                f"Cannot activate ML routing: Threshold criteria failed: {', '.join(failing)}. "
                f"Observe more shadow queries or adjust project thresholds."
            )

        self.is_active = True
        return self.get_status_report(thresholds=thresholds)

    def deactivate(self) -> ShadowEvaluationStatusReport:
        """Deactivates ML routing and returns system to passive shadow evaluation."""
        self.is_active = False
        return self.get_status_report()

    def clear_history(self) -> None:
        """Clears observed shadow events for fresh evaluation runs."""
        with self._lock:
            self._events.clear()


# Default singleton instance for application runtime
default_shadow_router = ShadowRouterService()
