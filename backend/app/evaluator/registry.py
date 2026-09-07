"""Evaluator registry and coordination engine.

Provides a pluggable registry allowing heuristic, classifier, or model-based
evaluators to be registered and invoked transparently without coupling to routing
or model execution.
"""

from typing import Any
from .base import BaseEvaluator, StubEvaluator
from .heuristic import HeuristicIncompletenessEvaluator
from ..schemas.evaluator import EvaluationRequest, EvaluationResult


class EvaluatorRegistry:
    """Coordinator and registry for pluggable evaluator implementations."""

    def __init__(self, default_evaluator: BaseEvaluator | None = None):
        self._evaluators: dict[str, BaseEvaluator] = {}
        self._default_evaluator_id: str | None = None

        if default_evaluator:
            self.register_evaluator(default_evaluator, set_default=True)

    def register_evaluator(self, evaluator: BaseEvaluator, set_default: bool = False) -> None:
        """Register a pluggable evaluator implementation."""
        if not isinstance(evaluator, BaseEvaluator):
            raise TypeError("Evaluator must inherit from BaseEvaluator")
        eid = evaluator.evaluator_id
        self._evaluators[eid] = evaluator
        if set_default or self._default_evaluator_id is None:
            self._default_evaluator_id = eid

    def set_default_evaluator(self, evaluator_id: str) -> None:
        """Set the default evaluator by identifier."""
        if evaluator_id not in self._evaluators:
            raise KeyError(f"Evaluator '{evaluator_id}' is not registered")
        self._default_evaluator_id = evaluator_id

    def get_evaluator(self, evaluator_id: str | None = None) -> BaseEvaluator:
        """Retrieve an evaluator by ID, or return the default evaluator."""
        target_id = evaluator_id or self._default_evaluator_id
        if not target_id or target_id not in self._evaluators:
            raise KeyError(f"No evaluator registered for ID '{target_id}'")
        return self._evaluators[target_id]

    def list_evaluators(self) -> list[str]:
        """List registered evaluator identifiers."""
        return list(self._evaluators.keys())

    async def evaluate(
        self,
        request: EvaluationRequest,
        evaluator_id: str | None = None
    ) -> EvaluationResult:
        """Execute evaluation using the designated or default evaluator."""
        evaluator = self.get_evaluator(evaluator_id)
        return await evaluator.evaluate(request)


def create_default_evaluator_registry() -> EvaluatorRegistry:
    """Create default evaluator registry initialized with StubEvaluator and HeuristicIncompletenessEvaluator."""
    reg = EvaluatorRegistry()
    stub = StubEvaluator()
    reg.register_evaluator(stub, set_default=True)
    heuristic = HeuristicIncompletenessEvaluator()
    reg.register_evaluator(heuristic, set_default=False)
    return reg


default_evaluator_registry = create_default_evaluator_registry()
