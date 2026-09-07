"""Tests for Centralized Category-Based and Time-Sensitivity TTL Selection Policy.

Verifies:
1. Static facts (factual questions, translation, arithmetic, greetings) receive long TTL (24h / 86400s).
2. Standard categories (coding, summarization, rewriting, analysis, reasoning) receive standard TTL (1h / 3600s).
3. Dynamic topics (debugging, status/version keywords, allowed time-sensitive queries) receive short TTL (5m / 300s).
4. Real-time time-sensitive queries receive 0s TTL and NO_CACHE bypass when allow_time_sensitive is False.
5. Granular category overrides and custom configuration options work as expected.
6. Environment variable overrides for TTL thresholds.
7. Response cache storage and API integration correctly apply the category TTL to expires_at and ttl_remaining_s.
"""

import os
import time
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.contract import (
    NormalizedQueryPackage,
    ClientMetadata,
    TaskCategory,
    ModelTier,
    CacheOutcome,
)
from app.cache import (
    CachePolicy,
    CachePolicyConfig,
    CacheDecision,
    TTLCategory,
    TTLPolicyConfig,
    TTLPolicy,
    ResponseCacheManager,
    InMemoryCacheStore,
    default_response_cache,
    default_deduplicator,
    default_semantic_cache,
)


client = TestClient(app)


