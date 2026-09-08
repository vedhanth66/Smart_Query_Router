"""Unit tests for disabled-by-default experimental stronger prompt compression and A/B routing."""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.gateway import default_gateway
from app.schemas.contract import (
    QualityEvaluationStatus,
    ExperimentalCompressionResult,
    CompressionABComparisonResult,
    LocalFeatures,
    ModelTier,
)
from app.optimizer import (
    ExperimentalCompressionConfig,
    SensitivityValidator,
    StrongerPromptCompressor,
    default_experimental_compressor,
    is_experimental_compression_allowed,
    compare_ab_compression_routing,
)

client = TestClient(app)


def test_disabled_by_default_and_production_safety_lock():
    """Verify experimental compression is disabled by default and locked in production mode."""
    # 1. Default config check
    default_cfg = ExperimentalCompressionConfig()
    assert default_cfg.enabled is False
    assert default_cfg.environment == "production"
    assert is_experimental_compression_allowed(default_cfg) is False

    # 2. Production safety lock check: even if enabled=True, production environment MUST lock it
    accidental_prod_cfg = ExperimentalCompressionConfig(enabled=True, environment="production")
    assert is_experimental_compression_allowed(accidental_prod_cfg) is False

    # 3. Default compressor execution is safely bypassed
    prompt = "In order to understand machine learning, could you please explain linear regression?"
    result = default_experimental_compressor.compress(prompt)

    assert result.is_compressed is False
    assert result.original_input == prompt
    assert result.compressed_input == prompt
    assert result.compression_ratio == 1.0
    assert result.token_savings == 0
    assert "FEATURE_DISABLED_BY_DEFAULT" in result.bypass_reason or "FEATURE_LOCKED_IN_PRODUCTION" in result.bypass_reason


def test_permitted_execution_in_explicit_test_environment():
    """Verify compression executes when enabled=True and environment is non-production."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    assert is_experimental_compression_allowed(test_cfg) is True

    compressor = StrongerPromptCompressor(config=test_cfg)
    prompt = "In order to improve performance, can you please explain database indexing at this point in time?"
    result = compressor.compress(prompt)

    assert result.is_compressed is True
    assert result.original_input == prompt
    assert result.compressed_input != prompt
    assert result.char_savings > 0
    assert result.compression_ratio < 1.0
    assert result.token_savings > 0
    assert "to" in result.compressed_input
    assert "now" in result.compressed_input


def test_preserves_original_input_always():
    """Verify original input text is strictly preserved under all branches and bypasses."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    compressor = StrongerPromptCompressor(config=test_cfg)

    inputs = [
        "What is connection pooling?",
        "In order to test, can you please explain?",
        "def hello(): pass",
        "Solve $ x + y = 10 $",
        "Visit https://claude.ai",
        'Read the "Terms of Service" carefully',
    ]

    for inp in inputs:
        res = compressor.compress(inp)
        assert res.original_input == inp, f"Original input altered for {repr(inp)}"


def test_high_sensitivity_code_bypassed():
    """Verify code blocks, inline code, and programming syntax strictly bypass compression."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    compressor = StrongerPromptCompressor(config=test_cfg)

    # 1. Fenced code block
    code_fenced = "Can you please review this code?\n```python\ndef test():\n    return 42\n```"
    res1 = compressor.compress(code_fenced)
    assert res1.is_compressed is False
    assert "HIGH_SENSITIVITY_CODE" in res1.detected_sensitivities
    assert res1.bypass_reason == "BYPASS_HIGH_SENSITIVITY_CODE"
    assert res1.compressed_input == code_fenced

    # 2. Inline code
    code_inline = "Can you please explain `let x = 10`?"
    res2 = compressor.compress(code_inline)
    assert res2.is_compressed is False
    assert "HIGH_SENSITIVITY_CODE" in res2.detected_sensitivities

    # 3. Programming syntax keywords
    code_kw = "In order to optimize: function calculateTotal() { return sum; }"
    res3 = compressor.compress(code_kw)
    assert res3.is_compressed is False
    assert "HIGH_SENSITIVITY_CODE" in res3.detected_sensitivities


def test_high_sensitivity_math_bypassed():
    """Verify mathematical notation (LaTeX display/inline math, formulas) strictly bypasses compression."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    compressor = StrongerPromptCompressor(config=test_cfg)

    # 1. Display math
    math_display = "Can you please solve this equation: $$ \\int_{0}^{\\infty} e^{-x^2} dx $$"
    res1 = compressor.compress(math_display)
    assert res1.is_compressed is False
    assert "HIGH_SENSITIVITY_MATH" in res1.detected_sensitivities
    assert res1.bypass_reason == "BYPASS_HIGH_SENSITIVITY_MATH"

    # 2. Inline math
    math_inline = "Can you please find $ f(x) = 2x + 1 $ at $ x = 3 $?"
    res2 = compressor.compress(math_inline)
    assert res2.is_compressed is False
    assert "HIGH_SENSITIVITY_MATH" in res2.detected_sensitivities


