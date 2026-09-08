"""Unit and integration tests for Smart-Routing Benchmark Pipeline Evaluation.

Verifies:
1. Full Smart-Routing Pipeline Execution:
   Runs benchmark items through optimizer, deterministic routing, small model,
   completeness evaluation, and conditional escalation.
2. Metrics Capture:
   - Local-handled percentage
   - Cache hit rate
   - Small-model percentage
   - Strong-model percentage
   - Escalation rate
   - Token estimates (input, output, total, mean per query)
   - Latency (mean, p50 median, p95)
   - Error rate
   - Quality measures (format compliance, non-empty rate, completeness, lexical overlap against strong baseline)
3. 13-Category Task Representation & Context Transfer:
   Evaluates queries across all 13 canonical task categories and ensures multi-turn context
   turns are faithfully mapped to ContextCandidateTurn objects.
4. Error Resilience:
   Sub-system exceptions or individual item failures are safely contained with status="ERROR"
   without aborting the batch evaluation.
5. Comparative Baseline Parity:
   Validates token and lexical overlap comparison against StrongModelBaselineReport references.
6. Serialization & Persistence:
   Lossless JSON saving and loading of SmartRoutingEvaluationReport.
7. FastAPI REST Endpoints:
   POST /api/v1/benchmark/smart-routing/evaluate and GET /api/v1/benchmark/smart-routing/latest.
"""

import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
import app.main as main_mod
from app.schemas.contract import (
    CoarseRoute,
    ComplexityLevel,
    TaskCategory,
)
from app.schemas.benchmark import (
    AnswerQualityReference,
    BaselineItemResult,
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkModelTier,
    BenchmarkTaskType,
    ConversationTurn,
    CorrectnessRequirement,
    HallucinationTolerance,
    LabelProvenance,
    LabelVerificationMethod,
    PiiRiskLevel,
    QualityCriteria,
    SensitivityFlags,
    SmartRoutingEvaluationReport,
    SmartRoutingItemResult,
    StrongModelBaselineReport,
    TokenUsageEstimate,
)
from app.dataset.benchmark_validator import load_benchmark_dataset
from app.dataset.smart_routing_runner import (
    SmartRoutingBenchmarkRunner,
    _compute_jaccard_similarity,
    _extract_local_features,
    _resolve_local_content,
    load_smart_routing_report,
    save_smart_routing_report,
)


# -----------------------------------------------------------------------------
# Fixtures & Helpers
# -----------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app)


def make_test_item(
    item_id: str = "bench_test_001",
    query: str = "What is the capital of France?",
    task_type: BenchmarkTaskType = BenchmarkTaskType.FACTUAL_QUESTION,
    expected_route: CoarseRoute = CoarseRoute.SIMPLE_MODEL_CANDIDATE,
    context_turns: list[ConversationTurn] | None = None,
    has_context: bool = False,
    format_compliance: str | None = None,
) -> BenchmarkItem:
    return BenchmarkItem(
        id=item_id,
        query=query,
        context_turns=context_turns or [],
        has_context_dependency=has_context,
        task_type=task_type,
        expected_route=expected_route,
        complexity_label=ComplexityLevel.LOW,
        target_model_tier=BenchmarkModelTier.FAST_CHEAP,
        quality_criteria=QualityCriteria(
            correctness=CorrectnessRequirement.SEMANTIC_TRUTH,
            completeness="FULL_COVERAGE",
            conciseness="BALANCED",
            context_adherence=has_context,
            format_compliance=format_compliance,
            hallucination_tolerance=HallucinationTolerance.ZERO_TOLERANCE,
            rubric_description="Accurate and complete reference response.",
        ),
        sensitivity_flags=SensitivityFlags(
            pii_level=PiiRiskLevel.NONE,
            contains_credentials=False,
            contains_proprietary_code=False,
            safety_sensitive=False,
            requires_redaction=False,
            authorized_for_benchmark=True,
        ),
        route_provenance=LabelProvenance(
            method=LabelVerificationMethod.REFERENCE_MODEL_EVALUATED,
            evaluator_id="reference_judge",
            confidence=0.95,
        ),
        complexity_provenance=LabelProvenance(
            method=LabelVerificationMethod.REFERENCE_MODEL_EVALUATED,
            evaluator_id="reference_judge",
            confidence=0.95,
        ),
        task_provenance=LabelProvenance(
            method=LabelVerificationMethod.REFERENCE_MODEL_EVALUATED,
            evaluator_id="reference_judge",
            confidence=0.95,
        ),
    )


