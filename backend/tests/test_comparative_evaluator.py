"""Unit and integration tests for Disaggregated Comparative Benchmark Evaluation.

Verifies:
1. No Single "Savings" Number Without Denominators & Assumptions:
   Guarantees all savings/deltas have explicit numerator, denominator, unit, and formula.
2. Disaggregated Savings Dimensions:
   - Input-token savings vs output-token savings vs cache savings vs avoided-call savings.
   - Small-model substitution savings vs local-handled savings vs cache savings vs escalation overhead.
3. Configurable & Explicit Pricing Model:
   Verifies standard default list rates and custom user overrides for per-tier token pricing.
4. Avoided-Model-Call Savings:
   Accurately tracks queries handled locally on-device or via cache without cloud model calls.
5. Latency & Quality Deltas:
   Verifies mean/p50/p95 latency deltas, format compliance deltas, non-empty deltas,
   lexical overlap distribution, and parity/degraded/improved verdict categorizations.
6. End-to-End Integration & Persistence:
   Evaluates canonical benchmark dataset, attaches comparative analysis, and verifies JSON roundtrip.
7. FastAPI Endpoints:
   POST /api/v1/benchmark/compare and GET /api/v1/benchmark/compare/latest.
"""

import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.main as main_mod
from app.schemas.contract import CoarseRoute, ComplexityLevel
from app.schemas.benchmark import (
    AnswerQualityReference,
    BaselineItemResult,
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkModelTier,
    BenchmarkTaskType,
    ComparativeEvaluationReport,
    MetricWithDenominator,
    PricingConfig,
    QualityCriteria,
    SensitivityFlags,
    SmartRoutingEvaluationReport,
    SmartRoutingItemResult,
    SmartRoutingQualityMetrics,
    StrongModelBaselineReport,
    TokenUsageEstimate,
)
from app.dataset.benchmark_validator import load_benchmark_dataset
from app.dataset.baseline_runner import StrongModelBaselineRunner
from app.dataset.smart_routing_runner import SmartRoutingBenchmarkRunner
from app.dataset.comparative_evaluator import (
    ComparativeBenchmarkEvaluator,
    _make_metric,
    load_comparative_report,
    save_comparative_report,
)


@pytest.fixture
def client():
    return TestClient(app)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def make_test_baseline_report() -> StrongModelBaselineReport:
    """Constructs a deterministic baseline report with 3 items."""
    item1 = BaselineItemResult(
        item_id="item_greet",
        task_type=BenchmarkTaskType.GREETING,
        expected_route=CoarseRoute.LOCAL_ELIGIBLE,
        complexity_label=ComplexityLevel.LOW,
        target_model_tier=BenchmarkModelTier.FAST_CHEAP,
        latency_ms=10.0,
        token_usage=TokenUsageEstimate(input_tokens=10, output_tokens=10, total_tokens=20),
        answer_quality=AnswerQualityReference(
            content="Hello there!",
            finish_reason="stop",
            model_id="gpt-4o-2024-08-06",
            provider_name="strong_model",
            content_length_chars=12,
            meets_format_compliance=True,
        ),
        status="SUCCESS",
    )
    item2 = BaselineItemResult(
        item_id="item_simple",
        task_type=BenchmarkTaskType.FACTUAL_QUESTION,
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        complexity_label=ComplexityLevel.LOW,
        target_model_tier=BenchmarkModelTier.FAST_CHEAP,
        latency_ms=20.0,
        token_usage=TokenUsageEstimate(input_tokens=50, output_tokens=50, total_tokens=100),
        answer_quality=AnswerQualityReference(
            content="Water boils at 100 degrees Celsius.",
            finish_reason="stop",
            model_id="gpt-4o-2024-08-06",
            provider_name="strong_model",
            content_length_chars=35,
            meets_format_compliance=True,
        ),
        status="SUCCESS",
    )
    item3 = BaselineItemResult(
        item_id="item_complex",
        task_type=BenchmarkTaskType.REASONING,
        expected_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE,
        complexity_label=ComplexityLevel.HIGH,
        target_model_tier=BenchmarkModelTier.STRONG,
        latency_ms=50.0,
        token_usage=TokenUsageEstimate(input_tokens=100, output_tokens=200, total_tokens=300),
        answer_quality=AnswerQualityReference(
            content="Detailed multi-step deduction...",
            finish_reason="stop",
            model_id="gpt-4o-2024-08-06",
            provider_name="strong_model",
            content_length_chars=100,
            meets_format_compliance=True,
        ),
        status="SUCCESS",
    )

    return StrongModelBaselineReport(
        run_id="base_test_001",
        dataset_id="test_ds",
        dataset_version="1.0.0",
        model_id="gpt-4o-2024-08-06",
        total_queries=3,
        successful_queries=3,
        failed_queries=0,
        total_latency_ms=80.0,
        mean_latency_ms=26.67,
        p50_latency_ms=20.0,
        p95_latency_ms=47.0,
        total_input_tokens=160,
        total_output_tokens=260,
        total_tokens=420,
        mean_tokens_per_query=140.0,
        by_task_type={},
        items=[item1, item2, item3],
    )


