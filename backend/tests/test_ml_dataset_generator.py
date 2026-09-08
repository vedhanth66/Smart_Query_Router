"""Unit and integration tests for ML Feature Dataset Generation Engine.

Verifies:
1. Feature Extraction Completeness:
   - Local signals (char/word count, estimated tokens, code/math/table cues, ratios).
   - Context signals (turn count, total chars, last turn chars, reference cues).
2. Target Label Derivation:
   - Local-eligible assignment.
   - Simple-model candidate with parity.
   - Complex-model candidate.
   - Empirical escalation / quality degradation promotion.
3. Privacy & Sanitization Isolation:
   - raw_query omitted by default (raw_query=None).
   - Authorization gating and redaction of PII/secrets.
   - Deterministic SHA-256 query hashing.
   - Privacy audit metrics accounting.
4. Tabular & Multi-Format Serializers:
   - Flattened dictionary generation (to_flat_dict / to_tabular_dicts).
   - CSV export with headers.
   - JSON Lines (.jsonl) streaming export.
   - JSON file persistence roundtrip.
5. FastAPI Endpoints:
   - POST /api/v1/benchmark/ml-dataset/generate
   - GET /api/v1/benchmark/ml-dataset/latest
   - Error cases (404 on missing dataset or uninitialized latest).
6. Production Inference Path Decoupling:
   - Guarantees POST /api/v1/optimize remains untouched and operational.
"""

