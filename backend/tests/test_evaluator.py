"""Unit and integration tests for the Standardized Evaluator Result Structure and Pluggable Interface.

Verifies:
1. EvaluationResult standardized structure containing completeness, confidence, detected_issues,
   and escalation_recommendation.
2. Value bounds and schema constraints (0.0 <= completeness <= 1.0, 0.0 <= confidence <= 1.0, extra="forbid").
3. EvaluationIssue structured flaw representation (severity, category, message, issue_code).
4. StubEvaluator reference execution validating the contract without intelligence.
5. Pluggability of heuristic evaluators.
6. Pluggability of classifier evaluators.
7. Pluggability of model-based evaluators.
8. EvaluatorRegistry coordination, default selection, and registration mechanics.
9. Strict architectural separation from routing and model execution.
"""

import pytest
from pydantic import ValidationError

from app.schemas.evaluator import (
    EscalationRecommendation,
    IssueSeverity,
    IssueCategory,
    EvaluatorType,
    EvaluationIssue,
    EvaluationRequest,
    EvaluationResult,
)
from app.evaluator.base import BaseEvaluator, StubEvaluator
from app.evaluator.registry import (
    EvaluatorRegistry,
    default_evaluator_registry,
    create_default_evaluator_registry,
)
from app.schemas.contract import CoarseRoute, DecisionType
from app.gateway import default_gateway


# --- 1. Standardized EvaluationResult Schema & Fields ---

def test_standardized_evaluation_result_structure():
    """Verify EvaluationResult enforces completeness, confidence, detected_issues, and escalation."""
    issue = EvaluationIssue(
        issue_code="MISSING_RETURN_TYPE",
        message="The generated function signature is missing a return type hint",
        severity=IssueSeverity.LOW,
        category=IssueCategory.FORMATTING,
        location="line 12",
        metadata={"language": "python"}
    )

    res = EvaluationResult(
        evaluation_id="eval_test_001",
        completeness=0.92,
        confidence=0.88,
        detected_issues=[issue],
        escalation_recommendation=EscalationRecommendation.NO_ESCALATION,
        evaluator_id="test-evaluator-v1",
        evaluator_type=EvaluatorType.HEURISTIC,
        latency_ms=3.45,
        explanation="Response fulfills prompt criteria with minor formatting issue.",
        metadata={"rule_count": 5}
    )

    data = res.model_dump()
    assert data["evaluation_id"] == "eval_test_001"
    assert data["completeness"] == 0.92
    assert data["confidence"] == 0.88
    assert len(data["detected_issues"]) == 1
    assert data["detected_issues"][0]["issue_code"] == "MISSING_RETURN_TYPE"
    assert data["detected_issues"][0]["severity"] == "low"
    assert data["detected_issues"][0]["category"] == "formatting"
    assert data["escalation_recommendation"] == "no_escalation"
    assert data["evaluator_id"] == "test-evaluator-v1"
    assert data["evaluator_type"] == "heuristic"
    assert data["latency_ms"] == 3.45
    assert data["explanation"] is not None


# --- 2. Numerical Bounds & Constraint Validation ---

def test_evaluation_result_completeness_and_confidence_bounds():
    """Verify completeness and confidence reject values outside 0.0 - 1.0."""
    valid_base = {
        "evaluation_id": "eval_test_bounds",
        "completeness": 0.5,
        "confidence": 0.5,
        "detected_issues": [],
        "escalation_recommendation": EscalationRecommendation.NO_ESCALATION,
        "evaluator_id": "bounds-eval",
        "evaluator_type": EvaluatorType.STUB,
    }

    # Completeness > 1.0 rejected
    with pytest.raises(ValidationError):
        EvaluationResult(**{**valid_base, "completeness": 1.01})

    # Completeness < 0.0 rejected
    with pytest.raises(ValidationError):
        EvaluationResult(**{**valid_base, "completeness": -0.01})

    # Confidence > 1.0 rejected
    with pytest.raises(ValidationError):
        EvaluationResult(**{**valid_base, "confidence": 1.5})

    # Confidence < 0.0 rejected
    with pytest.raises(ValidationError):
        EvaluationResult(**{**valid_base, "confidence": -0.1})


