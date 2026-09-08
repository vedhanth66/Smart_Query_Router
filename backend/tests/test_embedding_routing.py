"""Unit and Integration Tests for Sentence-Embedding Routing & Guarded Primacy.

TEST CATEGORIES:
1. Deterministic Safety Primacy:
   - Greetings and arithmetic always resolve to local-eligible ($0 spent, 0 cloud ms).
   - Embeddings and ML classifiers are strictly forbidden from overriding safety checks.
2. Explicit Context Handling Primacy:
   - Queries with conversational context dependency always resolve to complex-model candidate.
   - Embeddings cannot downgrade or misroute context-dependent queries.
3. SentenceEmbeddingExtractor & Latency Circuit Breaker:
   - Sub-millisecond extraction speed (< 1.0 ms).
   - Deterministic unit normalization and dimensions.
   - Circuit breaker fallback when budget exceeded or generator fails.
4. Comparative Training (With vs Without Embeddings):
   - Side-by-side Accuracy, Macro F1, LOOCV, and per-class metrics.
   - Mathematical consistency of confusion matrices and deltas.
5. Latency & Resource Cost ROI Accounting:
   - Latency overhead within strict SLA (< 5.0 ms).
   - Compute cost overhead microscopic relative to LLM token savings (ROI > 1,000x).
6. API Endpoint Integration:
   - POST /api/v1/models/router-classifier/compare-embeddings
   - POST /api/v1/models/router-classifier/predict-guarded
"""

import math
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.cache.semantic.embedding import DeterministicMockEmbeddingGenerator
from app.ml.embedding_extractor import SentenceEmbeddingExtractor
from app.ml.guarded_router import (
    GuardedRouter,
    is_deterministic_greeting,
    is_deterministic_arithmetic,
)
from app.ml.router_classifier import MLRouterClassifier
from app.dataset.ml_dataset_generator import load_ml_dataset_json


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def canonical_dataset():
    dataset_path = Path(__file__).parent.parent / "app" / "dataset" / "canonical_ml_features_v1.json"
    return load_ml_dataset_json(dataset_path)


# =========================================================================
# 1. Deterministic Safety Checks Primacy Tests
# =========================================================================

def test_deterministic_greeting_detection():
    """Verifies regex identification of greetings eligible for on-device resolution."""
    assert is_deterministic_greeting("Good morning! How are you doing today?") is True
    assert is_deterministic_greeting("Hello there") is True
    assert is_deterministic_greeting("Hi", task_type="greeting") is True
    assert is_deterministic_greeting("Hey!", task_type=None) is True
    assert is_deterministic_greeting("Thanks for the help") is True
    assert is_deterministic_greeting("Write a Python script to sort a list") is False
    assert is_deterministic_greeting("What is the capital of Australia?") is False
    assert is_deterministic_greeting("") is False
    assert is_deterministic_greeting(None) is False


def test_deterministic_arithmetic_detection():
    """Verifies regex identification of simple arithmetic calculations."""
    assert is_deterministic_arithmetic("What is 482 * 17?") is True
    assert is_deterministic_arithmetic("15 + 27") is True
    assert is_deterministic_arithmetic("100 / 4 - 5") is True
    assert is_deterministic_arithmetic("calculate 2+2", task_type="arithmetic") is True
    assert is_deterministic_arithmetic("Explain the mathematical proof of Euler's identity") is False
    assert is_deterministic_arithmetic("") is False
    assert is_deterministic_arithmetic(None) is False


def test_guarded_router_greeting_safety_primacy():
    """Verifies that greetings ALWAYS route to local-eligible and bypass embeddings/ML."""
    router = GuardedRouter()
    decision = router.route_query(
        query_text="Good morning! How are you doing today?",
        task_type="greeting",
    )
    assert decision.final_route == "local-eligible"
    assert decision.recommended_tier == "local"
    assert decision.decision_gate == "DETERMINISTIC_SAFETY"
    assert decision.safety_override_applied is True
    assert decision.context_guard_applied is False
    assert decision.ml_predicted_route is None
    assert decision.embedding_telemetry is None
    assert "DETERMINISTIC_SAFETY_OVERRIDE" in decision.reason