def make_package(
    query_text: str,
    task_category: TaskCategory = TaskCategory.FACTUAL_QUESTION,
    user_id: str = "alice",
    tenant_id: str = "corp",
    request_id: str = "req-ttl-test",
) -> NormalizedQueryPackage:
    return NormalizedQueryPackage(
        request_id=request_id,
        query_text=query_text,
        task_category=task_category,
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


# ==============================================================================
# 1. Category-Based TTL Selection Tests
# ==============================================================================

def test_static_facts_assigned_long_ttl():
    """Static facts (factual, translation, arithmetic, greeting) must receive 24-hour TTL."""
    policy = CachePolicy()

    static_cases = [
        ("What is the capital of Australia?", TaskCategory.FACTUAL_QUESTION),
        ("Translate good morning into Japanese", TaskCategory.TRANSLATION),
        ("Calculate 25 multiplied by 40", TaskCategory.ARITHMETIC),
        ("Hello there, how are you?", TaskCategory.GREETING),
    ]

    for query, cat in static_cases:
        pkg = make_package(query_text=query, task_category=cat)
        decision = policy.evaluate(pkg)
        assert decision.is_cacheable is True
        assert decision.ttl_seconds == 86_400, f"Query '{query}' expected 86400s TTL, got {decision.ttl_seconds}"
        assert decision.ttl_category == TTLCategory.STATIC_FACTS
        assert "STATIC_FACTS" in decision.ttl_reason


def test_standard_categories_assigned_standard_ttl():
    """General tasks (coding, summarization, rewriting, reasoning) must receive 1-hour TTL."""
    policy = CachePolicy()

    standard_cases = [
        ("Implement quicksort algorithm in Python", TaskCategory.CODING),
        ("Summarize the main themes of Hamlet", TaskCategory.SUMMARIZATION),
        ("Rewrite this paragraph to be more concise", TaskCategory.REWRITING),
        ("Compare relational databases with document stores", TaskCategory.COMPARISON),
        ("Analyze the time complexity of dijkstra algorithm", TaskCategory.ANALYSIS),
        ("Explain the trade-offs of microservices architecture", TaskCategory.REASONING),
    ]

    for query, cat in standard_cases:
        pkg = make_package(query_text=query, task_category=cat)
        decision = policy.evaluate(pkg)
        assert decision.is_cacheable is True
        assert decision.ttl_seconds == 3_600, f"Query '{query}' expected 3600s TTL, got {decision.ttl_seconds}"
        assert decision.ttl_category == TTLCategory.STANDARD
        assert "STANDARD" in decision.ttl_reason


def test_dynamic_topics_and_debugging_assigned_short_ttl():
    """Dynamic tasks (debugging, status/health/release keywords) must receive short 5-minute TTL."""
    policy = CachePolicy()

    # 1. Debugging task category
    pkg_debug = make_package(
        query_text="Why is my connection pooling throwing connection reset by peer?",
        task_category=TaskCategory.DEBUGGING,
    )
    dec_debug = policy.evaluate(pkg_debug)
    assert dec_debug.is_cacheable is True
    assert dec_debug.ttl_seconds == 300
    assert dec_debug.ttl_category == TTLCategory.DYNAMIC
    assert "DYNAMIC" in dec_debug.ttl_reason

    # 2. Dynamic keywords in query text (status, uptime, current version, latest release)
    dynamic_kw_queries = [
        ("What is the current server status?", TaskCategory.FACTUAL_QUESTION),
        ("Check the cluster uptime and health metrics", TaskCategory.FACTUAL_QUESTION),
        ("What are the features in the latest release of Python?", TaskCategory.FACTUAL_QUESTION),
        ("Show me the current version changelog", TaskCategory.FACTUAL_QUESTION),
    ]

    for query, cat in dynamic_kw_queries:
        pkg = make_package(query_text=query, task_category=cat)
        dec = policy.evaluate(pkg)
        assert dec.is_cacheable is True
        assert dec.ttl_seconds == 300, f"Query '{query}' expected 300s TTL, got {dec.ttl_seconds}"
        assert dec.ttl_category == TTLCategory.DYNAMIC
        assert "DYNAMIC_KEYWORD_MATCH" in dec.ttl_reason


# ==============================================================================
# 2. Time-Sensitive Topics & Strict Bypass Behavior
# ==============================================================================

def test_time_sensitive_query_strict_no_cache_by_default():
    """Time-sensitive queries must be bypassed (ttl=0, NO_CACHE) when allow_time_sensitive is False."""
    policy = CachePolicy(config=CachePolicyConfig(allow_time_sensitive=False))

    time_queries = [
        "What is today's date right now?",
        "What time is it in London at this moment?",
        "What is the current weather in Paris?",
        "Latest breaking news today",
    ]

    for query in time_queries:
        pkg = make_package(query_text=query, task_category=TaskCategory.FACTUAL_QUESTION)
        dec = policy.evaluate(pkg)
        assert dec.is_cacheable is False
        assert dec.ttl_seconds == 0
        assert dec.ttl_category == TTLCategory.NO_CACHE
        assert "TIME_SENSITIVE_TASK" in dec.bypass_reason


def test_time_sensitive_query_short_ttl_when_explicitly_allowed():
    """When allow_time_sensitive is explicitly enabled, queries receive short dynamic TTL."""
    policy = CachePolicy(config=CachePolicyConfig(allow_time_sensitive=True))

    pkg = make_package(
        query_text="What is today's date right now?",
        task_category=TaskCategory.FACTUAL_QUESTION,
    )
    dec = policy.evaluate(pkg)
    assert dec.is_cacheable is True
    assert dec.ttl_seconds == 300
    assert dec.ttl_category == TTLCategory.DYNAMIC
    assert "TIME_SENSITIVE_ALLOWED_DYNAMIC_TTL" in dec.ttl_reason


# ==============================================================================
# 3. Centralized Configurability & Overrides
# ==============================================================================

def test_custom_ttl_configuration_and_category_overrides():
    """Custom TTLPolicyConfig must override default durations and support category overrides."""
    custom_ttl_cfg = TTLPolicyConfig(
        static_facts_ttl_seconds=172_800,  # 48 hours
        standard_ttl_seconds=7_200,        # 2 hours
        dynamic_ttl_seconds=120,           # 2 minutes
        category_overrides={
            TaskCategory.CODING: 1_800,     # Custom: coding gets 30m
            TaskCategory.TRANSLATION: 600,  # Custom: translation gets 10m
        }
    )
    policy = CachePolicy(config=CachePolicyConfig(ttl_policy_config=custom_ttl_cfg))

    # Overridden category: Coding
    pkg_coding = make_package("Write an LRU cache in Rust", TaskCategory.CODING)
    dec_coding = policy.evaluate(pkg_coding)
    assert dec_coding.ttl_seconds == 1_800
    assert "CATEGORY_OVERRIDE" in dec_coding.ttl_reason

    # Overridden category: Translation
    pkg_trans = make_package("Translate hello to German", TaskCategory.TRANSLATION)
    dec_trans = policy.evaluate(pkg_trans)
    assert dec_trans.ttl_seconds == 600
    assert "CATEGORY_OVERRIDE" in dec_trans.ttl_reason

    # Non-overridden Static Fact: Factual Question -> custom 48 hours
    pkg_fact = make_package("What is the distance from Earth to Moon?", TaskCategory.FACTUAL_QUESTION)
    dec_fact = policy.evaluate(pkg_fact)
    assert dec_fact.ttl_seconds == 172_800

    # Non-overridden Standard Category: Summarization -> custom 2 hours
    pkg_sum = make_package("Summarize the meeting notes", TaskCategory.SUMMARIZATION)
    dec_sum = policy.evaluate(pkg_sum)
    assert dec_sum.ttl_seconds == 7_200

    # Non-overridden Dynamic Topic: Debugging -> custom 2 minutes
    pkg_deb = make_package("Fix this memory leak", TaskCategory.DEBUGGING)
    dec_deb = policy.evaluate(pkg_deb)
    assert dec_deb.ttl_seconds == 120


def test_environment_variable_configuration(monkeypatch):
    """TTLPolicyConfig.from_env must read TTL overrides from environment variables."""
    monkeypatch.setenv("ROUTER_TTL_STATIC_FACTS_SECONDS", "43200")  # 12h
    monkeypatch.setenv("ROUTER_TTL_STANDARD_SECONDS", "1800")       # 30m
    monkeypatch.setenv("ROUTER_TTL_DYNAMIC_SECONDS", "60")          # 1m

    env_cfg = TTLPolicyConfig.from_env()
    assert env_cfg.static_facts_ttl_seconds == 43_200
    assert env_cfg.standard_ttl_seconds == 1_800
    assert env_cfg.dynamic_ttl_seconds == 60

    policy = CachePolicy(config=CachePolicyConfig(ttl_policy_config=env_cfg))
    pkg = make_package("What is the capital of France?", TaskCategory.FACTUAL_QUESTION)
    dec = policy.evaluate(pkg)
    assert dec.ttl_seconds == 43_200


# ==============================================================================
# 4. Storage & Expiration Integration Tests
# ==============================================================================

@pytest.mark.anyio
async def test_store_response_calculates_expires_at_based_on_category():
    """Verify ResponseCacheManager calculates expires_at and stores ttl_category in metadata."""
    store = InMemoryCacheStore()
    manager = ResponseCacheManager(store=store, policy=CachePolicy())

    # 1. Store Static Fact (24 hours)
    pkg_fact = make_package("What is the chemical symbol for gold?", TaskCategory.FACTUAL_QUESTION)
    t_before = time.time()
    key_fact = await manager.store_response(
        package=pkg_fact,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        content="The chemical symbol for gold is Au.",
    )
    t_after = time.time()

    entry_fact = await store.get(key_fact)
    assert entry_fact is not None
    assert entry_fact.ttl_seconds == 86_400
    assert entry_fact.metadata["ttl_category"] == "static_facts"
    assert t_before + 86_400 <= entry_fact.expires_at <= t_after + 86_400

    # 2. Store Coding Response (1 hour)
    pkg_code = make_package("How to reverse a string in Python?", TaskCategory.CODING)
    t_before = time.time()
    key_code = await manager.store_response(
        package=pkg_code,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        content="s[::-1]",
    )
    t_after = time.time()

    entry_code = await store.get(key_code)
    assert entry_code is not None
    assert entry_code.ttl_seconds == 3_600
    assert entry_code.metadata["ttl_category"] == "standard"
    assert t_before + 3_600 <= entry_code.expires_at <= t_after + 3_600

    # 3. Store Dynamic/Debugging Response (5 minutes)
    pkg_dyn = make_package("Why is my server status showing degraded?", TaskCategory.DEBUGGING)
    t_before = time.time()
    key_dyn = await manager.store_response(
        package=pkg_dyn,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini",
        model_version="2024-07-18",
        provider="small_model",
        content="Check the CPU throttling and socket descriptors.",
    )
    t_after = time.time()

    entry_dyn = await store.get(key_dyn)
    assert entry_dyn is not None
    assert entry_dyn.ttl_seconds == 300
    assert entry_dyn.metadata["ttl_category"] == "dynamic"
    assert t_before + 300 <= entry_dyn.expires_at <= t_after + 300


# ==============================================================================
# 5. End-to-End API /api/v1/optimize Integration Tests
# ==============================================================================

@pytest.fixture(autouse=True)
def clear_all_caches():
    client.post("/api/v1/cache/clear")
    client.post("/api/v1/dedup/clear")
    client.post("/api/v1/semantic-cache/clear")
    yield


def test_api_optimize_static_facts_ttl_remaining():
    """Verify that caching a static fact via /api/v1/optimize yields ttl_remaining_s > 80,000."""
    req = {
        "request_id": "req-api-fact-1",
        "query_text": "What is the speed of light in vacuum?",
        "task_category": "factual question",
        "complexity_score": 0.15,
        "context_candidates": [],
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "user_id": "alice",
            "tenant_id": "corp",
        },
        "execute_route": True,
        "user_id": "alice",
        "tenant_id": "corp",
    }
    # Initial request: MISS
    res1 = client.post("/api/v1/optimize", json=req)
    assert res1.status_code == 200
    assert res1.json()["execution_metadata"]["cache_outcome"] == "MISS"

    # Subsequent request: HIT with 24h TTL
    res2 = client.post("/api/v1/optimize", json=req)
    assert res2.status_code == 200
    meta2 = res2.json()["execution_metadata"]
    assert meta2["cache_outcome"] == "HIT"
    eval_meta = meta2["evaluation_metadata"]
    assert eval_meta["cached"] is True
    assert eval_meta["ttl_category"] == "static_facts"
    assert eval_meta["ttl_remaining_s"] > 80_000