def test_rejection_of_extra_forbidden_fields():
    """Verify extra fields are strictly forbidden on evaluator schemas."""
    # EvaluationResult rejects extra fields
    with pytest.raises(ValidationError):
        EvaluationResult(
            evaluation_id="eval_extra",
            completeness=0.8,
            confidence=0.8,
            detected_issues=[],
            escalation_recommendation=EscalationRecommendation.NO_ESCALATION,
            evaluator_id="stub",
            evaluator_type=EvaluatorType.STUB,
            unauthorized_secret_field="leak"
        )

    # EvaluationIssue rejects extra fields
    with pytest.raises(ValidationError):
        EvaluationIssue(
            issue_code="ERR",
            message="Test error",
            severity=IssueSeverity.HIGH,
            rogue_field="unexpected"
        )

    # EvaluationRequest rejects extra fields
    with pytest.raises(ValidationError):
        EvaluationRequest(
            prompt="Hello",
            candidate_response="Hi",
            arbitrary_extra="forbidden"
        )


# --- 3. Structured EvaluationIssue Validation ---

def test_evaluation_issue_attributes_and_defaults():
    """Verify EvaluationIssue structures and category defaults."""
    issue = EvaluationIssue(
        issue_code="TRUNCATED_RESPONSE",
        message="Response ends abruptly before concluding reasoning.",
        severity=IssueSeverity.CRITICAL,
    )

    assert issue.issue_code == "TRUNCATED_RESPONSE"
    assert issue.severity == IssueSeverity.CRITICAL
    assert issue.category == IssueCategory.OTHER
    assert issue.location is None
    assert issue.metadata == {}

    # All severity levels supported
    for sev in [IssueSeverity.LOW, IssueSeverity.MEDIUM, IssueSeverity.HIGH, IssueSeverity.CRITICAL]:
        iss = EvaluationIssue(issue_code="CODE", message="Msg", severity=sev)
        assert iss.severity == sev

    # All escalation recommendations supported
    for rec in [
        EscalationRecommendation.NO_ESCALATION,
        EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL,
        EscalationRecommendation.ESCALATE_TO_HUMAN,
        EscalationRecommendation.UNCERTAIN,
    ]:
        res = EvaluationResult(
            evaluation_id="eval_rec_test",
            completeness=1.0,
            confidence=0.9,
            detected_issues=[],
            escalation_recommendation=rec,
            evaluator_id="rec-test",
            evaluator_type=EvaluatorType.STUB,
        )
        assert res.escalation_recommendation == rec


# --- 4. StubEvaluator Baseline Execution Without Intelligence ---

@pytest.mark.anyio
async def test_stub_evaluator_baseline_execution():
    """Verify StubEvaluator produces valid standardized result without intelligence."""
    evaluator = StubEvaluator()
    assert evaluator.evaluator_id == "stub-evaluator-v1"
    assert evaluator.evaluator_type == EvaluatorType.STUB
    assert evaluator.check_health() is True

    req = EvaluationRequest(
        prompt="Write a Python script to parse CSV files",
        candidate_response="import csv\nwith open('data.csv') as f:\n    reader = csv.reader(f)\n    for r in reader: print(r)",
        task_category="coding",
        correlation_id="corr_eval_test_01"
    )

    res = await evaluator.evaluate(req)

    assert isinstance(res, EvaluationResult)
    assert res.completeness == 1.0
    assert res.confidence == 0.5
    assert len(res.detected_issues) == 0
    assert res.escalation_recommendation == EscalationRecommendation.NO_ESCALATION
    assert res.evaluator_id == "stub-evaluator-v1"
    assert res.evaluator_type == EvaluatorType.STUB
    assert res.latency_ms >= 0.0
    assert res.metadata["stub_mode"] is True
    assert "corr_eval_test_01" in res.evaluation_id


# --- 5. Pluggability: Heuristic Evaluator Implementation ---

