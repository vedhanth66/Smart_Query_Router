"""Schemas for Machine Learning Routing Feature Datasets.

ISOLATION & PRIVACY GUARANTEES:
1. Strict Offline Isolation:
   These schemas are strictly decoupled from the online production inference path.
2. Privacy & Sanitization Gating:
   - Private user content is scrubbed by default (raw_query = None).
   - Only explicitly authorized queries (authorized_for_research=True) retain bounded,
     PII-redacted text snippets.
   - Deterministic SHA-256 query hashes allow indexing and deduplication without prompt exposure.
3. Multi-Format Tabular Serialization:
   Records can be transformed into flattened dictionaries, CSV rows, or JSON Lines
   suitable for ML training pipelines (scikit-learn, XGBoost, PyTorch).
"""

from __future__ import annotations

import csv
import io
import json
import time
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contract import ComplexityLevel
from app.schemas.benchmark import (
    BenchmarkModelTier,
    BenchmarkTaskType,
)


class MLTargetRoute(str, Enum):
    """Ground-truth optimal routing class for supervised ML routing experiments."""
    LOCAL_ELIGIBLE = "local-eligible"
    SIMPLE_MODEL_CANDIDATE = "simple-model candidate"
    COMPLEX_MODEL_CANDIDATE = "complex-model candidate"


class LabelDerivationMethod(str, Enum):
    """Method used to derive the optimal ground-truth ML routing label."""
    BENCHMARK_PROVENANCE = "BENCHMARK_PROVENANCE"
    EMPIRICAL_QUALITY_VERIFIED = "EMPIRICAL_QUALITY_VERIFIED"
    ESCALATION_CORRECTED = "ESCALATION_CORRECTED"
    MANUAL_HEURISTIC = "MANUAL_HEURISTIC"


class MLLocalSignals(BaseModel):
    """Non-generative local features extracted client-side or on-device."""
    model_config = ConfigDict(extra="forbid")

    char_count: int = Field(..., ge=0, description="Total characters in query")
    word_count: int = Field(..., ge=0, description="Total whitespace-delimited words")
    estimated_tokens: int = Field(..., ge=0, description="Heuristic token count estimate")
    has_code: bool = Field(..., description="Presence of programming syntax or code snippets")
    has_math: bool = Field(..., description="Presence of mathematical expressions or operators")
    has_questions: bool = Field(..., description="Presence of question marks or query interrogatives")
    has_urls: bool = Field(..., description="Presence of HTTP/HTTPS URLs")
    has_tables: bool = Field(..., description="Presence of markdown or ASCII table formatting")
    has_code_blocks: bool = Field(..., description="Presence of triple backtick code fences")
    has_rich_input: bool = Field(..., description="Whether query contains complex markdown or tables")
    detected_cues: list[str] = Field(default_factory=list, description="List of detected structural cues")
    detected_cues_count: int = Field(default=0, ge=0, description="Number of detected cues")
    uppercase_ratio: float = Field(..., ge=0.0, le=1.0, description="Fraction of uppercase letters")
    numeric_ratio: float = Field(..., ge=0.0, le=1.0, description="Fraction of numeric digits")
    special_char_ratio: float = Field(..., ge=0.0, le=1.0, description="Fraction of punctuation/special characters")


class MLContextSignals(BaseModel):
    """Conversational context and multi-turn dependency indicators."""
    model_config = ConfigDict(extra="forbid")

    has_context_dependency: bool = Field(..., description="Whether query relies on conversation history")
    context_turn_count: int = Field(..., ge=0, description="Number of prior conversation turns")
    context_total_chars: int = Field(..., ge=0, description="Total characters in prior turns")
    context_last_turn_chars: int = Field(..., ge=0, description="Character length of immediately prior turn")
    has_context_cues: bool = Field(..., description="Presence of reference pronouns or context markers")


class MLRoutingOutcome(BaseModel):
    """Observed runtime behavior from the deterministic/heuristic router."""
    model_config = ConfigDict(extra="forbid")

    actual_route: str = Field(..., description="Route selected by router")
    model_tier: str = Field(..., description="Model tier executed (local, fast_cheap, strong)")
    decision_type: str = Field(..., description="Decision type (DECIDED, FALLBACK, etc.)")
    is_cache_hit: bool = Field(default=False, description="Whether served from cache")
    is_local_handled: bool = Field(default=False, description="Whether resolved on-device")
    is_small_model: bool = Field(default=False, description="Whether executed on small model")
    is_escalated: bool = Field(default=False, description="Whether escalated to strong model")
    latency_ms: float = Field(..., ge=0.0, description="Execution latency in milliseconds")
    token_usage_total: int = Field(..., ge=0, description="Total tokens consumed")
    token_usage_input: int = Field(..., ge=0, description="Input tokens consumed")
    token_usage_output: int = Field(..., ge=0, description="Output tokens generated")