def make_test_baseline_item(
    item_id: str = "bench_test_001",
    content: str = "Paris is the capital of France.",
) -> BaselineItemResult:
    return BaselineItemResult(
        item_id=item_id,
        task_type=BenchmarkTaskType.FACTUAL_QUESTION,
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        complexity_label=ComplexityLevel.LOW,
        target_model_tier=BenchmarkModelTier.FAST_CHEAP,
        latency_ms=120.0,
        token_usage=TokenUsageEstimate(input_tokens=10, output_tokens=10, total_tokens=20),
        answer_quality=AnswerQualityReference(
            content=content,
            finish_reason="stop",
            model_id="gpt-4o-2024-08-06",
            model_version="2024-08-06",
            provider_name="strong_model",
            content_length_chars=len(content),
            meets_format_compliance=True,
        ),
        status="SUCCESS",
    )


# -----------------------------------------------------------------------------
# 1. Helper Function Tests
# -----------------------------------------------------------------------------

class TestSmartRoutingHelpers:
    """Test feature extraction, lexical similarity, and local completion helpers."""

    def test_extract_local_features(self):
        feats = _extract_local_features("What is the code for def calculate(): pass ?")
        assert feats.has_code is True
        assert feats.has_questions is True
        assert feats.word_count == 9
        assert "code_syntax" in feats.detected_cues

    def test_compute_jaccard_similarity_exact_and_disjoint(self):
        sim_exact = _compute_jaccard_similarity("The capital is Paris", "The capital is Paris")
        assert sim_exact == 1.0

        sim_disjoint = _compute_jaccard_similarity("apple orange banana", "car boat aeroplane")
        assert sim_disjoint == 0.0

        sim_partial = _compute_jaccard_similarity("the quick brown fox", "the slow brown turtle")
        assert sim_partial is not None
        assert 0.0 < sim_partial < 1.0

    def test_compute_jaccard_similarity_none_empty(self):
        assert _compute_jaccard_similarity(None, "hello") is None
        assert _compute_jaccard_similarity("", "hello") is None
        assert _compute_jaccard_similarity("hello", "") is None

    def test_resolve_local_content(self):
        greeting_item = make_test_item(query="Hello there!")
        assert "Hello!" in _resolve_local_content(greeting_item)

        arith_item = make_test_item(query="25 * 4")
        assert _resolve_local_content(arith_item) == "100"


# -----------------------------------------------------------------------------
# 2. Smart Routing Runner Unit Tests
# -----------------------------------------------------------------------------

