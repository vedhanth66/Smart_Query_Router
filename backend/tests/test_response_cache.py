"""Tests for Exact-Match Response Cache subsystem.

Verifies:
1. CacheKey determinism and canonical whitespace normalization.
2. CacheKey sensitivity to prompt, context turns, model ID, and model version.
3. Cache hit and miss lifecycle (miss -> store -> hit).
4. Strict bypass of time-sensitive queries by default (e.g. current time, today's date).
5. Explicit policy override allowing caching of time-sensitive tasks.
6. TTL expiration and automatic eviction on read.
7. Explicit invalidation rules (by key, by model/version, and clear).
8. Bounded LRU capacity eviction.
9. Storage interface pluggability (custom BaseCacheStore).
10. End-to-end integration with FastAPI /api/v1/optimize endpoint.
11. Diagnostic endpoints /api/v1/cache/stats and /api/v1/cache/clear.
"""

import time
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.contract import (
    NormalizedQueryPackage,
    ContextCandidateTurn,
    ClientMetadata,
    CoarseRoute,
    TaskCategory,
    ModelTier,
    CacheOutcome,
)
from app.cache import (
    BaseCacheStore,
    CacheKey,
    CachedResponse,
    CacheStats,
    InMemoryCacheStore,
    CachePolicy,
    CachePolicyConfig,
    ResponseCacheManager,
    default_response_cache,
)

client = TestClient(app)

BASE_CLIENT_METADATA = ClientMetadata(
    extension_version="0.1.0",
    client_type="chrome_extension",
    schema_version="1.0",
    hostname="claude.ai",
)


@pytest.fixture(autouse=True)
def clear_cache_before_each_test():
    """Ensure a clean cache before and after every test."""
    if hasattr(default_response_cache.store, "_entries"):
        default_response_cache.store._entries.clear()
    yield
    if hasattr(default_response_cache.store, "_entries"):
        default_response_cache.store._entries.clear()


# --- 1. CacheKey Determinism and Sensitivity Tests ---

def test_cache_key_determinism_and_whitespace_normalization():
    """Verify identical inputs (and whitespace variations) produce the exact same cache key."""
    key1 = CacheKey.from_inputs(
        prompt="Explain PostgreSQL MVCC in detail.",
        context_turns=None,
        model_id="gpt-4o-mini-2024-07-18",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )

    key2 = CacheKey.from_inputs(
        prompt="  Explain   PostgreSQL   MVCC in   detail.  ",
        context_turns=None,
        model_id="gpt-4o-mini-2024-07-18",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )

    assert key1.compute_key() == key2.compute_key()
    assert "fast_cheap" in key1.compute_key()
    assert "gpt-4o-mini-2024-07-18" in key1.compute_key()


def test_cache_key_sensitivity_to_prompt():
    """Verify different prompts produce strictly different cache keys."""
    key_a = CacheKey.from_inputs(
        prompt="What is SQLite?",
        context_turns=None,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )
    key_b = CacheKey.from_inputs(
        prompt="What is PostgreSQL?",
        context_turns=None,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )

    assert key_a.compute_key() != key_b.compute_key()


def test_cache_key_sensitivity_to_context_turns():
    """Verify changing context candidates produces a distinct cache key."""
    turns1 = [
        {"turn_id": "t1", "role": "user", "content": "Hello", "original_index": 0}
    ]
    turns2 = [
        {"turn_id": "t1", "role": "user", "content": "Different context", "original_index": 0}
    ]

    key_no_context = CacheKey.from_inputs(
        prompt="Summarize this",
        context_turns=None,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )
    key_ctx1 = CacheKey.from_inputs(
        prompt="Summarize this",
        context_turns=turns1,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )
    key_ctx2 = CacheKey.from_inputs(
        prompt="Summarize this",
        context_turns=turns2,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )

    assert key_no_context.compute_key() != key_ctx1.compute_key()
    assert key_ctx1.compute_key() != key_ctx2.compute_key()


