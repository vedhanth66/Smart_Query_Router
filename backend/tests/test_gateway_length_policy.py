"""Tests for task-aware output length guidance in the Model Gateway.

Verifies:
1. Concise limits for simple utility/factual tasks (FACTUAL_QUESTION, ARITHMETIC, GREETING, TRANSLATION).
2. Code preservation limits and formatting directives for coding and debugging tasks.
3. Explicit structured count extraction and proportional budget allocation (bullets, items, steps).
4. Unconstrained/expansive limits for open-ended reasoning and deep analysis.
5. Standard balanced limits for summarization, rewriting, and unspecified tasks.
6. System prompt preservation without clobbering existing custom persona instructions.
7. Gateway coordinator layer integration and adapter decoupling.
8. Non-truncation of responses (no post-hoc slicing as primary strategy).
9. API integration via /api/v1/optimize and /api/v1/gateway endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.schemas.contract import TaskCategory, ModelTier
from app.gateway import (
    default_gateway,
    GatewayRequest,
    GatewayResponse,
    OutputLengthTier,
    OutputLengthPolicyConfig,
    LengthGuidance,
    OutputLengthPolicy,
    default_length_policy,
)
from app.gateway.length_policy import extract_requested_count


client = TestClient(app)

BASE_CLIENT_METADATA = {
    "extension_version": "0.1.0",
    "client_type": "chrome_extension",
    "schema_version": "1.0",
    "hostname": "claude.ai",
}


# =====================================================================
# 1. Unit Tests: Requested Count Extraction
# =====================================================================

def test_extract_requested_count():
    """Verify regex extraction of explicitly requested structural counts."""
    # Positive matches
    assert extract_requested_count("Give me 5 reasons why Rust is fast") == (5, "reasons")
    assert extract_requested_count("List 3 bullet points about photosynthesis") == (3, "bullet points")
    assert extract_requested_count("Top 10 tips for clean code") == (10, "tips")
    assert extract_requested_count("Provide 4 examples of polymorphism") == (4, "examples")
    assert extract_requested_count("Summarize in 2 sections:") == (2, "sections")
    assert extract_requested_count("Explain the process in 7 steps") == (7, "steps")
    assert extract_requested_count("List exactly 6 items needed") == (6, "items")

    # Guards against false positives
    assert extract_requested_count("What is 5 + 3?") is None  # Math
    assert extract_requested_count("Explain HTTP 404 status code") is None  # HTTP code
    assert extract_requested_count("Event took place in 2024") is None  # Date/year
    assert extract_requested_count("What is the meaning of life?") is None


# =====================================================================
# 2. Unit Tests: OutputLengthPolicy Tier Resolution & Directive Shaping
# =====================================================================

def test_concise_utility_tier_resolution():
    """Simple utility/factual queries resolve to CONCISE_UTILITY with <= 256 tokens."""
    policy = OutputLengthPolicy()
    
    for category in [
        TaskCategory.FACTUAL_QUESTION,
        TaskCategory.ARITHMETIC,
        TaskCategory.GREETING,
        TaskCategory.TRANSLATION,
    ]:
        guidance = policy.resolve_guidance(
            prompt="What is the capital of France?",
            task_category=category,
        )
        assert guidance.length_tier == OutputLengthTier.CONCISE_UTILITY
        assert guidance.tier == OutputLengthTier.CONCISE_UTILITY
        assert guidance.max_tokens <= 256
        assert "concise" in guidance.system_guidance.lower()


def test_coding_expansive_tier_resolution():
    """Coding and debugging queries resolve to CODING_EXPANSIVE with high token budget and formatting directives."""
    policy = OutputLengthPolicy()
    
    for category in [TaskCategory.CODING, TaskCategory.DEBUGGING]:
        guidance = policy.resolve_guidance(
            prompt="Write a quicksort implementation in Python",
            task_category=category,
        )
        assert guidance.length_tier == OutputLengthTier.CODING_EXPANSIVE
        assert guidance.tier == OutputLengthTier.CODING_EXPANSIVE
        assert guidance.max_tokens >= 3584
        assert "complete" in guidance.system_guidance.lower()
        assert "indentation" in guidance.system_guidance.lower()


def test_structured_count_tier_resolution_and_proportional_scaling():
    """Structured requests with explicit counts scale token limits proportionally."""
    policy = OutputLengthPolicy()
    
    # 3 items: base (150) + 3 * 120 = 510 tokens
    guidance_3 = policy.resolve_guidance(
        prompt="List 3 bullet points comparing TCP and UDP",
        task_category=TaskCategory.ANALYSIS,
    )
    assert guidance_3.length_tier == OutputLengthTier.STRUCTURED_COUNT
    assert guidance_3.detected_requested_count == 3
    assert guidance_3.detected_structure_type == "bullet points"
    assert guidance_3.max_tokens == 150 + (3 * 120)
    assert "Provide exactly 3 bullet points" in guidance_3.system_guidance

    # 10 items: base (150) + 10 * 120 = 1350 tokens
    guidance_10 = policy.resolve_guidance(
        prompt="Give me 10 tips for productivity",
        task_category=TaskCategory.UNKNOWN,
    )
    assert guidance_10.length_tier == OutputLengthTier.STRUCTURED_COUNT
    assert guidance_10.detected_requested_count == 10
    assert guidance_10.max_tokens == 150 + (10 * 120)
    assert "Provide exactly 10 tips" in guidance_10.system_guidance

    # Clamping at lower and upper bounds
    guidance_min = policy.resolve_guidance("Give 1 example", task_category=TaskCategory.UNKNOWN)
    assert guidance_min.max_tokens >= 256  # Clamped at min 256

    guidance_max = policy.resolve_guidance("List 50 items", task_category=TaskCategory.UNKNOWN)
    assert guidance_max.max_tokens <= 3584  # Clamped at max 3584


def test_open_reasoning_tier_resolution():
    """Open-ended reasoning tasks receive less constrained token ceiling."""
    policy = OutputLengthPolicy()
    
    for category in [
        TaskCategory.REASONING,
        TaskCategory.ANALYSIS,
        TaskCategory.COMPARISON,
        TaskCategory.CREATIVE_WRITING,
    ]:
        guidance = policy.resolve_guidance(
            prompt="Analyze the philosophical implications of artificial consciousness",
            task_category=category,
        )
        assert guidance.length_tier == OutputLengthTier.OPEN_REASONING
        assert guidance.max_tokens >= 4096
        assert "thorough" in guidance.system_guidance.lower()


def test_standard_balanced_tier_resolution():
    """Summarization, rewriting, and unspecified categories receive standard balanced limit."""
    policy = OutputLengthPolicy()
    
    for category in [TaskCategory.SUMMARIZATION, TaskCategory.REWRITING, TaskCategory.UNKNOWN]:
        guidance = policy.resolve_guidance(
            prompt="Summarize this article",
            task_category=category,
        )
        assert guidance.length_tier == OutputLengthTier.STANDARD_BALANCED
        assert guidance.max_tokens == 1024


# =====================================================================
# 3. Request Shaping & System Prompt Preservation
# =====================================================================

def test_apply_to_request_preserves_custom_system_prompt():
    """Existing system prompt instructions and personas are preserved when length guidance is added."""
    policy = OutputLengthPolicy()
    
    req = GatewayRequest(
        prompt="What is 2 + 2?",
        system_prompt="You are an encouraging math tutor.",
        max_tokens=1024,
    )
    shaped = policy.apply_to_request(req, task_category=TaskCategory.ARITHMETIC)
    
    # max_tokens adjusted to concise limit
    assert shaped.max_tokens == 256
    # Original system prompt preserved with guidance appended
    assert shaped.system_prompt.startswith("You are an encouraging math tutor.\n\n[Length Guidance]:")
    assert "[Length Guidance]: Provide a concise" in shaped.system_prompt
    # Metadata contains structured guidance record
    assert "length_guidance" in shaped.metadata
    assert shaped.metadata["length_guidance"]["length_tier"] == "concise_utility"


def test_apply_to_request_without_prior_system_prompt():
    """When no system prompt exists, length guidance directive forms the complete system prompt."""
    policy = OutputLengthPolicy()
    
    req = GatewayRequest(prompt="Explain quicksort", max_tokens=1024)
    shaped = policy.apply_to_request(req, task_category=TaskCategory.CODING)
    
    assert shaped.max_tokens == 3584
    assert shaped.system_prompt.startswith("[Length Guidance]:")
    assert "complete" in shaped.system_prompt


def test_apply_to_request_caller_stricter_limit_preserved():
    """If caller explicitly requested a tighter token budget than standard default, it is respected."""
    policy = OutputLengthPolicy()
    
    req = GatewayRequest(prompt="Write a poem", max_tokens=128)
    shaped = policy.apply_to_request(req, task_category=TaskCategory.CREATIVE_WRITING)
    
    # Caller had 128 tokens, which is strictly less than policy's 4096, so tight budget is respected
    assert shaped.max_tokens == 128


def test_apply_to_request_disabled_via_metadata():
    """Setting apply_length_policy=False in request metadata leaves request unmodified."""
    policy = OutputLengthPolicy()
    
    req = GatewayRequest(
        prompt="Tell me a joke",
        max_tokens=500,
        metadata={"apply_length_policy": False},
    )
    shaped = policy.apply_to_request(req, task_category=TaskCategory.GREETING)
    
    assert shaped.max_tokens == 500
    assert shaped.system_prompt is None
    assert "length_guidance" not in shaped.metadata


# =====================================================================
# 4. Gateway Coordinator Integration & Adapter Decoupling
# =====================================================================

@pytest.mark.anyio
async def test_gateway_execute_applies_length_guidance():
    """ModelGateway.execute applies length policy and records guidance in response raw_metadata."""
    req = GatewayRequest(
        prompt="What is 15 * 6?",
        tier=ModelTier.FAST_CHEAP,
        provider="small_model",
        metadata={"task_category": TaskCategory.ARITHMETIC.value},
    )
    
    res = await default_gateway.execute(req)
    
    assert res is not None
    assert "length_guidance" in res.raw_metadata
    guidance = res.raw_metadata["length_guidance"]
    assert guidance["length_tier"] == "concise_utility"
    assert guidance["max_tokens"] <= 256


@pytest.mark.anyio
async def test_gateway_execute_coding_preserves_code_without_truncation():
    """Coding request through gateway receives expansive budget and remains untruncated."""
    req = GatewayRequest(
        prompt="Write a Python class for a LRU Cache with full type annotations",
        tier=ModelTier.STRONG,
        provider="strong_model",
        metadata={"task_category": TaskCategory.CODING.value},
    )
    
    res = await default_gateway.execute(req)
    
    assert res is not None
    assert "length_guidance" in res.raw_metadata
    guidance = res.raw_metadata["length_guidance"]
    assert guidance["length_tier"] == "coding_expansive"
    assert guidance["max_tokens"] >= 3584
    # Ensure completion was not truncated
    assert res.finish_reason != "length"
    assert len(res.content) > 0


def test_adapter_decoupling_verification():
    """Adapters themselves do not have task_category dependencies; logic is in gateway coordinator."""
    from app.gateway.adapters.small_model_adapter import SmallModelAdapter
    from app.gateway.adapters.strong_model_adapter import StrongModelAdapter

    small = SmallModelAdapter()
    strong = StrongModelAdapter()

    # Neither adapter should contain task length logic or task_category parameter
    import inspect
    small_sig = inspect.signature(small.execute_fast)
    strong_sig = inspect.signature(strong.execute_strong)

    assert "task_category" not in small_sig.parameters
    assert "task_category" not in strong_sig.parameters


# =====================================================================
# 5. API End-to-End Integration
# =====================================================================

def test_api_gateway_execute_with_task_category():
    """POST /api/v1/gateway/execute applies length guidance when task_category is provided."""
    payload = {
        "prompt": "What is the boiling point of water at sea level?",
        "tier": "fast_cheap",
        "provider": "small_model",
    }
    
    resp = client.post(
        "/api/v1/gateway/execute?task_category=factual%20question",
        json=payload,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "raw_metadata" in data
    assert "length_guidance" in data["raw_metadata"]
    guidance = data["raw_metadata"]["length_guidance"]
    assert guidance["length_tier"] == "concise_utility"
    assert guidance["max_tokens"] <= 256


def test_api_optimize_endpoint_simple_route_records_length_guidance():
    """POST /api/v1/optimize with simple route records length guidance in evaluation metadata."""
    payload = {
        "request_id": "req_test_len_simple",
        "correlation_id": "corr_test_len_simple",
        "query_text": "What is the capital of Japan?",
        "coarse_route": "simple-model candidate",
        "execute_route": True,
        "task_category": "factual question",
        "client_metadata": BASE_CLIENT_METADATA,
    }
    
    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    
    exec_meta = data.get("execution_metadata")
    assert exec_meta is not None
    eval_meta = exec_meta.get("evaluation_metadata")
    assert eval_meta is not None
    assert "length_guidance" in eval_meta
    guidance = eval_meta["length_guidance"]
    assert guidance is not None
    assert guidance["length_tier"] == "concise_utility"
    assert guidance["max_tokens"] <= 256


def test_api_optimize_endpoint_structured_count_in_evaluation_metadata():
    """POST /api/v1/optimize with requested count populates structured count guidance."""
    payload = {
        "request_id": "req_test_len_count",
        "correlation_id": "corr_test_len_count",
        "query_text": "List 5 bullet points on how photosynthesis works",
        "coarse_route": "simple-model candidate",
        "execute_route": True,
        "client_metadata": BASE_CLIENT_METADATA,
    }
    
    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    
    exec_meta = data.get("execution_metadata")
    assert exec_meta is not None
    eval_meta = exec_meta.get("evaluation_metadata")
    assert eval_meta is not None
    assert "length_guidance" in eval_meta
    guidance = eval_meta["length_guidance"]
    assert guidance is not None
    assert guidance["length_tier"] == "structured_count"
    assert guidance["detected_requested_count"] == 5
    assert guidance["max_tokens"] == 150 + (5 * 120)
