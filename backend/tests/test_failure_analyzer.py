"""Unit and integration tests for Router Failure Analysis and Misrouting Diagnosis.

Verifies:
1. Failure Class Detection:
   - Queries routed too cheaply (ROUTED_TOO_CHEAPLY).
   - Queries routed too expensively (ROUTED_TOO_EXPENSIVELY).
   - Missed local handling (MISSED_LOCAL_HANDLING).
   - Unexpected escalations (UNEXPECTED_ESCALATION).
   - Optimal choices (NONE).
2. Disaggregated Category Breakdown:
   - Covers all 13 canonical task categories.
   - Verifies counts, accuracy rates, and primary failure classifications.
3. Context Dependency Breakdown:
   - Partitions into context_dependent (True) vs standalone (False).
   - Guarantees sum of partitions equals total queries analyzed.
4. Actionable Diagnoses & Recommendations:
   - Root-cause explanations, token impacts, dollar impacts, and tuning advice.
5. Canonical Benchmark Integration:
   - Full evaluation on 15-item canonical benchmark dataset.
6. Serialization & Persistence:
   - Lossless JSON saving and loading of RouterFailureAnalysisReport.
7. FastAPI REST Endpoints:
   - POST /api/v1/benchmark/failures/analyze and GET /api/v1/benchmark/failures/latest.
"""

import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.main as main_mod
from app.schemas.contract import CoarseRoute, ComplexityLevel
from app.schemas.benchmark import (
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkModelTier,
    BenchmarkTaskType,
    ContextDependencyFailureBreakdown,
    PricingConfig,
    QueryFailureItem,
    RouterFailureAnalysisReport,
    RouterFailureClass,
    SmartRoutingEvaluationReport,
    SmartRoutingItemResult,
    SmartRoutingQualityMetrics,
    TaskCategoryFailureBreakdown,
    TokenUsageEstimate,
)
from app.dataset.benchmark_validator import load_benchmark_dataset
from app.dataset.baseline_runner import StrongModelBaselineRunner
from app.dataset.smart_routing_runner import SmartRoutingBenchmarkRunner
from app.dataset.failure_analyzer import (
    RouterFailureAnalyzer,
    load_failure_report,
    save_failure_report,
)


@pytest.fixture
def client():
    return TestClient(app)


def make_sr_item(
    item_id: str,
    task_type: BenchmarkTaskType,
    expected_route: CoarseRoute,
    actual_route: str,
    is_local: bool = False,
    is_small: bool = False,
    is_strong: bool = False,
    is_escalated: bool = False,
    has_context: bool = False,
    query_text: str = "Test prompt",
) -> SmartRoutingItemResult:
    return SmartRoutingItemResult(
        item_id=item_id,
        task_type=task_type,
        expected_route=expected_route,
        actual_route=actual_route,
        decision_type="OPTIMIZED",
        reason_code="TEST_REASON",
        is_local_handled=is_local,
        is_cache_hit=False,
        is_small_model=is_small,
        is_strong_model=is_strong,
        is_escalated=is_escalated,
        has_context_dependency=has_context,
        query_text=query_text,
        latency_ms=10.0,
        token_usage=TokenUsageEstimate(input_tokens=20, output_tokens=30, total_tokens=50),
        content="Test completion",
        format_compliant=True,
        status="SUCCESS",
    )


# -----------------------------------------------------------------------------
# 1. Failure Class Detection Unit Tests
# -----------------------------------------------------------------------------