def test_high_sensitivity_quoted_legal_text_bypassed():
    """Verify quoted text, terms of service, copyright, and legal clauses bypass compression."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    compressor = StrongerPromptCompressor(config=test_cfg)

    # 1. Quoted text
    quoted = 'Can you please analyze "The quick brown fox jumps over the lazy dog"?'
    res1 = compressor.compress(quoted)
    assert res1.is_compressed is False
    assert "HIGH_SENSITIVITY_QUOTED_LEGAL_TEXT" in res1.detected_sensitivities

    # 2. Legal disclaimer / terms
    legal = "Please summarize our Terms of Service and limitation of liability clause."
    res2 = compressor.compress(legal)
    assert res2.is_compressed is False
    assert "HIGH_SENSITIVITY_QUOTED_LEGAL_TEXT" in res2.detected_sensitivities


def test_high_sensitivity_urls_bypassed():
    """Verify URLs and URIs strictly bypass compression."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    compressor = StrongerPromptCompressor(config=test_cfg)

    url_prompt = "Can you please check the documentation at https://api.openai.com/v1/models?"
    res = compressor.compress(url_prompt)
    assert res.is_compressed is False
    assert "HIGH_SENSITIVITY_URL" in res.detected_sensitivities
    assert res.bypass_reason == "BYPASS_HIGH_SENSITIVITY_URL"


def test_compression_ratio_and_token_savings_metrics():
    """Verify mathematical calculation of compression ratio and token savings."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    compressor = StrongerPromptCompressor(config=test_cfg)

    prompt = (
        "In order to understand the historical context, "
        "could you please explain to me the origins of the Industrial Revolution at this point in time?"
    )
    result = compressor.compress(prompt)

    assert result.is_compressed is True
    # Verify character calculations
    assert result.original_char_count == len(prompt)
    assert result.compressed_char_count == len(result.compressed_input)
    assert result.char_savings == result.original_char_count - result.compressed_char_count
    assert result.char_savings > 0

    expected_ratio = round(result.compressed_char_count / result.original_char_count, 4)
    assert result.compression_ratio == expected_ratio
    assert result.compression_ratio < 1.0

    # Verify token calculations
    assert result.token_savings == result.original_token_estimate - result.compressed_token_estimate
    assert result.token_savings > 0


@pytest.mark.anyio
async def test_ab_comparison_against_uncompressed_routing():
    """Verify A/B comparison routing evaluates uncompressed Variant A vs compressed Variant B concurrently."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    prompt = "In order to optimize our backend, could you please explain connection pooling?"

    ab_result = await compare_ab_compression_routing(
        gateway=default_gateway,
        prompt=prompt,
        tier=ModelTier.FAST_CHEAP,
        override_config=test_cfg,
        correlation_id="test_ab_123",
    )

    assert isinstance(ab_result, CompressionABComparisonResult)
    assert ab_result.comparison_id == "test_ab_123"
    assert ab_result.variant_a_prompt == prompt  # Variant A: uncompressed
    assert ab_result.variant_b_prompt != prompt  # Variant B: compressed
    assert ab_result.is_compressed is True
    assert ab_result.compression_ratio < 1.0
    assert ab_result.estimated_token_savings > 0
    assert ab_result.variant_a_latency_ms >= 0.0
    assert ab_result.variant_b_latency_ms >= 0.0
    assert ab_result.quality_evaluation_status == QualityEvaluationStatus.UNVALIDATED
    assert ab_result.quality_evaluation_status.value == "UNVALIDATED_PENDING_EMPIRICAL_EVALUATION"


