"""Disaggregated comparative benchmark evaluation engine.

ISOLATION & EVALUATION GUARANTEES:
1. No Single "Savings" Number Without Denominators & Assumptions:
   Every savings or delta metric is encapsulated in a MetricWithDenominator object containing:
   - numerator (absolute difference)
   - denominator (explicit baseline reference value)
   - relative_change_pct ((numerator / denominator) * 100)
   - unit ("tokens", "USD", "ms", "queries", "ratio")
   - formula (mathematical definition)
   - interpretation (directionality explanation)
2. Disaggregated Savings Dimensions:
   - Input-token savings (cloud prompt token reduction)
   - Output-token savings (cloud completion token reduction)
   - Cache savings (tokens & cost saved specifically by exact and semantic cache hits)
   - Avoided-model-call savings (queries handled completely on-device without cloud LLMs)
   - Latency changes (mean, p50 median, p95 with speedup factor)
   - Quality changes (format compliance delta, non-empty delta, completeness delta, lexical overlap, parity/degradation counts)
3. Configurable & Explicit Pricing Model:
   Takes a PricingConfig specifying per-million token rates for strong and small models,
   and per-query proxy costs for local execution and cache lookups.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.schemas.benchmark import (
    AvoidedCallsBreakdown,
    BaselineItemResult,
    ComparativeEvaluationReport,
    CostSavingsBreakdown,
    ItemComparativeDetail,
    LatencyDeltaBreakdown,
    MetricWithDenominator,
    PricingConfig,
    QualityDeltaBreakdown,
    SmartRoutingEvaluationReport,
    SmartRoutingItemResult,
    StrongModelBaselineReport,
    TokenSavingsBreakdown,
)


def _make_metric(
    numerator: float,
    denominator: float,
    unit: str,
    formula: str,
    interpretation: str,
    round_digits: int = 4,
) -> MetricWithDenominator:
    """Helper to safely build MetricWithDenominator with explicit rounding and zero-check."""
    pct: float | None = None
    if denominator != 0.0:
        pct = round((numerator / denominator) * 100.0, 2)

    return MetricWithDenominator(
        numerator=round(numerator, round_digits),
        denominator=round(denominator, round_digits),
        relative_change_pct=pct,
        unit=unit,
        formula=formula,
        interpretation=interpretation,
    )


class ComparativeBenchmarkEvaluator:
    """Evaluates smart-routing performance relative to an always-strong baseline."""

    def __init__(self, default_pricing: PricingConfig | None = None) -> None:
        self.pricing = default_pricing or PricingConfig()

    def compare(
        self,
        baseline_report: StrongModelBaselineReport,
        smart_routing_report: SmartRoutingEvaluationReport,
        pricing: PricingConfig | None = None,
    ) -> ComparativeEvaluationReport:
        """Computes disaggregated savings, latency changes, and quality deltas."""
        cfg = pricing or self.pricing

        # Index baseline items by item_id
        base_map: dict[str, BaselineItemResult] = {
            item.item_id: item for item in baseline_report.items
        }

        item_details: list[ItemComparativeDetail] = []

        total_base_cost = 0.0
        total_routing_cost = 0.0

        cache_tokens_saved = 0
        avoided_calls_tokens_saved = 0
        avoided_calls_count = 0

        small_substitution_savings_total = 0.0
        local_savings_total = 0.0
        cache_savings_total = 0.0
        escalation_overhead_total = 0.0

        base_fc_met = 0
        base_fc_total = 0
        sr_fc_met = 0
        sr_fc_total = 0

        quality_parity_count = 0
        quality_degraded_count = 0
        quality_improved_count = 0

        for sr_item in smart_routing_report.items:
            base_item = base_map.get(sr_item.item_id)
            if not base_item:
                continue

            # 1. Baseline costs
            base_inp = base_item.token_usage.input_tokens
            base_out = base_item.token_usage.output_tokens
            base_cost = (
                (base_inp * cfg.strong_input_price_per_million / 1_000_000.0)
                + (base_out * cfg.strong_output_price_per_million / 1_000_000.0)
            )
            total_base_cost += base_cost

            # 2. Smart routing costs & avoided calls
            sr_inp = sr_item.token_usage.input_tokens
            sr_out = sr_item.token_usage.output_tokens

            sr_cost = 0.0
            if sr_item.is_local_handled:
                sr_cost = cfg.local_cost_per_query
                avoided_calls_count += 1
                avoided_calls_tokens_saved += base_item.token_usage.total_tokens
                local_savings_total += (base_cost - sr_cost)

            elif sr_item.is_cache_hit:
                sr_cost = cfg.cache_lookup_cost_per_query
                avoided_calls_count += 1
                avoided_calls_tokens_saved += base_item.token_usage.total_tokens
                cache_tokens_saved += base_item.token_usage.total_tokens
                cache_savings_total += (base_cost - sr_cost)

            elif sr_item.is_small_model and not sr_item.is_escalated:
                sr_cost = (
                    (sr_inp * cfg.small_input_price_per_million / 1_000_000.0)
                    + (sr_out * cfg.small_output_price_per_million / 1_000_000.0)
                )
                small_substitution_savings_total += (base_cost - sr_cost)

            elif sr_item.is_escalated:
                # Incurred small model attempt + strong model execution
                small_overhead = (
                    (sr_inp * cfg.small_input_price_per_million / 1_000_000.0)
                    + (sr_out * cfg.small_output_price_per_million / 1_000_000.0)
                )
                strong_exec_cost = (
                    (sr_inp * cfg.strong_input_price_per_million / 1_000_000.0)
                    + (sr_out * cfg.strong_output_price_per_million / 1_000_000.0)
                )
                sr_cost = small_overhead + strong_exec_cost
                escalation_overhead_total += small_overhead

            else:
                # Direct strong model execution
                sr_cost = (
                    (sr_inp * cfg.strong_input_price_per_million / 1_000_000.0)
                    + (sr_out * cfg.strong_output_price_per_million / 1_000_000.0)
                )

            total_routing_cost += sr_cost
            cost_savings_item = base_cost - sr_cost

            # 3. Latency delta
            lat_delta = round(base_item.latency_ms - sr_item.latency_ms, 2)

            # 4. Token delta
            tok_delta = base_item.token_usage.total_tokens - sr_item.token_usage.total_tokens

            # 5. Quality assessment per item
            base_fc = base_item.answer_quality.meets_format_compliance
            sr_fc = sr_item.format_compliant

            if base_fc is not None:
                base_fc_total += 1
                if base_fc:
                    base_fc_met += 1

            if sr_fc is not None:
                sr_fc_total += 1
                if sr_fc:
                    sr_fc_met += 1

            # Quality verdict classification
            verdict = "PARITY"
            if base_fc is not None and sr_fc is not None:
                if base_fc and not sr_fc:
                    verdict = "DEGRADED"
                    quality_degraded_count += 1
                elif not base_fc and sr_fc:
                    verdict = "IMPROVED"
                    quality_improved_count += 1
                else:
                    verdict = "PARITY"
                    quality_parity_count += 1
            else:
                if sr_item.status != "SUCCESS":
                    verdict = "DEGRADED"
                    quality_degraded_count += 1
                else:
                    verdict = "PARITY"
                    quality_parity_count += 1

            item_details.append(
                ItemComparativeDetail(
                    item_id=sr_item.item_id,
                    task_type=sr_item.task_type,
                    route=sr_item.actual_route,
                    baseline_tokens=base_item.token_usage,
                    routing_tokens=sr_item.token_usage,
                    token_delta=tok_delta,
                    baseline_cost_usd=round(base_cost, 6),
                    routing_cost_usd=round(sr_cost, 6),
                    cost_savings_usd=round(cost_savings_item, 6),
                    baseline_latency_ms=base_item.latency_ms,
                    routing_latency_ms=sr_item.latency_ms,
                    latency_delta_ms=lat_delta,
                    baseline_format_compliant=base_fc,
                    routing_format_compliant=sr_fc,
                    lexical_overlap=sr_item.lexical_overlap,
                    quality_verdict=verdict,
                )
            )

        n_items = len(item_details)

        # ---------------------------------------------------------------------
        # 1. Token Savings Breakdown
        # ---------------------------------------------------------------------
        inp_saved = baseline_report.total_input_tokens - smart_routing_report.total_input_tokens
        out_saved = baseline_report.total_output_tokens - smart_routing_report.total_output_tokens
        tot_saved = baseline_report.total_tokens - smart_routing_report.total_tokens

        token_savings = TokenSavingsBreakdown(
            input_token_savings=_make_metric(
                numerator=float(inp_saved),
                denominator=float(baseline_report.total_input_tokens),
                unit="tokens",
                formula="(baseline_input_tokens - routing_input_tokens) / baseline_input_tokens * 100",
                interpretation="Positive indicates reduction in input tokens passed to cloud models",
            ),
            output_token_savings=_make_metric(
                numerator=float(out_saved),
                denominator=float(baseline_report.total_output_tokens),
                unit="tokens",
                formula="(baseline_output_tokens - routing_output_tokens) / baseline_output_tokens * 100",
                interpretation="Positive indicates reduction in output tokens generated by models",
            ),
            total_token_savings=_make_metric(
                numerator=float(tot_saved),
                denominator=float(baseline_report.total_tokens),
                unit="tokens",
                formula="(baseline_total_tokens - routing_total_tokens) / baseline_total_tokens * 100",
                interpretation="Positive indicates net overall token savings across benchmark queries",
            ),
            cache_token_savings=_make_metric(
                numerator=float(cache_tokens_saved),
                denominator=float(baseline_report.total_tokens),
                unit="tokens",
                formula="cache_hit_baseline_tokens / baseline_total_tokens * 100",
                interpretation="Tokens spared exclusively via exact or semantic cache hits",
            ),
            avoided_call_token_savings=_make_metric(
                numerator=float(avoided_calls_tokens_saved),
                denominator=float(baseline_report.total_tokens),
                unit="tokens",
                formula="avoided_calls_baseline_tokens / baseline_total_tokens * 100",
                interpretation="Tokens spared from queries completely avoiding cloud model execution",
            ),
        )

        # ---------------------------------------------------------------------
        # 2. Cost Savings Breakdown
        # ---------------------------------------------------------------------
        net_cost_saved = total_base_cost - total_routing_cost

        cost_savings = CostSavingsBreakdown(
            pricing_assumptions=cfg,
            baseline_total_cost_usd=round(total_base_cost, 6),
            smart_routing_total_cost_usd=round(total_routing_cost, 6),
            net_cost_savings=_make_metric(
                numerator=net_cost_saved,
                denominator=total_base_cost,
                unit="USD",
                formula="(baseline_total_cost - smart_routing_total_cost) / baseline_total_cost * 100",
                interpretation="Positive indicates dollar savings relative to always-strong baseline",
                round_digits=6,
            ),
            small_model_substitution_savings=_make_metric(
                numerator=small_substitution_savings_total,
                denominator=total_base_cost,
                unit="USD",
                formula="small_model_savings / baseline_total_cost * 100",
                interpretation="Cost saved by routing simple queries to small model instead of strong",
                round_digits=6,
            ),
            local_handled_savings=_make_metric(
                numerator=local_savings_total,
                denominator=total_base_cost,
                unit="USD",
                formula="local_handled_savings / baseline_total_cost * 100",
                interpretation="Cost saved by handling greetings and arithmetic on-device without cloud fees",
                round_digits=6,
            ),
            cache_hit_savings=_make_metric(
                numerator=cache_savings_total,
                denominator=total_base_cost,
                unit="USD",
                formula="cache_hit_savings / baseline_total_cost * 100",
                interpretation="Cost saved by serving queries from exact or semantic cache",
                round_digits=6,
            ),
            escalation_cost_overhead=_make_metric(
                numerator=escalation_overhead_total,
                denominator=total_base_cost,
                unit="USD",
                formula="escalation_retry_cost / baseline_total_cost * 100",
                interpretation="Dollar overhead incurred when incomplete small-model attempts were escalated",
                round_digits=6,
            ),
        )

        # ---------------------------------------------------------------------
        # 3. Avoided Calls Breakdown
        # ---------------------------------------------------------------------
        local_cnt = smart_routing_report.local_handled_count
        cache_cnt = smart_routing_report.cache_hit_count
        tot_q = smart_routing_report.total_queries

        avoided_calls = AvoidedCallsBreakdown(
            total_queries=tot_q,
            avoided_calls_count=avoided_calls_count,
            avoided_calls_ratio=_make_metric(
                numerator=float(avoided_calls_count),
                denominator=float(tot_q),
                unit="queries",
                formula="avoided_calls_count / total_queries * 100",
                interpretation="Percentage of queries completely avoiding cloud model execution",
            ),
            local_handled_calls=_make_metric(
                numerator=float(local_cnt),
                denominator=float(tot_q),
                unit="queries",
                formula="local_handled_count / total_queries * 100",
                interpretation="Proportion of queries resolved locally on-device",
            ),
            cache_hit_calls=_make_metric(
                numerator=float(cache_cnt),
                denominator=float(tot_q),
                unit="queries",
                formula="cache_hit_count / total_queries * 100",
                interpretation="Proportion of queries served from cache",
            ),
            avoided_call_token_savings=_make_metric(
                numerator=float(avoided_calls_tokens_saved),
                denominator=float(baseline_report.total_tokens),
                unit="tokens",
                formula="avoided_calls_tokens / baseline_total_tokens * 100",
                interpretation="Share of total baseline tokens eliminated by avoided calls",
            ),
            avoided_call_cost_savings_usd=_make_metric(
                numerator=local_savings_total + cache_savings_total,
                denominator=total_base_cost,
                unit="USD",
                formula="(local_savings + cache_savings) / baseline_total_cost * 100",
                interpretation="Share of total baseline dollar cost eliminated by avoided calls",
                round_digits=6,
            ),
        )

        # ---------------------------------------------------------------------
        # 4. Latency Delta Breakdown
        # ---------------------------------------------------------------------
        lat_mean_delta = baseline_report.mean_latency_ms - smart_routing_report.mean_latency_ms
        lat_p50_delta = baseline_report.p50_latency_ms - smart_routing_report.p50_latency_ms
        lat_p95_delta = baseline_report.p95_latency_ms - smart_routing_report.p95_latency_ms

        speedup: float | None = None
        if smart_routing_report.mean_latency_ms > 0.0:
            speedup = round(baseline_report.mean_latency_ms / smart_routing_report.mean_latency_ms, 2)

        latency_changes = LatencyDeltaBreakdown(
            mean_latency_delta_ms=_make_metric(
                numerator=lat_mean_delta,
                denominator=baseline_report.mean_latency_ms,
                unit="ms",
                formula="(baseline_mean_latency - routing_mean_latency) / baseline_mean_latency * 100",
                interpretation="Positive indicates latency reduction (faster execution); negative indicates overhead",
            ),
            p50_latency_delta_ms=_make_metric(
                numerator=lat_p50_delta,
                denominator=baseline_report.p50_latency_ms,
                unit="ms",
                formula="(baseline_p50_latency - routing_p50_latency) / baseline_p50_latency * 100",
                interpretation="Median latency delta compared to strong baseline",
            ),
            p95_latency_delta_ms=_make_metric(
                numerator=lat_p95_delta,
                denominator=baseline_report.p95_latency_ms,
                unit="ms",
                formula="(baseline_p95_latency - routing_p95_latency) / baseline_p95_latency * 100",
                interpretation="95th percentile latency delta compared to strong baseline",
            ),
            speedup_factor=speedup,
        )

        # ---------------------------------------------------------------------
        # 5. Quality Delta Breakdown
        # ---------------------------------------------------------------------
        base_fc_rate = (base_fc_met / base_fc_total) if base_fc_total > 0 else 1.0
        sr_fc_rate = smart_routing_report.quality_metrics.format_compliance_rate
        fc_delta = sr_fc_rate - base_fc_rate

        non_empty_base = 1.0  # Baseline had 100% non-empty
        non_empty_sr = smart_routing_report.quality_metrics.non_empty_rate
        non_empty_delta = non_empty_sr - non_empty_base

        lex_overlap = smart_routing_report.quality_metrics.mean_lexical_overlap
        lex_metric = (
            _make_metric(
                numerator=lex_overlap,
                denominator=1.0,
                unit="ratio",
                formula="mean_jaccard_similarity / 1.0 * 100",
                interpretation="Mean token Jaccard similarity between smart routing answers and strong baseline answers",
            )
            if lex_overlap is not None
            else None
        )

        quality_changes = QualityDeltaBreakdown(
            format_compliance_delta=_make_metric(
                numerator=fc_delta,
                denominator=base_fc_rate if base_fc_rate > 0 else 1.0,
                unit="ratio",
                formula="(smart_routing_compliance_rate - baseline_compliance_rate) / baseline_compliance_rate * 100",
                interpretation="Positive indicates format compliance gain; negative indicates compliance drop",
            ),
            non_empty_rate_delta=_make_metric(
                numerator=non_empty_delta,
                denominator=non_empty_base,
                unit="ratio",
                formula="(smart_routing_non_empty_rate - baseline_non_empty_rate) / baseline_non_empty_rate * 100",
                interpretation="Non-empty response rate difference relative to baseline",
            ),
            mean_completeness_delta=None,
            mean_lexical_overlap=lex_metric,
            items_evaluated=n_items,
            quality_parity_count=quality_parity_count,
            quality_degraded_count=quality_degraded_count,
            quality_improved_count=quality_improved_count,
        )

        run_id = f"comp_eval_{int(time.time())}"

        return ComparativeEvaluationReport(
            run_id=run_id,
            baseline_run_id=baseline_report.run_id,
            smart_routing_run_id=smart_routing_report.run_id,
            dataset_id=smart_routing_report.dataset_id,
            dataset_version=smart_routing_report.dataset_version,
            pricing_assumptions=cfg,
            token_savings=token_savings,
            cost_savings=cost_savings,
            avoided_calls=avoided_calls,
            latency_changes=latency_changes,
            quality_changes=quality_changes,
            item_details=item_details,
        )


def save_comparative_report(
    report: ComparativeEvaluationReport,
    file_path: Path | str,
) -> None:
    """Serializes a ComparativeEvaluationReport to disk as formatted JSON."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2) + "\n")


def load_comparative_report(file_path: Path | str) -> ComparativeEvaluationReport:
    """Loads and validates a ComparativeEvaluationReport from disk."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Comparative report file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ComparativeEvaluationReport.model_validate(data)
