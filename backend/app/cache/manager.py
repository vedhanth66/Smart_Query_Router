"""High-level coordinator managing response cache policy and storage."""

import time
from typing import Any
from .base import BaseCacheStore, CacheKey, CachedResponse, CacheStats
from .memory_store import InMemoryCacheStore
from .policy import CachePolicy, CachePolicyConfig, CacheDecision
from ..schemas.contract import NormalizedQueryPackage, ModelTier, CacheOutcome


class ResponseCacheManager:
    """High-level manager coordinating cache policy, key derivation, and storage.
    
    Guarantees:
    - Clean abstraction isolating cache implementation from router core.
    - Deterministic key derivation incorporating prompt, context fingerprint, and model version.
    - Strict adherence to time-sensitive task bypass rules.
    - Support for pluggable backend stores (in-memory, Redis, Memcached).
    """

    def __init__(
        self,
        store: BaseCacheStore | None = None,
        policy: CachePolicy | None = None
    ):
        self.store = store or InMemoryCacheStore()
        self.policy = policy or CachePolicy()

    def generate_cache_key(
        self,
        package: NormalizedQueryPackage,
        tier: ModelTier,
        model_id: str,
        model_version: str | None,
        provider: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> CacheKey:
        """Derives a normalized, version-aware CacheKey from the request package and model identity."""
        context_turns = [t.model_dump() for t in package.context_candidates] if package.context_candidates else None
        
        effective_tenant = (
            tenant_id
            or getattr(package, "tenant_id", None)
            or (getattr(package.client_metadata, "tenant_id", None) if package.client_metadata else None)
            or "default_tenant"
        )
        effective_user = (
            user_id
            or getattr(package, "user_id", None)
            or (getattr(package.client_metadata, "user_id", None) if package.client_metadata else None)
            or "default_user"
        )

        return CacheKey.from_inputs(
            prompt=package.query_text,
            context_turns=context_turns,
            model_id=model_id,
            model_version=model_version,
            model_tier=tier,
            provider=provider,
            schema_version=package.client_metadata.schema_version if package.client_metadata else "1.0",
            tenant_id=effective_tenant,
            user_id=effective_user,
        )

    async def check_cache(
        self,
        package: NormalizedQueryPackage,
        tier: ModelTier,
        model_id: str,
        model_version: str | None,
        provider: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[CachedResponse | None, CacheOutcome, str | None, str | None]:
        """Checks the cache for an exact match.
        
        Returns:
            (cached_response, cache_outcome, bypass_reason, computed_cache_key)
        """
        # 1. Evaluate cacheability policy
        decision = self.policy.evaluate(package)
        if not decision.is_cacheable:
            # Bypass cache
            if hasattr(self.store, "_stats"):
                self.store._stats.bypasses += 1
            return None, CacheOutcome.BYPASS, decision.bypass_reason, None

        # 2. Compute canonical key
        cache_key = self.generate_cache_key(
            package=package,
            tier=tier,
            model_id=model_id,
            model_version=model_version,
            provider=provider,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        key_str = cache_key.compute_key()

        # 3. Lookup in store
        cached = await self.store.get(key_str)
        if cached is not None:
            return cached, CacheOutcome.HIT, None, key_str

        return None, CacheOutcome.MISS, None, key_str

    async def store_response(
        self,
        package: NormalizedQueryPackage,
        tier: ModelTier,
        model_id: str,
        model_version: str | None,
        provider: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        """Stores a successful model response in the cache if eligible under policy.
        
        Returns the computed cache key string if stored, or None if bypassed.
        """
        decision = self.policy.evaluate(package)
        if not decision.is_cacheable:
            return None

        cache_key = self.generate_cache_key(
            package=package,
            tier=tier,
            model_id=model_id,
            model_version=model_version,
            provider=provider,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        key_str = cache_key.compute_key()

        effective_ttl = ttl_seconds or decision.ttl_seconds
        now = time.time()
        expires_at = now + effective_ttl

        entry_meta = dict(metadata or {})
        entry_meta.setdefault(
            "ttl_category",
            decision.ttl_category.value if hasattr(decision.ttl_category, "value") else str(decision.ttl_category),
        )
        entry_meta.setdefault("ttl_reason", decision.ttl_reason)

        entry = CachedResponse(
            content=content,
            model_id=model_id,
            model_version=model_version,
            model_tier=tier,
            provider=provider,
            tenant_id=cache_key.tenant_id,
            user_id=cache_key.user_id,
            created_at=now,
            expires_at=expires_at,
            ttl_seconds=effective_ttl,
            hit_count=0,
            last_accessed_at=now,
            metadata=entry_meta,
        )

        await self.store.set(key_str, entry, ttl_seconds=effective_ttl)
        return key_str

    async def invalidate_by_model(self, model_id: str, model_version: str | None = None) -> int:
        """Invalidates all cached entries for a given model identifier and optional version."""
        return await self.store.invalidate_by_model(model_id, model_version)

    async def delete(self, key: str) -> bool:
        """Deletes a single cache entry by key."""
        return await self.store.delete(key)

    async def clear(self) -> None:
        """Clears all entries from the underlying cache store."""
        await self.store.clear()

    async def purge_expired(self) -> int:
        """Purges expired entries from the underlying cache store."""
        return await self.store.purge_expired()

    async def get_stats(self) -> dict[str, Any]:
        """Returns diagnostic statistics."""
        stats = await self.store.get_stats()
        return stats.to_dict()


# Default singleton instance using in-memory store and standard policy
default_response_cache = ResponseCacheManager()
