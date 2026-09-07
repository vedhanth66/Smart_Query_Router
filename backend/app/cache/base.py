"""Base models and storage interface for exact-match response cache."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any
from pydantic import BaseModel, Field as PydanticField, ConfigDict
from ..schemas.contract import ModelTier


@dataclass(frozen=True)
class CacheKey:
    """Deterministic, context- and model-version-aware cache key.
    
    Includes canonical representations of:
    - prompt text (or hash)
    - context turns / context fingerprint
    - target model tier
    - target provider name
    - executed concrete model identifier
    - executed model release version
    - protocol schema version
    - tenant identifier
    - user identifier
    """
    prompt_hash: str
    context_fingerprint: str
    model_id: str
    model_version: str
    model_tier: ModelTier
    provider: str
    schema_version: str = "1.0"
    tenant_id: str = "default_tenant"
    user_id: str = "default_user"

    @classmethod
    def from_inputs(
        cls,
        prompt: str,
        context_turns: list[dict[str, Any]] | None,
        model_id: str,
        model_version: str | None,
        model_tier: ModelTier,
        provider: str,
        schema_version: str = "1.0",
        tenant_id: str = "default_tenant",
        user_id: str = "default_user",
    ) -> "CacheKey":
        """Factory creating a normalized CacheKey from raw inputs."""
        # 1. Canonical prompt hash (normalized whitespace)
        canonical_prompt = " ".join(prompt.strip().split())
        prompt_h = hashlib.sha256(canonical_prompt.encode("utf-8")).hexdigest()

        # 2. Context fingerprint
        if not context_turns:
            context_fp = "none"
        else:
            # Deterministic representation of turn id, role, and content hash
            canonical_turns = []
            for turn in sorted(context_turns, key=lambda t: t.get("original_index", 0)):
                turn_id = str(turn.get("turn_id", ""))
                role = str(turn.get("role", ""))
                content = str(turn.get("content", ""))
                content_h = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]
                canonical_turns.append(f"{turn_id}:{role}:{content_h}")
            raw_context = "|".join(canonical_turns)
            context_fp = hashlib.sha256(raw_context.encode("utf-8")).hexdigest()[:32]

        clean_model_id = (model_id or "unknown").strip().lower()
        clean_model_version = (model_version or "latest").strip().lower()
        clean_provider = (provider or "default").strip().lower()
        clean_tenant_id = (tenant_id or "default_tenant").strip()
        clean_user_id = (user_id or "default_user").strip()

        return cls(
            prompt_hash=prompt_h,
            context_fingerprint=context_fp,
            model_id=clean_model_id,
            model_version=clean_model_version,
            model_tier=model_tier,
            provider=clean_provider,
            schema_version=schema_version,
            tenant_id=clean_tenant_id,
            user_id=clean_user_id,
        )

    def compute_key(self) -> str:
        """Computes deterministic collision-resistant cache key string.
        
        Format: sqr:resp:v1:<tenant_id>:<user_id>:<model_tier>:<model_id>:<digest[:24]>
        """
        components = {
            "p": self.prompt_hash,
            "c": self.context_fingerprint,
            "m": self.model_id,
            "mv": self.model_version,
            "t": self.model_tier.value,
            "pr": self.provider,
            "s": self.schema_version,
            "ten": self.tenant_id,
            "u": self.user_id,
        }
        canonical_json = json.dumps(components, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        return f"sqr:resp:v1:{self.tenant_id}:{self.user_id}:{self.model_tier.value}:{self.model_id}:{digest[:24]}"


class CachedResponse(BaseModel):
    """Cached response entry storing model completion and provenance metadata."""
    model_config = ConfigDict(extra="forbid")

    content: str = PydanticField(..., description="Cached model output completion")
    model_id: str = PydanticField(..., description="Executed model identifier")
    model_version: str | None = PydanticField(default=None, description="Executed model version")
    model_tier: ModelTier = PydanticField(..., description="Capability tier")
    provider: str = PydanticField(default="default", description="Provider adapter name")
    tenant_id: str = PydanticField(default="default_tenant", description="Tenant/organization identifier")
    user_id: str = PydanticField(default="default_user", description="Caller user identifier")
    created_at: float = PydanticField(
        default_factory=time.time,
        description="Epoch timestamp in seconds when entry was cached"
    )
    expires_at: float = PydanticField(
        ...,
        description="Epoch timestamp in seconds when entry expires"
    )
    ttl_seconds: int = PydanticField(..., ge=1, description="TTL duration in seconds")
    hit_count: int = PydanticField(default=0, ge=0, description="Number of times served from cache")
    last_accessed_at: float = PydanticField(
        default_factory=time.time,
        description="Epoch timestamp in seconds when entry was last accessed"
    )
    metadata: dict[str, Any] = PydanticField(
        default_factory=dict,
        description="Non-sensitive execution and evaluation metadata"
    )

    def is_expired(self, now: float | None = None) -> bool:
        """Checks if the cached entry has expired based on current timestamp."""
        current_time = now if now is not None else time.time()
        return current_time >= self.expires_at


@dataclass
class CacheStats:
    """Operational statistics for response cache."""
    hits: int = 0
    misses: int = 0
    bypasses: int = 0
    sets: int = 0
    evictions: int = 0
    expirations: int = 0
    invalidations: int = 0
    current_entries: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        total_lookups = self.hits + self.misses
        hit_ratio = round(self.hits / total_lookups, 4) if total_lookups > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "bypasses": self.bypasses,
            "sets": self.sets,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "invalidations": self.invalidations,
            "current_entries": self.current_entries,
            "hit_ratio": hit_ratio,
        }


class BaseCacheStore(ABC):
    """Abstract interface for response cache storage implementations."""

    @abstractmethod
    async def get(self, key: str) -> CachedResponse | None:
        """Retrieve cached response by key. Returns None on miss or expiration."""
        pass

    @abstractmethod
    async def set(self, key: str, value: CachedResponse, ttl_seconds: int | None = None) -> None:
        """Store cached response under key with TTL."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete specific entry by key. Returns True if found and deleted."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all entries from cache."""
        pass

    @abstractmethod
    async def invalidate_by_model(self, model_id: str, model_version: str | None = None) -> int:
        """Invalidate all cached entries matching model identifier and optional version.
        
        Returns the number of invalidated entries.
        """
        pass

    @abstractmethod
    async def purge_expired(self) -> int:
        """Explicitly purge expired entries from storage. Returns count purged."""
        pass

    @abstractmethod
    async def get_stats(self) -> CacheStats:
        """Return operational cache statistics."""
        pass