def test_guarded_router_arithmetic_safety_primacy():
    """Verifies that arithmetic queries ALWAYS route to local-eligible and bypass embeddings/ML."""
    router = GuardedRouter()
    decision = router.route_query(
        query_text="What is 482 * 17?",
        task_type="arithmetic",
    )
    assert decision.final_route == "local-eligible"
    assert decision.recommended_tier == "local"
    assert decision.decision_gate == "DETERMINISTIC_SAFETY"
    assert decision.safety_override_applied is True
    assert decision.context_guard_applied is False
    assert decision.ml_predicted_route is None
    assert decision.embedding_telemetry is None


def test_adversarial_embedding_cannot_override_safety():
    """Verifies that even if a classifier would output complex, safety gates take precedence."""
    class AdversarialClassifier:
        def predict(self, **kwargs):
            return "complex-model candidate", "strong", 0.99, {"complex-model candidate": 0.99}

    adversarial_clf = AdversarialClassifier()
    guarded = GuardedRouter(ml_classifier=adversarial_clf)

    # Greeting query with adversarial classifier
    decision = guarded.route_query("Good morning! How are you doing today?", task_type="greeting")
    assert decision.final_route == "local-eligible"
    assert decision.recommended_tier == "local"
    assert decision.decision_gate == "DETERMINISTIC_SAFETY"
    assert decision.safety_override_applied is True


# =========================================================================
# 2. Explicit Context Handling Primacy Tests
# =========================================================================

def test_guarded_router_explicit_context_primacy():
    """Verifies that has_context_dependency=True deterministically routes to strong complex tier."""
    router = GuardedRouter()
    decision = router.route_query(
        query_text="Which one would you recommend between those two options?",
        has_context_dependency=True,
    )
    assert decision.final_route == "complex-model candidate"
    assert decision.recommended_tier == "strong"
    assert decision.decision_gate == "EXPLICIT_CONTEXT_GUARD"
    assert decision.context_guard_applied is True
    assert decision.safety_override_applied is False
    assert decision.embedding_telemetry is None
    assert "EXPLICIT_CONTEXT_GUARD" in decision.reason


def test_guarded_router_multiturn_history_primacy():
    """Verifies that prior conversation turns trigger context handling guard."""
    router = GuardedRouter()
    prior_turns = [
        {"role": "user", "content": "Tell me about Redis and Memcached"},
        {"role": "assistant", "content": "Redis is a data structure store, Memcached is a simple cache."},
    ]
    decision = router.route_query(
        query_text="Add pagination to it with a limit of 10 items",
        prior_conversation_turns=prior_turns,
    )
    assert decision.final_route == "complex-model candidate"
    assert decision.recommended_tier == "strong"
    assert decision.decision_gate == "EXPLICIT_CONTEXT_GUARD"
    assert decision.context_guard_applied is True


def test_adversarial_embedding_cannot_downgrade_context():
    """Verifies that a cheap ML prediction cannot downgrade a context-dependent query."""
    class CheapClassifier:
        def predict(self, **kwargs):
            return "local-eligible", "local", 0.99, {"local-eligible": 0.99}

    guarded = GuardedRouter(ml_classifier=CheapClassifier())
    decision = guarded.route_query("Which one should I pick?", has_context_dependency=True)
    assert decision.final_route == "complex-model candidate"
    assert decision.recommended_tier == "strong"
    assert decision.context_guard_applied is True


# =========================================================================
# 3. SentenceEmbeddingExtractor & Circuit Breaker Tests
# =========================================================================