class TestSmartRoutingBenchmarkRunner:
    """Test runner execution paths, metrics collection, and context preservation."""

    @pytest.mark.anyio
    async def test_single_item_local_eligible(self):
        runner = SmartRoutingBenchmarkRunner()
        item = make_test_item(
            item_id="bench_greet_001",
            query="Hello, good morning!",
            task_type=BenchmarkTaskType.GREETING,
            expected_route=CoarseRoute.LOCAL_ELIGIBLE,
        )

        result: SmartRoutingItemResult = await runner.run_item(item)

        assert result.item_id == "bench_greet_001"
        assert result.status == "SUCCESS"
        assert result.is_local_handled is True
        assert result.is_small_model is False
        assert result.is_strong_model is False
        assert result.is_escalated is False
        assert result.content is not None
        assert "Hello!" in result.content
        assert result.latency_ms >= 0.0

    @pytest.mark.anyio
    async def test_single_item_small_model_candidate(self):
        runner = SmartRoutingBenchmarkRunner()
        item = make_test_item(
            item_id="bench_fact_001",
            query="What is the boiling point of water at sea level?",
            task_type=BenchmarkTaskType.FACTUAL_QUESTION,
            expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        )

        result: SmartRoutingItemResult = await runner.run_item(item)

        assert result.item_id == "bench_fact_001"
        assert result.status == "SUCCESS"
        # Deterministic router in test environment executes simple tasks via small or escalated
        assert result.is_small_model is True or result.is_strong_model is True
        assert result.content is not None
        assert len(result.content) > 0
        assert result.token_usage.total_tokens > 0

    @pytest.mark.anyio
    async def test_context_dependent_item_execution(self):
        runner = SmartRoutingBenchmarkRunner()
        context_turns = [
            ConversationTurn(role="user", content="I am designing a database schema for an e-commerce platform."),
            ConversationTurn(role="assistant", content="PostgreSQL is an excellent relational database choice."),
        ]
        item = make_test_item(
            item_id="bench_ctx_001",
            query="Which indexing strategy should I use for order lookups?",
            task_type=BenchmarkTaskType.CONTEXT_DEPENDENT,
            expected_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE,
            context_turns=context_turns,
            has_context=True,
        )

        result: SmartRoutingItemResult = await runner.run_item(item)

        assert result.item_id == "bench_ctx_001"
        assert result.status == "SUCCESS"
        assert result.content is not None
        assert len(result.content) > 0

    @pytest.mark.anyio
    async def test_canonical_benchmark_smart_routing(self):
        """Run smart routing evaluation on the full 15-item canonical benchmark."""
        canonical_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "dataset", "canonical_benchmark.json"
        )
        dataset = load_benchmark_dataset(canonical_path)
        assert len(dataset.items) == 15

        runner = SmartRoutingBenchmarkRunner(concurrency=4)
        report: SmartRoutingEvaluationReport = await runner.run_smart_routing_evaluation(dataset)

        # Verification of required aggregate metrics
        assert report.total_queries == 15
        assert report.successful_queries == 15
        assert report.failed_queries == 0
        assert report.error_rate == 0.0

        # Percentages: local, small, strong, escalation
        assert report.local_handled_count >= 1
        assert report.local_handled_pct > 0.0
        assert report.small_model_count >= 1
        assert report.small_model_pct > 0.0
        assert report.strong_model_count >= 1
        assert report.strong_model_pct > 0.0
        assert report.escalation_rate >= 0.0

        # Latency statistics
        assert report.mean_latency_ms >= 0.0
        assert report.p50_latency_ms <= report.p95_latency_ms

        # Token statistics
        assert report.total_tokens > 0
        assert report.mean_tokens_per_query > 0

        # Quality metrics
        assert report.quality_metrics.format_compliance_rate >= 0.0
        assert report.quality_metrics.non_empty_rate == 1.0

        # All 13 task categories represented
        for task_type in BenchmarkTaskType:
            assert task_type.value in report.by_task_type
            assert report.by_task_type[task_type.value]["count"] >= 1

    @pytest.mark.anyio
    async def test_comparative_baseline_lexical_overlap(self):
        """Verify lexical overlap calculation when a strong baseline report is provided."""
        item = make_test_item(
            item_id="bench_overlap_001",
            query="What is the speed of light in vacuum?",
            task_type=BenchmarkTaskType.FACTUAL_QUESTION,
        )
        dataset = BenchmarkDataset(
            dataset_id="test_overlap_ds",
            version="1.0.0",
            description="Test overlap calculation",
            items=[item],
        )

        baseline_item = make_test_baseline_item(
            item_id="bench_overlap_001",
            content="The speed of light in vacuum is approximately 299,792,458 meters per second.",
        )
        baseline_report = StrongModelBaselineReport(
            run_id="base_run_001",
            dataset_id="test_overlap_ds",
            dataset_version="1.0.0",
            model_id="gpt-4o-2024-08-06",
            model_version="2024-08-06",
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            total_latency_ms=100.0,
            mean_latency_ms=100.0,
            p50_latency_ms=100.0,
            p95_latency_ms=100.0,
            total_input_tokens=10,
            total_output_tokens=10,
            total_tokens=20,
            mean_tokens_per_query=20.0,
            by_task_type={},
            items=[baseline_item],
        )

        runner = SmartRoutingBenchmarkRunner()
        report = await runner.run_smart_routing_evaluation(
            dataset=dataset,
            baseline_report=baseline_report,
        )

        assert report.total_queries == 1
        assert report.quality_metrics.mean_lexical_overlap is not None
        assert report.items[0].lexical_overlap is not None

    @pytest.mark.anyio
    async def test_error_resilience_on_item_exception(self):
        """Verify individual query exception is caught and marked status=ERROR without failing report."""
        item_ok = make_test_item(item_id="item_ok", query="Hello!")
        item_err = make_test_item(item_id="item_err", query="throw an error")

        dataset = BenchmarkDataset(
            dataset_id="test_error_resilience",
            version="1.0.0",
            description="Test error resilience",
            items=[item_ok, item_err],
        )

        runner = SmartRoutingBenchmarkRunner()

        from app.main import evaluate_optimization
        original_eval = evaluate_optimization

        async def mock_evaluate(**kwargs):
            pkg = kwargs.get("package")
            if pkg and "throw an error" in pkg.query_text:
                raise RuntimeError("Simulated router failure")
            return await original_eval(**kwargs)

        with patch("app.main.evaluate_optimization", side_effect=mock_evaluate):
            report = await runner.run_smart_routing_evaluation(dataset)

        assert report.total_queries == 2
        assert report.successful_queries == 1
        assert report.failed_queries == 1
        assert report.error_rate == 50.0

        failed_item = next(i for i in report.items if i.item_id == "item_err")
        assert failed_item.status == "ERROR"
        assert "Simulated router failure" in (failed_item.error_message or "")


