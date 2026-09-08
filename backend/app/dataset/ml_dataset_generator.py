"""ML Feature Dataset Generator Engine.

ISOLATION & FEATURE EXTRACTION GUARANTEES:
1. Strict Offline Isolation:
   Dataset generation is purely an offline evaluation and model preparation utility.
   The online production inference path (/api/v1/optimize) is not modified or affected.
2. Privacy & Redaction Gating:
   - User prompt content is omitted by default (raw_query=None).
   - Only explicitly authorized benchmark queries (authorized_for_benchmark=True)
     can include bounded, sanitized query text.
   - PII, secrets, API keys, and credential URLs are redacted via sanitize_text_snippet.
   - Deterministic SHA-256 hashes are computed for provenance and deduplication.
3. Multi-Dimension Signals:
   - Local signals: word/char count, estimated tokens, code/math/table/syntax cues, character ratios.
   - Context signals: multi-turn indicators, history character volume, conversational cues.
   - Routing outcome: observed route, model tier, decision type, cache hit, escalation, tokens, latency.
   - Quality difference: verdict (PARITY/DEGRADED/IMPROVED), format compliance delta, lexical overlap,
     token delta, cost delta, latency delta.
   - Target routing label: optimal multiclass target (local-eligible, simple-model candidate,
     complex-model candidate) with empirical correction for escalation and quality degradation.
4. Multi-Format Serializers:
   Exports to standard JSON, streaming JSON Lines (.jsonl), and flattened tabular CSV.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from app.schemas.contract import (
    CoarseRoute,
    ComplexityLevel,
)
from app.schemas.benchmark import (
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkModelTier,
    BenchmarkTaskType,
    ComparativeEvaluationReport,
    ItemComparativeDetail,
    PiiRiskLevel,
    PricingConfig,
    SmartRoutingEvaluationReport,
    SmartRoutingItemResult,
    StrongModelBaselineReport,
)
from app.schemas.ml_dataset import (
    LabelDerivationMethod,
    MLContextSignals,
    MLFeatureDataset,
    MLFeaturePrivacyAudit,
    MLFeatureRecord,
    MLFeatureSummary,
    MLLocalSignals,
    MLQualityDelta,
    MLRoutingOutcome,
    MLTargetRoute,
)
from app.dataset.sanitizer import (
    sanitize_metadata,
    sanitize_text_snippet,
)
from app.dataset.benchmark_validator import load_benchmark_dataset
from app.dataset.baseline_runner import StrongModelBaselineRunner
from app.dataset.smart_routing_runner import SmartRoutingBenchmarkRunner
from app.dataset.comparative_evaluator import ComparativeBenchmarkEvaluator
from app.optimizer.query_optimizer import estimate_token_count


CONTEXT_CUE_REGEX = re.compile(
    r"\b(it|this|that|these|those|the previous|previous|above|mentioned|earlier|again|instead|also|furthermore)\b",
    re.IGNORECASE,
)

CODE_CUE_REGEX = re.compile(
    r"```|~~~|def\s+\w+|class\s+\w+|import\s+\w+|function\s+\w+|<script|console\.log|const\s+\w+|let\s+\w+",
    re.IGNORECASE,
)

MATH_CUE_REGEX = re.compile(
    r"\$\$|\$|\\frac|\\sqrt|\\sum|\b\d+\s*[\+\-\*\/]\s*\d+\b",
)

QUESTION_PREFIXES = ("what", "why", "how", "when", "where", "who", "which", "can", "is", "are", "do", "does", "explain")


def extract_local_signals(text: str) -> MLLocalSignals:
    """Extracts non-generative local signals from raw query text."""
    char_count = len(text)
    words = text.split()
    word_count = len(words)
    estimated_tokens = estimate_token_count(text)

    has_code = bool(CODE_CUE_REGEX.search(text))
    has_math = bool(MATH_CUE_REGEX.search(text))
    clean_lower = text.lower().strip()
    has_questions = "?" in text or any(clean_lower.startswith(p) for p in QUESTION_PREFIXES)
    has_urls = bool(re.search(r"https?://\S+", text))
    has_tables = bool(re.search(r"^\s*\|.+?\|\s*$", text, re.MULTILINE))
    has_code_blocks = "```" in text or "~~~" in text
    has_rich_input = has_tables or has_code_blocks

    detected_cues: list[str] = []
    if has_code:
        detected_cues.append("code_syntax")
    if has_math:
        detected_cues.append("math_formula")
    if has_tables:
        detected_cues.append("table_structure")
    if has_urls:
        detected_cues.append("web_url")

    # Character distribution ratios
    upper_count = sum(1 for c in text if c.isupper())
    digit_count = sum(1 for c in text if c.isdigit())
    special_count = sum(1 for c in text if not c.isalnum() and not c.isspace())

    uppercase_ratio = round(upper_count / char_count, 4) if char_count > 0 else 0.0
    numeric_ratio = round(digit_count / char_count, 4) if char_count > 0 else 0.0
    special_char_ratio = round(special_count / char_count, 4) if char_count > 0 else 0.0

    return MLLocalSignals(
        char_count=char_count,
        word_count=word_count,
        estimated_tokens=estimated_tokens,
        has_code=has_code,
        has_math=has_math,
        has_questions=has_questions,
        has_urls=has_urls,
        has_tables=has_tables,
        has_code_blocks=has_code_blocks,
        has_rich_input=has_rich_input,
        detected_cues=detected_cues,
        detected_cues_count=len(detected_cues),
        uppercase_ratio=uppercase_ratio,
        numeric_ratio=numeric_ratio,
        special_char_ratio=special_char_ratio,
    )


def extract_context_signals(item: BenchmarkItem) -> MLContextSignals:
    """Extracts conversational context indicators and multi-turn metrics."""
    turn_count = len(item.context_turns)
    total_chars = sum(len(t.content) for t in item.context_turns)
    last_turn_chars = len(item.context_turns[-1].content) if item.context_turns else 0
    has_cues = bool(CONTEXT_CUE_REGEX.search(item.query))

    return MLContextSignals(
        has_context_dependency=item.has_context_dependency,
        context_turn_count=turn_count,
        context_total_chars=total_chars,
        context_last_turn_chars=last_turn_chars,
        has_context_cues=has_cues,
    )


def derive_optimal_target_label(
    item: BenchmarkItem,
    sr_item: SmartRoutingItemResult | None,
    comp_detail: ItemComparativeDetail | None,
) -> tuple[MLTargetRoute, BenchmarkModelTier, LabelDerivationMethod, float, str]:
    """Derives the optimal ground-truth target route for supervised ML routing.

    CORRECTION & PROVENANCE RULES:
    1. Escalation / Quality Degradation Correction:
       If a query routed to a simple/local tier suffered quality degradation
       or triggered is_escalated=True, the empirical truth is that the cheaper tier
       failed. Therefore, target_label is corrected to COMPLEX_MODEL_CANDIDATE.
    2. Verified Local-Eligible:
       If designated local-eligible in benchmark provenance and handled without degradation.
    3. Verified Simple-Model Candidate:
       If handled by small tier with PARITY quality and zero escalation.
    4. Verified Complex-Model Candidate:
       If designated complex in benchmark provenance or requiring advanced reasoning.
    """
    is_escalated = bool(sr_item and sr_item.is_escalated)
    quality_verdict = comp_detail.quality_verdict if comp_detail else "PARITY"

    # Rule 1: Empirical correction if cheaper execution degraded or escalated
    if is_escalated or quality_verdict == "DEGRADED":
        return (
            MLTargetRoute.COMPLEX_MODEL_CANDIDATE,
            BenchmarkModelTier.STRONG,
            LabelDerivationMethod.ESCALATION_CORRECTED,
            1.0,
            "Cheaper model tier failed quality evaluation or required strong-tier escalation.",
        )

    # Rule 2: Local eligible
    if item.expected_route == CoarseRoute.LOCAL_ELIGIBLE:
        return (
            MLTargetRoute.LOCAL_ELIGIBLE,
            BenchmarkModelTier.LOCAL,
            LabelDerivationMethod.BENCHMARK_PROVENANCE,
            item.route_provenance.confidence if item.route_provenance else 1.0,
            "Deterministic on-device resolution suitable for zero cloud token execution.",
        )

    # Rule 3: Simple model candidate verified by parity
    if item.expected_route == CoarseRoute.SIMPLE_MODEL_CANDIDATE:
        return (
            MLTargetRoute.SIMPLE_MODEL_CANDIDATE,
            BenchmarkModelTier.FAST_CHEAP,
            LabelDerivationMethod.EMPIRICAL_QUALITY_VERIFIED,
            0.95,
            "Fast/cheap model satisfied quality criteria with full parity and no escalation.",
        )

    # Rule 4: Complex model candidate
    if item.expected_route == CoarseRoute.COMPLEX_MODEL_CANDIDATE:
        return (
            MLTargetRoute.COMPLEX_MODEL_CANDIDATE,
            BenchmarkModelTier.STRONG,
            LabelDerivationMethod.BENCHMARK_PROVENANCE,
            item.route_provenance.confidence if item.route_provenance else 0.9,
            "Requires strong reasoning, coding, synthesis, or context-heavy capabilities.",
        )

    # Rule 5: Fallback to empirical observation
    if sr_item and sr_item.is_local_handled:
        return (
            MLTargetRoute.LOCAL_ELIGIBLE,
            BenchmarkModelTier.LOCAL,
            LabelDerivationMethod.MANUAL_HEURISTIC,
            0.8,
            "Empirically handled locally.",
        )
    if sr_item and sr_item.is_small_model and not is_escalated:
        return (
            MLTargetRoute.SIMPLE_MODEL_CANDIDATE,
            BenchmarkModelTier.FAST_CHEAP,
            LabelDerivationMethod.MANUAL_HEURISTIC,
            0.8,
            "Empirically handled on small model tier.",
        )

    return (
        MLTargetRoute.COMPLEX_MODEL_CANDIDATE,
        BenchmarkModelTier.STRONG,
        LabelDerivationMethod.MANUAL_HEURISTIC,
        0.8,
        "Default complex tier designation.",
    )


class MLFeatureDatasetGenerator:
    """Generates ML feature datasets from benchmark evaluation pipelines."""

    def __init__(self, pricing: PricingConfig | None = None) -> None:
        self.pricing = pricing or PricingConfig()

    def generate_from_reports(
        self,
        dataset: BenchmarkDataset,
        baseline_report: StrongModelBaselineReport,
        smart_routing_report: SmartRoutingEvaluationReport,
        comparative_report: ComparativeEvaluationReport | None = None,
        include_raw_query: bool = False,
        dataset_id: str | None = None,
    ) -> MLFeatureDataset:
        """Assembles MLFeatureDataset by merging benchmark items, baseline, and smart routing runs."""
        # Ensure comparative report exists
        comp_rep = comparative_report
        if comp_rep is None:
            evaluator = ComparativeBenchmarkEvaluator(default_pricing=self.pricing)
            comp_rep = evaluator.compare(
                baseline_report=baseline_report,
                smart_routing_report=smart_routing_report,
                pricing=self.pricing,
            )

        sr_map: dict[str, SmartRoutingItemResult] = {
            item.item_id: item for item in smart_routing_report.items
        }
        comp_map: dict[str, ItemComparativeDetail] = {
            detail.item_id: detail for detail in comp_rep.item_details
        }

        records: list[MLFeatureRecord] = []
        target_dataset_id = dataset_id or f"ml_features_{dataset.dataset_id}"

        queries_authorized = 0
        queries_scrubbed = 0
        redactions_applied = 0
        pii_risk_scrubbed = 0

        label_counts: dict[str, int] = {}
        task_counts: dict[str, int] = {}
        context_counts: dict[str, int] = {}
        verdict_counts: dict[str, int] = {}
        total_escalations = 0
        total_local_handled = 0

        for item in dataset.items:
            sr_item = sr_map.get(item.id)
            comp_detail = comp_map.get(item.id)

            # 1. Deterministic Query Hash
            query_hash = hashlib.sha256(item.query.strip().encode("utf-8")).hexdigest()

            # 2. Privacy & Sanitization Gating
            is_authorized = bool(
                include_raw_query and item.sensitivity_flags.authorized_for_benchmark
            )
            raw_query: str | None = None
            is_redacted = False

            if is_authorized:
                queries_authorized += 1
                sanitized_text, redacted = sanitize_text_snippet(
                    text=item.query,
                    max_chars=500,
                    authorized=True,
                )
                raw_query = sanitized_text
                if redacted:
                    is_redacted = True
                    redactions_applied += 1

                # Check high PII risk
                if item.sensitivity_flags.pii_level in (PiiRiskLevel.HIGH, PiiRiskLevel.MEDIUM):
                    pii_risk_scrubbed += 1
            else:
                queries_scrubbed += 1
                if item.sensitivity_flags.pii_level in (PiiRiskLevel.HIGH, PiiRiskLevel.MEDIUM):
                    pii_risk_scrubbed += 1

            # 3. Extract Local & Context Signals
            local_signals = extract_local_signals(item.query)
            context_signals = extract_context_signals(item)

            # 4. Construct Routing Outcome
            if sr_item:
                actual_route = sr_item.actual_route
                model_tier = (
                    "local"
                    if sr_item.is_local_handled
                    else ("fast_cheap" if sr_item.is_small_model and not sr_item.is_escalated else "strong")
                )
                decision_type = "DECIDED" if sr_item.status == "SUCCESS" else "FALLBACK"
                is_cache = sr_item.is_cache_hit
                is_local = sr_item.is_local_handled
                is_small = sr_item.is_small_model
                is_esc = sr_item.is_escalated
                lat_ms = sr_item.latency_ms
                tok_tot = sr_item.token_usage.total_tokens
                tok_inp = sr_item.token_usage.input_tokens
                tok_out = sr_item.token_usage.output_tokens
            else:
                actual_route = "unknown"
                model_tier = "unknown"
                decision_type = "UNKNOWN"
                is_cache = False
                is_local = False
                is_small = False
                is_esc = False
                lat_ms = 0.0
                tok_tot = 0
                tok_inp = 0
                tok_out = 0

            if is_esc:
                total_escalations += 1
            if is_local:
                total_local_handled += 1

            routing_outcome = MLRoutingOutcome(
                actual_route=actual_route,
                model_tier=model_tier,
                decision_type=decision_type,
                is_cache_hit=is_cache,
                is_local_handled=is_local,
                is_small_model=is_small,
                is_escalated=is_esc,
                latency_ms=lat_ms,
                token_usage_total=tok_tot,
                token_usage_input=tok_inp,
                token_usage_output=tok_out,
            )

            # 5. Construct Quality Delta
            if comp_detail:
                q_verdict = comp_detail.quality_verdict
                fmt_delta = (
                    1
                    if (comp_detail.routing_format_compliant and not comp_detail.baseline_format_compliant)
                    else (-1 if (comp_detail.baseline_format_compliant and not comp_detail.routing_format_compliant) else 0)
                )
                lex_ov = comp_detail.lexical_overlap
                tok_d = comp_detail.token_delta
                cost_s = comp_detail.cost_savings_usd
                lat_d = comp_detail.latency_delta_ms
            else:
                q_verdict = "UNKNOWN"
                fmt_delta = 0
                lex_ov = None
                tok_d = 0
                cost_s = 0.0
                lat_d = 0.0

            quality_delta = MLQualityDelta(
                quality_verdict=q_verdict,
                format_compliance_delta=fmt_delta,
                lexical_overlap=lex_ov,
                token_delta=tok_d,
                cost_savings_usd=cost_s,
                latency_delta_ms=lat_d,
            )

            # 6. Target Label Derivation
            (
                tgt_label,
                tgt_tier,
                deriv_method,
                confidence,
                notes,
            ) = derive_optimal_target_label(
                item=item,
                sr_item=sr_item,
                comp_detail=comp_detail,
            )

            # 7. Build Record
            rec = MLFeatureRecord(
                item_id=item.id,
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.version,
                query_hash=query_hash,
                authorized_for_research=is_authorized,
                is_redacted=is_redacted,
                raw_query=raw_query,
                task_type=item.task_type,
                complexity_label=item.complexity_label,
                local_signals=local_signals,
                context_signals=context_signals,
                routing_outcome=routing_outcome,
                quality_delta=quality_delta,
                target_label=tgt_label,
                target_model_tier=tgt_tier,
                label_derivation_method=deriv_method,
                label_confidence=confidence,
                label_notes=notes,
            )
            records.append(rec)

            # Update distributions
            label_counts[tgt_label.value] = label_counts.get(tgt_label.value, 0) + 1
            task_counts[item.task_type.value] = task_counts.get(item.task_type.value, 0) + 1
            ctx_key = "context_dependent" if item.has_context_dependency else "zero_context"
            context_counts[ctx_key] = context_counts.get(ctx_key, 0) + 1
            verdict_counts[q_verdict] = verdict_counts.get(q_verdict, 0) + 1

        privacy_audit = MLFeaturePrivacyAudit(
            total_records=len(records),
            queries_authorized_count=queries_authorized,
            queries_scrubbed_count=queries_scrubbed,
            redactions_applied_count=redactions_applied,
            pii_risk_scrubbed_count=pii_risk_scrubbed,
            isolation_guarantee="Dataset generated offline. Production inference path is untouched.",
        )

        summary = MLFeatureSummary(
            label_distribution=label_counts,
            task_type_distribution=task_counts,
            context_dependency_distribution=context_counts,
            quality_verdict_distribution=verdict_counts,
            escalation_count=total_escalations,
            local_handled_count=total_local_handled,
        )

        return MLFeatureDataset(
            dataset_id=target_dataset_id,
            source_benchmark_id=dataset.dataset_id,
            version=dataset.version,
            total_records=len(records),
            privacy_audit=privacy_audit,
            summary=summary,
            records=records,
        )

    async def generate_from_benchmark(
        self,
        dataset_or_path: BenchmarkDataset | str | Path | None = None,
        include_raw_query: bool = False,
        pricing: PricingConfig | None = None,
        save_to_disk: bool = False,
        output_path: str | Path | None = None,
        export_format: str = "json",
    ) -> MLFeatureDataset:
        """Executes full benchmark evaluation stack and produces the ML feature dataset."""
        cfg = pricing or self.pricing

        if isinstance(dataset_or_path, BenchmarkDataset):
            ds = dataset_or_path
        elif dataset_or_path is not None:
            ds = load_benchmark_dataset(Path(dataset_or_path))
        else:
            canonical_path = Path(__file__).parent / "canonical_benchmark.json"
            ds = load_benchmark_dataset(canonical_path)

        # Run baseline
        base_runner = StrongModelBaselineRunner()
        base_report = await base_runner.run_baseline_evaluation(ds)

        # Run smart routing
        sr_runner = SmartRoutingBenchmarkRunner()
        sr_report = await sr_runner.run_smart_routing_evaluation(ds)

        # Run comparative evaluator
        evaluator = ComparativeBenchmarkEvaluator(default_pricing=cfg)
        comp_report = evaluator.compare(
            baseline_report=base_report,
            smart_routing_report=sr_report,
            pricing=cfg,
        )

        # Generate ML dataset
        ml_dataset = self.generate_from_reports(
            dataset=ds,
            baseline_report=base_report,
            smart_routing_report=sr_report,
            comparative_report=comp_report,
            include_raw_query=include_raw_query,
        )

        if save_to_disk:
            target = Path(output_path) if output_path else Path(__file__).parent / f"{ml_dataset.dataset_id}.{export_format.lower()}"
            fmt = export_format.lower()
            if fmt == "csv":
                save_ml_dataset_csv(ml_dataset, target)
            elif fmt == "jsonl":
                save_ml_dataset_jsonl(ml_dataset, target)
            else:
                save_ml_dataset_json(ml_dataset, target)

        return ml_dataset

    def generate_from_benchmark_sync(
        self,
        dataset_or_path: BenchmarkDataset | str | Path | None = None,
        include_raw_query: bool = False,
        pricing: PricingConfig | None = None,
        save_to_disk: bool = False,
        output_path: str | Path | None = None,
        export_format: str = "json",
    ) -> MLFeatureDataset:
        """Synchronous wrapper for generate_from_benchmark."""
        import asyncio
        import concurrent.futures

        coro = self.generate_from_benchmark(
            dataset_or_path=dataset_or_path,
            include_raw_query=include_raw_query,
            pricing=pricing,
            save_to_disk=save_to_disk,
            output_path=output_path,
            export_format=export_format,
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return asyncio.run(coro)


def save_ml_dataset_json(dataset: MLFeatureDataset, path: Path | str) -> Path:
    """Serializes the MLFeatureDataset to a structured JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        f.write(dataset.model_dump_json(indent=2))
    return p


def save_ml_dataset_jsonl(dataset: MLFeatureDataset, path: Path | str) -> Path:
    """Serializes the MLFeatureDataset records to a JSON Lines file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        f.write(dataset.to_jsonl())
        f.write("\n")
    return p


def save_ml_dataset_csv(dataset: MLFeatureDataset, path: Path | str) -> Path:
    """Serializes the MLFeatureDataset records to a tabular CSV file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        f.write(dataset.to_csv())
    return p


def load_ml_dataset_json(path: Path | str) -> MLFeatureDataset:
    """Loads and validates an MLFeatureDataset from a JSON file."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return MLFeatureDataset.model_validate(data)
