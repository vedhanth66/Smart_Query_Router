"""Integration and unit tests for small-model route evaluation and escalation.

Verifies:
1. Small model evaluation pass: retains original small-model completion, escalation_occurred=False, escalation_reason=None.
2. Incompleteness escalation: unclosed code fence or truncated answer triggers escalation to stronger model.
3. Low confidence escalation: confidence below configured threshold triggers escalation to stronger model.
4. Requested count mismatch escalation: mismatch between requested count and numbered items triggers escalation.
5. Loop prevention guarantee: stronger model execution is strictly terminal; no recursive or repeated evaluation.
6. Fail-open on escalation failure: if the stronger model fails (timeout/error) during escalation, the original small-model completion is safely retained without blocking the user.
7. Configurable thresholds: environment variables override default confidence and completeness thresholds.
"""

import os
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app, get_eval_confidence_threshold, get_eval_completeness_threshold
from app.gateway import (
    ModelTier,
    GatewayRequest,
    GatewayResponse,
    default_gateway,
    GatewayTimeoutError,
    GatewayError,
)
from app.schemas.contract import CoarseRoute
from app.schemas.evaluator import (
    EvaluationResult,
    EvaluationIssue,
    IssueSeverity,
    IssueCategory,
    EscalationRecommendation,
    EvaluatorType,
)

client = TestClient(app)

BASE_CLIENT_METADATA = {
    "extension_version": "0.1.0",
    "client_type": "chrome_extension",
    "schema_version": "1.0",
    "hostname": "claude.ai",
}


# --- 1. Successful Small-Model Evaluation Pass ---

def test_small_model_eval_pass_retains_original():
    """When the small-model answer passes evaluation, retain original response without escalation."""
    payload = {
        "request_id": "req_test_pass_01",
        "correlation_id": "corr_test_pass_01",
        "query_text": "What is the capital of France?",
        "coarse_route": "simple-model candidate",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }

    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["coarse_route"] == "simple-model candidate"
    exec_meta = data["execution_metadata"]
    assert exec_meta is not None
    assert exec_meta["model_id"] == "gpt-4o-mini-2024-07-18"
    assert exec_meta["model_version"] == "2024-07-18"
    assert exec_meta["escalation_occurred"] is False
    assert exec_meta["escalation_reason"] is None
    assert exec_meta["failure_category"] == "NONE"
    assert exec_meta["fallback_applied"] is False
    assert exec_meta["executed_content"] is not None

    eval_meta = exec_meta["evaluation_metadata"]
    assert eval_meta is not None
    assert eval_meta["escalation_recommendation"] == "no_escalation"
    assert eval_meta["passed"] is True
    assert eval_meta["completeness"] >= 0.70
    assert eval_meta["confidence"] >= 0.70


# --- 2. Incompleteness Escalation (Unclosed Code Fence) ---

