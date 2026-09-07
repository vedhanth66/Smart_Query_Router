"""Semantic Cache Manager coordinating embedding, similarity search, candidate validation, and eligibility."""

from dataclasses import dataclass, field
import hashlib
import time
from typing import Any

from ...schemas.contract import NormalizedQueryPackage, ModelTier
from .embedding import BaseEmbeddingGenerator, DeterministicMockEmbeddingGenerator
from .similarity import BaseSimilarityIndex, InMemoryCosineSimilarityIndex, SimilarityMatch
from .validator import (
    BaseCandidateValidator,
    SemanticCandidateValidator,
    CandidateValidationResult,
    compute_context_fingerprint,
)
from .eligibility import (
    BaseSemanticEligibilityPolicy,
    SemanticEligibilityPolicy,
    SemanticEligibilityDecision,
    SemanticPolicyConfig,
)


class SemanticCacheManager:
    """High-level coordinator uniting all four semantic caching interfaces.
    
    Guarantees:
    - Disabled by default (semantic matching is NOT enabled in active routing yet).
    - Strict decoupling between vector retrieval and safety validation.
    - Multi-tenant and multi-user isolation.
    """

    def __init__(
        self,
        embedding_generator: BaseEmbeddingGenerator | None = None,
        similarity_index: BaseSimilarityIndex | None = None,
        candidate_validator: BaseCandidateValidator | None = None,
        eligibility_policy: BaseSemanticEligibilityPolicy | None = None,
    ):
        self.embedding_generator = embedding_generator or DeterministicMockEmbeddingGenerator()
        self.similarity_index = similarity_index or InMemoryCosineSimilarityIndex()
        self.candidate_validator = candidate_validator or SemanticCandidateValidator()
        self.eligibility_policy = eligibility_policy or SemanticEligibilityPolicy()

    async def lookup_candidate(
        self,
        package: NormalizedQueryPackage,
        expected_tier: ModelTier | None = None,
        expected_model_id: str | None = None,
        expected_model_version: str | None = None,
        tenant_id: str = "default_tenant",
        user_id: str = "default_user",
        top_k: int = 5,
    ) -> tuple[SimilarityMatch | None, CandidateValidationResult | None, SemanticEligibilityDecision]:
        """Performs eligibility evaluation, similarity retrieval, and safety validation.
        
        Returns:
            (valid_match_or_none, validation_result_or_none, eligibility_decision)
        """
        # 1. Eligibility Check
        decision = self.eligibility_policy.evaluate(package)
        if not decision.is_eligible:
            return None, None, decision

        # 2. Embedding Generation
        query_vector = await self.embedding_generator.embed_text(package.query_text)

        # 3. Vector Proximity Search (retrieval phase)
        filter_criteria = {"tenant_id": tenant_id, "user_id": user_id}
        matches = await self.similarity_index.search(
            query_vector=query_vector,
            top_k=top_k,
            min_score=decision.required_similarity_threshold,
            filter_criteria=filter_criteria,
        )

        if not matches:
            return None, None, decision

        # 4. Independent Safety Validation (safety phase)
        # Vector proximity ALONE is never sufficient! Context, model version,
        # temporal freshness, and task category must also strictly agree.
        last_validation: CandidateValidationResult | None = None
        for match in matches:
            val_result = self.candidate_validator.validate(
                package=package,
                candidate_metadata=match.metadata,
                similarity_score=match.score,
                min_similarity_threshold=decision.required_similarity_threshold,
                expected_tier=expected_tier,
                expected_model_id=expected_model_id,
                expected_model_version=expected_model_version,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            last_validation = val_result
            if val_result.is_valid:
                # Both retrieval similarity AND multi-dimensional safety validation passed!
                return match, val_result, decision

        # Candidates found by vector search, but ALL failed safety validation
        return None, last_validation, decision

    async def index_candidate(
        self,
        entry_id: str,
        query_text: str,
        content: str,
        package: NormalizedQueryPackage,
        tier: ModelTier,
        model_id: str,
        model_version: str | None,
        tenant_id: str = "default_tenant",
        user_id: str = "default_user",
        is_time_sensitive: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Indexes a completion into the vector store with rich provenance metadata."""
        vector = await self.embedding_generator.embed_text(query_text)
        context_fp = compute_context_fingerprint(package)

        ttl_sec, ttl_cat, _ = self.eligibility_policy._cache_policy.ttl_policy.resolve_ttl(
            package=package,
            is_time_sensitive=is_time_sensitive,
        )

        meta = dict(metadata or {})
        meta.update({
            "query_text": query_text,
            "content": content,
            "model_tier": tier.value if hasattr(tier, "value") else str(tier),
            "model_id": model_id,
            "model_version": model_version or "latest",
            "task_category": package.task_category.value if package.task_category else None,
            "context_fingerprint": context_fp,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "is_time_sensitive": is_time_sensitive,
            "ttl_seconds": ttl_sec,
            "ttl_category": ttl_cat.value if hasattr(ttl_cat, "value") else str(ttl_cat),
            "indexed_at": time.time(),
        })

        await self.similarity_index.add(
            entry_id=entry_id,
            vector=vector,
            metadata=meta,
        )
        return entry_id

    async def clear(self) -> None:
        """Clears the underlying similarity index."""
        await self.similarity_index.clear()

    async def get_stats(self) -> dict[str, Any]:
        """Returns diagnostic statistics for semantic cache manager."""
        entry_count = await self.similarity_index.count()
        allowed_cats = (
            [c.value for c in self.eligibility_policy.config.allowed_categories]
            if self.eligibility_policy.config.allowed_categories
            else None
        )
        return {
            "total_entries": entry_count,
            "enabled": self.eligibility_policy.config.enabled,
            "allowed_categories": allowed_cats,
            "static_informational_threshold": self.eligibility_policy.config.static_informational_threshold,
            "embedding_model": self.embedding_generator.model_name,
            "embedding_dimension": self.embedding_generator.dimension,
        }


# Default singleton instance (disabled by default)
default_semantic_cache = SemanticCacheManager()