def test_quality_claim_guardrail_unvalidated_status():
    """Verify quality status is strictly unvalidated and no claims of quality parity/improvement are made."""
    test_cfg = ExperimentalCompressionConfig(enabled=True, environment="test")
    compressor = StrongerPromptCompressor(config=test_cfg)

    prompt = "Could you please tell me what is the speed of light?"
    result = compressor.compress(prompt)

    # Invariant: Quality parity is never assumed or claimed without empirical evaluation data
    assert result.quality_evaluation_status == QualityEvaluationStatus.UNVALIDATED
    assert result.quality_evaluation_status.value == "UNVALIDATED_PENDING_EMPIRICAL_EVALUATION"


def test_api_endpoints_experimental_compress_and_compare_ab():
    """Verify /api/v1/experimental/compress and /api/v1/experimental/compare-ab endpoints."""
    # 1. /api/v1/experimental/compress with default production config -> bypassed
    resp_default = client.post(
        "/api/v1/experimental/compress",
        json={"query_text": "In order to learn, can you please explain?"}
    )
    assert resp_default.status_code == 200
    data_default = resp_default.json()
    assert data_default["is_compressed"] is False
    assert data_default["compression_ratio"] == 1.0

    # 2. /api/v1/experimental/compress with explicit test environment opt-in -> compressed
    resp_test = client.post(
        "/api/v1/experimental/compress",
        json={
            "query_text": "In order to learn, could you please explain to me quantum computing?",
            "enabled": True,
            "environment": "test"
        }
    )
    assert resp_test.status_code == 200
    data_test = resp_test.json()
    assert data_test["is_compressed"] is True
    assert data_test["compression_ratio"] < 1.0
    assert data_test["quality_evaluation_status"] == "UNVALIDATED_PENDING_EMPIRICAL_EVALUATION"

    # 3. /api/v1/experimental/compress with code structure -> bypassed
    resp_code = client.post(
        "/api/v1/experimental/compress",
        json={
            "query_text": "In order to test: `def foo(): pass`",
            "enabled": True,
            "environment": "test"
        }
    )
    assert resp_code.status_code == 200
    data_code = resp_code.json()
    assert data_code["is_compressed"] is False
    assert "HIGH_SENSITIVITY_CODE" in data_code["detected_sensitivities"]

    # 4. /api/v1/experimental/compare-ab endpoint
    resp_ab = client.post(
        "/api/v1/experimental/compare-ab",
        json={
            "prompt": "In order to prepare, could you please explain photosynthesis?",
            "enabled": True,
            "environment": "test",
            "correlation_id": "api_ab_test_1"
        }
    )
    assert resp_ab.status_code == 200
    data_ab = resp_ab.json()
    assert data_ab["comparison_id"] == "api_ab_test_1"
    assert data_ab["variant_a_prompt"] == "In order to prepare, could you please explain photosynthesis?"
    assert data_ab["is_compressed"] is True
    assert data_ab["compression_ratio"] < 1.0
    assert data_ab["quality_evaluation_status"] == "UNVALIDATED_PENDING_EMPIRICAL_EVALUATION"


def test_api_optimize_endpoint_backward_compatibility():
    """Verify /api/v1/optimize preserves original behavior and experimental compression is None by default."""
    payload = {
        "request_id": "req_compat_test",
        "correlation_id": "corr_compat_test",
        "query_text": "What is PostgreSQL connection pooling?",
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
        }
    }
    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req_compat_test"
    # Experimental compression is None in production default mode
    assert data["experimental_compression"] is None
    # Baseline query_optimization is still active
    assert data["query_optimization"] is not None
    assert data["query_optimization"]["original_query"] == payload["query_text"]
