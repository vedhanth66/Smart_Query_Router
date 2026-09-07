"""Abstract base class and reference stub for pluggable evaluators.

Defines:
- BaseEvaluator: Common interface for all evaluator implementations (heuristic, classifier, model-based).
- StubEvaluator: Reference non-intelligent baseline evaluator validating the contract.
"""

from abc import ABC, abstractmethod
import time
from ..schemas.evaluator import (
    EvaluatorType,
    EscalationRecommendation,
    EvaluationIssue,
    EvaluationRequest,
    EvaluationResult,
)


class BaseEvaluator(ABC):
    """Abstract base class defining the evaluator interface.
    
    Responsibilities:
    - Defines the standard contract for evaluating candidate responses.
    - Decoupled from routing policies and model gateway execution.
    - Allows heuristic, classifier, or model-based (LLM-as-judge) evaluators
      to be plugged in transparently without altering callers.
    """

    @property
    @abstractmethod
    def evaluator_id(self) -> str:
        """Unique identifier for this evaluator implementation."""
        raise NotImplementedError

    @property
    @abstractmethod
    def evaluator_type(self) -> EvaluatorType:
        """Architectural type of the evaluator (heuristic, classifier, model_based, stub)."""
        raise NotImplementedError

    @abstractmethod
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        """Evaluate a candidate response against the query prompt.
        
        Args:
            request: Standardized EvaluationRequest containing prompt, candidate_response, etc.
            
        Returns:
            Standardized EvaluationResult containing completeness, confidence,
            detected_issues, and an overall escalation_recommendation.
        """
        raise NotImplementedError

    def check_health(self) -> bool:
        """Check operational health and readiness of the evaluator."""
        return True


class StubEvaluator(BaseEvaluator):
    """Reference baseline evaluator validating the contract without intelligence.
    
    Provides a predictable, deterministic implementation that adheres strictly
    to the standardized EvaluationResult schema without introducing heuristic
    intelligence or machine learning models.
    """

    DEFAULT_EVALUATOR_ID = "stub-evaluator-v1"

    def __init__(
        self,
        evaluator_id: str = DEFAULT_EVALUATOR_ID,
        default_completeness: float = 1.0,
        default_confidence: float = 0.5,
        default_recommendation: EscalationRecommendation = EscalationRecommendation.NO_ESCALATION,
    ):
        self._evaluator_id = evaluator_id
        self._default_completeness = max(0.0, min(1.0, default_completeness))
        self._default_confidence = max(0.0, min(1.0, default_confidence))
        self._default_recommendation = default_recommendation

    @property
    def evaluator_id(self) -> str:
        return self._evaluator_id

    @property
    def evaluator_type(self) -> EvaluatorType:
        return EvaluatorType.STUB

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        """Execute neutral stub evaluation."""
        start_time = time.perf_counter()
        elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 3)

        eval_id = f"eval_{int(time.time() * 1000)}_{self.evaluator_id}"
        if request.correlation_id:
            eval_id = f"{request.correlation_id}_{eval_id}"

        return EvaluationResult(
            evaluation_id=eval_id,
            completeness=self._default_completeness,
            confidence=self._default_confidence,
            detected_issues=[],
            escalation_recommendation=self._default_recommendation,
            evaluator_id=self.evaluator_id,
            evaluator_type=self.evaluator_type,
            latency_ms=elapsed_ms,
            explanation="Baseline stub evaluation completed without intelligence.",
            metadata={
                "stub_mode": True,
                "prompt_length": len(request.prompt),
                "response_length": len(request.candidate_response),
            },
        )