def test_cache_key_sensitivity_to_model_version():
    """Verify updating model version changes key to avoid returning incompatible results."""
    key_v1 = CacheKey.from_inputs(
        prompt="Write a fibonacci function",
        context_turns=None,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )
    key_v2 = CacheKey.from_inputs(
        prompt="Write a fibonacci function",
        context_turns=None,
        model_id="gpt-4o-mini",
        model_version="2024-11-20",  # Upgraded model release
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )

    assert key_v1.compute_key() != key_v2.compute_key()


def test_cache_key_sensitivity_to_model_tier_and_id():
    """Verify different model tier or model ID changes key."""
    key_small = CacheKey.from_inputs(
        prompt="Explain gravity",
        context_turns=None,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        model_tier=ModelTier.FAST_CHEAP,
        provider="small_model",
    )
    key_strong = CacheKey.from_inputs(
        prompt="Explain gravity",
        context_turns=None,
        model_id="gpt-4o",
        model_version="2024-07-18",
        model_tier=ModelTier.STRONG,
        provider="strong_model",
    )

    assert key_small.compute_key() != key_strong.compute_key()


# --- 2. Cache Hit & Miss Lifecycle Tests ---

@pytest.mark.anyio
async def test_cache_hit_and_miss_lifecycle():
    """Verify initial lookup misses, storing succeeds, and subsequent lookup hits."""
    manager = ResponseCacheManager(store=InMemoryCacheStore(), policy=CachePolicy())

    package = NormalizedQueryPackage(
        request_id="req_test_cache_01",
        query_text="What is the boiling point of water?",
        client_metadata=BASE_CLIENT_METADATA,
    )

    # 1. First check: Cache MISS
    cached, outcome, bypass_reason, key = await manager.check_cache(
        package=package,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
    )
    assert cached is None
    assert outcome == CacheOutcome.MISS
    assert key is not None

    # 2. Store response
    stored_key = await manager.store_response(
        package=package,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        content="Water boils at 100 degrees Celsius at standard atmospheric pressure.",
        metadata={"source": "physics_fact"},
        ttl_seconds=3600,
    )
    assert stored_key == key

    # 3. Second check: Cache HIT
    cached_hit, outcome_hit, _, _ = await manager.check_cache(
        package=package,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
    )
    assert outcome_hit == CacheOutcome.HIT
    assert cached_hit is not None
    assert "100 degrees Celsius" in cached_hit.content
    assert cached_hit.hit_count == 1
    assert cached_hit.metadata["source"] == "physics_fact"

    # Verify operational stats
    stats = await manager.get_stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 1
    assert stats["sets"] == 1
    assert stats["current_entries"] == 1


# --- 3. Time-Sensitivity Policy & Bypass Tests ---

@pytest.mark.anyio
async def test_time_sensitive_query_bypasses_cache_by_default():
    """Verify queries asking for current time or date strictly bypass cache by default."""
    manager = ResponseCacheManager()

    time_queries = [
        "What time is it right now?",
        "What is the current time in UTC?",
        "Tell me today's date",
        "What day is it today?",
        "What is the weather today?",
        "What is the current stock price of Apple?",
    ]

    for q in time_queries:
        pkg = NormalizedQueryPackage(
            request_id=f"req_time_{hash(q)}",
            query_text=q,
            client_metadata=BASE_CLIENT_METADATA,
        )

        cached, outcome, bypass_reason, key = await manager.check_cache(
            package=pkg,
            tier=ModelTier.FAST_CHEAP,
            model_id="gpt-4o-mini",
            model_version="2024-07-18",
            provider="small_model",
        )

        assert cached is None
        assert outcome == CacheOutcome.BYPASS
        assert "TIME_SENSITIVE_TASK" in str(bypass_reason)

        # Store should also refuse to store
        stored = await manager.store_response(
            package=pkg,
            tier=ModelTier.FAST_CHEAP,
            model_id="gpt-4o-mini",
            model_version="2024-07-18",
            provider="small_model",
            content="It is currently 10:30 PM.",
        )
        assert stored is None


