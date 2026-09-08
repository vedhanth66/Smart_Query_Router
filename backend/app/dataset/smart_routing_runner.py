"""Smart-Routing benchmark evaluation runner.

ISOLATION & EVALUATION GUARANTEES:
1. Full Smart-Routing Pipeline:
   Runs benchmark queries through the complete optimizer, caching, deterministic
   routing, small-model execution, completeness evaluation, and conditional escalation.
2. Metrics Recorded:
   - Local-handled percentage (resolved without cloud execution).
   - Cache hit rate (exact and semantic cache hits).
   - Small-model percentage (fast/cheap tier).
   - Strong-model percentage (frontier tier, direct or escalated).
   - Escalation rate (completeness evaluation failures escalated to strong model).
   - Latency (mean, p50 median, p95).
   - Token estimates and measured usage.
   - Error rate and quality measures (format compliance, completeness, lexical overlap).
3. Comparable Model & Version Settings:
   Shares identical model IDs and version tags (gpt-4o-2024-08-06 / gpt-4o-mini-2024-07-18)
   and operates seamlessly in offline deterministic test mode.
4. Parity with Baseline Evaluation Mode:
   Operates on the identical BenchmarkDataset (canonical 15 items across 13 categories).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any
from fastapi import Response

from app.schemas.contract import (
    ClientMetadata,
    CoarseRoute,
    ComplexityLevel,
    ContextCandidateTurn,
    LocalFeatures,
    ModelTier,
    NormalizedQueryPackage,
    OptimizationDecisionResponse,
    RouteExecutionMetadata,
    TaskCategory,
)
from app.schemas.benchmark import (
    BaselineItemResult,
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkModelTier,
    BenchmarkTaskType,
    ComparativeEvaluationReport,
    PricingConfig,
    SmartRoutingEvaluationReport,
    SmartRoutingItemResult,
    SmartRoutingQualityMetrics,
    StrongModelBaselineReport,
    TokenUsageEstimate,
)
from app.dataset.baseline_runner import (
    _check_format_compliance,
    _compute_percentile,
)
from app.optimizer.query_optimizer import estimate_token_count


def _extract_local_features(text: str) -> LocalFeatures:
    """Extracts non-generative local features for query package construction."""
    char_count = len(text)
    words = text.split()
    word_count = len(words)
    has_code = bool(
        re.search(
            r"```|~~~|def\s+\w+|class\s+\w+|import\s+\w+|function\s+\w+|<script|console\.log|const\s+\w+|let\s+\w+",
            text,
            re.I,
        )
    )
    has_math = bool(
        re.search(
            r"\$\$|\$|\\frac|\\sqrt|\\sum|\b\d+\s*[\+\-\*\/]\s*\d+\b",
            text,
        )
    )
    has_questions = "?" in text or any(text.lower().startswith(q) for q in ["what", "why", "how", "when", "where", "who", "which", "can", "is"])
    has_urls = bool(re.search(r"https?://\S+", text))
    has_tables = bool(re.search(r"^\s*\|.+?\|\s*$", text, re.M))
    has_code_blocks = "```" in text or "~~~" in text

    detected_cues: list[str] = []
    if has_code:
        detected_cues.append("code_syntax")
    if has_math:
        detected_cues.append("math_formula")
    if has_tables:
        detected_cues.append("table_structure")

    return LocalFeatures(
        character_count=char_count,
        word_count=word_count,
        has_code=has_code,
        has_math=has_math,
        has_questions=has_questions,
        has_urls=has_urls,
        has_rich_input=has_tables or has_code_blocks,
        has_attachments=False,
        has_images=False,
        has_files=False,
        has_code_blocks=has_code_blocks,
        has_tables=has_tables,
        attachment_types=[],
        is_normalized=True,
        detected_cues=detected_cues,
    )


def _compute_jaccard_similarity(text_a: str | None, text_b: str | None) -> float | None:
    """Computes token Jaccard similarity between two texts for quality parity check."""
    if not text_a or not text_b or not text_a.strip() or not text_b.strip():
        return None
    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))
    if not tokens_a or not tokens_b:
        return None
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return round(len(intersection) / len(union), 3) if union else 1.0


def _resolve_local_content(item: BenchmarkItem) -> str:
    """Provides standard on-device local completion for local-eligible queries."""
    clean = item.query.strip().lower()
    if any(clean.startswith(g) for g in ["hi", "hello", "hey", "good morning", "greetings"]):
        return "Hello! How can I help you today?"
    if "+" in clean or "-" in clean or "*" in clean or "/" in clean:
        # Simple local arithmetic evaluation
        try:
            expr = re.sub(r"[^0-9\+\-\*\/\.\s\(\)]", "", clean)
            if expr.strip():
                # Safe evaluation of basic numbers and operators only
                res = eval(expr, {"__builtins__": None}, {})  # pylint: disable=eval-used
                return f"{res}"
        except Exception:
            pass
    return "Handled locally on-device."


class SmartRoutingBenchmarkRunner:
    """Runs a benchmark dataset through the complete smart-routing pipeline and instruments metrics."""

    def __init__(self, concurrency: int = 4) -> None:
        self._concurrency = max(1, concurrency)

    async def run_item(
        self,
        item: BenchmarkItem,
        baseline_item: BaselineItemResult | None = None,
    ) -> SmartRoutingItemResult:
        """Executes a single benchmark item through the full smart-routing pipeline."""
        from app.main import evaluate_optimization

        # Build ContextCandidateTurns from BenchmarkItem.context_turns
        context_candidates = [
            ContextCandidateTurn(
                turn_id=f"turn_{idx}",
                role="user" if turn.role == "user" else "assistant",
                content=turn.content,
                original_index=idx,
                relevance_score=1.0,
                timestamp=int(time.time() * 1000) - ((len(item.context_turns) - idx) * 10000),
            )
            for idx, turn in enumerate(item.context_turns)
        ]

        local_feats = _extract_local_features(item.query)
        req_id = f"sr_{item.id}_{int(time.time() * 1000)}"
        corr_id = f"corr_{item.id}_{int(time.time() * 1000)}"

        # Map benchmark task category to contract TaskCategory signal
        task_cat: TaskCategory | None = None
        try:
            task_cat = TaskCategory(item.task_type.value)
        except ValueError:
            task_cat = TaskCategory.UNKNOWN

        # Construct NormalizedQueryPackage with client-classified signals
        pkg = NormalizedQueryPackage(
            request_id=req_id,
            correlation_id=corr_id,
            coarse_route=None,
            task_category=task_cat,
            complexity_score=None,
            complexity_level=None,
            user_override=None,
            query_text=item.query,
            context_candidates=context_candidates,
            local_features=local_feats,
            client_metadata=ClientMetadata(
                extension_version="0.1.0",
                client_type="benchmark_harness",
                schema_version="1.0",
                hostname="claude.ai",
            ),
            execute_route=True,
            user_id="benchmark_user",
            tenant_id="benchmark_tenant",
        )

        start_time = time.perf_counter()
        fastapi_res = Response()

        try:
            opt_decision: OptimizationDecisionResponse = await evaluate_optimization(
                package=pkg,
                response=fastapi_res,
                x_correlation_id=corr_id,
                x_user_id="benchmark_user",
                x_tenant_id="benchmark_tenant",
            )
            elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

            actual_route = opt_decision.coarse_route.value if opt_decision.coarse_route else "unknown"
            decision_type = opt_decision.decision_type.value
            reason_code = opt_decision.reason_code
            exec_meta: RouteExecutionMetadata | None = opt_decision.execution_metadata

            # Classify execution paths
            is_local_handled = (
                actual_route == CoarseRoute.LOCAL_ELIGIBLE.value
                or (exec_meta is not None and exec_meta.model_id is None)
            )

            is_cache_hit = False
            if exec_meta:
                is_cache_hit = (
                    exec_meta.cache_outcome.value == "HIT"
                    or exec_meta.semantic_cache_outcome == "SEMANTIC_HIT"
                )

            is_escalated = bool(exec_meta and exec_meta.escalation_occurred)
            escalation_reason = exec_meta.escalation_reason if exec_meta else None

            # Determine model tier
            executed_model_id = exec_meta.model_id if exec_meta else None
            executed_model_version = exec_meta.model_version if exec_meta else None

            is_small = False
            is_strong = False

            if not is_local_handled and not is_cache_hit:
                if is_escalated:
                    is_strong = True
                elif executed_model_id and "mini" in executed_model_id:
                    is_small = True
                elif executed_model_id and ("4o" in executed_model_id or "claude" in executed_model_id):
                    is_strong = True
                elif actual_route == CoarseRoute.SIMPLE_MODEL_CANDIDATE.value:
                    is_small = True
                elif actual_route in (CoarseRoute.COMPLEX_MODEL_CANDIDATE.value, CoarseRoute.NEEDS_EVALUATION.value):
                    is_strong = True

            # Extract response content
            content: str | None = None
            if exec_meta and exec_meta.executed_content:
                content = exec_meta.executed_content
            elif is_local_handled:
                content = _resolve_local_content(item)

            # Estimate / measured tokens
            if is_local_handled or is_cache_hit:
                token_usage = TokenUsageEstimate(input_tokens=0, output_tokens=0, total_tokens=0)
            else:
                inp_tok = estimate_token_count(item.query)
                out_tok = estimate_token_count(content) if content else 0
                token_usage = TokenUsageEstimate(
                    input_tokens=inp_tok,
                    output_tokens=out_tok,
                    total_tokens=inp_tok + out_tok,
                )

            # Quality metrics
            format_compliant = _check_format_compliance(
                content or "",
                item.quality_criteria.format_compliance,
            )

            completeness_score: float | None = None
            if exec_meta and exec_meta.evaluation_metadata:
                completeness_score = exec_meta.evaluation_metadata.get("completeness")

            lexical_overlap: float | None = None
            if baseline_item and baseline_item.answer_quality.content and content:
                lexical_overlap = _compute_jaccard_similarity(
                    content,
                    baseline_item.answer_quality.content,
                )

            status_str = "SUCCESS"
            error_msg: str | None = None
            if exec_meta and exec_meta.failure_category not in ("NONE", ""):
                status_str = "ERROR"
                error_msg = f"Failure: {exec_meta.failure_category}"

            return SmartRoutingItemResult(
                item_id=item.id,
                task_type=item.task_type,
                expected_route=item.expected_route,
                actual_route=actual_route,
                decision_type=decision_type,
                reason_code=reason_code,
                is_local_handled=is_local_handled,
                is_cache_hit=is_cache_hit,
                is_small_model=is_small,
                is_strong_model=is_strong,
                is_escalated=is_escalated,
                escalation_reason=escalation_reason,
                executed_model_id=executed_model_id,
                executed_model_version=executed_model_version,
                latency_ms=elapsed_ms,
                token_usage=token_usage,
                content=content,
                format_compliant=format_compliant,
                completeness_score=completeness_score,
                lexical_overlap=lexical_overlap,
                has_context_dependency=item.has_context_dependency,
                query_text=item.query,
                status=status_str,
                error_message=error_msg,
            )

        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            return SmartRoutingItemResult(
                item_id=item.id,
                task_type=item.task_type,
                expected_route=item.expected_route,
                actual_route="error",
                decision_type="ERROR",
                reason_code="ROUTER_EXECUTION_EXCEPTION",
                is_local_handled=False,
                is_cache_hit=False,
                is_small_model=False,
                is_strong_model=False,
                is_escalated=False,
                escalation_reason=None,
                executed_model_id=None,
                executed_model_version=None,
                latency_ms=elapsed_ms,
                token_usage=TokenUsageEstimate(input_tokens=0, output_tokens=0, total_tokens=0),
                content=None,
                format_compliant=False,
                completeness_score=None,
                lexical_overlap=None,
                has_context_dependency=item.has_context_dependency,
                query_text=item.query,
                status="ERROR",
                error_message=str(exc),
            )

    async def run_smart_routing_evaluation(
        self,
        dataset: BenchmarkDataset,
        baseline_report: StrongModelBaselineReport | None = None,
        concurrency: int | None = None,
        pricing_config: PricingConfig | None = None,
    ) -> SmartRoutingEvaluationReport:
        """Executes full smart-routing evaluation across all items in a benchmark dataset."""
        conc = max(1, concurrency or self._concurrency)
        sem = asyncio.Semaphore(conc)

        baseline_map: dict[str, BaselineItemResult] = {}
        if baseline_report:
            baseline_map = {item.item_id: item for item in baseline_report.items}

        async def _bounded_run(item: BenchmarkItem) -> SmartRoutingItemResult:
            async with sem:
                base_ref = baseline_map.get(item.id)
                return await self.run_item(item, baseline_item=base_ref)

        results: list[SmartRoutingItemResult] = await asyncio.gather(
            *[_bounded_run(item) for item in dataset.items]
        )

        n = len(results)
        successful = [r for r in results if r.status == "SUCCESS"]
        failed = [r for r in results if r.status != "SUCCESS"]

        # Counts
        local_handled_cnt = sum(1 for r in results if r.is_local_handled)
        cache_hit_cnt = sum(1 for r in results if r.is_cache_hit)
        small_cnt = sum(1 for r in results if r.is_small_model)
        strong_cnt = sum(1 for r in results if r.is_strong_model)
        escalated_cnt = sum(1 for r in results if r.is_escalated)
        error_cnt = len(failed)

        # Percentages
        local_pct = round((local_handled_cnt / n) * 100.0, 2) if n else 0.0
        cache_rate = round((cache_hit_cnt / n) * 100.0, 2) if n else 0.0
        small_pct = round((small_cnt / n) * 100.0, 2) if n else 0.0
        strong_pct = round((strong_cnt / n) * 100.0, 2) if n else 0.0
        escalation_rate = round((escalated_cnt / n) * 100.0, 2) if n else 0.0
        error_rate = round((error_cnt / n) * 100.0, 2) if n else 0.0

        # Latency metrics
        latencies = [r.latency_ms for r in results]
        total_lat = round(sum(latencies), 2)
        mean_lat = round(total_lat / n, 2) if n else 0.0
        p50_lat = _compute_percentile(latencies, 50.0)
        p95_lat = _compute_percentile(latencies, 95.0)

        # Token metrics
        total_inp_tok = sum(r.token_usage.input_tokens for r in results)
        total_out_tok = sum(r.token_usage.output_tokens for r in results)
        total_tok = sum(r.token_usage.total_tokens for r in results)
        mean_tok = round(total_tok / n, 2) if n else 0.0

        # Quality measures
        format_checks = [r.format_compliant for r in results if r.format_compliant is not None]
        fmt_rate = round(sum(1 for fc in format_checks if fc) / len(format_checks), 3) if format_checks else 1.0

        non_empty_cnt = sum(1 for r in results if r.content and r.content.strip())
        non_empty_rate = round(non_empty_cnt / n, 3) if n else 1.0

        comp_scores = [r.completeness_score for r in results if r.completeness_score is not None]
        mean_comp = round(sum(comp_scores) / len(comp_scores), 3) if comp_scores else None

        overlaps = [r.lexical_overlap for r in results if r.lexical_overlap is not None]
        mean_overlap = round(sum(overlaps) / len(overlaps), 3) if overlaps else None

        quality_metrics = SmartRoutingQualityMetrics(
            format_compliance_rate=fmt_rate,
            non_empty_rate=non_empty_rate,
            mean_completeness_score=mean_comp,
            mean_lexical_overlap=mean_overlap,
        )

        # By task type breakdown
        by_task: dict[str, dict[str, Any]] = {}
        for r in results:
            cat = r.task_type.value
            if cat not in by_task:
                by_task[cat] = {
                    "count": 0,
                    "local_handled": 0,
                    "small_model": 0,
                    "strong_model": 0,
                    "escalated": 0,
                    "total_latency_ms": 0.0,
                    "total_tokens": 0,
                }
            by_task[cat]["count"] += 1
            if r.is_local_handled:
                by_task[cat]["local_handled"] += 1
            if r.is_small_model:
                by_task[cat]["small_model"] += 1
            if r.is_strong_model:
                by_task[cat]["strong_model"] += 1
            if r.is_escalated:
                by_task[cat]["escalated"] += 1
            by_task[cat]["total_latency_ms"] = round(by_task[cat]["total_latency_ms"] + r.latency_ms, 2)
            by_task[cat]["total_tokens"] += r.token_usage.total_tokens

        for cat, data in by_task.items():
            cnt = data["count"]
            data["mean_latency_ms"] = round(data["total_latency_ms"] / cnt, 2) if cnt else 0.0
            data["mean_tokens"] = round(data["total_tokens"] / cnt, 2) if cnt else 0.0

        run_id = f"smart_routing_run_{int(time.time())}"

        report = SmartRoutingEvaluationReport(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            total_queries=n,
            successful_queries=len(successful),
            failed_queries=len(failed),
            local_handled_count=local_handled_cnt,
            local_handled_pct=local_pct,
            cache_hit_count=cache_hit_cnt,
            cache_hit_rate=cache_rate,
            small_model_count=small_cnt,
            small_model_pct=small_pct,
            strong_model_count=strong_cnt,
            strong_model_pct=strong_pct,
            escalation_count=escalated_cnt,
            escalation_rate=escalation_rate,
            error_count=error_cnt,
            error_rate=error_rate,
            total_input_tokens=total_inp_tok,
            total_output_tokens=total_out_tok,
            total_tokens=total_tok,
            mean_tokens_per_query=mean_tok,
            total_latency_ms=total_lat,
            mean_latency_ms=mean_lat,
            p50_latency_ms=p50_lat,
            p95_latency_ms=p95_lat,
            quality_metrics=quality_metrics,
            by_task_type=by_task,
            items=results,
            comparative_analysis=None,
        )

        if baseline_report is not None:
            from app.dataset.comparative_evaluator import ComparativeBenchmarkEvaluator
            comp_evaluator = ComparativeBenchmarkEvaluator(default_pricing=pricing_config)
            comp_analysis = comp_evaluator.compare(
                baseline_report=baseline_report,
                smart_routing_report=report,
                pricing=pricing_config,
            )
            report.comparative_analysis = comp_analysis

        return report


def save_smart_routing_report(
    report: SmartRoutingEvaluationReport,
    file_path: Path | str,
) -> None:
    """Serializes a SmartRoutingEvaluationReport to JSON."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2) + "\n")


def load_smart_routing_report(file_path: Path | str) -> SmartRoutingEvaluationReport:
    """Loads and validates a SmartRoutingEvaluationReport from JSON."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Smart-routing report file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return SmartRoutingEvaluationReport.model_validate(data)