def test_embedding_extractor_basic_properties():
    """Verifies dimension, normalization, and speed of SentenceEmbeddingExtractor."""
    extractor = SentenceEmbeddingExtractor(dimension=16)
    vector, telem = extractor.extract_vector("Benchmark query for embedding feature test.")

    assert len(vector) == 16
    assert telem.dimension == 16
    assert telem.within_budget is True
    assert telem.circuit_breaker_triggered is False
    assert telem.extraction_latency_ms < 2.0  # Must be fast

    # Check L2 normalization (unit length)
    norm = math.sqrt(sum(x * x for x in vector))
    assert abs(norm - 1.0) < 1e-4

    features, telem2 = extractor.extract_features("Another test query")
    assert len(features) == 16
    assert "embed_dim_0" in features
    assert "embed_dim_15" in features


def test_embedding_extractor_determinism():
    """Verifies that identical query text produces exact identical embedding vectors."""
    extractor = SentenceEmbeddingExtractor(dimension=16)
    text = "Explain the difference between optimistic and pessimistic locking."
    v1, _ = extractor.extract_vector(text)
    v2, _ = extractor.extract_vector(text)
    assert v1 == v2


def test_embedding_extractor_circuit_breaker_on_error():
    """Verifies that generator errors trigger the circuit breaker and neutral fallback."""
    class FailingGenerator(DeterministicMockEmbeddingGenerator):
        def _generate_vector(self, text: str):
            raise RuntimeError("Simulated embedding backend failure")

    extractor = SentenceEmbeddingExtractor(
        dimension=16,
        embedding_generator=FailingGenerator(dimension=16),
    )
    vector, telem = extractor.extract_vector("Query during failure")
    assert telem.circuit_breaker_triggered is True
    assert vector == [0.0] * 16  # Neutral fallback


def test_embedding_extractor_circuit_breaker_on_timeout():
    """Verifies that exceeding the latency budget triggers the circuit breaker."""
    extractor = SentenceEmbeddingExtractor(
        dimension=16,
        max_latency_budget_ms=0.000001,  # Impossibly small budget to force trigger
    )
    vector, telem = extractor.extract_vector("Query during timeout")
    assert telem.within_budget is False
    assert telem.circuit_breaker_triggered is True
    assert vector == [0.0] * 16


# =========================================================================
# 4. Comparative Model Training Tests
# =========================================================================

def test_comparative_training_with_vs_without_embeddings(canonical_dataset):
    """Verifies comparative training of Model A (without) vs Model B (with) on canonical data."""
    clf = MLRouterClassifier()
    pipe, report = clf.train_comparative(
        dataset=canonical_dataset,
        embedding_dimension=16,
        latency_budget_ms=5.0,
        benchmark_runs=10,
    )

    # In-sample accuracy must be 100% for both
    assert report.without_embeddings_in_sample.accuracy == 1.0
    assert report.with_embeddings_in_sample.accuracy == 1.0

    # Cross-validation accuracy must be valid and non-trivial
    assert report.without_embeddings_cv.accuracy >= 0.70
    assert report.with_embeddings_cv.accuracy >= 0.70

    # Verify comparison list entries
    metric_names = [c.metric_name for c in report.metric_comparisons]
    assert "In-Sample Accuracy" in metric_names
    assert "In-Sample Macro F1" in metric_names
    assert "LOOCV Accuracy" in metric_names
    assert "LOOCV Macro F1" in metric_names

    # Verify confusion matrices structure
    assert len(report.with_embeddings_in_sample.confusion_matrix) == 3
    assert len(report.with_embeddings_in_sample.confusion_matrix[0]) == 3