@pytest.mark.anyio
async def test_time_sensitive_query_allowed_when_policy_explicitly_allows():
    """Verify time-sensitive queries are cacheable when policy explicitly enables it."""
    config = CachePolicyConfig(allow_time_sensitive=True)
    policy = CachePolicy(config=config)
    manager = ResponseCacheManager(store=InMemoryCacheStore(), policy=policy)

    pkg = NormalizedQueryPackage(
        request_id="req_allow_ts",
        query_text="What time is it right now?",
        client_metadata=BASE_CLIENT_METADATA,
    )

    cached, outcome, bypass_reason, key = await manager.check_cache(
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
    )
    assert outcome == CacheOutcome.MISS  # Eligible for cache lookup!
    assert bypass_reason is None

    stored = await manager.store_response(
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        content="Current timestamp: 1710000000",
    )
    assert stored is not None

    hit, hit_outcome, _, _ = await manager.check_cache(
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
    )
    assert hit_outcome == CacheOutcome.HIT
    assert hit.content == "Current timestamp: 1710000000"


# --- 4. TTL Expiration Tests ---

@pytest.mark.anyio
async def test_ttl_expiration_behavior():
    """Verify expired entries return None and are automatically evicted on read."""
    store = InMemoryCacheStore()
    manager = ResponseCacheManager(store=store)

    pkg = NormalizedQueryPackage(
        request_id="req_ttl_test",
        query_text="Explain quantum entanglement briefly",
        client_metadata=BASE_CLIENT_METADATA,
    )

    # Store with effective TTL of 1 second
    key = await manager.store_response(
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        content="Quantum entanglement is a physical phenomenon...",
        ttl_seconds=1,
    )

    # Immediate lookup: HIT
    hit, outcome, _, _ = await manager.check_cache(
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
    )
    assert outcome == CacheOutcome.HIT
    assert hit is not None

    # Simulate expiration by adjusting expires_at into the past
    raw_entry = store._entries[key]
    store._entries[key] = raw_entry.model_copy(update={"expires_at": time.time() - 5.0})

    # Lookup after expiration: MISS and evicted from memory
    expired_hit, expired_outcome, _, _ = await manager.check_cache(
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
    )
    assert expired_outcome == CacheOutcome.MISS
    assert expired_hit is None
    assert key not in store._entries

    stats = await store.get_stats()
    assert stats.expirations == 1


# --- 5. Cache Invalidation Rules ---

@pytest.mark.anyio
async def test_cache_invalidation_by_key():
    """Verify explicit key deletion."""
    store = InMemoryCacheStore()
    manager = ResponseCacheManager(store=store)

    pkg = NormalizedQueryPackage(
        request_id="req_inv_key",
        query_text="What is a binary search tree?",
        client_metadata=BASE_CLIENT_METADATA,
    )

    key = await manager.store_response(
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        content="A binary search tree is a rooted binary tree data structure.",
    )

    assert await manager.delete(key) is True
    assert await manager.delete(key) is False  # Already deleted

    hit, outcome, _, _ = await manager.check_cache(
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
    )
    assert outcome == CacheOutcome.MISS


