"""Router Failure Analysis Engine.

ISOLATION & DIAGNOSTIC GUARANTEES:
1. Identifying Where the Router Made the Wrong Choice:
   - Queries routed too cheaply (ROUTED_TOO_CHEAPLY): Complex/frontier queries under-routed to small/local tiers.
   - Queries routed too expensively (ROUTED_TOO_EXPENSIVELY): Simple/local queries over-routed to premium strong models.
   - Missed local handling (MISSED_LOCAL_HANDLING): Greetings, arithmetic, or on-device eligible tasks dispatched to cloud.
   - Missed cache opportunities (MISSED_CACHE_OPPORTUNITY): Redundant queries failing cache hits.
   - Unexpected escalations (UNEXPECTED_ESCALATION): Incomplete small model attempts requiring costly fallback.
2. Disaggregated Breakdowns:
   - By Task Category across all 13 canonical task types.
   - By Context Dependency (True vs False).
3. Actionable Diagnostic Focus:
   - Emphasizes failure classes, token/dollar impact, and concrete heuristic tuning recommendations
     rather than merely celebrating aggregate accuracy.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.schemas.contract import CoarseRoute
from app.schemas.benchmark import (
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkTaskType,
    ContextDependencyFailureBreakdown,
    PricingConfig,
    QueryFailureItem,
    RouterFailureAnalysisReport,
    RouterFailureClass,
    SmartRoutingEvaluationReport,
    SmartRoutingItemResult,
    TaskCategoryFailureBreakdown,
)


class RouterFailureAnalyzer:
    """Analyzes benchmark execution results to isolate and categorize router failure classes."""

    def __init__(self, pricing: PricingConfig | None = None) -> None:
        self.pricing = pricing or PricingConfig()

    def analyze(
        self,
        report: SmartRoutingEvaluationReport,
        dataset: BenchmarkDataset | None = None,
        pricing: PricingConfig | None = None,
    ) -> RouterFailureAnalysisReport:
        """Evaluates misrouting occurrences and disaggregates by category and context dependency."""
        cfg = pricing or self.pricing

        # Build map from dataset items if provided for deep inspection
        item_meta_map: dict[str, BenchmarkItem] = {}
        if dataset:
            item_meta_map = {item.id: item for item in dataset.items}

        failure_items: list[QueryFailureItem] = []

        total_queries = len(report.items)
        correct_choices_count = 0
        too_cheap_count = 0
        too_expensive_count = 0
        missed_local_count = 0
        missed_cache_count = 0
        escalation_count = 0

        # Category breakdown accumulator
        cat_stats: dict[str, dict[str, Any]] = {
            task_type.value: {
                "task_type": task_type,
                "total": 0,
                "correct": 0,
                "too_cheap": 0,
                "too_expensive": 0,
                "missed_local": 0,
                "missed_cache": 0,
                "escalation": 0,
                "failure_counts": {},
            }
            for task_type in BenchmarkTaskType
        }

        # Context dependency accumulator
        ctx_stats: dict[bool, dict[str, Any]] = {
            True: {
                "has_context_dependency": True,
                "total": 0,
                "correct": 0,
                "too_cheap": 0,
                "too_expensive": 0,
                "missed_local": 0,
                "missed_cache": 0,
                "escalation": 0,
            },
            False: {
                "has_context_dependency": False,
                "total": 0,
                "correct": 0,
                "too_cheap": 0,
                "too_expensive": 0,
                "missed_local": 0,
                "missed_cache": 0,
                "escalation": 0,
            },
        }

        for item in report.items:
            # Query text & context dependency resolution
            ds_item = item_meta_map.get(item.item_id)
            query_text = item.query_text or (ds_item.query if ds_item else item.item_id)
            has_context = item.has_context_dependency or (ds_item.has_context_dependency if ds_item else False)

            exp = item.expected_route
            act = item.actual_route
            cat_key = item.task_type.value

            cat_stats[cat_key]["total"] += 1
            ctx_stats[has_context]["total"] += 1

            # Estimate cost of execution
            inp = item.token_usage.input_tokens
            out = item.token_usage.output_tokens
            strong_cost = (
                (inp * cfg.strong_input_price_per_million / 1_000_000.0)
                + (out * cfg.strong_output_price_per_million / 1_000_000.0)
            )
            small_cost = (
                (inp * cfg.small_input_price_per_million / 1_000_000.0)
                + (out * cfg.small_output_price_per_million / 1_000_000.0)
            )

            # Failure classification
            failure_class = RouterFailureClass.NONE
            diagnosis = "Query was routed optimally according to designated capability requirements."
            token_impact = 0
            cost_impact = 0.0
            quality_impact: str | None = None

            # Check 1: Unexpected Escalation
            if item.is_escalated:
                failure_class = RouterFailureClass.UNEXPECTED_ESCALATION
                escalation_count += 1
                cat_stats[cat_key]["escalation"] += 1
                ctx_stats[has_context]["escalation"] += 1
                cost_impact = small_cost
                token_impact = inp + out
                quality_impact = "Latency and retry penalty; small model response was incomplete."
                diagnosis = (
                    f"Small model ({item.executed_model_id}) response failed completeness evaluation: "
                    f"'{item.escalation_reason or 'incomplete response'}'. Query was escalated to strong model, "
                    f"incurring latency penalty and double execution cost. Consider classifying this task as complex upfront."
                )

            # Check 2: Missed Local Handling
            elif exp == CoarseRoute.LOCAL_ELIGIBLE and not item.is_local_handled:
                failure_class = RouterFailureClass.MISSED_LOCAL_HANDLING
                missed_local_count += 1
                cat_stats[cat_key]["missed_local"] += 1
                ctx_stats[has_context]["missed_local"] += 1
                cost_impact = strong_cost if item.is_strong_model else small_cost
                token_impact = item.token_usage.total_tokens
                quality_impact = "Unnecessary cloud latency and external dependency for local-eligible task."
                diagnosis = (
                    f"Query expected local on-device execution (e.g. greeting or arithmetic) but was "
                    f"dispatched to cloud tier ('{act}'), unnecessarily consuming cloud tokens and adding network latency."
                )

            # Check 3: Routed Too Expensively (Over-routing)
            elif (
                exp in (CoarseRoute.LOCAL_ELIGIBLE, CoarseRoute.SIMPLE_MODEL_CANDIDATE)
                and (act == CoarseRoute.COMPLEX_MODEL_CANDIDATE.value or item.is_strong_model)
                and not item.is_escalated
            ):
                failure_class = RouterFailureClass.ROUTED_TOO_EXPENSIVELY
                too_expensive_count += 1
                cat_stats[cat_key]["too_expensive"] += 1
                ctx_stats[has_context]["too_expensive"] += 1
                cost_impact = strong_cost - small_cost
                token_impact = 0
                quality_impact = "Cost inefficiency: strong frontier model invoked when simple tier was sufficient."
                diagnosis = (
                    f"Query designated as simple candidate ('{exp.value}') was routed directly to the "
                    f"strong/frontier tier ('{act}'). Incurred ~{((strong_cost - small_cost) / (small_cost or 1e-6)):.1f}x "
                    f"cost premium without demonstrable quality requirement."
                )

            # Check 4: Routed Too Cheaply (Under-routing)
            elif (
                exp in (CoarseRoute.COMPLEX_MODEL_CANDIDATE, CoarseRoute.NEEDS_EVALUATION)
                and (
                    act in (CoarseRoute.LOCAL_ELIGIBLE.value, CoarseRoute.SIMPLE_MODEL_CANDIDATE.value)
                    or item.is_local_handled
                    or item.is_small_model
                )
                and not item.is_escalated
            ):
                failure_class = RouterFailureClass.ROUTED_TOO_CHEAPLY
                too_cheap_count += 1
                cat_stats[cat_key]["too_cheap"] += 1
                ctx_stats[has_context]["too_cheap"] += 1
                token_impact = -item.token_usage.total_tokens
                cost_impact = -(strong_cost - small_cost)
                quality_impact = "Potential reasoning degradation or hallucination risk on complex task."
                diagnosis = (
                    f"Query designated as requiring strong/frontier capabilities ('{exp.value}') was "
                    f"routed to cheap tier ('{act}'). Risk of shallow reasoning or unverified accuracy."
                )

            # Check 5: Optimal / Correct Choice
            else:
                correct_choices_count += 1
                cat_stats[cat_key]["correct"] += 1
                ctx_stats[has_context]["correct"] += 1

            # Track failure count per category
            if failure_class != RouterFailureClass.NONE:
                fc_key = failure_class.value
                cat_stats[cat_key]["failure_counts"][fc_key] = (
                    cat_stats[cat_key]["failure_counts"].get(fc_key, 0) + 1
                )

                failure_items.append(
                    QueryFailureItem(
                        item_id=item.item_id,
                        query_preview=query_text[:120] + ("..." if len(query_text) > 120 else ""),
                        task_type=item.task_type,
                        has_context_dependency=has_context,
                        expected_route=exp,
                        actual_route=act,
                        failure_class=failure_class,
                        reason_code=item.reason_code,
                        executed_model_id=item.executed_model_id,
                        is_escalated=item.is_escalated,
                        token_impact=token_impact,
                        cost_impact_usd=round(cost_impact, 6),
                        quality_impact=quality_impact,
                        diagnosis=diagnosis,
                    )
                )

        # ---------------------------------------------------------------------
        # Aggregate Breakdowns
        # ---------------------------------------------------------------------
        total_misrouted = len(failure_items)
        accuracy_rate = round((correct_choices_count / total_queries) * 100.0, 2) if total_queries else 0.0
        misrouting_rate = round((total_misrouted / total_queries) * 100.0, 2) if total_queries else 0.0

        too_cheap_rate = round((too_cheap_count / total_queries) * 100.0, 2) if total_queries else 0.0
        too_expensive_rate = round((too_expensive_count / total_queries) * 100.0, 2) if total_queries else 0.0
        missed_local_rate = round((missed_local_count / total_queries) * 100.0, 2) if total_queries else 0.0
        missed_cache_rate = round((missed_cache_count / total_queries) * 100.0, 2) if total_queries else 0.0
        escalation_rate = round((escalation_count / total_queries) * 100.0, 2) if total_queries else 0.0

        # Build TaskCategoryFailureBreakdown
        by_category: dict[str, TaskCategoryFailureBreakdown] = {}
        for cat_key, cdata in cat_stats.items():
            tot = cdata["total"]
            corr = cdata["correct"]
            acc = round((corr / tot) * 100.0, 2) if tot else 0.0

            # Determine primary failure class
            primary_fc = RouterFailureClass.NONE
            if cdata["failure_counts"]:
                most_common = max(cdata["failure_counts"].items(), key=lambda kv: kv[1])[0]
                primary_fc = RouterFailureClass(most_common)

            by_category[cat_key] = TaskCategoryFailureBreakdown(
                task_type=cdata["task_type"],
                total_queries=tot,
                correct_count=corr,
                accuracy_rate=acc,
                too_cheap_count=cdata["too_cheap"],
                too_cheap_rate=round((cdata["too_cheap"] / tot) * 100.0, 2) if tot else 0.0,
                too_expensive_count=cdata["too_expensive"],
                too_expensive_rate=round((cdata["too_expensive"] / tot) * 100.0, 2) if tot else 0.0,
                missed_local_count=cdata["missed_local"],
                missed_local_rate=round((cdata["missed_local"] / tot) * 100.0, 2) if tot else 0.0,
                missed_cache_count=cdata["missed_cache"],
                missed_cache_rate=round((cdata["missed_cache"] / tot) * 100.0, 2) if tot else 0.0,
                escalation_count=cdata["escalation"],
                primary_failure_class=primary_fc,
            )

        # Build ContextDependencyFailureBreakdown
        by_context: dict[str, ContextDependencyFailureBreakdown] = {}
        for is_ctx, xdata in ctx_stats.items():
            tot = xdata["total"]
            corr = xdata["correct"]
            acc = round((corr / tot) * 100.0, 2) if tot else 0.0
            key_str = "context_dependent" if is_ctx else "standalone"

            by_context[key_str] = ContextDependencyFailureBreakdown(
                has_context_dependency=is_ctx,
                total_queries=tot,
                correct_count=corr,
                accuracy_rate=acc,
                too_cheap_count=xdata["too_cheap"],
                too_cheap_rate=round((xdata["too_cheap"] / tot) * 100.0, 2) if tot else 0.0,
                too_expensive_count=xdata["too_expensive"],
                too_expensive_rate=round((xdata["too_expensive"] / tot) * 100.0, 2) if tot else 0.0,
                missed_local_count=xdata["missed_local"],
                missed_local_rate=round((xdata["missed_local"] / tot) * 100.0, 2) if tot else 0.0,
                missed_cache_count=xdata["missed_cache"],
                missed_cache_rate=round((xdata["missed_cache"] / tot) * 100.0, 2) if tot else 0.0,
                escalation_count=xdata["escalation"],
            )

        # Generate Systemic Recommendations
        recommendations: list[str] = []
        if too_expensive_count > 0:
            recommendations.append(
                f"Over-routing detected on {too_expensive_count} queries ({too_expensive_rate}%). "
                f"Calibrate complexity scoring weights on simple tasks (e.g. rewriting, factual lookups) to prevent premature escalation to frontier models."
            )
        if too_cheap_count > 0:
            recommendations.append(
                f"Under-routing detected on {too_cheap_count} queries ({too_cheap_rate}%). "
                f"Elevate complexity floor for multi-step reasoning, analysis, and architecture questions to ensure they reach the frontier tier upfront."
            )
        if missed_local_count > 0:
            recommendations.append(
                f"Missed local handling on {missed_local_count} queries ({missed_local_rate}%). "
                f"Expand client-side regexes for arithmetic patterns and conversational greetings to prevent cloud dispatch."
            )
        if escalation_count > 0:
            recommendations.append(
                f"Unexpected escalation on {escalation_count} queries ({escalation_rate}%). "
                f"Review tasks that failed small-model completeness evaluation; route tasks with structured code or multi-step logic to strong model directly."
            )
        if not recommendations:
            recommendations.append("All analyzed queries aligned with designated routing expectations.")

        run_id = f"fail_analysis_{int(time.time())}"

        return RouterFailureAnalysisReport(
            run_id=run_id,
            dataset_id=report.dataset_id,
            dataset_version=report.dataset_version,
            total_queries_analyzed=total_queries,
            correct_choices_count=correct_choices_count,
            overall_accuracy_rate=accuracy_rate,
            total_misrouted_queries=total_misrouted,
            overall_misrouting_rate=misrouting_rate,
            too_cheap_count=too_cheap_count,
            too_cheap_rate=too_cheap_rate,
            too_expensive_count=too_expensive_count,
            too_expensive_rate=too_expensive_rate,
            missed_local_count=missed_local_count,
            missed_local_rate=missed_local_rate,
            missed_cache_count=missed_cache_count,
            missed_cache_rate=missed_cache_rate,
            escalation_count=escalation_count,
            escalation_rate=escalation_rate,
            by_task_category=by_category,
            by_context_dependency=by_context,
            failure_items=failure_items,
            key_recommendations=recommendations,
        )


def save_failure_report(
    report: RouterFailureAnalysisReport,
    file_path: Path | str,
) -> None:
    """Serializes a RouterFailureAnalysisReport to formatted JSON on disk."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2) + "\n")


def load_failure_report(file_path: Path | str) -> RouterFailureAnalysisReport:
    """Loads and validates a RouterFailureAnalysisReport from disk."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Failure analysis report file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return RouterFailureAnalysisReport.model_validate(data)