class TestFailureClassDetection:
    """Verify classification logic identifying suboptimal router decisions."""

    def test_detect_optimal_routing(self):
        item = make_sr_item(
            item_id="opt_001",
            task_type=BenchmarkTaskType.FACTUAL_QUESTION,
            expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
            actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
            is_small=True,
        )
        report = SmartRoutingEvaluationReport(
            run_id="run_test",
            dataset_id="ds_test",
            total_queries=1,
            successful_queries=1,
            total_input_tokens=20,
            total_output_tokens=30,
            total_tokens=50,
            mean_tokens_per_query=50.0,
            total_latency_ms=10.0,
            mean_latency_ms=10.0,
            p50_latency_ms=10.0,
            p95_latency_ms=10.0,
            quality_metrics=SmartRoutingQualityMetrics(),
            items=[item],
        )

        analyzer = RouterFailureAnalyzer()
        res = analyzer.analyze(report)

        assert res.total_misrouted_queries == 0
        assert res.correct_choices_count == 1
        assert res.overall_accuracy_rate == 100.0
        assert len(res.failure_items) == 0

    def test_detect_missed_local_handling(self):
        """Expected LOCAL_ELIGIBLE, but dispatched to small cloud model."""
        item = make_sr_item(
            item_id="missed_loc_001",
            task_type=BenchmarkTaskType.GREETING,
            expected_route=CoarseRoute.LOCAL_ELIGIBLE,
            actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
            is_local=False,
            is_small=True,
        )
        report = SmartRoutingEvaluationReport(
            run_id="run_test",
            dataset_id="ds_test",
            total_queries=1,
            successful_queries=1,
            total_input_tokens=20,
            total_output_tokens=30,
            total_tokens=50,
            mean_tokens_per_query=50.0,
            total_latency_ms=10.0,
            mean_latency_ms=10.0,
            p50_latency_ms=10.0,
            p95_latency_ms=10.0,
            quality_metrics=SmartRoutingQualityMetrics(),
            items=[item],
        )

        analyzer = RouterFailureAnalyzer()
        res = analyzer.analyze(report)

        assert res.total_misrouted_queries == 1
        assert res.missed_local_count == 1
        assert res.missed_local_rate == 100.0
        assert len(res.failure_items) == 1
        assert res.failure_items[0].failure_class == RouterFailureClass.MISSED_LOCAL_HANDLING
        assert "local" in res.failure_items[0].diagnosis.lower()

    def test_detect_routed_too_expensively(self):
        """Expected SIMPLE_MODEL_CANDIDATE, but routed to COMPLEX_MODEL_CANDIDATE."""
        item = make_sr_item(
            item_id="over_001",
            task_type=BenchmarkTaskType.REWRITING,
            expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
            actual_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE.value,
            is_strong=True,
        )
        report = SmartRoutingEvaluationReport(
            run_id="run_test",
            dataset_id="ds_test",
            total_queries=1,
            successful_queries=1,
            total_input_tokens=20,
            total_output_tokens=30,
            total_tokens=50,
            mean_tokens_per_query=50.0,
            total_latency_ms=10.0,
            mean_latency_ms=10.0,
            p50_latency_ms=10.0,
            p95_latency_ms=10.0,
            quality_metrics=SmartRoutingQualityMetrics(),
            items=[item],
        )

        analyzer = RouterFailureAnalyzer()
        res = analyzer.analyze(report)

        assert res.total_misrouted_queries == 1
        assert res.too_expensive_count == 1
        assert res.too_expensive_rate == 100.0
        assert res.failure_items[0].failure_class == RouterFailureClass.ROUTED_TOO_EXPENSIVELY
        assert res.failure_items[0].cost_impact_usd > 0.0

    def test_detect_routed_too_cheaply(self):
        """Expected COMPLEX_MODEL_CANDIDATE, but routed to SIMPLE_MODEL_CANDIDATE."""
        item = make_sr_item(
            item_id="under_001",
            task_type=BenchmarkTaskType.REASONING,
            expected_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE,
            actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
            is_small=True,
        )
        report = SmartRoutingEvaluationReport(
            run_id="run_test",
            dataset_id="ds_test",
            total_queries=1,
            successful_queries=1,
            total_input_tokens=20,
            total_output_tokens=30,
            total_tokens=50,
            mean_tokens_per_query=50.0,
            total_latency_ms=10.0,
            mean_latency_ms=10.0,
            p50_latency_ms=10.0,
            p95_latency_ms=10.0,
            quality_metrics=SmartRoutingQualityMetrics(),
            items=[item],
        )

        analyzer = RouterFailureAnalyzer()
        res = analyzer.analyze(report)

        assert res.total_misrouted_queries == 1
        assert res.too_cheap_count == 1
        assert res.too_cheap_rate == 100.0
        assert res.failure_items[0].failure_class == RouterFailureClass.ROUTED_TOO_CHEAPLY
        assert "risk" in res.failure_items[0].diagnosis.lower()

    def test_detect_unexpected_escalation(self):
        """Small model candidate failed completeness check and was escalated."""
        item = make_sr_item(
            item_id="esc_001",
            task_type=BenchmarkTaskType.CODING,
            expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
            actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
            is_escalated=True,
            is_strong=True,
        )
        report = SmartRoutingEvaluationReport(
            run_id="run_test",
            dataset_id="ds_test",
            total_queries=1,
            successful_queries=1,
            total_input_tokens=20,
            total_output_tokens=30,
            total_tokens=50,
            mean_tokens_per_query=50.0,
            total_latency_ms=10.0,
            mean_latency_ms=10.0,
            p50_latency_ms=10.0,
            p95_latency_ms=10.0,
            quality_metrics=SmartRoutingQualityMetrics(),
            items=[item],
        )

        analyzer = RouterFailureAnalyzer()
        res = analyzer.analyze(report)

        assert res.total_misrouted_queries == 1
        assert res.escalation_count == 1
        assert res.escalation_rate == 100.0
        assert res.failure_items[0].failure_class == RouterFailureClass.UNEXPECTED_ESCALATION