@pytest.mark.anyio
async def test_cache_invalidation_by_model_version():
    """Verify bulk invalidation by model release version."""
    store = InMemoryCacheStore()
    manager = ResponseCacheManager(store=store)

    # 2 entries for gpt-4o-mini version 2024-07-18
    pkg1 = NormalizedQueryPackage(request_id="r1", query_text="Query one", client_metadata=BASE_CLIENT_METADATA)
    pkg2 = NormalizedQueryPackage(request_id="r2", query_text="Query two", client_metadata=BASE_CLIENT_METADATA)
    # 1 entry for gpt-4o version 2024-07-18
    pkg3 = NormalizedQueryPackage(request_id="r3", query_text="Query three", client_metadata=BASE_CLIENT_METADATA)

    await manager.store_response(pkg1, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model", "Resp 1")
    await manager.store_response(pkg2, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model", "Resp 2")
    await manager.store_response(pkg3, ModelTier.STRONG, "gpt-4o", "2024-07-18", "strong_model", "Resp 3")

    assert len(store._entries) == 3

    # Invalidate all gpt-4o-mini entries for version 2024-07-18
    invalidated_count = await manager.invalidate_by_model("gpt-4o-mini", "2024-07-18")
    assert invalidated_count == 2
    assert len(store._entries) == 1

    # gpt-4o entry remains
    hit, outcome, _, _ = await manager.check_cache(pkg3, ModelTier.STRONG, "gpt-4o", "2024-07-18", "strong_model")
    assert outcome == CacheOutcome.HIT
    assert hit.content == "Resp 3"


@pytest.mark.anyio
async def test_cache_clear():
    """Verify clear empties cache completely."""
    store = InMemoryCacheStore()
    manager = ResponseCacheManager(store=store)

    for i in range(5):
        pkg = NormalizedQueryPackage(request_id=f"r_{i}", query_text=f"Prompt number {i}", client_metadata=BASE_CLIENT_METADATA)
        await manager.store_response(pkg, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model", f"Content {i}")

    assert len(store._entries) == 5
    await manager.clear()
    assert len(store._entries) == 0


# --- 6. Clean Interface & Storage Pluggability ---

@pytest.mark.anyio
async def test_clean_storage_interface_pluggability():
    """Verify custom store implementation works seamlessly behind BaseCacheStore abstraction."""
    class CustomMockStore(BaseCacheStore):
        def __init__(self):
            self.backend_map = {}
            self.get_calls = 0

        async def get(self, key: str) -> CachedResponse | None:
            self.get_calls += 1
            return self.backend_map.get(key)

        async def set(self, key: str, value: CachedResponse, ttl_seconds: int | None = None) -> None:
            self.backend_map[key] = value

        async def delete(self, key: str) -> bool:
            return self.backend_map.pop(key, None) is not None

        async def clear(self) -> None:
            self.backend_map.clear()

        async def invalidate_by_model(self, model_id: str, model_version: str | None = None) -> int:
            to_del = [k for k, v in self.backend_map.items() if v.model_id == model_id]
            for k in to_del:
                del self.backend_map[k]
            return len(to_del)

        async def purge_expired(self) -> int:
            return 0

        async def get_stats(self) -> CacheStats:
            return CacheStats(current_entries=len(self.backend_map))

    custom_store = CustomMockStore()
    manager = ResponseCacheManager(store=custom_store)

    pkg = NormalizedQueryPackage(request_id="r_plug", query_text="Custom store test", client_metadata=BASE_CLIENT_METADATA)
    key = await manager.store_response(pkg, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model", "Custom stored content")

    assert key in custom_store.backend_map
    hit, outcome, _, _ = await manager.check_cache(pkg, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model")
    assert outcome == CacheOutcome.HIT
    assert custom_store.get_calls == 1
    assert hit.content == "Custom stored content"


# --- 7. Bounded LRU Capacity Eviction ---

@pytest.mark.anyio
async def test_bounded_lru_capacity_eviction():
    """Verify least-recently-used item is evicted when max capacity is reached."""
    store = InMemoryCacheStore(max_entries=3)
    manager = ResponseCacheManager(store=store)

    pkg1 = NormalizedQueryPackage(request_id="r1", query_text="Prompt 1", client_metadata=BASE_CLIENT_METADATA)
    pkg2 = NormalizedQueryPackage(request_id="r2", query_text="Prompt 2", client_metadata=BASE_CLIENT_METADATA)
    pkg3 = NormalizedQueryPackage(request_id="r3", query_text="Prompt 3", client_metadata=BASE_CLIENT_METADATA)
    pkg4 = NormalizedQueryPackage(request_id="r4", query_text="Prompt 4", client_metadata=BASE_CLIENT_METADATA)

    k1 = await manager.store_response(pkg1, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model", "C1")
    k2 = await manager.store_response(pkg2, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model", "C2")
    k3 = await manager.store_response(pkg3, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model", "C3")

    # Access pkg1 so pkg2 becomes the oldest (LRU)
    await manager.check_cache(pkg1, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model")

    # Insert 4th entry: should evict pkg2
    k4 = await manager.store_response(pkg4, ModelTier.FAST_CHEAP, "gpt-4o-mini", "2024-07-18", "small_model", "C4")

    assert len(store._entries) == 3
    assert k2 not in store._entries
    assert k1 in store._entries
    assert k3 in store._entries
    assert k4 in store._entries


# --- 8. API Integration Tests (/api/v1/optimize) ---

def test_api_optimize_endpoint_cache_hit_and_miss():
    """Verify duplicate identical query receives MISS then HIT with near-zero latency."""
    payload = {
        "request_id": "req_api_cache_test_01",
        "correlation_id": "corr_api_cache_test_01",
        "query_text": "What is the primary function of an operating system kernel?",
        "coarse_route": "simple-model candidate",
        "execute_route": True,
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "hostname": "claude.ai",
        },
    }

    # Call 1: Cache MISS
    resp1 = client.post("/api/v1/optimize", json=payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    exec_meta1 = data1["execution_metadata"]
    assert exec_meta1["cache_outcome"] == "MISS"
    assert exec_meta1["cache_key"] is not None
    assert exec_meta1["latency_ms"] >= 0.0

    # Call 2: Cache HIT
    payload2 = dict(payload)
    payload2["request_id"] = "req_api_cache_test_02"
    resp2 = client.post("/api/v1/optimize", json=payload2)
    assert resp2.status_code == 200
    data2 = resp2.json()
    exec_meta2 = data2["execution_metadata"]

    assert exec_meta2["cache_outcome"] == "HIT"
    assert exec_meta2["cache_key"] == exec_meta1["cache_key"]
    assert exec_meta2["executed_content"] == exec_meta1["executed_content"]
    assert exec_meta2["evaluation_metadata"]["cached"] is True
    assert exec_meta2["evaluation_metadata"]["hit_count"] == 1


def test_api_optimize_time_sensitive_query_bypasses_cache():
    """Verify time-sensitive query executed via API receives CacheOutcome.BYPASS."""
    payload = {
        "request_id": "req_api_ts_01",
        "correlation_id": "corr_api_ts_01",
        "query_text": "What is the current time right now?",
        "coarse_route": "simple-model candidate",
        "execute_route": True,
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "hostname": "claude.ai",
        },
    }

    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    exec_meta = data["execution_metadata"]

    assert exec_meta["cache_outcome"] == "BYPASS"


def test_api_cache_stats_and_clear_endpoints():
    """Verify /api/v1/cache/stats and /api/v1/cache/clear endpoints."""
    # 1. Stats endpoint
    stats_resp = client.get("/api/v1/cache/stats")
    assert stats_resp.status_code == 200
    stats_data = stats_resp.json()
    assert "hits" in stats_data
    assert "misses" in stats_data
    assert "current_entries" in stats_data

    # 2. Clear endpoint
    clear_resp = client.post("/api/v1/cache/clear")
    assert clear_resp.status_code == 200
    clear_data = clear_resp.json()
    assert clear_data["status"] == "ok"

    # 3. Stats after clear
    stats_resp_after = client.get("/api/v1/cache/stats")
    assert stats_resp_after.json()["current_entries"] == 0