class MLQualityDelta(BaseModel):
    """Comparative quality, latency, and cost difference relative to always-strong baseline."""
    model_config = ConfigDict(extra="forbid")

    quality_verdict: str = Field(..., description="Verdict: PARITY, DEGRADED, IMPROVED, or UNKNOWN")
    format_compliance_delta: int = Field(..., description="+1 if router met and base failed, -1 if router failed, 0 if matched")
    lexical_overlap: float | None = Field(default=None, ge=0.0, le=1.0, description="Jaccard token similarity vs strong baseline")
    token_delta: int = Field(..., description="Tokens saved relative to baseline (positive = savings)")
    cost_savings_usd: float = Field(..., description="Dollar cost saved relative to baseline (positive = savings)")
    latency_delta_ms: float = Field(..., description="Latency delta relative to baseline (positive = speedup)")


class MLFeatureRecord(BaseModel):
    """Single unified tabular instance for ML router model training and evaluation."""
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(..., description="Unique benchmark item identifier")
    dataset_id: str = Field(..., description="Source dataset identifier")
    dataset_version: str = Field(default="1.0.0", description="Source dataset version")
    query_hash: str = Field(..., description="SHA-256 hash of normalized query for deduplication")
    authorized_for_research: bool = Field(default=False, description="Explicit research authorization flag")
    is_redacted: bool = Field(default=False, description="Whether query text was scrubbed/redacted")
    raw_query: str | None = Field(default=None, description="Sanitized query text (only if authorized)")
    task_type: BenchmarkTaskType = Field(..., description="Canonical task type")
    complexity_label: ComplexityLevel | None = Field(default=None, description="Benchmark complexity level")
    local_signals: MLLocalSignals = Field(..., description="Local feature vector")
    context_signals: MLContextSignals = Field(..., description="Context dependency feature vector")
    routing_outcome: MLRoutingOutcome = Field(..., description="Observed routing outcome")
    quality_delta: MLQualityDelta = Field(..., description="Quality delta vs strong model baseline")
    target_label: MLTargetRoute = Field(..., description="Ground-truth optimal target route for ML classifier")
    target_model_tier: BenchmarkModelTier = Field(..., description="Optimal target model tier")
    label_derivation_method: LabelDerivationMethod = Field(..., description="Method used to derive target label")
    label_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Target label confidence score")
    label_notes: str | None = Field(default=None, description="Justification or empirical correction rationale")

    def to_flat_dict(self) -> dict[str, Any]:
        """Flattens the hierarchical record into a tabular dictionary for ML training."""
        return {
            "item_id": self.item_id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "query_hash": self.query_hash,
            "authorized_for_research": self.authorized_for_research,
            "is_redacted": self.is_redacted,
            "task_type": self.task_type.value,
            "complexity_label": self.complexity_label.value if self.complexity_label else "UNKNOWN",
            # Local numeric & boolean signals
            "signal_char_count": self.local_signals.char_count,
            "signal_word_count": self.local_signals.word_count,
            "signal_estimated_tokens": self.local_signals.estimated_tokens,
            "signal_has_code": self.local_signals.has_code,
            "signal_has_math": self.local_signals.has_math,
            "signal_has_questions": self.local_signals.has_questions,
            "signal_has_urls": self.local_signals.has_urls,
            "signal_has_tables": self.local_signals.has_tables,
            "signal_has_code_blocks": self.local_signals.has_code_blocks,
            "signal_has_rich_input": self.local_signals.has_rich_input,
            "signal_detected_cues_count": self.local_signals.detected_cues_count,
            "signal_uppercase_ratio": self.local_signals.uppercase_ratio,
            "signal_numeric_ratio": self.local_signals.numeric_ratio,
            "signal_special_char_ratio": self.local_signals.special_char_ratio,
            # Context dependency signals
            "context_has_dependency": self.context_signals.has_context_dependency,
            "context_turn_count": self.context_signals.context_turn_count,
            "context_total_chars": self.context_signals.context_total_chars,
            "context_last_turn_chars": self.context_signals.context_last_turn_chars,
            "context_has_cues": self.context_signals.has_context_cues,
            # Routing outcome
            "outcome_actual_route": self.routing_outcome.actual_route,
            "outcome_model_tier": self.routing_outcome.model_tier,
            "outcome_decision_type": self.routing_outcome.decision_type,
            "outcome_is_cache_hit": self.routing_outcome.is_cache_hit,
            "outcome_is_local_handled": self.routing_outcome.is_local_handled,
            "outcome_is_small_model": self.routing_outcome.is_small_model,
            "outcome_is_escalated": self.routing_outcome.is_escalated,
            "outcome_latency_ms": self.routing_outcome.latency_ms,
            "outcome_tokens_total": self.routing_outcome.token_usage_total,
            # Quality deltas
            "quality_verdict": self.quality_delta.quality_verdict,
            "quality_format_compliance_delta": self.quality_delta.format_compliance_delta,
            "quality_lexical_overlap": self.quality_delta.lexical_overlap if self.quality_delta.lexical_overlap is not None else 0.0,
            "quality_token_delta": self.quality_delta.token_delta,
            "quality_cost_savings_usd": self.quality_delta.cost_savings_usd,
            "quality_latency_delta_ms": self.quality_delta.latency_delta_ms,
            # ML Target Label
            "target_label": self.target_label.value,
            "target_model_tier": self.target_model_tier.value,
            "label_derivation_method": self.label_derivation_method.value,
            "label_confidence": self.label_confidence,
        }