def test_api_optimize_standard_coding_ttl_remaining():
    """Verify that caching a coding task via /api/v1/optimize yields ttl_remaining_s around 3600."""
    req = {
        "request_id": "req-api-code-1",
        "query_text": "How do I implement binary search in Python?",
        "task_category": "coding",
        "complexity_score": 0.25,
        "context_candidates": [],
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "user_id": "alice",
            "tenant_id": "corp",
        },
        "execute_route": True,
        "user_id": "alice",
        "tenant_id": "corp",
    }
    # Initial request: MISS
    res1 = client.post("/api/v1/optimize", json=req)
    assert res1.status_code == 200
    assert res1.json()["execution_metadata"]["cache_outcome"] == "MISS"

    # Subsequent request: HIT with 1h TTL
    res2 = client.post("/api/v1/optimize", json=req)
    assert res2.status_code == 200
    meta2 = res2.json()["execution_metadata"]
    assert meta2["cache_outcome"] == "HIT"
    eval_meta = meta2["evaluation_metadata"]
    assert eval_meta["cached"] is True
    assert eval_meta["ttl_category"] == "standard"
    assert 3_000 < eval_meta["ttl_remaining_s"] <= 3_600


def test_api_optimize_dynamic_topic_ttl_remaining():
    """Verify that a query with dynamic keywords receives dynamic TTL (300s)."""
    req = {
        "request_id": "req-api-dyn-1",
        "query_text": "What is the cluster uptime and health status?",
        "task_category": "factual question",
        "complexity_score": 0.20,
        "context_candidates": [],
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "user_id": "alice",
            "tenant_id": "corp",
        },
        "execute_route": True,
        "user_id": "alice",
        "tenant_id": "corp",
    }
    # Initial request: MISS
    res1 = client.post("/api/v1/optimize", json=req)
    assert res1.status_code == 200
    assert res1.json()["execution_metadata"]["cache_outcome"] == "MISS"

    # Subsequent request: HIT with 5m TTL
    res2 = client.post("/api/v1/optimize", json=req)
    assert res2.status_code == 200
    meta2 = res2.json()["execution_metadata"]
    assert meta2["cache_outcome"] == "HIT"
    eval_meta = meta2["evaluation_metadata"]
    assert eval_meta["cached"] is True
    assert eval_meta["ttl_category"] == "dynamic"
    assert 0 < eval_meta["ttl_remaining_s"] <= 300