def make_test_smart_routing_report() -> SmartRoutingEvaluationReport:
    """Constructs a deterministic smart routing report matching baseline items."""
    # item1: handled locally (0 cloud tokens, $0 cost)
    item1 = SmartRoutingItemResult(
        item_id="item_greet",
        task_type=BenchmarkTaskType.GREETING,
        expected_route=CoarseRoute.LOCAL_ELIGIBLE,
        actual_route=CoarseRoute.LOCAL_ELIGIBLE.value,
        decision_type="BYPASS",
        reason_code="SIMPLE_TASK_SIGNAL",
        is_local_handled=True,
        is_cache_hit=False,
        is_small_model=False,
        is_strong_model=False,
        is_escalated=False,
        latency_ms=2.0,
        token_usage=TokenUsageEstimate(input_tokens=0, output_tokens=0, total_tokens=0),
        content="Hello!",
        format_compliant=True,
        lexical_overlap=0.8,
        status="SUCCESS",
    )
    # item2: executed on small model (gpt-4o-mini)
    item2 = SmartRoutingItemResult(
        item_id="item_simple",
        task_type=BenchmarkTaskType.FACTUAL_QUESTION,
        expected_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        actual_route=CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
        decision_type="OPTIMIZED",
        reason_code="SIMPLE_TASK_SIGNAL",
        is_local_handled=False,
        is_cache_hit=False,
        is_small_model=True,
        is_strong_model=False,
        is_escalated=False,
        executed_model_id="gpt-4o-mini-2024-07-18",
        latency_ms=8.0,
        token_usage=TokenUsageEstimate(input_tokens=50, output_tokens=50, total_tokens=100),
        content="Water boils at 100C.",
        format_compliant=True,
        lexical_overlap=0.75,
        status="SUCCESS",
    )
    # item3: executed on strong model (gpt-4o)
    item3 = SmartRoutingItemResult(
        item_id="item_complex",
        task_type=BenchmarkTaskType.REASONING,
        expected_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE,
        actual_route=CoarseRoute.COMPLEX_MODEL_CANDIDATE.value,
        decision_type="OPTIMIZED",
        reason_code="COMPLEX_TASK_SIGNAL",
        is_local_handled=False,
        is_cache_hit=False,
        is_small_model=False,
        is_strong_model=True,
        is_escalated=False,
        executed_model_id="gpt-4o-2024-08-06",
        latency_ms=45.0,
        token_usage=TokenUsageEstimate(input_tokens=100, output_tokens=200, total_tokens=300),
        content="Detailed multi-step deduction...",
        format_compliant=True,
        lexical_overlap=1.0,
        status="SUCCESS",
    )

    return SmartRoutingEvaluationReport(
        run_id="sr_test_001",
        dataset_id="test_ds",
        dataset_version="1.0.0",
        total_queries=3,
        successful_queries=3,
        failed_queries=0,
        local_handled_count=1,
        local_handled_pct=33.33,
        cache_hit_count=0,
        cache_hit_rate=0.0,
        small_model_count=1,
        small_model_pct=33.33,
        strong_model_count=1,
        strong_model_pct=33.33,
        escalation_count=0,
        escalation_rate=0.0,
        error_count=0,
        error_rate=0.0,
        total_input_tokens=150,
        total_output_tokens=250,
        total_tokens=400,
        mean_tokens_per_query=133.33,
        total_latency_ms=55.0,
        mean_latency_ms=18.33,
        p50_latency_ms=8.0,
        p95_latency_ms=41.3,
        quality_metrics=SmartRoutingQualityMetrics(
            format_compliance_rate=1.0,
            non_empty_rate=1.0,
            mean_completeness_score=0.95,
            mean_lexical_overlap=0.85,
        ),
        by_task_type={},
        items=[item1, item2, item3],
    )


