"""Guarded ML Router Engine.

GOVERNANCE & HIERARCHY GUARANTEES:
1. Gate 1: Deterministic Safety Checks Primacy:
   Greetings, conversational pleasantries, and deterministic arithmetic
   are ALWAYS resolved locally on-device ($0 spent, 0 cloud ms).
   Embeddings and ML classifiers are strictly forbidden from overriding
   these local safety guarantees.
2. Gate 2: Explicit Context Handling Primacy:
   Any query with explicit conversational context dependency (has_context_dependency=True
   or multi-turn context references) is routed to a context-aware strong tier.
   Embeddings cannot downgrade or misroute context-dependent queries.
3. Gate 3: Auxiliary ML Classification:
   Sentence embeddings and local signals serve as auxiliary inputs to the ML
   classifier ONLY for independent queries that pass Gates 1 and 2.
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas.ml_model import (
    EmbeddingTelemetry,
    GuardedRoutingDecision,
)
from app.ml.embedding_extractor import SentenceEmbeddingExtractor
from app.dataset.ml_dataset_generator import (
    extract_local_signals,
    extract_context_signals,
)
from app.dataset.sanitizer import sanitize_text_snippet


# Regex patterns for deterministic safety checks
GREETING_PATTERN = re.compile(
    r"^\s*(hello|hi|hey|good morning|good afternoon|good evening|greetings|howdy|thanks|thank you)\b",
    re.IGNORECASE,
)

ARITHMETIC_PATTERN = re.compile(
    r"^\s*(what is\s+)?(\-?\d+(\.\d+)?\s*[\+\-\*\/]\s*\-?\d+(\.\d+)?(\s*[\+\-\*\/]\s*\-?\d+(\.\d+)?)*)\s*\??\s*$",
    re.IGNORECASE,
)


def is_deterministic_greeting(query: str | None, task_type: str | None = None) -> bool:
    """Checks if a query is a deterministic greeting eligible for on-device resolution."""
    if task_type and task_type.lower() in ("greeting", "greetings"):
        return True
    if not query:
        return False
    clean = query.strip()
    return bool(GREETING_PATTERN.match(clean)) and len(clean.split()) <= 12


def is_deterministic_arithmetic(query: str | None, task_type: str | None = None) -> bool:
    """Checks if a query is a simple deterministic arithmetic calculation."""
    if task_type and task_type.lower() in ("arithmetic", "math"):
        return True
    if not query:
        return False
    clean = query.strip()
    return bool(ARITHMETIC_PATTERN.match(clean))


class GuardedRouter:
    """Multi-tier router enforcing deterministic safety and context primacy."""

    def __init__(
        self,
        ml_classifier: Any | None = None,
        embedding_extractor: SentenceEmbeddingExtractor | None = None,
    ) -> None:
        self.classifier = ml_classifier
        self.embedding_extractor = embedding_extractor or SentenceEmbeddingExtractor()

    def route_query(
        self,
        query_text: str,
        task_type: str | None = None,
        complexity_label: str | None = None,
        has_context_dependency: bool = False,
        prior_conversation_turns: list[dict[str, Any]] | None = None,
        local_signals: dict[str, Any] | None = None,
        context_signals: dict[str, Any] | None = None,
    ) -> GuardedRoutingDecision:
        """Evaluates a query through the 3-gate guarded routing pipeline."""
        clean_raw = (query_text or "").strip()
        sanitized_query, _ = sanitize_text_snippet(clean_raw, authorized=True)
        query_to_eval = sanitized_query if sanitized_query is not None else clean_raw

        # -------------------------------------------------------------
        # GATE 1: Deterministic Safety Checks Primacy
        # -------------------------------------------------------------
        if is_deterministic_greeting(query_to_eval, task_type):
            return GuardedRoutingDecision(
                final_route="local-eligible",
                recommended_tier="local",
                decision_gate="DETERMINISTIC_SAFETY",
                safety_override_applied=True,
                context_guard_applied=False,
                ml_predicted_route=None,
                ml_confidence=None,
                embedding_telemetry=None,
                reason=(
                    "DETERMINISTIC_SAFETY_OVERRIDE: Greeting query resolved locally "
                    "on-device ($0 spent, 0 cloud ms). Embeddings/ML bypassed."
                ),
            )

        if is_deterministic_arithmetic(query_to_eval, task_type):
            return GuardedRoutingDecision(
                final_route="local-eligible",
                recommended_tier="local",
                decision_gate="DETERMINISTIC_SAFETY",
                safety_override_applied=True,
                context_guard_applied=False,
                ml_predicted_route=None,
                ml_confidence=None,
                embedding_telemetry=None,
                reason=(
                    "DETERMINISTIC_SAFETY_OVERRIDE: Basic arithmetic resolved locally "
                    "on-device ($0 spent, 0 cloud ms). Embeddings/ML bypassed."
                ),
            )

        # -------------------------------------------------------------
        # GATE 2: Explicit Context Handling Primacy
        # -------------------------------------------------------------
        turns = prior_conversation_turns or []
        is_context_dep = (
            has_context_dependency
            or (task_type and "context" in task_type.lower())
            or bool(turns)
        )

        if is_context_dep:
            return GuardedRoutingDecision(
                final_route="complex-model candidate",
                recommended_tier="strong",
                decision_gate="EXPLICIT_CONTEXT_GUARD",
                safety_override_applied=False,
                context_guard_applied=True,
                ml_predicted_route=None,
                ml_confidence=None,
                embedding_telemetry=None,
                reason=(
                    "EXPLICIT_CONTEXT_GUARD: Multi-turn conversational context dependency "
                    "detected. Routed deterministically to context-aware strong tier."
                ),
            )

        # -------------------------------------------------------------
        # GATE 3: Auxiliary ML Classification with Sentence Embeddings
        # -------------------------------------------------------------
        # Extract embedding features with latency telemetry & circuit breaker
        embed_features, embedding_telemetry = self.embedding_extractor.extract_features(
            query_to_eval
        )

        # Extract local and context signals if not provided
        loc_sigs = (
            local_signals
            if local_signals is not None
            else extract_local_signals(query_to_eval).model_dump()
        )
        if context_signals is not None:
            ctx_sigs = context_signals
        else:
            turn_count = len(turns)
            total_chars = sum(len(str(t.get("content", ""))) for t in turns)
            last_turn_chars = len(str(turns[-1].get("content", ""))) if turns else 0
            has_cues = bool(re.search(r"\b(it|this|that|these|those|the previous|previous|above|mentioned|earlier|again|instead|also|furthermore)\b", query_to_eval, re.IGNORECASE))
            ctx_sigs = {
                "has_context_dependency": is_context_dep,
                "context_turn_count": turn_count,
                "context_total_chars": total_chars,
                "context_last_turn_chars": last_turn_chars,
                "has_context_cues": has_cues,
            }

        resolved_task_type = task_type or "UNKNOWN"
        resolved_complexity = complexity_label or "UNKNOWN"

        if self.classifier is not None:
            # Predict using classifier
            try:
                pred_route, rec_tier, conf, _ = self.classifier.predict(
                    local_signals=loc_sigs,
                    task_type=resolved_task_type,
                    complexity_label=resolved_complexity,
                    context_signals=ctx_sigs,
                    embedding_features=embed_features,
                )
            except TypeError:
                pred_route, rec_tier, conf, _ = self.classifier.predict(
                    local_signals=loc_sigs,
                    task_type=resolved_task_type,
                    complexity_label=resolved_complexity,
                    context_signals=ctx_sigs,
                )

            return GuardedRoutingDecision(
                final_route=pred_route,
                recommended_tier=rec_tier,
                decision_gate="ML_CLASSIFIER_AUXILIARY",
                safety_override_applied=False,
                context_guard_applied=False,
                ml_predicted_route=pred_route,
                ml_confidence=conf,
                embedding_telemetry=embedding_telemetry,
                reason=(
                    f"ML_CLASSIFIER_AUXILIARY: Evaluated with {embedding_telemetry.dimension}-dim "
                    f"sentence embeddings ({embedding_telemetry.extraction_latency_ms:.2f}ms). "
                    f"Predicted {pred_route} with {conf:.2f} confidence."
                ),
            )

        # Default fallback if classifier is not loaded
        return GuardedRoutingDecision(
            final_route="complex-model candidate",
            recommended_tier="strong",
            decision_gate="ML_CLASSIFIER_AUXILIARY",
            safety_override_applied=False,
            context_guard_applied=False,
            ml_predicted_route="complex-model candidate",
            ml_confidence=0.5,
            embedding_telemetry=embedding_telemetry,
            reason="FALLBACK: Classifier not loaded; defaulting to safe complex tier.",
        )
