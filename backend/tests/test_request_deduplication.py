"""Tests for short-lived request deduplication, tenant/user isolation, and expiry."""

import asyncio
import time
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.cache import (
    InFlightDeduplicator,
    DeduplicationTimeoutError,
    default_deduplicator,
    default_response_cache,
    CacheKey,
    CachedResponse,
)
from app.gateway import default_gateway, GatewayRequest
from app.schemas.contract import (
    NormalizedQueryPackage,
    ClientMetadata,
    CoarseRoute,
    TaskCategory,
    ModelTier,
    CacheOutcome,
)


def create_test_package(
    query_text: str = "Explain quantum computing in detail",
    coarse_route: CoarseRoute = CoarseRoute.SIMPLE_MODEL_CANDIDATE,
    task_category: TaskCategory = TaskCategory.FACTUAL_QUESTION,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> NormalizedQueryPackage:
    return NormalizedQueryPackage(
        request_id=f"req-{time.time_ns()}",
        correlation_id=f"corr-{time.time_ns()}",
        coarse_route=coarse_route,
        task_category=task_category,
        query_text=query_text,
        client_metadata=ClientMetadata(
            extension_version="0.1.0",
            client_type="chrome_extension",
            schema_version="1.0",
            user_id=user_id,
            tenant_id=tenant_id,
        ),
        execute_route=True,
        user_id=user_id,
        tenant_id=tenant_id,
    )


# --------------------------------------------------------------------------
# Unit Tests for InFlightDeduplicator
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_dedup_key_generation_and_tenant_user_scoping():
    """Verify deduplication keys strictly incorporate tenant and user scopes."""
    dedup = InFlightDeduplicator()
    key_alice = dedup.generate_dedup_key("tenant_1", "alice", "sqr:resp:v1:hash1")
    key_bob = dedup.generate_dedup_key("tenant_1", "bob", "sqr:resp:v1:hash1")
    key_tenant2 = dedup.generate_dedup_key("tenant_2", "alice", "sqr:resp:v1:hash1")

    assert key_alice != key_bob
    assert key_alice != key_tenant2
    assert "alice" in key_alice
    assert "bob" in key_bob
    assert "tenant_2" in key_tenant2


@pytest.mark.anyio
async def test_concurrent_identical_requests_share_single_flight():
    """Verify simultaneous identical requests coalesce into a single execution."""
    dedup = InFlightDeduplicator(max_in_flight_seconds=5.0)
    call_count = 0

    async def mock_operation():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)  # Simulate model execution latency
        return {"result": "computed_value", "exec_count": call_count}

    key = dedup.generate_dedup_key("tenant_a", "user_1", "op_key_1")

    # Launch 5 concurrent calls
    tasks = [
        dedup.execute_or_join(key, mock_operation, timeout_seconds=2.0)
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    # Underlying operation must execute exactly once
    assert call_count == 1
    assert len(results) == 5

    # All 5 callers get identical result payload
    for r in results:
        assert r.data == {"result": "computed_value", "exec_count": 1}
        assert r.is_deduplicated is True
        assert r.waiters_count == 5

    # Exactly one leader and four followers
    leaders = [r for r in results if r.is_leader]
    followers = [r for r in results if not r.is_leader]
    assert len(leaders) == 1
    assert len(followers) == 4

    # Stats tracking
    stats = await dedup.get_stats()
    assert stats["leaders"] == 1
    assert stats["waiters"] == 4
    assert stats["active_flights"] == 0  # Cleaned up after completion


@pytest.mark.anyio
async def test_different_users_do_not_share_in_flight_operation():
    """Verify simultaneous requests from different users execute independently."""
    dedup = InFlightDeduplicator()
    call_count = 0

    async def mock_operation():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return f"result_{call_count}"

    key_alice = dedup.generate_dedup_key("tenant_1", "alice", "shared_prompt_hash")
    key_bob = dedup.generate_dedup_key("tenant_1", "bob", "shared_prompt_hash")

    # Alice and Bob run concurrently with identical prompt
    r_alice, r_bob = await asyncio.gather(
        dedup.execute_or_join(key_alice, mock_operation, timeout_seconds=2.0),
        dedup.execute_or_join(key_bob, mock_operation, timeout_seconds=2.0),
    )

    # Must execute twice: once for Alice, once for Bob
    assert call_count == 2
    assert r_alice.is_leader is True
    assert r_bob.is_leader is True
    assert r_alice.is_deduplicated is False
    assert r_bob.is_deduplicated is False
    assert r_alice.key != r_bob.key


@pytest.mark.anyio
async def test_different_tenants_do_not_share_in_flight_operation():
    """Verify simultaneous requests from different tenants execute independently."""
    dedup = InFlightDeduplicator()
    call_count = 0

    async def mock_operation():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "tenant_result"

    key_t1 = dedup.generate_dedup_key("tenant_alpha", "user_1", "hash")
    key_t2 = dedup.generate_dedup_key("tenant_beta", "user_1", "hash")

    r_t1, r_t2 = await asyncio.gather(
        dedup.execute_or_join(key_t1, mock_operation),
        dedup.execute_or_join(key_t2, mock_operation),
    )

    assert call_count == 2
    assert r_t1.is_leader is True
    assert r_t2.is_leader is True


@pytest.mark.anyio
async def test_deduplicator_timeout_raises_and_cleans_up():
    """Verify bounded timeout halts waiting callers and records telemetry."""
    dedup = InFlightDeduplicator(max_in_flight_seconds=0.2)

    async def slow_operation():
        await asyncio.sleep(1.0)
        return "slow_done"

    key = dedup.generate_dedup_key("t", "u", "slow_key")

    with pytest.raises(DeduplicationTimeoutError) as exc_info:
        await dedup.execute_or_join(key, slow_operation, timeout_seconds=0.05)

    assert "timed out after 0.05s" in str(exc_info.value)
    stats = await dedup.get_stats()
    assert stats["timeouts"] >= 1

    # Clean up background task
    await dedup.clear()


@pytest.mark.anyio
async def test_deduplicator_error_propagation_to_all_waiters():
    """Verify if the leader task fails, all waiting followers receive the exception."""
    dedup = InFlightDeduplicator()
    exec_count = 0

    async def failing_operation():
        nonlocal exec_count
        exec_count += 1
        await asyncio.sleep(0.05)
        raise RuntimeError("Gateway downstream failure")

    key = dedup.generate_dedup_key("t", "u", "fail_key")

    results = await asyncio.gather(
        dedup.execute_or_join(key, failing_operation, timeout_seconds=2.0),
        dedup.execute_or_join(key, failing_operation, timeout_seconds=2.0),
        return_exceptions=True
    )

    assert exec_count == 1
    assert len(results) == 2
    assert isinstance(results[0], RuntimeError)
    assert isinstance(results[1], RuntimeError)
    assert str(results[0]) == "Gateway downstream failure"
    assert str(results[1]) == "Gateway downstream failure"

    stats = await dedup.get_stats()
    assert stats["errors"] >= 1
    assert stats["active_flights"] == 0


# --------------------------------------------------------------------------
# Cache Isolation Tests
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cache_keys_and_stored_content_strictly_isolated_by_user():
    """Verify that User A's cached response is never accessible to User B."""
    await default_response_cache.clear()

    pkg_alice = create_test_package(query_text="Summarize python async", user_id="alice", tenant_id="corp")
    pkg_bob = create_test_package(query_text="Summarize python async", user_id="bob", tenant_id="corp")

    # Cache response for Alice
    cache_key_alice = await default_response_cache.store_response(
        package=pkg_alice,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        content="Alice's private summary",
        tenant_id="corp",
        user_id="alice",
    )
    assert cache_key_alice is not None
    assert "alice" in cache_key_alice

    # Bob checks cache for identical prompt and model
    cached_bob, outcome_bob, _, key_bob = await default_response_cache.check_cache(
        package=pkg_bob,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        tenant_id="corp",
        user_id="bob",
    )

    # Bob MUST get a MISS! Alice's cached response must not leak
    assert outcome_bob == CacheOutcome.MISS
    assert cached_bob is None
    assert "bob" in key_bob
    assert key_bob != cache_key_alice

    # Alice checks cache -> HIT!
    cached_alice, outcome_alice, _, key_alice = await default_response_cache.check_cache(
        package=pkg_alice,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        tenant_id="corp",
        user_id="alice",
    )
    assert outcome_alice == CacheOutcome.HIT
    assert cached_alice is not None
    assert cached_alice.content == "Alice's private summary"


# --------------------------------------------------------------------------
# End-to-End API Deduplication Tests (/api/v1/optimize)
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_api_simultaneous_identical_requests_share_flight(monkeypatch):
    """Verify simultaneous API calls from same user share one gateway execution."""
    await default_response_cache.clear()
    await default_deduplicator.clear()

    # Add small delay to gateway execute so concurrent requests reliably overlap in flight
    orig_execute = default_gateway.execute
    async def delayed_execute(req):
        await asyncio.sleep(0.06)
        return await orig_execute(req)
    monkeypatch.setattr(default_gateway, "execute", delayed_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "request_id": "req-shared-1",
            "correlation_id": "corr-shared-1",
            "coarse_route": CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
            "task_category": TaskCategory.FACTUAL_QUESTION.value,
            "query_text": "What is the capital of Australia and why was it chosen?",
            "client_metadata": {
                "extension_version": "0.1.0",
                "client_type": "chrome_extension",
                "schema_version": "1.0",
            },
            "execute_route": True,
        }

        # Send 3 concurrent POST requests with same X-User-ID
        headers = {
            "X-User-ID": "user-coalesce-test",
            "X-Tenant-ID": "tenant-coalesce-test",
        }

        responses = await asyncio.gather(
            client.post("/api/v1/optimize", json={**payload, "request_id": "req-1"}, headers=headers),
            client.post("/api/v1/optimize", json={**payload, "request_id": "req-2"}, headers=headers),
            client.post("/api/v1/optimize", json={**payload, "request_id": "req-3"}, headers=headers),
        )

        assert all(r.status_code == 200 for r in responses)
        data = [r.json() for r in responses]

        # Verify all received the executed content
        content_0 = data[0]["execution_metadata"]["executed_content"]
        assert content_0 is not None
        for d in data:
            assert d["execution_metadata"]["executed_content"] == content_0

        # Verify deduplication metadata: at least 1 leader and followers
        roles = [d["execution_metadata"]["deduplication_role"] for d in data]
        assert "leader" in roles
        assert "follower" in roles
        assert any(d["execution_metadata"]["is_deduplicated"] is True for d in data)


@pytest.mark.anyio
async def test_api_concurrent_different_users_do_not_share_flight():
    """Verify simultaneous API calls from different users execute independently."""
    await default_response_cache.clear()
    await default_deduplicator.clear()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "request_id": "req-diff-user",
            "coarse_route": CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
            "task_category": TaskCategory.FACTUAL_QUESTION.value,
            "query_text": "Describe the process of cellular respiration in cells",
            "client_metadata": {
                "extension_version": "0.1.0",
                "client_type": "chrome_extension",
                "schema_version": "1.0",
            },
            "execute_route": True,
        }

        # Concurrent requests with distinct user IDs
        r_alice, r_bob = await asyncio.gather(
            client.post("/api/v1/optimize", json=payload, headers={"X-User-ID": "alice-unique"}),
            client.post("/api/v1/optimize", json=payload, headers={"X-User-ID": "bob-unique"}),
        )

        assert r_alice.status_code == 200
        assert r_bob.status_code == 200

        data_alice = r_alice.json()
        data_bob = r_bob.json()

        # Both must be leaders of their own independent flights
        assert data_alice["execution_metadata"]["deduplication_role"] == "leader"
        assert data_bob["execution_metadata"]["deduplication_role"] == "leader"
        assert data_alice["execution_metadata"]["is_deduplicated"] is False
        assert data_bob["execution_metadata"]["is_deduplicated"] is False