# -----------------------------------------------------------------------------
# 1. PricingConfig & MetricWithDenominator Tests
# -----------------------------------------------------------------------------

class TestPricingConfigAndMetricHelpers:
    """Test pricing models, denominator guarantees, and percentage formulas."""

    def test_default_pricing_values_are_explicit(self):
        pricing = PricingConfig()
        assert pricing.currency == "USD"
        assert pricing.strong_input_price_per_million == 2.50
        assert pricing.strong_output_price_per_million == 10.00
        assert pricing.small_input_price_per_million == 0.15
        assert pricing.small_output_price_per_million == 0.60
        assert pricing.local_cost_per_query == 0.0
        assert pricing.cache_lookup_cost_per_query == 0.0
        assert len(pricing.pricing_source) > 0

    def test_custom_pricing_config_overrides(self):
        custom = PricingConfig(
            currency="EUR",
            strong_input_price_per_million=3.00,
            strong_output_price_per_million=12.00,
            small_input_price_per_million=0.20,
            small_output_price_per_million=0.80,
            local_cost_per_query=0.0001,
            cache_lookup_cost_per_query=0.00005,
            pricing_source="Enterprise negotiated pricing",
        )
        assert custom.currency == "EUR"
        assert custom.strong_input_price_per_million == 3.00
        assert custom.local_cost_per_query == 0.0001
        assert custom.pricing_source == "Enterprise negotiated pricing"

    def test_make_metric_calculates_percentage_and_preserves_denominator(self):
        metric = _make_metric(
            numerator=20.0,
            denominator=100.0,
            unit="tokens",
            formula="savings / baseline * 100",
            interpretation="Positive indicates savings",
        )
        assert metric.numerator == 20.0
        assert metric.denominator == 100.0
        assert metric.relative_change_pct == 20.0
        assert metric.unit == "tokens"
        assert metric.formula == "savings / baseline * 100"

    def test_make_metric_zero_denominator_safeguard(self):
        metric = _make_metric(
            numerator=0.0,
            denominator=0.0,
            unit="USD",
            formula="savings / 0",
            interpretation="No baseline cost available",
        )
        assert metric.numerator == 0.0
        assert metric.denominator == 0.0
        assert metric.relative_change_pct is None


# -----------------------------------------------------------------------------
# 2. Comparative Evaluator Unit Tests
# -----------------------------------------------------------------------------