# -----------------------------------------------------------------------------
# 3. Serialization & Persistence Tests
# -----------------------------------------------------------------------------

class TestSmartRoutingReportSerialization:
    """Test saving and loading smart-routing reports to and from JSON."""

    @pytest.mark.anyio
    async def test_save_and_load_smart_routing_report_roundtrip(self):
        runner = SmartRoutingBenchmarkRunner()
        dataset = BenchmarkDataset(
            dataset_id="roundtrip_sr_test",
            version="1.0.0",
            description="Roundtrip persistence test",
            items=[
                make_test_item(item_id="rt_sr_001", query="Hello!"),
                make_test_item(item_id="rt_sr_002", query="What is 5 + 5?"),
            ],
        )

        report = await runner.run_smart_routing_evaluation(dataset)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_path = tf.name

        try:
            save_smart_routing_report(report, temp_path)
            loaded_report = load_smart_routing_report(temp_path)

            assert loaded_report.run_id == report.run_id
            assert loaded_report.dataset_id == report.dataset_id
            assert loaded_report.total_queries == 2
            assert loaded_report.local_handled_count == report.local_handled_count
            assert loaded_report.local_handled_pct == report.local_handled_pct
            assert len(loaded_report.items) == 2
            assert loaded_report.items[0].item_id == "rt_sr_001"
            assert loaded_report.items[1].item_id == "rt_sr_002"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_load_non_existent_file_raises_error(self):
        with pytest.raises(FileNotFoundError):
            load_smart_routing_report("non_existent_sr_report_12345.json")


# -----------------------------------------------------------------------------
# 4. FastAPI Endpoints Integration Tests
# -----------------------------------------------------------------------------

class TestFastAPISmartRoutingEndpoints:
    """Test REST endpoints for triggering and inspecting smart-routing evaluations."""

    def test_evaluate_smart_routing_endpoint(self, client):
        response = client.post(
            "/api/v1/benchmark/smart-routing/evaluate",
            json={"concurrency": 4, "save_to_disk": False},
        )
        assert response.status_code == 200
        data = response.json()

        assert "run_id" in data
        assert data["total_queries"] == 15
        assert data["successful_queries"] == 15
        assert data["failed_queries"] == 0
        assert "local_handled_pct" in data
        assert "cache_hit_rate" in data
        assert "small_model_pct" in data
        assert "strong_model_pct" in data
        assert "escalation_rate" in data
        assert "quality_metrics" in data
        assert "mean_latency_ms" in data
        assert "total_tokens" in data
        assert "by_task_type" in data
        assert len(data["items"]) == 15

    def test_get_latest_smart_routing_endpoint(self, client):
        # Trigger evaluation first
        eval_res = client.post("/api/v1/benchmark/smart-routing/evaluate", json={})
        assert eval_res.status_code == 200
        run_id = eval_res.json()["run_id"]

        # Retrieve latest
        latest_res = client.get("/api/v1/benchmark/smart-routing/latest")
        assert latest_res.status_code == 200
        assert latest_res.json()["run_id"] == run_id

    def test_get_latest_before_run_returns_404(self, client):
        # Temporarily clear latest report
        original_report = main_mod._latest_smart_routing_report
        main_mod._latest_smart_routing_report = None
        try:
            res = client.get("/api/v1/benchmark/smart-routing/latest")
            assert res.status_code == 404
            assert "no smart-routing evaluation report" in res.json()["detail"].lower()
        finally:
            main_mod._latest_smart_routing_report = original_report

    def test_evaluate_invalid_dataset_path_returns_404(self, client):
        response = client.post(
            "/api/v1/benchmark/smart-routing/evaluate",
            json={"dataset_path": "non_existent_smart_routing_benchmark.json"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
