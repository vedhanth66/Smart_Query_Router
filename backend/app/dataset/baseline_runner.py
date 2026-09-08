"""Stronger model benchmark baseline evaluation runner.

ISOLATION & EVALUATION GUARANTEES:
1. Pure Strong-Model Baseline:
   Sends every benchmark query to the stronger model (e.g. gpt-4o-2024-08-06).
   Does NOT invoke, optimize, or compare against the query router or small models.
2. Metrics Recorded:
   - Wall-clock latency in milliseconds (mean, p50 median, p95).
   - Estimated and measured token usage (input, output, total).
   - Answer-quality reference text, model version, and quality criteria check.
3. Multi-Turn Context Integrity:
   Faithfully transfers conversational context turns to GatewayRequest for
   context-dependent follow-up items.
4. Error Containment & Resilience:
   Transient network or gateway errors on individual items are recorded with status
   'ERROR' without terminating the entire benchmark evaluation run.
5. Report Serialization:
   Saves and loads baseline reports to/from JSON for longitudinal experiments.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from app.gateway.base import (
    GatewayError,
    GatewayRequest,
    GatewayResponse,
    ModelTier,
)
from app.gateway.gateway import ModelGateway, default_gateway
from app.schemas.benchmark import (
    AnswerQualityReference,
    BaselineItemResult,
    BenchmarkDataset,
    BenchmarkItem,
    StrongModelBaselineReport,
    TokenUsageEstimate,
)


def _compute_percentile(values: list[float], percentile: float) -> float:
    """Computes an empirical percentile (e.g. 0.50 for median, 0.95 for p95)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = (percentile / 100.0) * (n - 1)
    lower = math.floor(idx)
    upper = math.ceil(idx)
    weight = idx - lower
    return round(sorted_vals[lower] * (1.0 - weight) + sorted_vals[upper] * weight, 2)


def _check_format_compliance(content: str, expected_format: str | None) -> bool | None:
    """Basic format compliance check against specified quality criteria."""
    if not expected_format:
        return None
    fmt = expected_format.strip().upper()
    if fmt == "JSON":
        try:
            # Check if content is valid JSON or contains a JSON code block
            clean = content.strip()
            if clean.startswith("```json") and clean.endswith("```"):
                clean = clean[7:-3].strip()
            elif clean.startswith("```") and clean.endswith("```"):
                clean = clean[3:-3].strip()
            json.loads(clean)
            return True
        except Exception:
            return False
    elif fmt in ("CODE", "CODE_SNIPPET"):
        return "```" in content or "def " in content or "function " in content
    elif fmt in ("MARKDOWN", "MARKDOWN_TABLE"):
        return "|" in content and "---" in content
    return True