class TestComparativeBenchmarkEvaluator:
    """Test disaggregated savings, avoided calls, and quality deltas."""

    def test_disaggregated_token_savings(self):
        base = make_test_baseline_report()
        sr = make_test_smart_routing_report()
        evaluator = ComparativeBenchmarkEvaluator()

        report: ComparativeEvaluationReport = evaluator.compare(base, sr)

        ts = report.token_savings

        # Input tokens: base 160 vs sr 150 = 10 saved
        assert ts.input_token_savings.numerator == 10.0
        assert ts.input_token_savings.denominator == 160.0
        assert ts.input_token_savings.relative_change_pct == pytest.approx(6.25, abs=0.01)

        # Output tokens: base 260 vs sr 250 = 10 saved
        assert ts.output_token_savings.numerator == 10.0
        assert ts.output_token_savings.denominator == 260.0
        assert ts.output_token_savings.relative_change_pct == pytest.approx(3.85, abs=0.01)

        # Total tokens: base 420 vs sr 400 = 20 saved
        assert ts.total_token_savings.numerator == 20.0
        assert ts.total_token_savings.denominator == 420.0
        assert ts.total_token_savings.relative_change_pct == pytest.approx(4.76, abs=0.01)

        # Avoided call token savings: item_greet saved 20 tokens
        assert ts.avoided_call_token_savings.numerator == 20.0
        assert ts.avoided_call_token_savings.denominator == 420.0
        assert ts.avoided_call_token_savings.relative_change_pct == pytest.approx(4.76, abs=0.01)

    def test_disaggregated_cost_savings(self):
        base = make_test_baseline_report()
        sr = make_test_smart_routing_report()
        evaluator = ComparativeBenchmarkEvaluator()

        report: ComparativeEvaluationReport = evaluator.compare(base, sr)
        cs = report.cost_savings

        # Baseline cost:
        # item_greet: (10*2.5 + 10*10)/1e6 = 125/1e6 = 0.000125
        # item_simple: (50*2.5 + 50*10)/1e6 = 625/1e6 = 0.000625
        # item_complex: (100*2.5 + 200*10)/1e6 = 2250/1e6 = 0.002250
        # Total baseline = 0.003000 USD
        assert cs.baseline_total_cost_usd == pytest.approx(0.003000, abs=1e-6)

        # Smart routing cost:
        # item_greet: 0.0 (local)
        # item_simple: (50*0.15 + 50*0.60)/1e6 = 37.5/1e6 = 0.0000375
        # item_complex: (100*2.5 + 200*10)/1e6 = 0.002250
        # Total routing = 0.0022875 USD
        assert cs.smart_routing_total_cost_usd == pytest.approx(0.002288, abs=1e-5)

        # Net savings: 0.003000 - 0.0022875 = 0.0007125 USD (approx 23.75%)
        assert cs.net_cost_savings.numerator == pytest.approx(0.000713, abs=1e-5)
        assert cs.net_cost_savings.denominator == pytest.approx(0.003000, abs=1e-5)
        assert cs.net_cost_savings.relative_change_pct == pytest.approx(23.75, abs=0.1)

        # Small model substitution savings:
        # item_simple baseline (0.000625) - small cost (0.0000375) = 0.0005875
        assert cs.small_model_substitution_savings.numerator == pytest.approx(0.000588, abs=1e-5)
        assert cs.small_model_substitution_savings.denominator == cs.baseline_total_cost_usd

        # Local handled savings:
        # item_greet baseline cost = 0.000125
        assert cs.local_handled_savings.numerator == pytest.approx(0.000125, abs=1e-5)
        assert cs.local_handled_savings.denominator == cs.baseline_total_cost_usd

    def test_avoided_calls_breakdown(self):
        base = make_test_baseline_report()
        sr = make_test_smart_routing_report()
        evaluator = ComparativeBenchmarkEvaluator()

        report: ComparativeEvaluationReport = evaluator.compare(base, sr)
        ac = report.avoided_calls

        assert ac.total_queries == 3
        assert ac.avoided_calls_count == 1
        assert ac.avoided_calls_ratio.numerator == 1.0
        assert ac.avoided_calls_ratio.denominator == 3.0
        assert ac.avoided_calls_ratio.relative_change_pct == pytest.approx(33.33, abs=0.01)
        assert ac.local_handled_calls.numerator == 1.0
        assert ac.cache_hit_calls.numerator == 0.0

    def test_latency_delta_and_speedup(self):
        base = make_test_baseline_report()
        sr = make_test_smart_routing_report()
        evaluator = ComparativeBenchmarkEvaluator()

        report: ComparativeEvaluationReport = evaluator.compare(base, sr)
        ld = report.latency_changes

        # Mean latency: 26.67ms -> 18.33ms = 8.34ms reduction (faster)
        assert ld.mean_latency_delta_ms.numerator == pytest.approx(8.34, abs=0.05)
        assert ld.mean_latency_delta_ms.denominator == pytest.approx(26.67, abs=0.05)
        assert ld.mean_latency_delta_ms.relative_change_pct > 0.0
        assert ld.speedup_factor == pytest.approx(1.45, abs=0.05)

    def test_quality_delta_and_parity_classification(self):
        base = make_test_baseline_report()
        sr = make_test_smart_routing_report()
        evaluator = ComparativeBenchmarkEvaluator()

        report: ComparativeEvaluationReport = evaluator.compare(base, sr)
        qd = report.quality_changes

        # Format compliance: both 100% compliant -> delta = 0.0
        assert qd.format_compliance_delta.numerator == 0.0
        assert qd.format_compliance_delta.denominator == 1.0
        assert qd.format_compliance_delta.relative_change_pct == 0.0

        # Non-empty: both 100% -> delta = 0.0
        assert qd.non_empty_rate_delta.numerator == 0.0

        # Parity counts: all 3 items in parity
        assert qd.items_evaluated == 3
        assert qd.quality_parity_count == 3
        assert qd.quality_degraded_count == 0
        assert qd.quality_improved_count == 0

        # Item details list
        assert len(report.item_details) == 3
        for item in report.item_details:
            assert item.quality_verdict == "PARITY"
            assert item.baseline_cost_usd > 0.0
            assert item.token_delta >= 0

    def test_quality_degradation_detected_on_failure(self):
        """Verify quality_degraded_count increments when routing item fails format compliance."""
        base = make_test_baseline_report()
        sr = make_test_smart_routing_report()

        # Mark second item as failing format compliance
        sr.items[1].format_compliant = False

        evaluator = ComparativeBenchmarkEvaluator()
        report = evaluator.compare(base, sr)

        assert report.quality_changes.quality_degraded_count == 1
        assert report.quality_changes.quality_parity_count == 2
        degraded_item = next(i for i in report.item_details if i.item_id == "item_simple")
        assert degraded_item.quality_verdict == "DEGRADED"