# -----------------------------------------------------------------------------
# 2. Task Category & Context Dependency Breakdown Tests
# -----------------------------------------------------------------------------

class TestBreakdownsByCategoryAndContext:
    """Verify exhaustive breakdowns across all 13 categories and context partitions."""

    def test_exhaustive_13_category_breakdown(self):
        items = [
            make_sr_item(
                item_id=f"item_{tt.value}",
                task_type=tt,
                expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
                actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
                is_small=True,
            )
            for tt in BenchmarkTaskType
        ]
        # Misroute one item deliberately
        items[0].actual_route = CoarseRoute.COMPLEX_MODEL_CANDIDATE.value
        items[0].is_strong_model = True
        items[0].is_small_model = False

        report = SmartRoutingEvaluationReport(
            run_id="run_test",
            dataset_id="ds_test",
            total_queries=len(items),
            successful_queries=len(items),
            total_input_tokens=100,
            total_output_tokens=100,
            total_tokens=200,
            mean_tokens_per_query=20.0,
            total_latency_ms=100.0,
            mean_latency_ms=10.0,
            p50_latency_ms=10.0,
            p95_latency_ms=10.0,
            quality_metrics=SmartRoutingQualityMetrics(),
            items=items,
        )

        analyzer = RouterFailureAnalyzer()
        res = analyzer.analyze(report)

        # Assert all 13 categories are present in by_task_category
        assert len(res.by_task_category) == len(BenchmarkTaskType)
        for tt in BenchmarkTaskType:
            assert tt.value in res.by_task_category
            b = res.by_task_category[tt.value]
            assert b.total_queries >= 1

        # Check misrouted category
        first_cat = items[0].task_type.value
        assert res.by_task_category[first_cat].too_expensive_count == 1
        assert res.by_task_category[first_cat].accuracy_rate == 0.0
        assert res.by_task_category[first_cat].primary_failure_class == RouterFailureClass.ROUTED_TOO_EXPENSIVELY

    def test_context_dependency_partition(self):
        item_ctx = make_sr_item(
            item_id="item_ctx",
            task_type=BenchmarkTaskType.CONTEXT_DEPENDENT,
            expected_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE,
            actual_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE.value,
            is_strong=True,
            has_context=True,
        )
        item_standalone = make_sr_item(
            item_id="item_std",
            task_type=BenchmarkTaskType.ARITHMETIC,
            expected_route=CoarseRoute.LOCAL_ELIGIBLE,
            actual_route=CoarseRoute.LOCAL_ELIGIBLE.value,
            is_local=True,
            has_context=False,
        )

        report = SmartRoutingEvaluationReport(
            run_id="run_test",
            dataset_id="ds_test",
            total_queries=2,
            successful_queries=2,
            total_input_tokens=20,
            total_output_tokens=20,
            total_tokens=40,
            mean_tokens_per_query=20.0,
            total_latency_ms=20.0,
            mean_latency_ms=10.0,
            p50_latency_ms=10.0,
            p95_latency_ms=10.0,
            quality_metrics=SmartRoutingQualityMetrics(),
            items=[item_ctx, item_standalone],
        )

        analyzer = RouterFailureAnalyzer()
        res = analyzer.analyze(report)

        assert "context_dependent" in res.by_context_dependency
        assert "standalone" in res.by_context_dependency

        ctx_breakdown = res.by_context_dependency["context_dependent"]
        std_breakdown = res.by_context_dependency["standalone"]

        assert ctx_breakdown.total_queries == 1
        assert ctx_breakdown.has_context_dependency is True
        assert std_breakdown.total_queries == 1
        assert std_breakdown.has_context_dependency is False
        assert ctx_breakdown.total_queries + std_breakdown.total_queries == res.total_queries_analyzed


