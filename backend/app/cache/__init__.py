"""Response Cache package for Smart Query Router."""

from .base import (
    BaseCacheStore,
    CacheKey,
    CachedResponse,
    CacheStats,
)
from .memory_store import InMemoryCacheStore
from .policy import (
    CachePolicy,
    CachePolicyConfig,
    CacheDecision,
)
from .ttl_policy import (
    TTLCategory,
    TTLPolicyConfig,
    BaseTTLPolicy,
    TTLPolicy,
)
from .manager import (
    ResponseCacheManager,
    default_response_cache,
)
from .deduplicator import (
    InFlightDeduplicator,
    default_deduplicator,
    CoalescedResult,
    DeduplicationTimeoutError,
    DeduplicatorStats,
)
from .semantic import (
    BaseEmbeddingGenerator,
    DeterministicMockEmbeddingGenerator,
    BaseSimilarityIndex,
    InMemoryCosineSimilarityIndex,
    SimilarityMatch,
    BaseCandidateValidator,
    SemanticCandidateValidator,
    CandidateValidationResult,
    SemanticRejectionReason,
    BaseSemanticEligibilityPolicy,
    SemanticEligibilityPolicy,
    SemanticEligibilityDecision,
    SemanticPolicyConfig,
    SemanticCacheManager,
    default_semantic_cache,
)

__all__ = [
    "BaseCacheStore",
    "CacheKey",
    "CachedResponse",
    "CacheStats",
    "InMemoryCacheStore",
    "CachePolicy",
    "CachePolicyConfig",
    "CacheDecision",
    "TTLCategory",
    "TTLPolicyConfig",
    "BaseTTLPolicy",
    "TTLPolicy",
    "ResponseCacheManager",
    "default_response_cache",
    "InFlightDeduplicator",
    "default_deduplicator",
    "CoalescedResult",
    "DeduplicationTimeoutError",
    "DeduplicatorStats",
    "BaseEmbeddingGenerator",
    "DeterministicMockEmbeddingGenerator",
    "BaseSimilarityIndex",
    "InMemoryCosineSimilarityIndex",
    "SimilarityMatch",
    "BaseCandidateValidator",
    "SemanticCandidateValidator",
    "CandidateValidationResult",
    "SemanticRejectionReason",
    "BaseSemanticEligibilityPolicy",
    "SemanticEligibilityPolicy",
    "SemanticEligibilityDecision",
    "SemanticPolicyConfig",
    "SemanticCacheManager",
    "default_semantic_cache",
]