# -----------------------------------------------------------------------------
# 3. Canonical Benchmark End-to-End Comparative Evaluation
# -----------------------------------------------------------------------------

class TestCanonicalBenchmarkComparativeRun:
    """Run full comparative pipeline on canonical 15-item benchmark dataset."""

    @pytest.mark.anyio
    async def test_full_canonical_comparative_pipeline(self):
        canonical_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "dataset", "canonical_benchmark.json"
        )
        dataset = load_benchmark_dataset(canonical_path)
        assert len(dataset.items) == 15

        # 1. Run baseline
        base_runner = StrongModelBaselineRunner()
        base_report = await base_runner.run_baseline_evaluation(dataset)

        # 2. Run smart routing with baseline attached
        sr_runner = SmartRoutingBenchmarkRunner()
        sr_report = await sr_runner.run_smart_routing_evaluation(
            dataset=dataset,
            baseline_report=base_report,
        )

        # Comparative analysis must be automatically populated
        assert sr_report.comparative_analysis is not None
        comp: ComparativeEvaluationReport = sr_report.comparative_analysis

        assert comp.dataset_id == dataset.dataset_id
        assert len(comp.item_details) == 15

        # Disaggregated metrics assertions
        assert comp.token_savings.input_token_savings.denominator == base_report.total_input_tokens
        assert comp.token_savings.total_token_savings.denominator == base_report.total_tokens

        assert comp.cost_savings.baseline_total_cost_usd > 0.0
        assert comp.cost_savings.net_cost_savings.denominator == comp.cost_savings.baseline_total_cost_usd

        assert comp.avoided_calls.total_queries == 15
        assert comp.avoided_calls.avoided_calls_count >= 1
        assert comp.avoided_calls.avoided_calls_ratio.denominator == 15.0

        assert comp.quality_changes.items_evaluated == 15
        assert comp.quality_changes.quality_parity_count + comp.quality_changes.quality_degraded_count == 15

    @pytest.mark.anyio
    async def test_comparative_report_serialization_roundtrip(self):
        base = make_test_baseline_report()
        sr = make_test_smart_routing_report()
        evaluator = ComparativeBenchmarkEvaluator()
        report = evaluator.compare(base, sr)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_path = tf.name

        try:
            save_comparative_report(report, temp_path)
            loaded = load_comparative_report(temp_path)

            assert loaded.run_id == report.run_id
            assert loaded.dataset_id == report.dataset_id
            assert loaded.token_savings.input_token_savings.numerator == report.token_savings.input_token_savings.numerator
            assert loaded.cost_savings.baseline_total_cost_usd == report.cost_savings.baseline_total_cost_usd
            assert len(loaded.item_details) == 3
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


