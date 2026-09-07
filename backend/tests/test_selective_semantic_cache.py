"""Tests for Selective Semantic Caching for Low-Risk Static Informational Requests.

Covers:
1. Similarity threshold calibration:
   - Genuine paraphrases/synonyms achieve >= 0.88 cosine similarity.
   - Subtle entity or domain divergences achieve < 0.88 cosine similarity.
2. Strict exclusion guardrails:
   - Current events, breaking news, live scores, temporal queries.
   - Personal data, PII (email, phone, SSN, credit card, self-referential PII).
   - Private documents, confidential memos, attachments, proprietary text.
   - Context-heavy conversation turns (any context turn present).
   - Non-static task categories (coding, debugging, creative writing, reasoning).
3. Safety validation layer:
   - Rejects candidate matches if task category or context differs even with high similarity.
   - Enforces strict tenant and user boundary isolation.
4. End-to-end /api/v1/optimize integration:
   - First request: exact cache MISS + semantic MISS -> gateway executes -> indexed.
   - Paraphrased request: semantic HIT -> returned instantly with <1ms latency.
   - Subtle divergent request: similarity < 0.88 -> semantic MISS -> gateway executes.
   - Current event / PII / context-heavy / unsupported category: semantic BYPASS.
5. Diagnostic API endpoints:
   - GET /api/v1/semantic-cache/stats
   - POST /api/v1/semantic-cache/clear
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.schemas.contract import (
    NormalizedQueryPackage,
    ClientMetadata,
    ContextCandidateTurn,
    TaskCategory,
    ModelTier,
    CacheOutcome,
)
from app.cache.semantic import (
    DeterministicMockEmbeddingGenerator,
    compute_cosine_similarity,
    SemanticEligibilityPolicy,
    SemanticPolicyConfig,
    SemanticCandidateValidator,
    CandidateValidationResult,
    SemanticRejectionReason,
    SemanticCacheManager,
    default_semantic_cache,
)
from app.cache import default_response_cache, default_deduplicator


client = TestClient(app)


def make_pkg(
    query_text: str,
    task_category: TaskCategory = TaskCategory.FACTUAL_QUESTION,
    context_turns: list[ContextCandidateTurn] | None = None,
    user_id: str = "alice",
    tenant_id: str = "corp",
    request_id: str = "req-sem-test",
) -> NormalizedQueryPackage:
    return NormalizedQueryPackage(
        request_id=request_id,
        query_text=query_text,
        task_category=task_category,
        context_candidates=context_turns or [],
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
# 1. Similarity Threshold Calibration Tests (>= 0.88 vs < 0.88)
# ==============================================================================

@pytest.mark.anyio
async def test_threshold_calibration_synonyms_and_paraphrases_pass():
    """Paraphrases of static informational queries must achieve >= 0.88 similarity."""
    embedder = DeterministicMockEmbeddingGenerator(dimension=128)

    pair1_a = "What is the capital of Australia?"
    pair1_b = "Which city is the capital of Australia?"
    vec1_a = await embedder.embed_text(pair1_a)
    vec1_b = await embedder.embed_text(pair1_b)
    sim1 = compute_cosine_similarity(vec1_a, vec1_b)
    assert sim1 >= 0.88, f"Expected similarity >= 0.88, got {sim1}"

    pair2_a = "Translate hello to Spanish"
    pair2_b = "Translate hello into Spanish"
    vec2_a = await embedder.embed_text(pair2_a)
    vec2_b = await embedder.embed_text(pair2_b)
    sim2 = compute_cosine_similarity(vec2_a, vec2_b)
    assert sim2 >= 0.88, f"Expected similarity >= 0.88, got {sim2}"


@pytest.mark.anyio
async def test_threshold_calibration_subtle_divergences_cleanly_rejected():
    """Subtle divergences in entity or physical medium must produce < 0.88 similarity."""
    embedder = DeterministicMockEmbeddingGenerator(dimension=128)

    # Subtle country change: Australia vs Austria
    vec_aust = await embedder.embed_text("What is the capital of Australia?")
    vec_oest = await embedder.embed_text("What is the capital of Austria?")
    sim_entity = compute_cosine_similarity(vec_aust, vec_oest)
    assert sim_entity < 0.88, f"Expected subtle entity change < 0.88, got {sim_entity}"

    # Medium change: speed of sound in air vs water
    vec_air = await embedder.embed_text("What is the speed of sound in air?")
    vec_water = await embedder.embed_text("What is the speed of sound in water?")
    sim_medium = compute_cosine_similarity(vec_air, vec_water)
    assert sim_medium < 0.88, f"Expected medium change < 0.88, got {sim_medium}"


# ==============================================================================
# 2. Strict Exclusion Guardrails Tests
# ==============================================================================

def test_exclusion_current_events_and_real_time_inquiries():
    """Current events, breaking news, and real-time market/weather queries must be rejected."""
    policy = SemanticEligibilityPolicy()

    current_event_queries = [
        "Breaking news about the international summit",
        "What is today's news regarding economic growth?",
        "What happened today in parliament?",
        "Check stock price of Apple Inc AAPL",
        "What is the crypto price of Bitcoin?",
        "What is the weather today in Tokyo?",
        "Latest election results for the city mayor",
        "Who won yesterday in the football league?",
        "Show me sports scores right now",
    ]

    for q in current_event_queries:
        pkg = make_pkg(query_text=q, task_category=TaskCategory.FACTUAL_QUESTION)
        dec = policy.evaluate(pkg)
        assert dec.is_eligible is False, f"Query '{q}' should not be eligible for semantic cache"
        assert "BYPASS_CURRENT_EVENTS" in dec.bypass_reason or "BYPASS_TIME_SENSITIVE" in dec.bypass_reason


def test_exclusion_personal_data_and_pii():
    """Personal data, PII markers, credentials, and self-referential PII must be rejected."""
    policy = SemanticEligibilityPolicy()

    pii_queries = [
        "Send confirmation to user.test@example.com please",
        "Call customer service at 555-123-4567 regarding my invoice",
        "My social security number is 000-12-3456",
        "Save credit card 4111 2222 3333 4444 for subscription renewal",
        "What is my password for the company portal?",
        "Show details of my account balance and recent transactions",
        "Where does my doctor practice medicine?",
        "I was born in Chicago Illinois in 1990",
    ]

    for q in pii_queries:
        pkg = make_pkg(query_text=q, task_category=TaskCategory.FACTUAL_QUESTION)
        dec = policy.evaluate(pkg)
        assert dec.is_eligible is False, f"Query '{q}' should be rejected due to PII"
        assert "BYPASS_PERSONAL_DATA" in dec.bypass_reason


def test_exclusion_private_documents_and_proprietary_text():
    """Uploaded attachments, private memos, and proprietary text must be rejected."""
    policy = SemanticEligibilityPolicy()

    private_queries = [
        "Summarize the attached document with financial statements",
        "Review this attachment containing internal strategy",
        "Here are our internal meeting notes from the board",
        "This confidential memo describes our acquisition targets",
        "The following is our company proprietary roadmap for Q4",
        "Explain the source code below from our private repo",
    ]

    for q in private_queries:
        pkg = make_pkg(query_text=q, task_category=TaskCategory.FACTUAL_QUESTION)
        dec = policy.evaluate(pkg)
        assert dec.is_eligible is False, f"Query '{q}' should be rejected due to private documents"
        assert "BYPASS_PRIVATE_DOCUMENTS" in dec.bypass_reason


def test_exclusion_context_heavy_conversation_turns():
    """Any query with prior conversation context turns must be bypassed (standalone only)."""
    policy = SemanticEligibilityPolicy()

    pkg_with_turn = make_pkg(
        query_text="What is the capital of Australia?",
        task_category=TaskCategory.FACTUAL_QUESTION,
        context_turns=[
            ContextCandidateTurn(
                turn_id="t1",
                role="user",
                content="Tell me about Oceania geography",
                original_index=0,
            )
        ],
    )
    dec = policy.evaluate(pkg_with_turn)
    assert dec.is_eligible is False
    assert dec.bypass_reason == "BYPASS_CONTEXT_HEAVY_CONVERSATION"


def test_exclusion_unsupported_task_categories():
    """Non-static task types (coding, debugging, creative writing, reasoning) must be rejected."""
    policy = SemanticEligibilityPolicy()

    disallowed = [
        TaskCategory.CODING,
        TaskCategory.DEBUGGING,
        TaskCategory.REASONING,
        TaskCategory.CREATIVE_WRITING,
        TaskCategory.ARITHMETIC,
    ]

    for cat in disallowed:
        pkg = make_pkg(query_text="Explain this general concept in detail", task_category=cat)
        dec = policy.evaluate(pkg)
        assert dec.is_eligible is False
        assert "BYPASS_UNSUPPORTED_TASK_TYPE" in dec.bypass_reason


def test_inclusion_static_informational_queries_pass():
    """Standalone factual questions and translation queries must pass eligibility."""
    policy = SemanticEligibilityPolicy()

    pkg_fact = make_pkg(
        query_text="What is the capital of Australia?",
        task_category=TaskCategory.FACTUAL_QUESTION,
    )
    dec_fact = policy.evaluate(pkg_fact)
    assert dec_fact.is_eligible is True
    assert dec_fact.bypass_reason is None
    assert dec_fact.required_similarity_threshold == 0.88

    pkg_trans = make_pkg(
        query_text="Translate thank you very much into French",
        task_category=TaskCategory.TRANSLATION,
    )
    dec_trans = policy.evaluate(pkg_trans)
    assert dec_trans.is_eligible is True
    assert dec_trans.bypass_reason is None
    assert dec_trans.required_similarity_threshold == 0.88


# ==============================================================================
# 3. Validation Layer Safety Separation Tests
# ==============================================================================

def test_validator_rejects_task_type_and_context_mismatch_even_with_high_similarity():
    """Safety validator must reject matches if context fingerprint or task category differs."""
    validator = SemanticCandidateValidator()
    pkg = make_pkg(
        query_text="What is the boiling point of water at sea level?",
        task_category=TaskCategory.FACTUAL_QUESTION,
    )

    # Candidate with task type mismatch
    candidate_meta_task_diff = {
        "context_fingerprint": "none",
        "is_time_sensitive": False,
        "model_tier": "fast_cheap",
        "model_id": "gpt-4o-mini-2024-07-18",
        "model_version": "2024-07-18",
        "task_category": "translation",  # Mismatch!
        "tenant_id": "corp",
        "user_id": "alice",
    }
    res_task = validator.validate(
        package=pkg,
        candidate_metadata=candidate_meta_task_diff,
        similarity_score=0.98,
        min_similarity_threshold=0.88,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert res_task.is_valid is False
    assert SemanticRejectionReason.TASK_TYPE_MISMATCH in res_task.rejection_reasons
    assert res_task.primary_reason() == "REJECTED_TASK_TYPE_MISMATCH"

    # Candidate with context fingerprint mismatch
    candidate_meta_ctx_diff = {
        "context_fingerprint": "ctx_hash_previous_chat",  # Mismatch!
        "is_time_sensitive": False,
        "model_tier": "fast_cheap",
        "model_id": "gpt-4o-mini-2024-07-18",
        "model_version": "2024-07-18",
        "task_category": "factual question",
        "tenant_id": "corp",
        "user_id": "alice",
    }
    res_ctx = validator.validate(
        package=pkg,
        candidate_metadata=candidate_meta_ctx_diff,
        similarity_score=0.99,
        min_similarity_threshold=0.88,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert res_ctx.is_valid is False
    assert SemanticRejectionReason.CONTEXT_MISMATCH in res_ctx.rejection_reasons
    assert res_ctx.primary_reason() == "REJECTED_CONTEXT_MISMATCH"


# ==============================================================================
# 4. End-to-End API Integration Tests (/api/v1/optimize)
# ==============================================================================

@pytest.fixture(autouse=True)
def clear_caches_before_test():
    client.post("/api/v1/cache/clear")
    client.post("/api/v1/dedup/clear")
    client.post("/api/v1/semantic-cache/clear")
    yield


def test_api_optimize_semantic_cache_lifecycle_paraphrase_hit():
    """Verify full lifecycle: MISS on initial query -> HIT on valid paraphrase."""
    # 1. Initial query: "What is the capital of Australia?"
    req_payload_1 = {
        "request_id": "req-init-1",
        "query_text": "What is the capital of Australia?",
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
    res1 = client.post("/api/v1/optimize", json=req_payload_1)
    assert res1.status_code == 200
    data1 = res1.json()
    meta1 = data1["execution_metadata"]

    assert meta1["cache_outcome"] == "MISS"
    assert meta1["semantic_cache_outcome"] == "SEMANTIC_MISS"
    assert meta1["semantic_validation_reason"] == "NO_SIMILAR_CANDIDATE_ABOVE_THRESHOLD"
    assert meta1["executed_content"] is not None
    initial_content = meta1["executed_content"]

    # 2. Paraphrased query from same user: "Which city is the capital of Australia?"
    req_payload_2 = {
        "request_id": "req-para-2",
        "query_text": "Which city is the capital of Australia?",
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
    res2 = client.post("/api/v1/optimize", json=req_payload_2)
    assert res2.status_code == 200
    data2 = res2.json()
    meta2 = data2["execution_metadata"]

    # Must be SEMANTIC HIT with instant return
    assert meta2["cache_outcome"] == "HIT"
    assert meta2["semantic_cache_outcome"] == "SEMANTIC_HIT"
    assert meta2["semantic_validation_reason"] == "VALIDATED_STATIC_INFORMATIONAL_MATCH"
    assert meta2["executed_content"] == initial_content
    assert meta2["evaluation_metadata"]["semantic_hit"] is True
    assert meta2["evaluation_metadata"]["similarity_score"] >= 0.88


def test_api_optimize_semantic_cache_rejects_divergent_query():
    """Subtle divergence (Australia vs Austria) must yield SEMANTIC_MISS and execute model."""
    # 1. Index Australia query
    req1 = {
        "request_id": "req-aust-1",
        "query_text": "What is the capital of Australia?",
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
    client.post("/api/v1/optimize", json=req1)

    # 2. Divergent query: Austria
    req2 = {
        "request_id": "req-oest-2",
        "query_text": "What is the capital of Austria?",
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
    res2 = client.post("/api/v1/optimize", json=req2)
    assert res2.status_code == 200
    meta2 = res2.json()["execution_metadata"]

    assert meta2["cache_outcome"] == "MISS"
    assert meta2["semantic_cache_outcome"] == "SEMANTIC_MISS"
    assert meta2["semantic_validation_reason"] == "NO_SIMILAR_CANDIDATE_ABOVE_THRESHOLD"


def test_api_optimize_semantic_cache_bypassed_for_current_events():
    """Real-time current events inquiries must record SEMANTIC_BYPASS."""
    req = {
        "request_id": "req-ce-1",
        "query_text": "What is the latest breaking news today in technology?",
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
    res = client.post("/api/v1/optimize", json=req)
    assert res.status_code == 200
    meta = res.json()["execution_metadata"]

    assert meta["cache_outcome"] == "BYPASS"
    assert meta["semantic_cache_outcome"] == "SEMANTIC_BYPASS"
    assert "BYPASS_CURRENT_EVENTS" in meta["semantic_validation_reason"] or "BYPASS_TIME_SENSITIVE" in meta["semantic_validation_reason"]


def test_api_optimize_semantic_cache_bypassed_for_pii():
    """Queries containing personal data markers must record SEMANTIC_BYPASS."""
    req = {
        "request_id": "req-pii-1",
        "query_text": "Please reset my password and update my phone number",
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
    res = client.post("/api/v1/optimize", json=req)
    assert res.status_code == 200
    meta = res.json()["execution_metadata"]

    assert meta["semantic_cache_outcome"] == "SEMANTIC_BYPASS"
    assert "BYPASS_PERSONAL_DATA" in meta["semantic_validation_reason"]


def test_api_optimize_semantic_cache_bypassed_for_context_heavy_conversation():
    """Queries with prior conversation context turns must record SEMANTIC_BYPASS."""
    req = {
        "request_id": "req-ctx-1",
        "query_text": "What is the capital of Australia?",
        "task_category": "factual question",
        "complexity_score": 0.20,
        "context_candidates": [
            {
                "turn_id": "t1",
                "role": "user",
                "content": "Tell me about Canberra.",
                "original_index": 0,
            }
        ],
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
    res = client.post("/api/v1/optimize", json=req)
    assert res.status_code == 200
    meta = res.json()["execution_metadata"]

    assert meta["semantic_cache_outcome"] == "SEMANTIC_BYPASS"
    assert meta["semantic_validation_reason"] == "BYPASS_CONTEXT_HEAVY_CONVERSATION"


def test_api_optimize_semantic_cache_isolated_between_users():
    """A semantic cache candidate indexed for Alice must NEVER be returned to Bob."""
    # Alice indexes completion for capital of Australia
    req_alice = {
        "request_id": "req-alice-1",
        "query_text": "What is the capital of Australia?",
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
    res_alice = client.post("/api/v1/optimize", json=req_alice)
    assert res_alice.status_code == 200

    # Bob queries paraphrased query in same tenant
    req_bob = {
        "request_id": "req-bob-1",
        "query_text": "Which city is the capital of Australia?",
        "task_category": "factual question",
        "complexity_score": 0.20,
        "context_candidates": [],
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "user_id": "bob",
            "tenant_id": "corp",
        },
        "execute_route": True,
        "user_id": "bob",
        "tenant_id": "corp",
    }
    res_bob = client.post("/api/v1/optimize", json=req_bob)
    assert res_bob.status_code == 200
    meta_bob = res_bob.json()["execution_metadata"]

    # Bob cannot hit Alice's candidate! It must be a MISS.
    assert meta_bob["semantic_cache_outcome"] == "SEMANTIC_MISS"
    assert meta_bob["cache_outcome"] == "MISS"


# ==============================================================================
# 5. Diagnostic Endpoints Tests
# ==============================================================================

def test_diagnostic_endpoints_stats_and_clear():
    """Verify GET /api/v1/semantic-cache/stats and POST /api/v1/semantic-cache/clear."""
    stats_res = client.get("/api/v1/semantic-cache/stats")
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["enabled"] is True
    assert stats["static_informational_threshold"] == 0.88
    assert "factual question" in stats["allowed_categories"]
    assert "translation" in stats["allowed_categories"]
    assert stats["embedding_model"] == "mock-embed-v1"
    assert stats["embedding_dimension"] == 128

    # Populate one entry via optimize
    req = {
        "request_id": "req-stats-1",
        "query_text": "What is the capital of Australia?",
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
    client.post("/api/v1/optimize", json=req)

    stats_after = client.get("/api/v1/semantic-cache/stats").json()
    assert stats_after["total_entries"] >= 1

    # Clear semantic cache
    clear_res = client.post("/api/v1/semantic-cache/clear")
    assert clear_res.status_code == 200
    assert clear_res.json()["status"] == "ok"

    stats_cleared = client.get("/api/v1/semantic-cache/stats").json()
    assert stats_cleared["total_entries"] == 0
