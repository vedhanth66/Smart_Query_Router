"""In-memory LRU response cache store with TTL expiration and invalidation rules."""

import asyncio
from collections import OrderedDict
import time
from typing import Any
from .base import BaseCacheStore, CachedResponse, CacheStats


class InMemoryCacheStore(BaseCacheStore):
    """Thread-safe and asyncio-safe in-memory cache store.
    
    Features:
    - Bounded LRU capacity eviction (OrderedDict).
    - Precise TTL expiration on read.
    - Explicit invalidation by key, model, and bulk purge.
    - Detailed operational statistics.
    """

    def __init__(self, max_entries: int = 5000):
        self.max_entries = max_entries
        self._entries: OrderedDict[str, CachedResponse] = OrderedDict()
        self._lock = asyncio.Lock()
        self._stats = CacheStats()

    async def get(self, key: str) -> CachedResponse | None:
        """Retrieve cached response by key. Returns None on miss or expiration."""
        async with self._lock:
            if key not in self._entries:
                self._stats.misses += 1
                return None

            entry = self._entries[key]
            now = time.time()
            if entry.is_expired(now):
                del self._entries[key]
                self._stats.expirations += 1
                self._stats.misses += 1
                return None

            # LRU promotion: move to end
            self._entries.move_to_end(key)
            entry.hit_count += 1
            entry.last_accessed_at = now
            self._stats.hits += 1
            return entry

    async def set(self, key: str, value: CachedResponse, ttl_seconds: int | None = None) -> None:
        """Store cached response under key with TTL."""
        async with self._lock:
            # If already exists, remove it first to re-insert at end
            if key in self._entries:
                del self._entries[key]

            # Bounded LRU eviction
            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)
                self._stats.evictions += 1

            self._entries[key] = value
            self._stats.sets += 1

    async def delete(self, key: str) -> bool:
        """Delete specific entry by key. Returns True if found and deleted."""
        async with self._lock:
            if key in self._entries:
                del self._entries[key]
                self._stats.invalidations += 1
                return True
            return False

    async def clear(self) -> None:
        """Clear all entries from cache."""
        async with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._stats.invalidations += count

    async def invalidate_by_model(self, model_id: str, model_version: str | None = None) -> int:
        """Invalidate all cached entries matching model identifier and optional version.
        
        Returns the number of invalidated entries.
        """
        clean_model = model_id.strip().lower()
        clean_version = model_version.strip().lower() if model_version else None

        async with self._lock:
            keys_to_remove = []
            for k, entry in self._entries.items():
                if entry.model_id.lower() == clean_model:
                    if clean_version is None or (entry.model_version and entry.model_version.lower() == clean_version):
                        keys_to_remove.append(k)

            for k in keys_to_remove:
                del self._entries[k]

            self._stats.invalidations += len(keys_to_remove)
            return len(keys_to_remove)

    async def purge_expired(self) -> int:
        """Explicitly purge expired entries from storage. Returns count purged."""
        now = time.time()
        async with self._lock:
            keys_to_purge = [k for k, v in self._entries.items() if v.is_expired(now)]
            for k in keys_to_purge:
                del self._entries[k]
            self._stats.expirations += len(keys_to_purge)
            return len(keys_to_purge)

    async def get_stats(self) -> CacheStats:
        """Return operational cache statistics."""
        async with self._lock:
            self._stats.current_entries = len(self._entries)
            # Return copy of stats
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                bypasses=self._stats.bypasses,
                sets=self._stats.sets,
                evictions=self._stats.evictions,
                expirations=self._stats.expirations,
                invalidations=self._stats.invalidations,
                current_entries=len(self._entries),
            )