class MockHeuristicEvaluator(BaseEvaluator):
    """Demonstrates plugging in a heuristic rule-based evaluator without changing callers."""

    @property
    def evaluator_id(self) -> str:
        return "heuristic-rule-evaluator-v1"

    @property
    def evaluator_type(self) -> EvaluatorType:
        return EvaluatorType.HEURISTIC

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        issues = []
        completeness = 1.0

        # Heuristic: check if prompt requested code but response has no code block
        if "code" in request.prompt.lower() and "```" not in request.candidate_response:
            issues.append(EvaluationIssue(
                issue_code="MISSING_CODE_BLOCK",
                message="Prompt requested code but no markdown code block was found in response",
                severity=IssueSeverity.HIGH,
                category=IssueCategory.COMPLETENESS
            ))
            completeness = 0.4

        escalation = (
            EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
            if issues
            else EscalationRecommendation.NO_ESCALATION
        )

        return EvaluationResult(
            evaluation_id="eval_heuristic_01",
            completeness=completeness,
            confidence=0.85,
            detected_issues=issues,
            escalation_recommendation=escalation,
            evaluator_id=self.evaluator_id,
            evaluator_type=self.evaluator_type,
            latency_ms=1.2,
            explanation="Heuristic structural checks applied.",
        )


@pytest.mark.anyio
async def test_pluggable_heuristic_evaluator():
    """Verify a heuristic evaluator can be plugged into the interface seamlessly."""
    evaluator = MockHeuristicEvaluator()

    # Case A: Missing code triggers issue and escalation
    req_missing_code = EvaluationRequest(
        prompt="Write code for binary search",
        candidate_response="Binary search works by repeatedly dividing the sorted search interval in half."
    )
    res_a = await evaluator.evaluate(req_missing_code)
    assert res_a.completeness == 0.4
    assert len(res_a.detected_issues) == 1
    assert res_a.detected_issues[0].issue_code == "MISSING_CODE_BLOCK"
    assert res_a.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert res_a.evaluator_type == EvaluatorType.HEURISTIC

    # Case B: Code block present satisfies heuristic
    req_ok = EvaluationRequest(
        prompt="Write code for binary search",
        candidate_response="```python\ndef bsearch(arr, x): pass\n```"
    )
    res_b = await evaluator.evaluate(req_ok)
    assert res_b.completeness == 1.0
    assert len(res_b.detected_issues) == 0
    assert res_b.escalation_recommendation == EscalationRecommendation.NO_ESCALATION


# --- 6. Pluggability: Classifier Evaluator Implementation ---

class MockClassifierEvaluator(BaseEvaluator):
    """Demonstrates plugging in a statistical/ML classifier evaluator."""

    @property
    def evaluator_id(self) -> str:
        return "classifier-defect-model-v1"

    @property
    def evaluator_type(self) -> EvaluatorType:
        return EvaluatorType.CLASSIFIER

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        # Mock statistical inference
        return EvaluationResult(
            evaluation_id="eval_cls_01",
            completeness=0.88,
            confidence=0.91,
            detected_issues=[],
            escalation_recommendation=EscalationRecommendation.NO_ESCALATION,
            evaluator_id=self.evaluator_id,
            evaluator_type=self.evaluator_type,
            latency_ms=8.5,
            explanation="Statistical defect classifier predicted no major flaws.",
            metadata={"defect_probability": 0.04}
        )


@pytest.mark.anyio
async def test_pluggable_classifier_evaluator():
    """Verify a classifier evaluator can be plugged in."""
    evaluator = MockClassifierEvaluator()
    req = EvaluationRequest(
        prompt="Summarize the latest financial report",
        candidate_response="Revenue increased by 14% year-over-year."
    )
    res = await evaluator.evaluate(req)
    assert res.evaluator_type == EvaluatorType.CLASSIFIER
    assert res.confidence == 0.91
    assert res.escalation_recommendation == EscalationRecommendation.NO_ESCALATION
    assert res.metadata["defect_probability"] == 0.04


# --- 7. Pluggability: Model-Based Evaluator (LLM-as-a-Judge) Implementation ---