def test_small_model_incompleteness_escalates_to_strong_model():
    """When small-model response has an unclosed code block, re-run with strong model."""
    incomplete_small_content = "Here is the code to calculate factorial:\n```python\ndef fact(n):\n    if n <= 1:\n        return 1"

    # Mock execute so small_model returns incomplete code, but strong_model returns complete code
    original_execute = default_gateway.execute

    async def mock_execute(request: GatewayRequest) -> GatewayResponse:
        if request.provider == "small_model":
            return GatewayResponse(
                content=incomplete_small_content,
                model_id="gpt-4o-mini-2024-07-18",
                model_version="2024-07-18",
                tier=ModelTier.FAST_CHEAP,
                latency_ms=85.0,
                provider_name="small_model",
                input_tokens=10,
                output_tokens=25,
            )
        else:
            return await original_execute(request)

    with patch.object(default_gateway, "execute", side_effect=mock_execute):
        payload = {
            "request_id": "req_test_incomp_01",
            "correlation_id": "corr_test_incomp_01",
            "query_text": "Write a python factorial function",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        # Strong model should have been executed upon escalation
        assert exec_meta["model_id"] == "gpt-4o-2024-08-06"
        assert exec_meta["model_version"] == "2024-08-06"
        assert exec_meta["escalation_occurred"] is True
        assert exec_meta["escalation_reason"] is not None
        assert "incomplete" in exec_meta["escalation_reason"].lower() or "unclosed" in exec_meta["escalation_reason"].lower()
        assert exec_meta["failure_category"] == "NONE"
        assert exec_meta["fallback_applied"] is False

        eval_meta = exec_meta["evaluation_metadata"]
        assert eval_meta is not None
        assert eval_meta["escalation_recommendation"] == "escalate_to_stronger_model"
        assert eval_meta["original_model_id"] == "gpt-4o-mini-2024-07-18"
        assert len(eval_meta["detected_issues"]) > 0
        assert any(i["issue_code"] == "PREMATURE_TRUNCATION" for i in eval_meta["detected_issues"])


# --- 3. Low Confidence Escalation ---

def test_small_model_low_confidence_triggers_escalation():
    """When evaluator returns confidence below threshold, re-run with stronger model."""
    low_confidence_eval = EvaluationResult(
        evaluation_id="eval_low_conf_01",
        completeness=0.85,
        confidence=0.45,  # Below default 0.70 threshold
        detected_issues=[],
        escalation_recommendation=EscalationRecommendation.NO_ESCALATION,
        evaluator_id="heuristic-incompleteness-v1",
        evaluator_type=EvaluatorType.HEURISTIC,
        latency_ms=1.5,
        explanation="Low evaluator confidence in result",
    )

    from app.evaluator.registry import default_evaluator_registry

    with patch.object(default_evaluator_registry, "evaluate", return_value=low_confidence_eval):
        payload = {
            "request_id": "req_test_low_conf_01",
            "correlation_id": "corr_test_low_conf_01",
            "query_text": "Tell me an ambiguous riddle",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["model_id"] == "gpt-4o-2024-08-06"
        assert exec_meta["escalation_occurred"] is True
        assert "LOW_CONFIDENCE" in exec_meta["escalation_reason"]
        assert "0.45" in exec_meta["escalation_reason"]
        assert exec_meta["evaluation_metadata"]["confidence"] == 0.45


# --- 4. Requested Count Mismatch Escalation ---

def test_small_model_requested_count_mismatch_escalates():
    """When prompt requests 5 items but small-model output provides only 2, escalate."""
    partial_list_content = "Here are the reasons:\n1. Static typing\n2. Tooling support\n"

    async def mock_execute(request: GatewayRequest) -> GatewayResponse:
        if request.provider == "small_model":
            return GatewayResponse(
                content=partial_list_content,
                model_id="gpt-4o-mini-2024-07-18",
                model_version="2024-07-18",
                tier=ModelTier.FAST_CHEAP,
                latency_ms=75.0,
                provider_name="small_model",
                input_tokens=15,
                output_tokens=30,
            )
        else:
            return GatewayResponse(
                content="Here are 5 reasons:\n1. Typing\n2. Tooling\n3. Refactoring\n4. Readability\n5. Community\n",
                model_id="gpt-4o-2024-08-06",
                model_version="2024-08-06",
                tier=ModelTier.STRONG,
                latency_ms=150.0,
                provider_name="strong_model",
                input_tokens=15,
                output_tokens=60,
            )

    with patch.object(default_gateway, "execute", side_effect=mock_execute):
        payload = {
            "request_id": "req_test_count_01",
            "correlation_id": "corr_test_count_01",
            "query_text": "Give me 5 reasons to adopt TypeScript",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["model_id"] == "gpt-4o-2024-08-06"
        assert exec_meta["escalation_occurred"] is True
        assert "REQUESTED_COUNT_MISMATCH" in exec_meta["escalation_reason"]
        assert exec_meta["evaluation_metadata"]["completeness"] < 0.70
        assert any(i["issue_code"] == "REQUESTED_COUNT_MISMATCH" for i in exec_meta["evaluation_metadata"]["detected_issues"])


# --- 5. Loop Prevention Guarantee ---

def test_loop_prevention_strong_model_execution_is_terminal():
    """Even if strong-model response is incomplete, do NOT re-evaluate or loop."""
    incomplete_code = "```python\ndef run():\n    incomplete"

    call_count = 0

    async def mock_execute(request: GatewayRequest) -> GatewayResponse:
        nonlocal call_count
        call_count += 1
        return GatewayResponse(
            content=incomplete_code,
            model_id="gpt-4o-2024-08-06" if request.provider == "strong_model" else "gpt-4o-mini-2024-07-18",
            model_version="2024-08-06" if request.provider == "strong_model" else "2024-07-18",
            tier=request.tier,
            latency_ms=50.0,
            provider_name=request.provider or "small_model",
            input_tokens=10,
            output_tokens=10,
        )

    with patch.object(default_gateway, "execute", side_effect=mock_execute):
        payload = {
            "request_id": "req_test_loop_01",
            "correlation_id": "corr_test_loop_01",
            "query_text": "Write code for a server",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        # Gateway execute must be called EXACTLY twice:
        # 1st: small_model -> evaluated as incomplete
        # 2nd: strong_model -> terminal, NO further evaluation or escalation loop
        assert call_count == 2
        exec_meta = data["execution_metadata"]
        assert exec_meta["escalation_occurred"] is True
        assert exec_meta["model_id"] == "gpt-4o-2024-08-06"


# --- 6. Fail-Open Resilience When Strong Model Fails During Escalation ---

def test_fail_open_retains_small_model_if_strong_fails_during_escalation():
    """If strong-model invocation times out or errors during escalation, retain small-model response."""
    incomplete_code = "```python\ndef run():\n    missing end"

    async def mock_execute(request: GatewayRequest) -> GatewayResponse:
        if request.provider == "small_model":
            return GatewayResponse(
                content=incomplete_code,
                model_id="gpt-4o-mini-2024-07-18",
                model_version="2024-07-18",
                tier=ModelTier.FAST_CHEAP,
                latency_ms=60.0,
                provider_name="small_model",
                input_tokens=10,
                output_tokens=15,
            )
        else:
            raise GatewayTimeoutError("Strong model timed out after 30.0s")

    with patch.object(default_gateway, "execute", side_effect=mock_execute):
        payload = {
            "request_id": "req_test_failopen_01",
            "correlation_id": "corr_test_failopen_01",
            "query_text": "Write a python function",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        # Retains the original small model completion
        assert exec_meta["model_id"] == "gpt-4o-mini-2024-07-18"
        assert exec_meta["executed_content"] == incomplete_code
        assert exec_meta["failure_category"] == "ESCALATION_FAILED_RETAINED_ORIGINAL"
        assert exec_meta["fallback_applied"] is True
        assert exec_meta["escalation_occurred"] is True
        assert "GatewayTimeoutError" in exec_meta["escalation_reason"]


# --- 7. Configurable Thresholds via Environment Variables ---

def test_configurable_thresholds_env_overrides():
    """Verify that environment variables configure evaluation thresholds."""
    with patch.dict(os.environ, {
        "ROUTER_EVAL_CONFIDENCE_THRESHOLD": "0.85",
        "ROUTER_EVAL_COMPLETENESS_THRESHOLD": "0.90",
    }):
        assert get_eval_confidence_threshold() == 0.85
        assert get_eval_completeness_threshold() == 0.90

    # Test fallback to default 0.70 when invalid or missing
    with patch.dict(os.environ, {
        "ROUTER_EVAL_CONFIDENCE_THRESHOLD": "invalid_num",
        "ROUTER_EVAL_COMPLETENESS_THRESHOLD": "2.5",  # Out of [0, 1] range
    }):
        assert get_eval_confidence_threshold() == 0.70
        assert get_eval_completeness_threshold() == 0.70


# --- 8. Structural Consistency Escalation (Output Fields Mismatch) ---

def test_structural_inconsistency_escalates_to_strong_model():
    """Verify that omitting explicitly requested output fields triggers escalation to stronger model."""
    incomplete_small_content = """Service Configuration:
- service_name: user-auth-service
- port: 8080
"""
    strong_complete_content = """Service Configuration:
- service_name: user-auth-service
- port: 8080
- health_check: /actuator/health
"""

    async def mock_execute(request: GatewayRequest) -> GatewayResponse:
        if request.provider == "small_model":
            return GatewayResponse(
                content=incomplete_small_content,
                model_id="gpt-4o-mini-2024-07-18",
                model_version="2024-07-18",
                tier=ModelTier.FAST_CHEAP,
                latency_ms=70.0,
                provider_name="small_model",
                input_tokens=20,
                output_tokens=30,
            )
        else:
            return GatewayResponse(
                content=strong_complete_content,
                model_id="gpt-4o-2024-08-06",
                model_version="2024-08-06",
                tier=ModelTier.STRONG,
                latency_ms=160.0,
                provider_name="strong_model",
                input_tokens=20,
                output_tokens=45,
            )

    with patch.object(default_gateway, "execute", side_effect=mock_execute):
        payload = {
            "request_id": "req_test_struct_field_01",
            "correlation_id": "corr_test_struct_field_01",
            "query_text": "Configure backend service with fields: service_name, port, health_check",
            "coarse_route": "simple-model candidate",
            "execute_route": True,
            "client_metadata": BASE_CLIENT_METADATA,
        }

        resp = client.post("/api/v1/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        exec_meta = data["execution_metadata"]
        assert exec_meta is not None
        assert exec_meta["model_id"] == "gpt-4o-2024-08-06"
        assert exec_meta["escalation_occurred"] is True
        assert "MISSING_REQUIRED_FIELD" in exec_meta["escalation_reason"]
        assert exec_meta["failure_category"] == "NONE"

        eval_meta = exec_meta["evaluation_metadata"]
        assert eval_meta is not None
        assert eval_meta["escalation_recommendation"] == "escalate_to_stronger_model"
        assert any(i["issue_code"] == "MISSING_REQUIRED_FIELD" for i in eval_meta["detected_issues"])