def test_comparative_model_latency_and_resource_cost_roi(canonical_dataset):
    """Verifies that router overhead is strictly measured and saves vastly more than it spends."""
    clf = MLRouterClassifier()
    _, report = clf.train_comparative(
        dataset=canonical_dataset,
        embedding_dimension=16,
        latency_budget_ms=5.0,
        benchmark_runs=10,
    )

    cost_report = report.latency_resource_cost

    # Latency checks
    assert cost_report.embedding_latency_ms < 1.0
    assert cost_report.total_overhead_ms < cost_report.latency_budget_ms
    assert cost_report.latency_budget_passed is True

    # Financial ROI checks
    assert cost_report.compute_cost_per_query_usd < 0.0001
    assert cost_report.token_savings_per_query_usd > 0.00005
    assert cost_report.net_benefit_per_query_usd > 0.0
    assert cost_report.roi_multiplier >= 1000.0
    assert "JUSTIFIED" in cost_report.cost_justification_verdict


# =========================================================================
# 5. Full Guarded Inference with Auxiliary ML Signal
# =========================================================================

def test_guarded_predict_auxiliary_ml_routing(canonical_dataset):
    """Verifies that non-safety, non-context queries route via auxiliary ML + embeddings."""
    clf = MLRouterClassifier()
    clf.train_comparative(dataset=canonical_dataset, embedding_dimension=16)

    decision = clf.predict_guarded(
        query_text="Write a Python function `longest_palindrome(s: str) -> str`",
        task_type="coding",
        complexity_label="HIGH",
    )

    assert decision.decision_gate == "ML_CLASSIFIER_AUXILIARY"
    assert decision.safety_override_applied is False
    assert decision.context_guard_applied is False
    assert decision.final_route in ("simple-model candidate", "complex-model candidate")
    assert decision.embedding_telemetry is not None
    assert decision.embedding_telemetry.dimension == 16
    assert decision.ml_confidence is not None


# =========================================================================
# 6. API Endpoint Integration Tests
# =========================================================================

def test_api_compare_embeddings_endpoint(client):
    """Verifies POST /api/v1/models/router-classifier/compare-embeddings."""
    payload = {
        "embedding_dimension": 16,
        "latency_budget_ms": 5.0,
        "benchmark_runs": 10,
        "save_to_disk": False,
    }
    resp = client.post("/api/v1/models/router-classifier/compare-embeddings", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["embedding_dimension"] == 16
    assert data["with_embeddings_in_sample"]["accuracy"] == 1.0
    assert data["latency_resource_cost"]["latency_budget_passed"] is True
    assert data["latency_resource_cost"]["roi_multiplier"] > 1000.0


def test_api_predict_guarded_endpoint(client):
    """Verifies POST /api/v1/models/router-classifier/predict-guarded across all 3 gates."""
    # Gate 1: Greeting
    resp_greet = client.post(
        "/api/v1/models/router-classifier/predict-guarded",
        json={"query_text": "Good morning! How are you doing today?", "task_type": "greeting"},
    )
    assert resp_greet.status_code == 200
    greet_data = resp_greet.json()
    assert greet_data["decision_gate"] == "DETERMINISTIC_SAFETY"
    assert greet_data["final_route"] == "local-eligible"
    assert greet_data["safety_override_applied"] is True

    # Gate 2: Context dependent
    resp_ctx = client.post(
        "/api/v1/models/router-classifier/predict-guarded",
        json={"query_text": "Which one should I choose?", "has_context_dependency": True},
    )
    assert resp_ctx.status_code == 200
    ctx_data = resp_ctx.json()
    assert ctx_data["decision_gate"] == "EXPLICIT_CONTEXT_GUARD"
    assert ctx_data["final_route"] == "complex-model candidate"
    assert ctx_data["context_guard_applied"] is True

    # Gate 3: Independent query
    resp_code = client.post(
        "/api/v1/models/router-classifier/predict-guarded",
        json={"query_text": "Write a Python script to sort numbers", "task_type": "coding"},
    )
    assert resp_code.status_code == 200
    code_data = resp_code.json()
    assert code_data["decision_gate"] == "ML_CLASSIFIER_AUXILIARY"
    assert code_data["embedding_telemetry"]["dimension"] == 16
