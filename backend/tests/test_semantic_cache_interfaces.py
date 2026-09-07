"""Tests for Semantic Caching interfaces, vector similarity, safety validation, and eligibility."""

import math
import pytest
from app.schemas.contract import (
    NormalizedQueryPackage,
    ClientMetadata,
    ContextCandidateTurn,
    ModelTier,
    TaskCategory,
)
from app.cache.semantic import (
    BaseEmbeddingGenerator,
    DeterministicMockEmbeddingGenerator,
    BaseSimilarityIndex,
    InMemoryCosineSimilarityIndex,
    SimilarityMatch,
    compute_cosine_similarity,
    BaseCandidateValidator,
    SemanticCandidateValidator,
    CandidateValidationResult,
    SemanticRejectionReason,
    compute_context_fingerprint,
    BaseSemanticEligibilityPolicy,
    SemanticEligibilityPolicy,
    SemanticEligibilityDecision,
    SemanticPolicyConfig,
    SemanticCacheManager,
    default_semantic_cache,
)


def create_query_package(
    query_text: str = "How do I reverse a linked list in Python?",
    task_category: TaskCategory = TaskCategory.CODING,
    context_turns: list[ContextCandidateTurn] | None = None,
    user_id: str = "alice",
    tenant_id: str = "corp",
) -> NormalizedQueryPackage:
    return NormalizedQueryPackage(
        request_id="req-sem-test-1",
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


# --------------------------------------------------------------------------
# 1. Embedding Generator Interface & Determinism Tests
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_embedding_generator_interface_and_determinism():
    """Verify embedding generator interface produces deterministic unit vectors."""
    generator = DeterministicMockEmbeddingGenerator(dimension=32)
    assert generator.dimension == 32
    assert generator.model_name == "mock-embed-v1"

    vec1 = await generator.embed_text("Explain distributed systems consensus")
    vec2 = await generator.embed_text("Explain distributed systems consensus")
    vec3 = await generator.embed_text("How to bake a chocolate cake")

    # Determinism: exact same input produces exact same vector
    assert vec1 == vec2
    assert len(vec1) == 32

    # L2 Unit norm: ||v|| ~ 1.0
    norm = math.sqrt(sum(x * x for x in vec1))
    assert math.isclose(norm, 1.0, abs_tol=1e-4)

    # Distinct text produces different vector
    assert vec1 != vec3

    # Batch embedding parity
    batch = await generator.embed_batch(["text one", "text two"])
    assert len(batch) == 2
    assert batch[0] == await generator.embed_text("text one")
    assert batch[1] == await generator.embed_text("text two")


# --------------------------------------------------------------------------
# 2. Similarity Lookup Interface & Vector Index Tests
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_similarity_index_storage_and_cosine_search():
    """Verify vector index storage, cosine ranking, and metadata filtering."""
    index = InMemoryCosineSimilarityIndex()
    assert await index.count() == 0

    # Index two vectors
    await index.add(
        entry_id="entry-1",
        vector=[1.0, 0.0, 0.0],
        metadata={"tenant_id": "tenant-a", "topic": "databases"},
    )
    await index.add(
        entry_id="entry-2",
        vector=[0.0, 1.0, 0.0],
        metadata={"tenant_id": "tenant-b", "topic": "baking"},
    )
    assert await index.count() == 2

    # Search with query vector close to entry-1
    matches = await index.search(query_vector=[0.9, 0.1, 0.0], top_k=2)
    assert len(matches) == 2
    assert matches[0].entry_id == "entry-1"
    assert matches[0].score > 0.8
    assert matches[1].entry_id == "entry-2"

    # Search with metadata filter
    filtered_matches = await index.search(
        query_vector=[0.9, 0.1, 0.0],
        filter_criteria={"tenant_id": "tenant-b"},
    )
    assert len(filtered_matches) == 1
    assert filtered_matches[0].entry_id == "entry-2"

    # Deletion and clear
    assert await index.delete("entry-1") is True
    assert await index.count() == 1
    await index.clear()
    assert await index.count() == 0


def test_compute_cosine_similarity_math():
    """Verify mathematical properties of cosine similarity function."""
    assert math.isclose(compute_cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)
    assert math.isclose(compute_cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)
    assert math.isclose(compute_cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)
    assert compute_cosine_similarity([], []) == 0.0


# --------------------------------------------------------------------------
# 3. Candidate Validation Interface & Safety Separation Tests
# --------------------------------------------------------------------------

def test_safety_separation_context_mismatch_rejected():
    """High vector similarity MUST be rejected if conversational context differs."""
    validator = SemanticCandidateValidator()

    # Query has context turn about Python
    query_pkg = create_query_package(
        query_text="How do I format this output?",
        context_turns=[
            ContextCandidateTurn(
                turn_id="turn-1",
                role="user",
                content="I am writing a Python FastAPI service.",
                original_index=0,
            )
        ]
    )

    # Candidate has context turn about Go
    candidate_metadata = {
        "context_fingerprint": "go_context_hash_different",
        "is_time_sensitive": False,
        "model_tier": "fast_cheap",
        "model_id": "gpt-4o-mini-2024-07-18",
        "model_version": "2024-07-18",
        "task_category": "coding",
        "tenant_id": "corp",
        "user_id": "alice",
    }

    result = validator.validate(
        package=query_pkg,
        candidate_metadata=candidate_metadata,
        similarity_score=0.98,  # Near-perfect vector similarity!
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )

    # MUST FAIL safety validation despite 0.98 embedding score
    assert result.is_valid is False
    assert SemanticRejectionReason.CONTEXT_MISMATCH in result.rejection_reasons


def test_safety_separation_time_sensitive_query_or_candidate_rejected():
    """High vector similarity MUST be rejected if query or candidate is time-sensitive."""
    validator = SemanticCandidateValidator()

    # Time-sensitive query
    query_pkg = create_query_package(
        query_text="What is the current time and date right now?",
        task_category=TaskCategory.FACTUAL_QUESTION,
    )

    candidate_metadata = {
        "context_fingerprint": "none",
        "is_time_sensitive": False,
        "model_tier": "fast_cheap",
        "model_id": "gpt-4o-mini-2024-07-18",
        "model_version": "2024-07-18",
        "task_category": "factual question",
        "tenant_id": "corp",
        "user_id": "alice",
    }

    result = validator.validate(
        package=query_pkg,
        candidate_metadata=candidate_metadata,
        similarity_score=0.99,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert result.is_valid is False
    assert SemanticRejectionReason.TIME_SENSITIVE_QUERY in result.rejection_reasons

    # Non-temporal query but candidate was flagged time-sensitive
    normal_pkg = create_query_package(query_text="Explain solar eclipses")
    candidate_metadata["is_time_sensitive"] = True
    result2 = validator.validate(
        package=normal_pkg,
        candidate_metadata=candidate_metadata,
        similarity_score=0.99,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert result2.is_valid is False
    assert SemanticRejectionReason.TIME_SENSITIVE_CANDIDATE in result2.rejection_reasons


def test_safety_separation_model_tier_and_version_incompatibility_rejected():
    """High similarity MUST be rejected if model tier, ID, or release version differs."""
    validator = SemanticCandidateValidator()
    query_pkg = create_query_package(query_text="Write a complex compiler parser")

    candidate_base = {
        "context_fingerprint": "none",
        "is_time_sensitive": False,
        "model_tier": "fast_cheap",
        "model_id": "gpt-4o-mini-2024-07-18",
        "model_version": "2024-07-18",
        "task_category": "coding",
        "tenant_id": "corp",
        "user_id": "alice",
    }

    # 1. Model Tier Mismatch: caller requests STRONG model, candidate is FAST_CHEAP
    res_tier = validator.validate(
        package=query_pkg,
        candidate_metadata=candidate_base,
        similarity_score=0.95,
        expected_tier=ModelTier.STRONG,  # Mismatch!
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert res_tier.is_valid is False
    assert SemanticRejectionReason.MODEL_TIER_MISMATCH in res_tier.rejection_reasons

    # 2. Concrete Model ID Mismatch
    res_id = validator.validate(
        package=query_pkg,
        candidate_metadata=candidate_base,
        similarity_score=0.95,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="claude-3-5-haiku-20241022",  # Mismatch!
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert res_id.is_valid is False
    assert SemanticRejectionReason.MODEL_ID_MISMATCH in res_id.rejection_reasons

    # 3. Model Version Mismatch
    res_ver = validator.validate(
        package=query_pkg,
        candidate_metadata=candidate_base,
        similarity_score=0.95,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2025-01-01",  # Newer version expected!
        tenant_id="corp",
        user_id="alice",
    )
    assert res_ver.is_valid is False
    assert SemanticRejectionReason.MODEL_VERSION_MISMATCH in res_ver.rejection_reasons


def test_safety_separation_task_type_and_tenant_user_mismatch_rejected():
    """High similarity MUST be rejected if task categories or tenant/user boundaries conflict."""
    validator = SemanticCandidateValidator()
    query_pkg = create_query_package(
        query_text="Write a sonnet about autumn trees",
        task_category=TaskCategory.CREATIVE_WRITING,
        user_id="alice",
        tenant_id="corp",
    )

    candidate_metadata = {
        "context_fingerprint": "none",
        "is_time_sensitive": False,
        "model_tier": "fast_cheap",
        "model_id": "gpt-4o-mini-2024-07-18",
        "model_version": "2024-07-18",
        "task_category": "coding",  # Mismatch: coding vs creative writing
        "tenant_id": "corp",
        "user_id": "bob",  # Mismatch: bob vs alice
    }

    res = validator.validate(
        package=query_pkg,
        candidate_metadata=candidate_metadata,
        similarity_score=0.96,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert res.is_valid is False
    assert SemanticRejectionReason.TASK_TYPE_MISMATCH in res.rejection_reasons
    assert SemanticRejectionReason.TENANT_USER_MISMATCH in res.rejection_reasons


def test_safety_validation_passes_when_all_dimensions_agree():
    """When similarity is high AND all safety dimensions agree, validation passes."""
    validator = SemanticCandidateValidator()
    query_pkg = create_query_package(
        query_text="Explain how binary search trees maintain ordering",
        task_category=TaskCategory.FACTUAL_QUESTION,
        user_id="alice",
        tenant_id="corp",
    )

    candidate_metadata = {
        "context_fingerprint": "none",
        "is_time_sensitive": False,
        "model_tier": "fast_cheap",
        "model_id": "gpt-4o-mini-2024-07-18",
        "model_version": "2024-07-18",
        "task_category": "factual question",
        "tenant_id": "corp",
        "user_id": "alice",
    }

    res = validator.validate(
        package=query_pkg,
        candidate_metadata=candidate_metadata,
        similarity_score=0.91,
        min_similarity_threshold=0.85,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert res.is_valid is True
    assert len(res.rejection_reasons) == 0


# --------------------------------------------------------------------------
# 4. Cache Eligibility Policy Tests
# --------------------------------------------------------------------------

def test_cache_eligibility_disabled_configuration():
    """When disabled, semantic caching eligibility must reject all queries."""
    policy = SemanticEligibilityPolicy(config=SemanticPolicyConfig(enabled=False))
    assert policy.config.enabled is False

    pkg = create_query_package(query_text="What is the capital of France?")
    decision = policy.evaluate(pkg)
    assert decision.is_eligible is False
    assert decision.bypass_reason == "SEMANTIC_CACHE_DISABLED"


def test_cache_eligibility_dynamic_thresholds_when_enabled():
    """Precision tasks must receive higher similarity thresholds than general tasks."""
    config = SemanticPolicyConfig(
        enabled=True,
        default_threshold=0.85,
        high_precision_threshold=0.92,
        allowed_categories={TaskCategory.CODING, TaskCategory.FACTUAL_QUESTION},
    )
    policy = SemanticEligibilityPolicy(config=config)

    # Precision task: Coding
    pkg_coding = create_query_package(
        query_text="Implement quicksort algorithm in C++",
        task_category=TaskCategory.CODING,
    )
    dec_coding = policy.evaluate(pkg_coding)
    assert dec_coding.is_eligible is True
    assert dec_coding.required_similarity_threshold == 0.92

    # General task: Factual
    pkg_factual = create_query_package(
        query_text="What is the deepest ocean trench on Earth?",
        task_category=TaskCategory.FACTUAL_QUESTION,
    )
    dec_factual = policy.evaluate(pkg_factual)
    assert dec_factual.is_eligible is True
    assert dec_factual.required_similarity_threshold == 0.88

        # Time-sensitive query bypassed even when enabled
    pkg_ts = create_query_package(query_text="What is today's date right now?")
    dec_ts = policy.evaluate(pkg_ts)
    assert dec_ts.is_eligible is False
    assert "TIME_SENSITIVE" in dec_ts.bypass_reason


# --------------------------------------------------------------------------
# 5. Semantic Cache Manager Coordination & Safe Inactive Default Tests
# --------------------------------------------------------------------------

@pytest.mark.anyio
async def test_semantic_cache_manager_disabled_in_active_pipeline():
    """Disabled semantic cache manager must never return matches."""
    disabled_policy = SemanticEligibilityPolicy(config=SemanticPolicyConfig(enabled=False))
    disabled_cache = SemanticCacheManager(eligibility_policy=disabled_policy)
    assert disabled_cache.eligibility_policy.config.enabled is False

    pkg = create_query_package()
    match, val_res, decision = await disabled_cache.lookup_candidate(pkg)
    assert match is None
    assert val_res is None
    assert decision.is_eligible is False


@pytest.mark.anyio
async def test_semantic_cache_manager_end_to_end_lookup_when_enabled():
    """Verify end-to-end lookup in staging mode when enabled in an isolated instance."""
    enabled_policy = SemanticEligibilityPolicy(
        config=SemanticPolicyConfig(
            enabled=True,
            allowed_categories={TaskCategory.CODING, TaskCategory.FACTUAL_QUESTION},
        )
    )
    manager = SemanticCacheManager(eligibility_policy=enabled_policy)

    pkg = create_query_package(
        query_text="How do I reverse a singly linked list in Python?",
        task_category=TaskCategory.CODING,
        user_id="alice",
        tenant_id="corp",
    )

    # 1. Index a completion candidate
    await manager.index_candidate(
        entry_id="entry-rev-list",
        query_text="How do I reverse a singly linked list in Python?",
        content="def reverse_list(head): ...",
        package=pkg,
        tier=ModelTier.FAST_CHEAP,
        model_id="gpt-4o-mini-2024-07-18",
        model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )

    # 2. Exact match query text: high similarity + safety valid
    match, val_res, decision = await manager.lookup_candidate(
        package=pkg,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert match is not None
    assert val_res is not None
    assert val_res.is_valid is True
    assert match.entry_id == "entry-rev-list"

    # 3. Same query text from DIFFERENT user -> isolated, no match returned
    match_other, _, _ = await manager.lookup_candidate(
        package=pkg,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="bob",  # Bob cannot access Alice's candidate!
    )
    assert match_other is None

    # 4. Same query text with CONFLICTING context turn -> bypassed by eligibility policy
    pkg_with_context = create_query_package(
        query_text="How do I reverse a singly linked list in Python?",
        task_category=TaskCategory.CODING,
        context_turns=[
            ContextCandidateTurn(
                turn_id="turn-x",
                role="user",
                content="Assume a doubly linked list instead.",
                original_index=0,
            )
        ],
        user_id="alice",
        tenant_id="corp",
    )
    match_ctx, val_ctx, dec_ctx = await manager.lookup_candidate(
        package=pkg_with_context,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2024-07-18",
        tenant_id="corp",
        user_id="alice",
    )
    assert match_ctx is None
    assert dec_ctx.is_eligible is False
    assert "CONTEXT_HEAVY" in dec_ctx.bypass_reason

    # 5. Incompatible model version -> candidate retrieved but rejected by safety validator
    match_ver, val_ver, dec_ver = await manager.lookup_candidate(
        package=pkg,
        expected_tier=ModelTier.FAST_CHEAP,
        expected_model_id="gpt-4o-mini-2024-07-18",
        expected_model_version="2025-01-01",  # Newer model version expected!
        tenant_id="corp",
        user_id="alice",
    )
    assert match_ver is None
    assert val_ver is not None
    assert val_ver.is_valid is False
    assert SemanticRejectionReason.MODEL_VERSION_MISMATCH in val_ver.rejection_reasons