class MLFeaturePrivacyAudit(BaseModel):
    """Audit metadata proving adherence to user data protection and privacy scrubbing."""
    model_config = ConfigDict(extra="forbid")

    total_records: int = Field(..., ge=0, description="Total records evaluated")
    queries_authorized_count: int = Field(..., ge=0, description="Records with explicit research authorization")
    queries_scrubbed_count: int = Field(..., ge=0, description="Records where prompt text was omitted/scrubbed")
    redactions_applied_count: int = Field(..., ge=0, description="Records where PII/secrets were redacted")
    pii_risk_scrubbed_count: int = Field(..., ge=0, description="High/Medium PII items strictly stripped")
    isolation_guarantee: str = Field(
        default="Dataset generated offline. Production inference path is untouched.",
        description="Assurance statement on pipeline boundaries"
    )


class MLFeatureSummary(BaseModel):
    """Aggregated statistical distributions across dataset records."""
    model_config = ConfigDict(extra="forbid")

    label_distribution: dict[str, int] = Field(default_factory=dict, description="Counts per target ML label")
    task_type_distribution: dict[str, int] = Field(default_factory=dict, description="Counts per task category")
    context_dependency_distribution: dict[str, int] = Field(default_factory=dict, description="Counts by context dependency")
    quality_verdict_distribution: dict[str, int] = Field(default_factory=dict, description="Counts by quality verdict")
    escalation_count: int = Field(default=0, ge=0, description="Total queries requiring escalation")
    local_handled_count: int = Field(default=0, ge=0, description="Total queries resolved on-device")


class MLFeatureDataset(BaseModel):
    """Top-level container for the machine learning routing feature dataset."""
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(..., description="Unique ML feature dataset identifier")
    source_benchmark_id: str = Field(..., description="Source benchmark dataset ID")
    version: str = Field(default="1.0.0", description="Feature dataset version")
    generated_at: int = Field(default_factory=lambda: int(time.time() * 1000), description="Generation timestamp ms")
    total_records: int = Field(..., ge=0, description="Number of instances in dataset")
    privacy_audit: MLFeaturePrivacyAudit = Field(..., description="Privacy and scrubbing audit summary")
    summary: MLFeatureSummary = Field(..., description="Statistical summary of features and labels")
    records: list[MLFeatureRecord] = Field(default_factory=list, description="Individual feature instances")

    def to_tabular_dicts(self) -> list[dict[str, Any]]:
        """Exports dataset as a list of flat dictionaries ready for pandas DataFrame creation."""
        return [record.to_flat_dict() for record in self.records]

    def to_csv(self) -> str:
        """Serializes the tabular records into CSV string format."""
        flat_records = self.to_tabular_dicts()
        if not flat_records:
            return ""

        output = io.StringIO()
        fieldnames = list(flat_records[0].keys())
        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in flat_records:
            writer.writerow(row)
        return output.getvalue()

    def to_jsonl(self) -> str:
        """Serializes the dataset records into JSON Lines format (one JSON object per line)."""
        lines: list[str] = []
        for record in self.records:
            lines.append(record.model_dump_json())
        return "\n".join(lines)