class MockModelBasedEvaluator(BaseEvaluator):
    """Demonstrates plugging in an LLM-as-a-judge model-based evaluator."""

    @property
    def evaluator_id(self) -> str:
        return "llm-judge-evaluator-v1"

    @property
    def evaluator_type(self) -> EvaluatorType:
        return EvaluatorType.MODEL_BASED

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        # Mock LLM evaluation with critical ambiguity detected
        issue = EvaluationIssue(
            issue_code="UNRESOLVED_CONSTRAINTS",
            message="Prompt requested O(1) space, but candidate response uses O(N) memory.",
            severity=IssueSeverity.CRITICAL,
            category=IssueCategory.ACCURACY,
        )
        return EvaluationResult(
            evaluation_id="eval_model_judge_01",
            completeness=0.35,
            confidence=0.95,
            detected_issues=[issue],
            escalation_recommendation=EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL,
            evaluator_id=self.evaluator_id,
            evaluator_type=self.evaluator_type,
            latency_ms=210.0,
            explanation="Judge model found space complexity violation.",
        )


@pytest.mark.anyio
async def test_pluggable_model_based_evaluator():
    """Verify a model-based (LLM-as-judge) evaluator can be plugged in."""
    evaluator = MockModelBasedEvaluator()
    req = EvaluationRequest(
        prompt="Implement an in-place array rotation with O(1) auxiliary space",
        candidate_response="def rotate(arr, k):\n    return arr[k:] + arr[:k]"
    )
    res = await evaluator.evaluate(req)
    assert res.evaluator_type == EvaluatorType.MODEL_BASED
    assert res.completeness == 0.35
    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert len(res.detected_issues) == 1
    assert res.detected_issues[0].issue_code == "UNRESOLVED_CONSTRAINTS"


# --- 8. EvaluatorRegistry Coordination & Management ---

@pytest.mark.anyio
async def test_evaluator_registry_registration_and_dispatch():
    """Verify EvaluatorRegistry manages evaluators and routes requests correctly."""
    registry = EvaluatorRegistry()
    stub = StubEvaluator(evaluator_id="stub-1")
    heuristic = MockHeuristicEvaluator()

    registry.register_evaluator(stub, set_default=True)
    registry.register_evaluator(heuristic, set_default=False)

    assert set(registry.list_evaluators()) == {"stub-1", "heuristic-rule-evaluator-v1"}
    assert registry.get_evaluator().evaluator_id == "stub-1"
    assert registry.get_evaluator("heuristic-rule-evaluator-v1").evaluator_id == "heuristic-rule-evaluator-v1"

    req = EvaluationRequest(
        prompt="Explain quantum entanglement",
        candidate_response="Quantum entanglement is a phenomenon where particles remain connected."
    )

    # Default evaluator invocation
    res_default = await registry.evaluate(req)
    assert res_default.evaluator_id == "stub-1"

    # Explicit evaluator invocation
    res_explicit = await registry.evaluate(req, evaluator_id="heuristic-rule-evaluator-v1")
    assert res_explicit.evaluator_id == "heuristic-rule-evaluator-v1"

    # Unregistered evaluator raises KeyError
    with pytest.raises(KeyError):
        registry.get_evaluator("non_existent_evaluator")

    # Invalid evaluator type registration raises TypeError
    with pytest.raises(TypeError):
        registry.register_evaluator("not_an_evaluator")


def test_default_evaluator_registry_singleton():
    """Verify the default evaluator registry singleton is pre-configured."""
    assert default_evaluator_registry is not None
    assert "stub-evaluator-v1" in default_evaluator_registry.list_evaluators()
    default_eval = default_evaluator_registry.get_evaluator()
    assert isinstance(default_eval, StubEvaluator)


# --- 9. Strict Separation from Routing and Model Execution ---

def test_evaluation_separation_from_routing_and_model_gateway():
    """Verify evaluation structures remain completely isolated from routing and gateway."""
    # Ensure BaseModelAdapter does not inherit or import BaseEvaluator
    from app.gateway.adapters.base import BaseModelAdapter
    assert not issubclass(BaseModelAdapter, BaseEvaluator)

    # Ensure ModelGateway is separate from EvaluatorRegistry
    from app.gateway.gateway import ModelGateway
    assert not issubclass(ModelGateway, EvaluatorRegistry)

    # Ensure CoarseRoute does not include evaluator recommendations
    coarse_values = [r.value for r in CoarseRoute]
    assert "no_escalation" not in coarse_values
    assert "escalate_to_stronger_model" not in coarse_values

    # Gateway remains operational and independent
    assert default_gateway is not None
    assert len(default_gateway.list_adapters()) >= 2
