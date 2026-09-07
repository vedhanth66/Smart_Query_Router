"""Standardized schemas for evaluator result structure and evaluation requests.

Defines:
- EscalationRecommendation: Standardized escalation advice.
- IssueSeverity: Severity classification for detected flaws.
- IssueCategory: Categorical classification for detected flaws.
- EvaluatorType: Architectural classification (heuristic, classifier, model-based, stub).
- EvaluationIssue: Structured issue representation.
- EvaluationRequest: Standardized input contract for evaluators.
- EvaluationResult: Standardized evaluator result structure.
"""

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


class EscalationRecommendation(str, Enum):
    """Overall escalation recommendation produced by an evaluator."""
    NO_ESCALATION = "no_escalation"
    ESCALATE_TO_STRONGER_MODEL = "escalate_to_stronger_model"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    UNCERTAIN = "uncertain"


class IssueSeverity(str, Enum):
    """Severity classification of detected issues."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueCategory(str, Enum):
    """Categorical classification of detected issues."""
    COMPLETENESS = "completeness"
    ACCURACY = "accuracy"
    FORMATTING = "formatting"
    SAFETY_POLICY = "safety_policy"
    AMBIGUITY = "ambiguity"
    OTHER = "other"


class EvaluatorType(str, Enum):
    """Architectural classification of evaluator implementations."""
    HEURISTIC = "heuristic"
    CLASSIFIER = "classifier"
    MODEL_BASED = "model_based"
    STUB = "stub"


class EvaluationIssue(BaseModel):
    """Structured flaw or deficiency detected in a candidate response."""
    model_config = ConfigDict(extra="forbid")

    issue_code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Machine-readable code identifying the issue (e.g. MISSING_CODE_BLOCK)"
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Human-readable description of the detected issue"
    )
    severity: IssueSeverity = Field(
        ...,
        description="Severity level of the issue"
    )
    category: IssueCategory = Field(
        default=IssueCategory.OTHER,
        description="Category classification of the issue"
    )
    location: str | None = Field(
        default=None,
        max_length=256,
        description="Optional pointer or location within the response (e.g. section or line range)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-sensitive structured details about the issue"
    )


class EvaluationRequest(BaseModel):
    """Standardized input contract for evaluator execution."""
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="Original query prompt presented to the model"
    )
    candidate_response: str = Field(
        ...,
        max_length=500_000,
        description="Candidate response text to be evaluated"
    )
    task_category: str | None = Field(
        default=None,
        max_length=64,
        description="Optional task category classification (e.g. coding, arithmetic)"
    )
    context_turns: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=20,
        description="Optional context turns from preceding conversation"
    )
    evaluation_criteria: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Optional specific criteria or checklist constraints to verify"
    )
    correlation_id: str | None = Field(
        default=None,
        max_length=128,
        description="Correlation ID linking evaluation to request/gateway execution"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-sensitive evaluation request metadata"
    )


class EvaluationResult(BaseModel):
    """Standardized evaluator result structure.
    
    Contains:
    - completeness: Score between 0.0 and 1.0 measuring fulfillment of prompt criteria.
    - confidence: Score between 0.0 and 1.0 indicating evaluator self-confidence.
    - detected_issues: Structured list of detected flaws or deficiencies.
    - escalation_recommendation: Overall recommendation (no_escalation, escalate_to_stronger_model, escalate_to_human, uncertain).
    """
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique identifier for this evaluation result"
    )
    completeness: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Quantitative completeness score between 0.0 and 1.0"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Evaluator confidence score between 0.0 and 1.0"
    )
    detected_issues: list[EvaluationIssue] = Field(
        default_factory=list,
        description="List of detected flaws or deficiencies"
    )
    escalation_recommendation: EscalationRecommendation = Field(
        ...,
        description="Overall escalation recommendation"
    )
    evaluator_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identifier of the evaluator that produced this result"
    )
    evaluator_type: EvaluatorType = Field(
        ...,
        description="Architectural type of the evaluator"
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Latency of the evaluation run in milliseconds"
    )
    explanation: str | None = Field(
        default=None,
        max_length=2048,
        description="Human-readable summary rationale for the evaluation outcome"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-sensitive evaluation metadata and sub-scores"
    )
