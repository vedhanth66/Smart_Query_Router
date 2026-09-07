"""Semantic caching interfaces, vector similarity, safety validation, and eligibility policies."""

from .embedding import (
    BaseEmbeddingGenerator,
    DeterministicMockEmbeddingGenerator,
)
from .similarity import (
    BaseSimilarityIndex,
    InMemoryCosineSimilarityIndex,
    SimilarityMatch,
    compute_cosine_similarity,
)
from .validator import (
    BaseCandidateValidator,
    SemanticCandidateValidator,
    CandidateValidationResult,
    SemanticRejectionReason,
    compute_context_fingerprint,
)
from .eligibility import (
    BaseSemanticEligibilityPolicy,
    SemanticEligibilityPolicy,
    SemanticEligibilityDecision,
    SemanticPolicyConfig,
)
from .manager import (
    SemanticCacheManager,
    default_semantic_cache,
)

__all__ = [
    "BaseEmbeddingGenerator",
    "DeterministicMockEmbeddingGenerator",
    "BaseSimilarityIndex",
    "InMemoryCosineSimilarityIndex",
    "SimilarityMatch",
    "compute_cosine_similarity",
    "BaseCandidateValidator",
    "SemanticCandidateValidator",
    "CandidateValidationResult",
    "SemanticRejectionReason",
    "compute_context_fingerprint",
    "BaseSemanticEligibilityPolicy",
    "SemanticEligibilityPolicy",
    "SemanticEligibilityDecision",
    "SemanticPolicyConfig",
    "SemanticCacheManager",
    "default_semantic_cache",
]