@pytest.mark.anyio
async def test_api_dedup_stats_and_clear_endpoints():
    """Test /api/v1/dedup/stats and /api/v1/dedup/clear endpoints."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_stats = await client.get("/api/v1/dedup/stats")
        assert r_stats.status_code == 200
        stats = r_stats.json()
        assert "active_flights" in stats
        assert "leaders" in stats
        assert "waiters" in stats
        assert "coalesce_ratio" in stats

        r_clear = await client.post("/api/v1/dedup/clear")
        assert r_clear.status_code == 200
        assert r_clear.json()["status"] == "ok"


@pytest.mark.anyio
async def test_subsequent_request_hits_cache_after_flight_completes(monkeypatch):
    """Verify concurrent requests share flight, and subsequent identical request hits cache."""
    await default_response_cache.clear()
    await default_deduplicator.clear()

    orig_execute = default_gateway.execute
    async def delayed_execute(req):
        await asyncio.sleep(0.05)
        return await orig_execute(req)
    monkeypatch.setattr(default_gateway, "execute", delayed_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "request_id": "req-seq-1",
            "coarse_route": CoarseRoute.SIMPLE_MODEL_CANDIDATE.value,
            "task_category": TaskCategory.FACTUAL_QUESTION.value,
            "query_text": "What is photosynthesis and why does it matter?",
            "client_metadata": {
                "extension_version": "0.1.0",
                "client_type": "chrome_extension",
                "schema_version": "1.0",
            },
            "execute_route": True,
        }
        headers = {"X-User-ID": "user-sequence-test"}

        # Phase 1: Two simultaneous requests coalesce
        r1, r2 = await asyncio.gather(
            client.post("/api/v1/optimize", json={**payload, "request_id": "req-seq-1"}, headers=headers),
            client.post("/api/v1/optimize", json={**payload, "request_id": "req-seq-2"}, headers=headers),
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["execution_metadata"]["executed_content"] == r2.json()["execution_metadata"]["executed_content"]

        # Phase 2: Third request from SAME user arrives sequentially -> Cache HIT!
        r3 = await client.post("/api/v1/optimize", json={**payload, "request_id": "req-seq-3"}, headers=headers)
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["execution_metadata"]["cache_outcome"] == "HIT"
        assert d3["execution_metadata"]["executed_content"] == r1.json()["execution_metadata"]["executed_content"]
        assert d3["execution_metadata"]["is_deduplicated"] is False

        # Phase 3: Same query from a DIFFERENT user -> Cache MISS! (Isolation preserved)
        r4 = await client.post("/api/v1/optimize", json={**payload, "request_id": "req-seq-4"}, headers={"X-User-ID": "other-user"})
        assert r4.status_code == 200
        d4 = r4.json()
        assert d4["execution_metadata"]["cache_outcome"] == "MISS"


@pytest.mark.anyio
async def test_concurrent_complex_model_requests_share_flight(monkeypatch):
    """Verify simultaneous requests routed to complex model coalesce as well."""
    await default_response_cache.clear()
    await default_deduplicator.clear()

    orig_execute = default_gateway.execute
    async def delayed_execute(req):
        await asyncio.sleep(0.05)
        return await orig_execute(req)
    monkeypatch.setattr(default_gateway, "execute", delayed_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "request_id": "req-complex-1",
            "coarse_route": CoarseRoute.COMPLEX_MODEL_CANDIDATE.value,
            "task_category": TaskCategory.CODING.value,
            "query_text": "Write a high-performance concurrent queue in Go with lock-free ring buffer",
            "client_metadata": {
                "extension_version": "0.1.0",
                "client_type": "chrome_extension",
                "schema_version": "1.0",
            },
            "execute_route": True,
        }
        headers = {"X-User-ID": "dev-user-1"}

        r1, r2 = await asyncio.gather(
            client.post("/api/v1/optimize", json={**payload, "request_id": "req-c1"}, headers=headers),
            client.post("/api/v1/optimize", json={**payload, "request_id": "req-c2"}, headers=headers),
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        d1 = r1.json()
        d2 = r2.json()

        assert d1["execution_metadata"]["executed_content"] == d2["execution_metadata"]["executed_content"]
        roles = {d1["execution_metadata"]["deduplication_role"], d2["execution_metadata"]["deduplication_role"]}
        assert roles == {"leader", "follower"}


@pytest.mark.anyio
async def test_purge_expired_flights_cleans_stale_tasks():
    """Verify purge_expired evicts tasks exceeding max_in_flight_seconds."""
    dedup = InFlightDeduplicator(max_in_flight_seconds=0.01)

    async def perpetual_task():
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            pass

    key = dedup.generate_dedup_key("t", "u", "perpetual")
    # Launch in background
    asyncio.create_task(dedup.execute_or_join(key, perpetual_task, timeout_seconds=5.0))
    await asyncio.sleep(0.03)

    # Purge expired
    purged_count = await dedup.purge_expired()
    assert purged_count >= 1

    stats = await dedup.get_stats()
    assert stats["evictions"] >= 1
    assert stats["active_flights"] == 0
    await dedup.clear()