# -----------------------------------------------------------------------------
# 4. FastAPI Endpoints Integration Tests
# -----------------------------------------------------------------------------

class TestFastAPIComparativeEndpoints:
    """Test REST API endpoints for disaggregated comparative benchmark evaluation."""

    def test_compare_endpoint_with_active_session_reports(self, client):
        # 1. Run baseline
        client.post("/api/v1/benchmark/baseline/evaluate", json={})
        # 2. Run smart routing
        client.post("/api/v1/benchmark/smart-routing/evaluate", json={"compare_with_baseline": True})

        # 3. Compare
        res = client.post("/api/v1/benchmark/compare", json={})
        assert res.status_code == 200
        data = res.json()

        assert "run_id" in data
        assert "token_savings" in data
        assert "cost_savings" in data
        assert "avoided_calls" in data
        assert "latency_changes" in data
        assert "quality_changes" in data
        assert "item_details" in data
        assert len(data["item_details"]) == 15

        # Check explicit denominator in token savings
        assert data["token_savings"]["total_token_savings"]["denominator"] > 0
        assert "formula" in data["token_savings"]["total_token_savings"]
        assert "interpretation" in data["token_savings"]["total_token_savings"]

        # Check explicit denominator in cost savings
        assert data["cost_savings"]["net_cost_savings"]["denominator"] > 0

    def test_get_latest_comparative_report(self, client):
        # Trigger compare first
        client.post("/api/v1/benchmark/baseline/evaluate", json={})
        client.post("/api/v1/benchmark/smart-routing/evaluate", json={})
        comp_res = client.post("/api/v1/benchmark/compare", json={})
        run_id = comp_res.json()["run_id"]

        # Retrieve latest
        latest_res = client.get("/api/v1/benchmark/compare/latest")
        assert latest_res.status_code == 200
        assert latest_res.json()["run_id"] == run_id

    def test_get_latest_before_run_returns_404(self, client):
        original = main_mod._latest_comparative_report
        main_mod._latest_comparative_report = None
        try:
            res = client.get("/api/v1/benchmark/compare/latest")
            assert res.status_code == 404
            assert "no comparative evaluation report" in res.json()["detail"].lower()
        finally:
            main_mod._latest_comparative_report = original

    def test_compare_endpoint_with_custom_pricing(self, client):
        client.post("/api/v1/benchmark/baseline/evaluate", json={})
        client.post("/api/v1/benchmark/smart-routing/evaluate", json={})

        custom_pricing = {
            "currency": "EUR",
            "strong_input_price_per_million": 5.00,
            "strong_output_price_per_million": 20.00,
            "small_input_price_per_million": 0.50,
            "small_output_price_per_million": 2.00,
            "local_cost_per_query": 0.0002,
            "cache_lookup_cost_per_query": 0.0001,
        }

        res = client.post(
            "/api/v1/benchmark/compare",
            json={"pricing_config": custom_pricing},
        )
        assert res.status_code == 200
        data = res.json()

        assert data["pricing_assumptions"]["currency"] == "EUR"
        assert data["pricing_assumptions"]["strong_input_price_per_million"] == 5.00
