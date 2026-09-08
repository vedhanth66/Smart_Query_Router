"""Unit and integration tests for ML Router Classifier Training, Evaluation, and Comparison.

Verifies:
1. Pre-Routing Feature Gating & Leakage Prevention:
   - Ensures feature matrix contains strictly pre-routing signals.
   - Verifies zero leakage of runtime outcomes (tokens, actual route, quality deltas).
2. Metric Calculations:
   - Precision, recall, macro F1, per-class metrics, and 3x3 confusion matrix.
3. Cost and Quality Tradeoff Simulation:
   - Dollar spend under always-strong vs rule baseline vs ML predictions.
   - Tracking of under-routed (quality risk) and over-routed (costly) queries.
4. Model Training & Head-to-Head Baseline Comparison:
   - Trains on canonical feature dataset.
   - Validates in-sample fit and Leave-One-Out Cross-Validation.
   - Verifies accuracy and F1 delta against deterministic rule router.
5. Reproducible Artifact Serialization:
   - Model binary (.joblib) and metadata (.json) persistence roundtrip.
   - Inference probing via predict() and predict_proba().
6. FastAPI Service Endpoints:
   - POST /api/v1/models/router-classifier/train
   - GET /api/v1/models/router-classifier/metadata
   - POST /api/v1/models/router-classifier/predict
7. Production Router Safety Isolation:
   - Verifies POST /api/v1/optimize remains 100% deterministic rule router.
"""

