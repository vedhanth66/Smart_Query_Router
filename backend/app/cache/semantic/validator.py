"""Candidate validation interface and safety verification for semantic caching.

Explicitly separates retrieval similarity (vector distance) from cache safety.
A high vector similarity score is NEVER sufficient on its own to serve a cached
completion. Context turns, time sensitivity, model ID/version, task category,
and tenant/user scope must strictly agree.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from typing import Any

from ...schemas.contract import NormalizedQueryPackage, ModelTier, TaskCategory
from ..policy import CachePolicy


class SemanticRejectionReason(str, Enum):
    """Reason codes why a similarity candidate was rejected by safety validation."""
    LOW_SIMILARITY = "LOW_SIMILARITY"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    TIME_SENSITIVE_QUERY = "TIME_SENSITIVE_QUERY"
    TIME_SENSITIVE_CANDIDATE = "TIME_SENSITIVE_CANDIDATE"
    MODEL_TIER_MISMATCH = "MODEL_TIER_MISMATCH"
    MODEL_ID_MISMATCH = "MODEL_ID_MISMATCH"
    MODEL_VERSION_MISMATCH = "MODEL_VERSION_MISMATCH"
    TASK_TYPE_MISMATCH = "TASK_TYPE_MISMATCH"
    TENANT_USER_MISMATCH = "TENANT_USER_MISMATCH"


@dataclass
class CandidateValidationResult:
    """Outcome of safety validation for a candidate retrieved from vector similarity search."""
    is_valid: bool
    rejection_reasons: list[SemanticRejectionReason] = field(default_factory=list)
    similarity_score: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "rejection_reasons": [r.value for r in self.rejection_reasons],
            "similarity_score": self.similarity_score,
            "details": self.details,
            "primary_reason": self.primary_reason(),
        }

    def primary_reason(self) -> str:
        """Returns concise primary validation outcome or rejection reason."""
        if self.is_valid:
            return "VALIDATED_STATIC_INFORMATIONAL_MATCH"
        if self.rejection_reasons:
            return f"REJECTED_{self.rejection_reasons[0].value}"
        return "REJECTED_UNKNOWN"


class BaseCandidateValidator(ABC):
    """Abstract interface for validating semantic cache candidates."""

    @abstractmethod
    def validate(
        self,
        package: NormalizedQueryPackage,
        candidate_metadata: dict[str, Any],
        similarity_score: float,
        min_similarity_threshold: float = 0.85,
        expected_tier: ModelTier | None = None,
        expected_model_id: str | None = None,
        expected_model_version: str | None = None,
        tenant_id: str = "default_tenant",
        user_id: str = "default_user",
    ) -> CandidateValidationResult:
        """Evaluate candidate safety. Returns CandidateValidationResult."""
        pass


def compute_context_fingerprint(package: NormalizedQueryPackage) -> str:
    """Computes deterministic context fingerprint matching CacheKey format."""
    if not package.context_candidates:
        return "none"

    canonical_turns = []
    for turn in sorted(package.context_candidates, key=lambda t: t.original_index):
        turn_id = str(turn.turn_id)
        role = str(turn.role)
        content = str(turn.content)
        content_h = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]
        canonical_turns.append(f"{turn_id}:{role}:{content_h}")
    raw_context = "|".join(canonical_turns)
    return hashlib.sha256(raw_context.encode("utf-8")).hexdigest()[:32]


class SemanticCandidateValidator(BaseCandidateValidator):
    """Multi-dimensional safety validator decoupling vector similarity from cache safety.
    
    Guarantees:
    - Never returns an answer solely due to embedding proximity.
    - Context consistency: turn count, roles, and content fingerprints must agree.
    - Time-sensitivity: neither query nor candidate may be time-sensitive.
    - Model & version parity: tier, concrete model ID, and release version must agree.
    - Task type consistency: task categories must not conflict.
    - Tenant & user boundary: strict isolation between callers.
    """

    def __init__(self, policy: CachePolicy | None = None):
        self._policy = policy or CachePolicy()

    def validate(
        self,
        package: NormalizedQueryPackage,
        candidate_metadata: dict[str, Any],
        similarity_score: float,
        min_similarity_threshold: float = 0.85,
        expected_tier: ModelTier | None = None,
        expected_model_id: str | None = None,
        expected_model_version: str | None = None,
        tenant_id: str = "default_tenant",
        user_id: str = "default_user",
    ) -> CandidateValidationResult:
        rejections: list[SemanticRejectionReason] = []
        details: dict[str, Any] = {
            "similarity_score": similarity_score,
            "min_threshold": min_similarity_threshold,
        }

        # 1. Similarity Threshold Verification
        if similarity_score < min_similarity_threshold:
            rejections.append(SemanticRejectionReason.LOW_SIMILARITY)

        # 2. Context Agreement Check
        query_context_fp = compute_context_fingerprint(package)
        cand_context_fp = candidate_metadata.get("context_fingerprint", "none")
        details["query_context_fp"] = query_context_fp
        details["candidate_context_fp"] = cand_context_fp

        if query_context_fp != cand_context_fp:
            rejections.append(SemanticRejectionReason.CONTEXT_MISMATCH)

        # 3. Time-Sensitivity Check
        is_query_ts, ts_reason = self._policy.is_time_sensitive(package)
        details["is_query_time_sensitive"] = is_query_ts
        if is_query_ts:
            rejections.append(SemanticRejectionReason.TIME_SENSITIVE_QUERY)

        is_cand_ts = candidate_metadata.get("is_time_sensitive", False)
        details["is_candidate_time_sensitive"] = is_cand_ts
        if is_cand_ts:
            rejections.append(SemanticRejectionReason.TIME_SENSITIVE_CANDIDATE)

        # 4. Model Tier Compatibility Check
        cand_tier = candidate_metadata.get("model_tier")
        if expected_tier is not None:
            expected_tier_val = expected_tier.value if hasattr(expected_tier, "value") else str(expected_tier)
            if cand_tier != expected_tier_val:
                rejections.append(SemanticRejectionReason.MODEL_TIER_MISMATCH)
                details["tier_mismatch"] = f"expected={expected_tier_val}, candidate={cand_tier}"

        # 5. Concrete Model ID Parity Check
        cand_model_id = candidate_metadata.get("model_id")
        if expected_model_id is not None:
            if str(cand_model_id).strip().lower() != str(expected_model_id).strip().lower():
                rejections.append(SemanticRejectionReason.MODEL_ID_MISMATCH)
                details["model_id_mismatch"] = f"expected={expected_model_id}, candidate={cand_model_id}"

        # 6. Model Release Version Parity Check
        cand_version = candidate_metadata.get("model_version")
        if expected_model_version is not None:
            if str(cand_version).strip().lower() != str(expected_model_version).strip().lower():
                rejections.append(SemanticRejectionReason.MODEL_VERSION_MISMATCH)
                details["version_mismatch"] = f"expected={expected_model_version}, candidate={cand_version}"

        # 7. Task Type Agreement Check
        cand_task = candidate_metadata.get("task_category")
        if package.task_category is not None and cand_task is not None:
            query_task_val = package.task_category.value if hasattr(package.task_category, "value") else str(package.task_category)
            if query_task_val != cand_task:
                rejections.append(SemanticRejectionReason.TASK_TYPE_MISMATCH)
                details["task_mismatch"] = f"query={query_task_val}, candidate={cand_task}"

        # 8. Tenant & User Scope Check
        cand_tenant = candidate_metadata.get("tenant_id", "default_tenant")
        cand_user = candidate_metadata.get("user_id", "default_user")
        if cand_tenant != tenant_id or cand_user != user_id:
            rejections.append(SemanticRejectionReason.TENANT_USER_MISMATCH)
            details["scope_mismatch"] = f"expected={tenant_id}:{user_id}, candidate={cand_tenant}:{cand_user}"

        is_valid = (len(rejections) == 0)
        return CandidateValidationResult(
            is_valid=is_valid,
            rejection_reasons=rejections,
            similarity_score=similarity_score,
            details=details,
        )