class StrongModelBaselineRunner:
    """Executes benchmark queries against the stronger model to build stable baseline datasets."""

    def __init__(
        self,
        gateway: ModelGateway | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self._gateway = gateway or default_gateway
        self._max_concurrency = max(1, max_concurrency)

    async def run_item(self, item: BenchmarkItem) -> BaselineItemResult:
        """Executes a single benchmark item on the stronger model with metrics instrumentation."""
        # Convert ConversationTurns into gateway context turn dicts
        context_dicts: list[dict[str, Any]] = [
            {"role": turn.role, "content": turn.content}
            for turn in item.context_turns
        ]

        gateway_req = GatewayRequest(
            prompt=item.query,
            tier=ModelTier.STRONG,
            context_turns=context_dicts,
            metadata={
                "benchmark_item_id": item.id,
                "task_type": item.task_type.value,
                "apply_length_policy": True,
            },
        )

        start_time = time.perf_counter()
        try:
            res: GatewayResponse = await self._gateway.execute_strong(
                gateway_req,
                task_category=item.task_type.value,
            )
            elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            # Prefer measured elapsed time if gateway latency is default/zero
            effective_latency = elapsed_ms if res.latency_ms <= 0 else res.latency_ms

            token_usage = TokenUsageEstimate(
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                total_tokens=res.input_tokens + res.output_tokens,
            )

            format_ok = _check_format_compliance(
                res.content,
                item.quality_criteria.format_compliance,
            )

            quality_ref = AnswerQualityReference(
                content=res.content,
                finish_reason=res.finish_reason,
                model_id=res.model_id,
                model_version=res.model_version,
                provider_name=res.provider_name,
                content_length_chars=len(res.content),
                meets_format_compliance=format_ok,
                quality_notes=f"Generated via {res.provider_name} in baseline evaluation mode",
            )

            return BaselineItemResult(
                item_id=item.id,
                task_type=item.task_type,
                expected_route=item.expected_route,
                complexity_label=item.complexity_label,
                target_model_tier=item.target_model_tier,
                latency_ms=effective_latency,
                token_usage=token_usage,
                answer_quality=quality_ref,
                status="SUCCESS",
                error_message=None,
            )

        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            # Graceful error containment
            dummy_tokens = TokenUsageEstimate(input_tokens=0, output_tokens=0, total_tokens=0)
            dummy_quality = AnswerQualityReference(
                content="",
                finish_reason="error",
                model_id="unknown",
                model_version=None,
                provider_name="strong_model",
                content_length_chars=0,
                meets_format_compliance=False,
                quality_notes=f"Execution error: {exc}",
            )
            return BaselineItemResult(
                item_id=item.id,
                task_type=item.task_type,
                expected_route=item.expected_route,
                complexity_label=item.complexity_label,
                target_model_tier=item.target_model_tier,
                latency_ms=elapsed_ms,
                token_usage=dummy_tokens,
                answer_quality=dummy_quality,
                status="ERROR",
                error_message=str(exc),
            )

    async def run_baseline_evaluation(
        self,
        dataset: BenchmarkDataset,
        concurrency: int | None = None,
    ) -> StrongModelBaselineReport:
        """Runs baseline evaluation across all items in a benchmark dataset."""
        conc = max(1, concurrency or self._max_concurrency)
        sem = asyncio.Semaphore(conc)

        async def _bounded_run(item: BenchmarkItem) -> BaselineItemResult:
            async with sem:
                return await self.run_item(item)

        results: list[BaselineItemResult] = await asyncio.gather(
            *[_bounded_run(item) for item in dataset.items]
        )

        successful = [r for r in results if r.status == "SUCCESS"]
        failed = [r for r in results if r.status != "SUCCESS"]

        latencies = [r.latency_ms for r in results]
        total_latency = round(sum(latencies), 2)
        mean_latency = round(total_latency / len(latencies), 2) if latencies else 0.0
        p50_latency = _compute_percentile(latencies, 50.0)
        p95_latency = _compute_percentile(latencies, 95.0)

        total_input_tok = sum(r.token_usage.input_tokens for r in results)
        total_output_tok = sum(r.token_usage.output_tokens for r in results)
        total_tok = sum(r.token_usage.total_tokens for r in results)
        mean_tok = round(total_tok / len(results), 2) if results else 0.0

        # By task type breakdown
        by_task: dict[str, dict[str, Any]] = {}
        for r in results:
            cat = r.task_type.value
            if cat not in by_task:
                by_task[cat] = {
                    "count": 0,
                    "successful": 0,
                    "failed": 0,
                    "total_latency_ms": 0.0,
                    "total_tokens": 0,
                }
            by_task[cat]["count"] += 1
            if r.status == "SUCCESS":
                by_task[cat]["successful"] += 1
            else:
                by_task[cat]["failed"] += 1
            by_task[cat]["total_latency_ms"] = round(
                by_task[cat]["total_latency_ms"] + r.latency_ms, 2
            )
            by_task[cat]["total_tokens"] += r.token_usage.total_tokens

        # Compute averages in by_task
        for cat, data in by_task.items():
            cnt = data["count"]
            data["mean_latency_ms"] = round(data["total_latency_ms"] / cnt, 2) if cnt else 0.0
            data["mean_tokens"] = round(data["total_tokens"] / cnt, 2) if cnt else 0.0

        # Model ID extraction from adapter or first successful item
        model_id = "gpt-4o-2024-08-06"
        model_version = "2024-08-06"
        if successful:
            model_id = successful[0].answer_quality.model_id
            model_version = successful[0].answer_quality.model_version

        run_id = f"baseline_run_{int(time.time())}"

        return StrongModelBaselineReport(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            model_id=model_id,
            model_version=model_version,
            total_queries=len(results),
            successful_queries=len(successful),
            failed_queries=len(failed),
            total_latency_ms=total_latency,
            mean_latency_ms=mean_latency,
            p50_latency_ms=p50_latency,
            p95_latency_ms=p95_latency,
            total_input_tokens=total_input_tok,
            total_output_tokens=total_output_tok,
            total_tokens=total_tok,
            mean_tokens_per_query=mean_tok,
            by_task_type=by_task,
            items=results,
        )


def save_baseline_report(
    report: StrongModelBaselineReport,
    file_path: Path | str,
) -> None:
    """Serializes a StrongModelBaselineReport to a formatted JSON file."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2) + "\n")


def load_baseline_report(file_path: Path | str) -> StrongModelBaselineReport:
    """Loads and validates a StrongModelBaselineReport from a JSON file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Baseline report file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return StrongModelBaselineReport.model_validate(data)