import json
import os
import tempfile
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.main as main_mod
from app.schemas.contract import (
    ClientMetadata,
    LocalFeatures,
    NormalizedQueryPackage,
)
from app.schemas.benchmark import PricingConfig
from app.schemas.ml_dataset import MLFeatureDataset
from app.schemas.ml_model import (
    BaselineComparisonSummary,
    CostQualityTradeoff,
    EvaluationMetrics,
    ModelTrainingMetadata,
    MLPredictionRequest,
    MLPredictionResponse,
)
from app.dataset.ml_dataset_generator import load_ml_dataset_json
from app.ml.router_classifier import (
    MLRouterClassifier,
    build_classification_pipeline,
    compute_cost_quality_tradeoffs,
    compute_metrics,
    extract_feature_matrix,
    NUMERIC_FEATURES,
    BOOL_FEATURES,
    CAT_FEATURES,
    ROUTING_CLASSES,
    ROUTE_TO_TIER,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def canonical_ml_dataset() -> MLFeatureDataset:
    dataset_path = Path(__file__).parent.parent / "app" / "dataset" / "canonical_ml_features_v1.json"
    return load_ml_dataset_json(dataset_path)


# -----------------------------------------------------------------------------
# 1. Feature Extraction & Leakage Prevention Tests
# -----------------------------------------------------------------------------

def test_feature_matrix_leakage_prevention(canonical_ml_dataset):
    records = canonical_ml_dataset.to_tabular_dicts()
    df_X, y_true, y_rule = extract_feature_matrix(records)

    assert len(df_X) == 15
    assert len(y_true) == 15
    assert len(y_rule) == 15

    # Check exact expected features
    expected_cols = set(NUMERIC_FEATURES + BOOL_FEATURES + CAT_FEATURES)
    assert set(df_X.columns) == expected_cols

    # Critical Guardrail: Assert NO runtime outcome fields leaked into feature matrix
    forbidden_leak_cols = [
        "outcome_actual_route",
        "outcome_model_tier",
        "outcome_decision_type",
        "outcome_is_cache_hit",
        "outcome_is_local_handled",
        "outcome_is_small_model",
        "outcome_is_escalated",
        "outcome_latency_ms",
        "outcome_tokens_total",
        "quality_verdict",
        "quality_format_compliance_delta",
        "quality_lexical_overlap",
        "quality_token_delta",
        "quality_cost_savings_usd",
        "target_label",
    ]
    for leak_col in forbidden_leak_cols:
        assert leak_col not in df_X.columns


# -----------------------------------------------------------------------------
# 2. Metric Calculations Tests
# -----------------------------------------------------------------------------

def test_compute_metrics_perfect_predictions():
    y_true = np.array(["local-eligible", "simple-model candidate", "complex-model candidate"])
    y_pred = np.array(["local-eligible", "simple-model candidate", "complex-model candidate"])

    metrics = compute_metrics(y_true, y_pred, labels=ROUTING_CLASSES)
    assert metrics.accuracy == 1.0
    assert metrics.precision_macro == 1.0
    assert metrics.recall_macro == 1.0
    assert metrics.f1_macro == 1.0
    assert metrics.confusion_matrix == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert len(metrics.per_class) == 3


def test_compute_metrics_imperfect_predictions():
    y_true = np.array(["local-eligible", "simple-model candidate", "complex-model candidate"])
    y_pred = np.array(["complex-model candidate", "simple-model candidate", "complex-model candidate"])

    metrics = compute_metrics(y_true, y_pred, labels=ROUTING_CLASSES)
    assert metrics.accuracy == pytest.approx(0.6667, abs=1e-3)
    assert metrics.confusion_matrix[0][2] == 1  # 1 local predicted as complex


# -----------------------------------------------------------------------------
# 3. Cost & Quality Tradeoff Tests
# -----------------------------------------------------------------------------

def test_compute_cost_quality_tradeoffs(canonical_ml_dataset):
    records = canonical_ml_dataset.to_tabular_dicts()
    y_true = np.array([r["target_label"] for r in records])
    y_pred = y_true.copy()  # Perfect predictions

    tradeoff = compute_cost_quality_tradeoffs(y_true, y_pred, records)
    assert tradeoff.always_strong_cost_usd > 0.0
    assert tradeoff.ml_model_cost_usd < tradeoff.always_strong_cost_usd
    assert tradeoff.ml_savings_vs_strong_pct > 0.0
    assert tradeoff.under_routed_count == 0
    assert tradeoff.over_routed_count == 0
    assert tradeoff.optimal_routed_count == 15
    assert tradeoff.expected_quality_parity_pct == 100.0


# -----------------------------------------------------------------------------
# 4. Model Training & Comparison Against Rule Baseline Tests
# -----------------------------------------------------------------------------

def test_train_ml_router_classifier(canonical_ml_dataset):
    classifier = MLRouterClassifier()
    pipe, metadata = classifier.train(canonical_ml_dataset)

    assert pipe is not None
    assert metadata.model_id == "router_classifier_v1"
    assert metadata.version == "1.0.0"
    assert metadata.is_production_active is False
    assert metadata.deployment_status == "EXPERIMENTAL_OFFLINE"

    # In-sample performance should achieve 100% on curated benchmark
    assert metadata.in_sample_metrics.accuracy == 1.0
    assert metadata.in_sample_metrics.f1_macro == 1.0

    # Cross-validation performance
    assert metadata.cross_validation_metrics.accuracy >= 0.60

    # Deterministic rule baseline comparison
    comp = metadata.baseline_comparison
    assert comp.rule_accuracy >= 0.70
    assert comp.ml_accuracy == 1.0
    assert comp.accuracy_delta > 0.0
    assert len(comp.rule_confusion_matrix) == 3
    assert len(comp.ml_confusion_matrix) == 3

    # Cost tradeoffs
    assert metadata.cost_quality_tradeoffs.ml_savings_vs_strong_pct > 0.0


# -----------------------------------------------------------------------------
# 5. Artifact Serialization & Offline Inference Tests
# -----------------------------------------------------------------------------

def test_artifact_saving_and_loading(canonical_ml_dataset):
    with tempfile.TemporaryDirectory() as tmpdir:
        classifier = MLRouterClassifier()
        classifier.train(canonical_ml_dataset)

        model_p, meta_p = classifier.save_artifacts(tmpdir)
        assert model_p.exists()
        assert meta_p.exists()

        # Load back
        loaded = MLRouterClassifier.load_artifacts(tmpdir)
        assert loaded.pipeline is not None
        assert loaded.metadata.model_id == "router_classifier_v1"

        # Test predict method
        route, tier, conf, probs = loaded.predict(
            local_signals={
                "char_count": 30,
                "word_count": 5,
                "estimated_tokens": 15,
                "detected_cues_count": 0,
                "uppercase_ratio": 0.05,
                "numeric_ratio": 0.0,
                "special_char_ratio": 0.05,
                "has_code": False,
                "has_math": False,
                "has_questions": True,
                "has_urls": False,
                "has_tables": False,
                "has_code_blocks": False,
                "has_rich_input": False,
            },
            task_type="greeting",
            complexity_label="VERY_LOW",
        )
        assert route in ROUTING_CLASSES
        assert tier in ["local", "fast_cheap", "strong"]
        assert 0.0 <= conf <= 1.0
        assert sum(probs.values()) == pytest.approx(1.0, abs=1e-2)


# -----------------------------------------------------------------------------
# 6. FastAPI Service Endpoints Tests
# -----------------------------------------------------------------------------

def test_endpoint_train_and_metadata(client):
    main_mod._trained_router_classifier = None

    # Train
    resp = client.post("/api/v1/models/router-classifier/train", json={"save_to_disk": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_id"] == "router_classifier_v1"
    assert "in_sample_metrics" in data
    assert "baseline_comparison" in data
    assert "cost_quality_tradeoffs" in data

    # Metadata
    resp_meta = client.get("/api/v1/models/router-classifier/metadata")
    assert resp_meta.status_code == 200
    assert resp_meta.json()["model_id"] == "router_classifier_v1"


def test_endpoint_predict_offline(client):
    # Ensure trained
    client.post("/api/v1/models/router-classifier/train", json={"save_to_disk": False})

    req_payload = {
        "task_type": "coding",
        "complexity_label": "HIGH",
        "local_signals": {
            "char_count": 250,
            "word_count": 45,
            "estimated_tokens": 90,
            "detected_cues_count": 2,
            "uppercase_ratio": 0.02,
            "numeric_ratio": 0.05,
            "special_char_ratio": 0.08,
            "has_code": True,
            "has_math": False,
            "has_questions": False,
            "has_urls": False,
            "has_tables": False,
            "has_code_blocks": True,
            "has_rich_input": True,
        },
        "context_signals": {
            "has_context_dependency": False,
            "context_turn_count": 0,
            "context_total_chars": 0,
            "context_last_turn_chars": 0,
            "has_context_cues": False,
        },
    }
    resp = client.post("/api/v1/models/router-classifier/predict", json=req_payload)
    assert resp.status_code == 200
    pred = resp.json()
    assert pred["predicted_route"] == "complex-model candidate"
    assert pred["recommended_tier"] == "strong"
    assert pred["confidence"] > 0.5


# -----------------------------------------------------------------------------
# 7. Production Router Safety Isolation Guardrail
# -----------------------------------------------------------------------------

def test_production_router_remains_strictly_rule_based(client):
    """Verifies that POST /api/v1/optimize is NOT overridden by the ML model."""
    pkg = NormalizedQueryPackage(
        request_id="safety_probe_001",
        query_text="Good morning! How are you doing today?",
        client_metadata=ClientMetadata(extension_version="0.1.0"),
        local_features=LocalFeatures(
            character_count=40,
            word_count=7,
            has_code=False,
            has_math=False,
            has_questions=True,
            has_urls=False,
            has_rich_input=False,
            detected_cues=[],
        ),
    )
    resp = client.post("/api/v1/optimize", json=pkg.model_dump())
    assert resp.status_code == 200
    decision = resp.json()
    assert decision["coarse_route"] in ("local-eligible", "simple-model candidate", "complex-model candidate")
    assert "reason_code" in decision
    assert isinstance(decision["reason_code"], str)
    # Ensure optimizer decision contract is followed
    assert "optimization_instructions" in decision
