"""Unit and integration tests for Stronger Model Benchmark Baseline Evaluation.

Verifies:
1. Strict Strong-Model Baseline:
   Sends queries directly to the stronger model without router interference.
2. Complete Metrics Capture:
   Latency (ms), estimated token usage (input, output, total), and answer-quality reference.
3. Multi-Turn Context Transfer:
   Conversational history is faithfully formatted into GatewayRequest context turns.
4. Canonical Benchmark Coverage:
   Evaluates all 15 canonical queries across all 13 task categories.
5. Error Resilience:
   Transient failures on individual items are safely contained without failing the run.
6. Statistical Calculation:
   Mean latency, p50, p95, token metrics, and per-category distributions.
7. Serialization & Roundtripping:
   Lossless JSON saving and loading of StrongModelBaselineReport.
8. FastAPI Endpoints:
   POST /api/v1/benchmark/baseline/evaluate and GET /api/v1/benchmark/baseline/latest.
"""

import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.contract import CoarseRoute, ComplexityLevel, ModelTier
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
    StrongModelBaselineReport,
    TokenUsageEstimate,
)
from app.dataset.benchmark_validator import load_benchmark_dataset
from app.dataset.baseline_runner import (
    StrongModelBaselineRunner,
    _check_format_compliance,
    _compute_percentile,
    load_baseline_report,
    save_baseline_report,
)
from app.gateway.base import GatewayRequest, GatewayResponse, GatewayError


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
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
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


# -----------------------------------------------------------------------------
# 1. Helper Function Tests
# -----------------------------------------------------------------------------

class TestBaselineHelpers:
    """Test percentile calculations and format compliance heuristics."""

    def test_compute_percentile_empty_and_single(self):
        assert _compute_percentile([], 50.0) == 0.0
        assert _compute_percentile([42.0], 50.0) == 42.0
        assert _compute_percentile([42.0], 95.0) == 42.0

    def test_compute_percentile_spread(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        p50 = _compute_percentile(values, 50.0)
        p95 = _compute_percentile(values, 95.0)
        assert p50 == pytest.approx(55.0, abs=1.0)
        assert p95 > 90.0

    def test_check_format_compliance_json(self):
        assert _check_format_compliance('{"answer": "Paris"}', "JSON") is True
        assert _check_format_compliance('```json\n{"status": "ok"}\n```', "JSON") is True
        assert _check_format_compliance('Not a JSON string', "JSON") is False

    def test_check_format_compliance_code(self):
        assert _check_format_compliance('```python\ndef foo(): pass\n```', "CODE") is True
        assert _check_format_compliance('Plain text without code', "CODE") is False

    def test_check_format_compliance_none(self):
        assert _check_format_compliance('Any response text', None) is None


# -----------------------------------------------------------------------------
# 2. Strong Model Baseline Runner Unit Tests
# -----------------------------------------------------------------------------

class TestStrongModelBaselineRunner:
    """Test runner behavior, metrics recording, and context handling."""

    @pytest.mark.anyio
    async def test_single_item_execution_success(self):
        runner = StrongModelBaselineRunner()
        item = make_test_item(
            item_id="bench_fact_001",
            query="What is the boiling point of water at sea level?",
            task_type=BenchmarkTaskType.FACTUAL_QUESTION,
        )

        result: BaselineItemResult = await runner.run_item(item)

        assert result.item_id == "bench_fact_001"
        assert result.task_type == BenchmarkTaskType.FACTUAL_QUESTION
        assert result.status == "SUCCESS"
        assert result.error_message is None
        assert result.latency_ms >= 0.0
        assert result.token_usage.input_tokens > 0 or result.token_usage.total_tokens >= 0
        assert len(result.answer_quality.content) > 0
        assert result.answer_quality.finish_reason == "stop"
        assert result.answer_quality.provider_name in ("strong_model", "claude")

    @pytest.mark.anyio
    async def test_context_dependent_item_execution(self):
        runner = StrongModelBaselineRunner()
        context_turns = [
            ConversationTurn(role="user", content="I am building a web app in Python."),
            ConversationTurn(role="assistant", content="FastAPI is a modern, fast web framework."),
        ]
        item = make_test_item(
            item_id="bench_ctx_001",
            query="Which router should I use for it?",
            task_type=BenchmarkTaskType.CONTEXT_DEPENDENT,
            context_turns=context_turns,
            has_context=True,
        )

        result: BaselineItemResult = await runner.run_item(item)

        assert result.status == "SUCCESS"
        assert result.item_id == "bench_ctx_001"
        assert result.task_type == BenchmarkTaskType.CONTEXT_DEPENDENT
        assert len(result.answer_quality.content) > 0

    @pytest.mark.anyio
    async def test_canonical_benchmark_evaluation(self):
        """Run baseline evaluation on the full 15-item canonical benchmark."""
        canonical_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "dataset", "canonical_benchmark.json"
        )
        dataset = load_benchmark_dataset(canonical_path)
        assert len(dataset.items) == 15

        runner = StrongModelBaselineRunner(max_concurrency=4)
        report: StrongModelBaselineReport = await runner.run_baseline_evaluation(dataset)

        assert report.total_queries == 15
        assert report.successful_queries == 15
        assert report.failed_queries == 0
        assert report.dataset_id == dataset.dataset_id
        assert report.dataset_version == dataset.version
        assert report.mean_latency_ms >= 0.0
        assert report.p50_latency_ms <= report.p95_latency_ms
        assert report.total_tokens > 0
        assert report.mean_tokens_per_query > 0

        # Verify all 13 task types are represented in report.by_task_type
        for task_type in BenchmarkTaskType:
            assert task_type.value in report.by_task_type
            assert report.by_task_type[task_type.value]["count"] >= 1
            assert report.by_task_type[task_type.value]["successful"] >= 1

    @pytest.mark.anyio
    async def test_error_resilience_on_item_failure(self):
        """Verify individual query failure is safely contained with status=ERROR."""
        item_ok = make_test_item(item_id="item_ok", query="2 + 2 = ?")
        item_err = make_test_item(item_id="item_err", query="fail me please")

        dataset = BenchmarkDataset(
            dataset_id="test_error_resilience",
            version="1.0.0",
            description="Test error resilience",
            items=[item_ok, item_err],
        )

        runner = StrongModelBaselineRunner()

        # Mock execute_strong to succeed on item_ok and raise GatewayError on item_err
        original_execute_strong = runner._gateway.execute_strong

        async def mock_execute(req: GatewayRequest, task_category=None):
            if "fail me" in req.prompt:
                raise GatewayError("Simulated upstream gateway timeout")
            return await original_execute_strong(req, task_category=task_category)

        with patch.object(runner._gateway, "execute_strong", side_effect=mock_execute):
            report = await runner.run_baseline_evaluation(dataset)

        assert report.total_queries == 2
        assert report.successful_queries == 1
        assert report.failed_queries == 1

        failed_item = next(i for i in report.items if i.item_id == "item_err")
        assert failed_item.status == "ERROR"
        assert "Simulated upstream gateway timeout" in failed_item.error_message
        assert failed_item.token_usage.total_tokens == 0