# -----------------------------------------------------------------------------
# 3. Canonical Benchmark Dataset End-to-End Failure Analysis
# -----------------------------------------------------------------------------

class TestCanonicalBenchmarkFailureAnalysis:
    """Run full failure diagnosis on the canonical benchmark evaluation results."""

    @pytest.mark.anyio
    async def test_canonical_failure_analysis_run(self):
        canonical_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "dataset", "canonical_benchmark.json"
        )
        dataset = load_benchmark_dataset(canonical_path)
        assert len(dataset.items) == 15

        # Run smart routing
        runner = SmartRoutingBenchmarkRunner(concurrency=4)
        sr_report = await runner.run_smart_routing_evaluation(dataset)

        analyzer = RouterFailureAnalyzer()
        fail_report: RouterFailureAnalysisReport = analyzer.analyze(
            report=sr_report,
            dataset=dataset,
        )

        assert fail_report.total_queries_analyzed == 15
        assert fail_report.overall_accuracy_rate >= 0.0
        assert fail_report.total_misrouted_queries == len(fail_report.failure_items)
        assert len(fail_report.by_task_category) == 13
        assert len(fail_report.key_recommendations) >= 1

        # Check that failure items have query previews and diagnoses
        for item in fail_report.failure_items:
            assert len(item.query_preview) > 0
            assert len(item.diagnosis) > 0

    def test_save_and_load_failure_report_roundtrip(self):
        item = make_sr_item(
            item_id="roundtrip_001",
            task_type=BenchmarkTaskType.DEBUGGING,
            expected_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE,
            actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
            is_small=True,
        )
        report = SmartRoutingEvaluationReport(
            run_id="run_rt",
            dataset_id="ds_rt",
            total_queries=1,
            successful_queries=1,
            total_input_tokens=20,
            total_output_tokens=30,
            total_tokens=50,
            mean_tokens_per_query=50.0,
            total_latency_ms=10.0,
            mean_latency_ms=10.0,
            p50_latency_ms=10.0,
            p95_latency_ms=10.0,
            quality_metrics=SmartRoutingQualityMetrics(),
            items=[item],
        )

        analyzer = RouterFailureAnalyzer()
        analysis = analyzer.analyze(report)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_path = tf.name

        try:
            save_failure_report(analysis, temp_path)
            loaded = load_failure_report(temp_path)

            assert loaded.run_id == analysis.run_id
            assert loaded.total_queries_analyzed == 1
            assert loaded.total_misrouted_queries == 1
            assert len(loaded.failure_items) == 1
            assert loaded.failure_items[0].failure_class == RouterFailureClass.ROUTED_TOO_CHEAPLY
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


# -----------------------------------------------------------------------------
# 4. FastAPI Endpoints Integration Tests
# -----------------------------------------------------------------------------

class TestFastAPIFailureEndpoints:
    """Test REST API endpoints for failure analysis."""

    def test_analyze_failures_endpoint_with_active_session(self, client):
        # 1. Run smart routing first
        client.post("/api/v1/benchmark/smart-routing/evaluate", json={})

        # 2. Analyze failures
        res = client.post("/api/v1/benchmark/failures/analyze", json={})
        assert res.status_code == 200
        data = res.json()

        assert "run_id" in data
        assert data["total_queries_analyzed"] == 15
        assert "overall_accuracy_rate" in data
        assert "overall_misrouting_rate" in data
        assert "by_task_category" in data
        assert len(data["by_task_category"]) == 13
        assert "by_context_dependency" in data
        assert "context_dependent" in data["by_context_dependency"]
        assert "standalone" in data["by_context_dependency"]
        assert "failure_items" in data
        assert "key_recommendations" in data

    def test_get_latest_failure_report(self, client):
        client.post("/api/v1/benchmark/smart-routing/evaluate", json={})
        analyze_res = client.post("/api/v1/benchmark/failures/analyze", json={})
        run_id = analyze_res.json()["run_id"]

        latest_res = client.get("/api/v1/benchmark/failures/latest")
        assert latest_res.status_code == 200
        assert latest_res.json()["run_id"] == run_id

    def test_get_latest_before_run_returns_404(self, client):
        original = main_mod._latest_failure_analysis_report
        main_mod._latest_failure_analysis_report = None
        try:
            res = client.get("/api/v1/benchmark/failures/latest")
            assert res.status_code == 404
            assert "no failure analysis report" in res.json()["detail"].lower()
        finally:
            main_mod._latest_failure_analysis_report = original