import csv
import io
import json
import os
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.main as main_mod
from app.schemas.contract import (
    ClientMetadata,
    CoarseRoute,
    ComplexityLevel,
    NormalizedQueryPackage,
    LocalFeatures,
)
from app.schemas.benchmark import (
    AnswerQualityReference,
    BaselineItemResult,
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkModelTier,
    BenchmarkTaskType,
    ConversationTurn,
    ItemComparativeDetail,
    LabelProvenance,
    LabelVerificationMethod,
    PiiRiskLevel,
    PricingConfig,
    QualityCriteria,
    SensitivityFlags,
    SmartRoutingEvaluationReport,
    SmartRoutingItemResult,
    SmartRoutingQualityMetrics,
    StrongModelBaselineReport,
    TokenUsageEstimate,
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
from app.dataset.benchmark_validator import load_benchmark_dataset
from app.dataset.ml_dataset_generator import (
    MLFeatureDatasetGenerator,
    derive_optimal_target_label,
    extract_context_signals,
    extract_local_signals,
    load_ml_dataset_json,
    save_ml_dataset_csv,
    save_ml_dataset_json,
    save_ml_dataset_jsonl,
)


@pytest.fixture
def client():
    return TestClient(app)


def _make_dummy_item(
    item_id: str = "test_001",
    query: str = "Hello world!",
    task_type: BenchmarkTaskType = BenchmarkTaskType.GREETING,
    expected_route: CoarseRoute = CoarseRoute.LOCAL_ELIGIBLE,
    has_context: bool = False,
    context_turns: list[ConversationTurn] | None = None,
    authorized: bool = True,
    requires_redaction: bool = False,
    pii_level: PiiRiskLevel = PiiRiskLevel.NONE,
) -> BenchmarkItem:
    prov = LabelProvenance(
        method=LabelVerificationMethod.DETERMINISTIC_VERIFIED,
        confidence=1.0,
    )
    return BenchmarkItem(
        id=item_id,
        query=query,
        context_turns=context_turns or [],
        has_context_dependency=has_context,
        task_type=task_type,
        expected_route=expected_route,
        complexity_label=ComplexityLevel.VERY_LOW,
        target_model_tier=BenchmarkModelTier.LOCAL,
        quality_criteria=QualityCriteria(rubric_description="Test rubric"),
        sensitivity_flags=SensitivityFlags(
            pii_level=pii_level,
            authorized_for_benchmark=authorized,
            requires_redaction=requires_redaction,
        ),
        route_provenance=prov,
        complexity_provenance=prov,
        task_provenance=prov,
    )


# -----------------------------------------------------------------------------
# 1. Feature Signal Extraction Tests
# -----------------------------------------------------------------------------

def test_extract_local_signals_basic():
    signals = extract_local_signals("What is 15 + 27?")
    assert signals.char_count == len("What is 15 + 27?")
    assert signals.word_count == 5
    assert signals.has_math is True
    assert signals.has_questions is True
    assert signals.has_code is False
    assert signals.has_tables is False
    assert signals.has_urls is False
    assert signals.has_rich_input is False
    assert "math_formula" in signals.detected_cues
    assert signals.numeric_ratio > 0.0
    assert signals.uppercase_ratio > 0.0
    assert signals.special_char_ratio > 0.0


def test_extract_local_signals_code_and_table():
    code_text = "```python\ndef add(a, b):\n    return a + b\n```\n| col1 | col2 |\n|---|---|\n| 1 | 2 |"
    signals = extract_local_signals(code_text)
    assert signals.has_code is True
    assert signals.has_code_blocks is True
    assert signals.has_tables is True
    assert signals.has_rich_input is True
    assert "code_syntax" in signals.detected_cues
    assert "table_structure" in signals.detected_cues


def test_extract_context_signals():
    turns = [
        ConversationTurn(role="user", content="Tell me about Python."),
        ConversationTurn(role="assistant", content="Python is a popular programming language."),
    ]
    item = _make_dummy_item(
        query="Can you summarize it in two bullets?",
        has_context=True,
        context_turns=turns,
    )
    ctx_signals = extract_context_signals(item)
    assert ctx_signals.has_context_dependency is True
    assert ctx_signals.context_turn_count == 2
    assert ctx_signals.context_total_chars > 0
    assert ctx_signals.context_last_turn_chars == len(turns[-1].content)
    assert ctx_signals.has_context_cues is True  # 'it' matched context cue regex


# -----------------------------------------------------------------------------
# 2. Optimal Target Label Derivation Tests
# -----------------------------------------------------------------------------

def test_derive_target_label_local_eligible():
    item = _make_dummy_item(expected_route=CoarseRoute.LOCAL_ELIGIBLE)
    tgt, tier, method, conf, notes = derive_optimal_target_label(item, None, None)
    assert tgt == MLTargetRoute.LOCAL_ELIGIBLE
    assert tier == BenchmarkModelTier.LOCAL
    assert method == LabelDerivationMethod.BENCHMARK_PROVENANCE
    assert conf == 1.0


def test_derive_target_label_simple_model_parity():
    item = _make_dummy_item(
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        task_type=BenchmarkTaskType.FACTUAL_QUESTION,
    )
    comp = ItemComparativeDetail(
        item_id=item.id,
        task_type=item.task_type,
        route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
        baseline_tokens=TokenUsageEstimate(input_tokens=10, output_tokens=20, total_tokens=30),
        routing_tokens=TokenUsageEstimate(input_tokens=10, output_tokens=20, total_tokens=30),
        token_delta=0,
        baseline_cost_usd=0.001,
        routing_cost_usd=0.0001,
        cost_savings_usd=0.0009,
        baseline_latency_ms=200.0,
        routing_latency_ms=100.0,
        latency_delta_ms=100.0,
        quality_verdict="PARITY",
    )
    sr_item = SmartRoutingItemResult(
        item_id=item.id,
        task_type=item.task_type,
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
        decision_type="DECIDED",
        reason_code="heuristic_match",
        status="SUCCESS",
        is_small_model=True,
        is_escalated=False,
        latency_ms=100.0,
        token_usage=TokenUsageEstimate(input_tokens=10, output_tokens=20, total_tokens=30),
    )
    tgt, tier, method, conf, notes = derive_optimal_target_label(item, sr_item, comp)
    assert tgt == MLTargetRoute.SIMPLE_MODEL_CANDIDATE
    assert tier == BenchmarkModelTier.FAST_CHEAP
    assert method == LabelDerivationMethod.EMPIRICAL_QUALITY_VERIFIED
    assert conf == 0.95


def test_derive_target_label_escalation_correction():
    """If a simple query required strong-tier escalation, ML target is corrected to COMPLEX."""
    item = _make_dummy_item(
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        task_type=BenchmarkTaskType.CODING,
    )
    sr_item = SmartRoutingItemResult(
        item_id=item.id,
        task_type=item.task_type,
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
        decision_type="DECIDED",
        reason_code="escalation_triggered",
        status="SUCCESS",
        is_small_model=True,
        is_escalated=True,  # Escalation was triggered
        latency_ms=450.0,
        token_usage=TokenUsageEstimate(input_tokens=50, output_tokens=100, total_tokens=150),
    )
    tgt, tier, method, conf, notes = derive_optimal_target_label(item, sr_item, None)
    assert tgt == MLTargetRoute.COMPLEX_MODEL_CANDIDATE
    assert tier == BenchmarkModelTier.STRONG
    assert method == LabelDerivationMethod.ESCALATION_CORRECTED
    assert conf == 1.0


def test_derive_target_label_degradation_correction():
    """If a simple query suffered DEGRADED quality, ML target is corrected to COMPLEX."""
    item = _make_dummy_item(
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        task_type=BenchmarkTaskType.REASONING,
    )
    comp = ItemComparativeDetail(
        item_id=item.id,
        task_type=item.task_type,
        route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
        baseline_tokens=TokenUsageEstimate(input_tokens=20, output_tokens=40, total_tokens=60),
        routing_tokens=TokenUsageEstimate(input_tokens=20, output_tokens=40, total_tokens=60),
        token_delta=0,
        baseline_cost_usd=0.002,
        routing_cost_usd=0.0002,
        cost_savings_usd=0.0018,
        baseline_latency_ms=300.0,
        routing_latency_ms=150.0,
        latency_delta_ms=150.0,
        quality_verdict="DEGRADED",  # Quality degraded!
    )
    tgt, tier, method, conf, notes = derive_optimal_target_label(item, None, comp)
    assert tgt == MLTargetRoute.COMPLEX_MODEL_CANDIDATE
    assert tier == BenchmarkModelTier.STRONG
    assert method == LabelDerivationMethod.ESCALATION_CORRECTED


# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# 3. Privacy & Sanitization Isolation Tests
# -----------------------------------------------------------------------------

@pytest.mark.anyio
async def test_privacy_default_omits_raw_query():
    """Default generation strictly omits raw_query (None) across all items."""
    ds_path = Path(__file__).parent.parent / "app" / "dataset" / "canonical_benchmark.json"
    generator = MLFeatureDatasetGenerator()
    ml_dataset = await generator.generate_from_benchmark(
        dataset_or_path=ds_path,
        include_raw_query=False,  # Default
    )

    assert ml_dataset.total_records == 15
    for record in ml_dataset.records:
        assert record.raw_query is None
        assert record.authorized_for_research is False
        assert len(record.query_hash) == 64  # Valid SHA-256 hash
    
    assert ml_dataset.privacy_audit.queries_authorized_count == 0
    assert ml_dataset.privacy_audit.queries_scrubbed_count == 15


@pytest.mark.anyio
async def test_privacy_authorized_redaction():
    """When authorized, queries are sanitized and secrets/PII are redacted."""
    item_secret = _make_dummy_item(
        item_id="item_sec_1",
        query="Deploy with secret sk-abcdef1234567890 and email test@company.com",
        authorized=True,
        requires_redaction=True,
    )
    item_unauthorized = _make_dummy_item(
        item_id="item_unauth_2",
        query="Private unapproved customer prompt",
        authorized=False,  # NOT authorized
    )
    dataset = BenchmarkDataset(
        dataset_id="test_priv_ds",
        description="Privacy test dataset",
        items=[item_secret, item_unauthorized],
    )

    generator = MLFeatureDatasetGenerator()
    ml_dataset = await generator.generate_from_benchmark(
        dataset_or_path=dataset,
        include_raw_query=True,
    )

    rec_sec = next(r for r in ml_dataset.records if r.item_id == "item_sec_1")
    assert rec_sec.authorized_for_research is True
    assert rec_sec.is_redacted is True
    assert rec_sec.raw_query is not None
    assert "sk-abcdef1234567890" not in rec_sec.raw_query
    assert "[REDACTED_SECRET]" in rec_sec.raw_query
    assert "[EMAIL]" in rec_sec.raw_query

    rec_unauth = next(r for r in ml_dataset.records if r.item_id == "item_unauth_2")
    assert rec_unauth.authorized_for_research is False
    assert rec_unauth.raw_query is None  # Strictly omitted!

    assert ml_dataset.privacy_audit.queries_authorized_count == 1
    assert ml_dataset.privacy_audit.queries_scrubbed_count == 1
    assert ml_dataset.privacy_audit.redactions_applied_count == 1


# -----------------------------------------------------------------------------
# 4. Multi-Format Serializers Tests
# -----------------------------------------------------------------------------

@pytest.mark.anyio
async def test_flat_dict_and_csv_serialization():
    ds_path = Path(__file__).parent.parent / "app" / "dataset" / "canonical_benchmark.json"
    generator = MLFeatureDatasetGenerator()
    ml_dataset = await generator.generate_from_benchmark(ds_path)

    # 1. Flattened dicts
    flat_rows = ml_dataset.to_tabular_dicts()
    assert len(flat_rows) == 15
    first = flat_rows[0]
    assert "item_id" in first
    assert "query_hash" in first
    assert "signal_char_count" in first
    assert "signal_word_count" in first
    assert "signal_estimated_tokens" in first
    assert "signal_has_code" in first
    assert "context_has_dependency" in first
    assert "outcome_actual_route" in first
    assert "quality_verdict" in first
    assert "target_label" in first

    # 2. CSV generation
    csv_text = ml_dataset.to_csv()
    assert isinstance(csv_text, str)
    lines = csv_text.strip().split("\n")
    assert len(lines) == 16  # 1 header + 15 data rows
    reader = csv.DictReader(io.StringIO(csv_text))
    csv_rows = list(reader)
    assert len(csv_rows) == 15
    assert csv_rows[0]["item_id"] == "bench_greet_001"

    # 3. JSON Lines generation
    jsonl_text = ml_dataset.to_jsonl()
    jsonl_lines = jsonl_text.strip().split("\n")
    assert len(jsonl_lines) == 15
    first_json = json.loads(jsonl_lines[0])
    assert first_json["item_id"] == "bench_greet_001"
    assert "local_signals" in first_json


@pytest.mark.anyio
async def test_file_persistence_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = Path(tmpdir) / "features.json"
        jsonl_path = Path(tmpdir) / "features.jsonl"
        csv_path = Path(tmpdir) / "features.csv"

        ds_path = Path(__file__).parent.parent / "app" / "dataset" / "canonical_benchmark.json"
        generator = MLFeatureDatasetGenerator()
        ml_dataset = await generator.generate_from_benchmark(ds_path)

        save_ml_dataset_json(ml_dataset, json_path)
        assert json_path.exists()
        loaded = load_ml_dataset_json(json_path)
        assert loaded.dataset_id == ml_dataset.dataset_id
        assert len(loaded.records) == 15

        save_ml_dataset_jsonl(ml_dataset, jsonl_path)
        assert jsonl_path.exists()

        save_ml_dataset_csv(ml_dataset, csv_path)
        assert csv_path.exists()


# -----------------------------------------------------------------------------
# 5. Canonical Benchmark End-to-End Evaluation Tests
# -----------------------------------------------------------------------------

@pytest.mark.anyio
async def test_canonical_benchmark_distributions():
    ds_path = Path(__file__).parent.parent / "app" / "dataset" / "canonical_benchmark.json"
    generator = MLFeatureDatasetGenerator()
    ml_dataset = await generator.generate_from_benchmark(ds_path)

    summary = ml_dataset.summary
    assert summary.local_handled_count >= 2
    assert summary.escalation_count >= 1
    # Check all target labels are represented
    assert "local-eligible" in summary.label_distribution
    assert "simple-model candidate" in summary.label_distribution
    assert "complex-model candidate" in summary.label_distribution
    # Check task types coverage
    assert len(summary.task_type_distribution) >= 12


# -----------------------------------------------------------------------------
# 6. FastAPI Service Endpoints Tests
# -----------------------------------------------------------------------------

def test_endpoint_generate_and_get_latest(client):
    main_mod._latest_ml_feature_dataset = None

    # Initial GET before run -> 404
    resp = client.get("/api/v1/benchmark/ml-dataset/latest")
    assert resp.status_code == 404

    # POST generate
    resp = client.post("/api/v1/benchmark/ml-dataset/generate", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_records"] == 15
    assert "privacy_audit" in data
    assert "records" in data
    assert len(data["records"]) == 15

    # GET latest after run -> 200
    resp_latest = client.get("/api/v1/benchmark/ml-dataset/latest")
    assert resp_latest.status_code == 200
    assert resp_latest.json()["dataset_id"] == data["dataset_id"]


def test_endpoint_save_to_disk_csv(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        out_csv = str(Path(tmpdir) / "exported_features.csv")
        resp = client.post(
            "/api/v1/benchmark/ml-dataset/generate",
            json={
                "save_to_disk": True,
                "output_path": out_csv,
                "export_format": "csv",
            },
        )
        assert resp.status_code == 200
        assert os.path.exists(out_csv)
        with open(out_csv, "r", encoding="utf-8") as f:
            content = f.read()
            assert "signal_char_count" in content


def test_endpoint_invalid_dataset_path(client):
    resp = client.post(
        "/api/v1/benchmark/ml-dataset/generate",
        json={"dataset_path": "non_existent_path_xyz.json"},
    )
    assert resp.status_code == 404


# -----------------------------------------------------------------------------
# 7. Production Inference Isolation Guardrail Test
# -----------------------------------------------------------------------------

def test_production_inference_remains_isolated(client):
    """Guarantees that POST /api/v1/optimize continues operating unaffected."""
    pkg = NormalizedQueryPackage(
        request_id="prod_test_001",
        query_text="Hello optimizer!",
        client_metadata=ClientMetadata(extension_version="0.1.0"),
        local_features=LocalFeatures(
            character_count=16,
            word_count=2,
            has_code=False,
            has_math=False,
            has_questions=False,
            has_urls=False,
            has_rich_input=False,
            detected_cues=[],
        ),
    )
    resp = client.post("/api/v1/optimize", json=pkg.model_dump())
    assert resp.status_code == 200
    decision = resp.json()
    assert "coarse_route" in decision
    assert decision["coarse_route"] in ("local-eligible", "simple-model candidate", "complex-model candidate")