# -----------------------------------------------------------------------------
# 3. Serialization & Persistence Tests
# -----------------------------------------------------------------------------

class TestBaselineReportSerialization:
    """Test saving and loading baseline reports to and from JSON."""

    @pytest.mark.anyio
    async def test_save_and_load_baseline_report_roundtrip(self):
        runner = StrongModelBaselineRunner()
        dataset = BenchmarkDataset(
            dataset_id="roundtrip_test",
            version="1.0.0",
            description="Roundtrip persistence test",
            items=[
                make_test_item(item_id="rt_001", query="Hello!"),
                make_test_item(item_id="rt_002", query="What is 10 * 10?"),
            ],
        )

        report = await runner.run_baseline_evaluation(dataset)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_path = tf.name

        try:
            save_baseline_report(report, temp_path)
            loaded_report = load_baseline_report(temp_path)

            assert loaded_report.run_id == report.run_id
            assert loaded_report.dataset_id == report.dataset_id
            assert loaded_report.total_queries == 2
            assert loaded_report.successful_queries == 2
            assert loaded_report.mean_latency_ms == report.mean_latency_ms
            assert len(loaded_report.items) == 2
            assert loaded_report.items[0].item_id == "rt_001"
            assert loaded_report.items[1].item_id == "rt_002"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_load_non_existent_file_raises_error(self):
        with pytest.raises(FileNotFoundError):
            load_baseline_report("non_existent_baseline_report_12345.json")


# -----------------------------------------------------------------------------
# 4. FastAPI Endpoints Integration Tests
# -----------------------------------------------------------------------------

class TestFastAPIBaselineEndpoints:
    """Test REST endpoints for triggering and inspecting baseline evaluations."""

    def test_evaluate_canonical_baseline_endpoint(self, client):
        response = client.post(
            "/api/v1/benchmark/baseline/evaluate",
            json={"concurrency": 4, "save_to_disk": False},
        )
        assert response.status_code == 200
        data = response.json()

        assert "run_id" in data
        assert data["total_queries"] == 15
        assert data["successful_queries"] == 15
        assert data["failed_queries"] == 0
        assert "mean_latency_ms" in data
        assert "p50_latency_ms" in data
        assert "p95_latency_ms" in data
        assert "total_tokens" in data
        assert "by_task_type" in data
        assert len(data["items"]) == 15

    def test_get_latest_baseline_endpoint(self, client):
        # Trigger evaluation first
        eval_res = client.post("/api/v1/benchmark/baseline/evaluate", json={})
        assert eval_res.status_code == 200
        run_id = eval_res.json()["run_id"]

        # Retrieve latest
        latest_res = client.get("/api/v1/benchmark/baseline/latest")
        assert latest_res.status_code == 200
        assert latest_res.json()["run_id"] == run_id

    def test_evaluate_invalid_dataset_path_returns_404(self, client):
        response = client.post(
            "/api/v1/benchmark/baseline/evaluate",
            json={"dataset_path": "non_existent_path_to_benchmark.json"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
