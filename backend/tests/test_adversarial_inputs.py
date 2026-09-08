"""Comprehensive Test Suite for Optimizer & Router Against Adversarial/Deceptive Inputs.

AUDIT CATEGORIES:
1. Very short but context-dependent queries ("Why?", "How?", "Next", "Fix it", "And that?")
2. Long but simple queries (200+ word verbose polite prompts; verifies no word-count routing bias and zero stopword stripping)
3. Code containing punctuation-sensitive syntax (Regex lookaheads, C++ templates, Rust lifetimes, bash pipes, Python indentation)
4. Mathematical notation (LaTeX display/inline math, proofs with arithmetic symbols that must not be hijacked by arithmetic rules)
5. Quoted text (Exact spacing in quotes, legal text, single and double quotes preserved verbatim)
6. Multilingual greetings (Accented characters, inverted punctuation, non-Latin scripts, safe fallthrough without bogus English answers)
7. Dynamic information requests (Real-time stock price, weather, headlines; temporal terms preserved; cache bypass/TTL=0)
8. Ambiguous follow-ups ("How about Python?", "Can you change it?", "Yes, please"; prefers doing nothing over risky transformation)

CORE INVARIANT:
The optimizer must PREFER DOING NOTHING over making a risky transformation.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.optimizer import (
    QueryOptimizer,
    default_query_optimizer,
    estimate_token_count,
)
from app.ml.guarded_router import (
    GuardedRouter,
    is_deterministic_greeting,
    is_deterministic_arithmetic,
)
from app.cache.ttl_policy import (
    TTLPolicy,
    TTLPolicyConfig,
    TTLCategory,
)
from app.cache.semantic import (
    SemanticEligibilityPolicy,
    SemanticPolicyConfig,
)
from app.schemas.contract import (
    NormalizedQueryPackage,
    ClientMetadata,
    TaskCategory,
    ContextCandidateTurn,
)

client = TestClient(app)


# =========================================================================
# Category 1: Very Short but Context-Dependent Queries
# =========================================================================

@pytest.mark.parametrize("short_query", [
    "Why?",
    "How?",
    "Next",
    "And that?",
    "Fix it",
    "Explain them",
    "Same with this",
    "Do it again",
    "More please",
    "Why not?",
])
def test_very_short_context_dependent_optimizer_preservation(short_query):
    """The optimizer must NOT attempt risky expansion, truncation, or token stripping on short queries."""
    optimizer = QueryOptimizer()
    res = optimizer.optimize(short_query)

    # Must preserve text exactly without mangling terminal punctuation or case
    assert res.optimized_query == short_query
    # Prefer doing nothing: no lossy changes
    assert res.char_savings == 0
    assert res.token_savings == 0


@pytest.mark.parametrize("short_query", [
    "Why?",
    "Fix it",
    "And that?",
    "Same with this",
])
def test_very_short_context_dependent_router_safety(short_query):
    """Very short context-dependent queries must route to context-aware tier and NEVER be hijacked as local."""
    router = GuardedRouter()
    decision = router.route_query(
        query_text=short_query,
        has_context_dependency=True,
    )
    # Primacy of Gate 2: must route to strong complex-model tier
    assert decision.final_route == "complex-model candidate"
    assert decision.recommended_tier == "strong"
    assert decision.context_guard_applied is True
    # Embeddings must not have been used to downgrade
    assert decision.decision_gate == "EXPLICIT_CONTEXT_GUARD"


# =========================================================================
# Category 2: Long but Simple Queries
# =========================================================================

def test_long_but_simple_query_preservation_and_routing():
    """A long prompt with polite filler must NOT have words stripped or be escalated solely on word count."""
    optimizer = QueryOptimizer()
    router = GuardedRouter()

    polite_long_query = (
        "Hello Claude, I hope you are having an absolutely wonderful and productive day today. "
        "I am currently sitting at my desk working on a few tasks and was wondering if you might "
        "be so kind as to assist me with a very simple and straightforward question. "
        "You see, I am planning a phone call with a friend who lives in France, and before dialing "
        "I simply wanted to confirm: what is the official capital city of France?"
    )

    # 1. Optimizer test: zero stopwords removed
    res = optimizer.optimize(polite_long_query)
    assert res.optimized_query == polite_long_query
    assert "capital city of France" in res.optimized_query
    assert "Hello Claude, I hope you are having" in res.optimized_query
    assert res.char_savings == 0

    # 2. Router test: NOT a greeting (too long and substantive), but independent
    # Must NOT route to local-eligible (not a trivial greeting)
    assert is_deterministic_greeting(polite_long_query) is False

    decision = router.route_query(
        query_text=polite_long_query,
        has_context_dependency=False,
        task_type="factual question",
    )
    # Passes Gate 1 and Gate 2 cleanly to Gate 3 (auxiliary ML)
    assert decision.decision_gate == "ML_CLASSIFIER_AUXILIARY"
    assert decision.safety_override_applied is False
    assert decision.context_guard_applied is False


# =========================================================================
# Category 3: Code Containing Punctuation-Sensitive Syntax
# =========================================================================

def test_code_punctuation_sensitive_regex_lookarounds():
    """Regex containing lookaheads, lookbehinds, character classes, and quantifiers must be 100% preserved."""
    optimizer = QueryOptimizer()

    regex_pattern = r"(?<=[A-Z]{2})\d{4}(?=[!@#$%^&*()_+=\-{}[\]:;\"'<>,.?/])"
    query = f"Explain what this regular expression matches: `{regex_pattern}`"

    res = optimizer.optimize(query)
    assert f"`{regex_pattern}`" in res.optimized_query
    assert res.optimized_query == query


def test_code_cpp_template_syntax():
    """C++ template angle brackets and shift operators must never be collapsed or stripped."""
    optimizer = QueryOptimizer()

    cpp_code = (
        "template <typename T, typename = std::enable_if_t<(sizeof(T) > 4)>>\n"
        "auto shift_val(const T& val) -> decltype(val >> 2) {\n"
        "    return val >> 2;\n"
        "}"
    )
    query = f"Here is the C++ code:\n```cpp\n{cpp_code}\n```\nIs this valid C++17?"

    res = optimizer.optimize(query)
    assert cpp_code in res.optimized_query
    assert "<typename T, typename = std::enable_if_t<(sizeof(T) > 4)>>" in res.optimized_query
    assert "val >> 2" in res.optimized_query


def test_code_rust_lifetimes_and_macros():
    """Rust lifetime ticks and macro exclamation marks must remain strictly intact."""
    optimizer = QueryOptimizer()

    rust_snippet = "fn parse<'a, T: 'a + Display>(buf: &'a str) -> Result<&'a T, Box<dyn Error>>"
    query = f"Review this signature: `{rust_snippet}`"

    res = optimizer.optimize(query)
    assert f"`{rust_snippet}`" in res.optimized_query
    assert "'a" in res.optimized_query


def test_code_bash_pipeline_redirection():
    """Bash pipelines with descriptors 2>&1, pipes, and redirects must remain intact."""
    optimizer = QueryOptimizer()

    bash_cmd = "find . -type f -name '*.log' -exec grep -Hn 'ERROR' {} + | awk -F: '{print $1, $2}' > /dev/null 2>&1"
    query = f"Run this command:\n```bash\n{bash_cmd}\n```"

    res = optimizer.optimize(query)
    assert bash_cmd in res.optimized_query
    assert "> /dev/null 2>&1" in res.optimized_query


def test_code_python_indentation_and_slices():
    """Python indentation (4 spaces) and slice step syntax (1::2) must be preserved."""
    optimizer = QueryOptimizer()

    py_code = (
        "def filter_matrix(grid: list[list[int]]) -> list[int]:\n"
        "    if not grid or not grid[0]:\n"
        "        return []\n"
        "    return [row[1::2] for row in grid if sum(row) > 0]"
    )
    query = f"```python\n{py_code}\n```"

    res = optimizer.optimize(query)
    assert py_code in res.optimized_query
    assert "    if not grid or not grid[0]:" in res.optimized_query
    assert "    return [row[1::2] for row in grid if sum(row) > 0]" in res.optimized_query


# =========================================================================
# Category 4: Mathematical Notation
# =========================================================================

def test_latex_display_and_inline_math_preservation():
    """LaTeX display math $$...$$ and inline math $...$ must remain completely untouched."""
    optimizer = QueryOptimizer()

    latex_display = "$$\\int_{-\\infty}^{\\infty} e^{-x^2} dx = \\sqrt{\\pi}$$"
    latex_inline = "$f(x) = \\frac{\\partial \\psi}{\\partial t} + \\nabla^2 \\psi$"
    query = f"Calculate the Gaussian integral {latex_display} and compare with {latex_inline}."

    res = optimizer.optimize(query)
    assert latex_display in res.optimized_query
    assert latex_inline in res.optimized_query


def test_math_proof_with_operators_not_hijacked_as_arithmetic():
    """Equations and algebraic proof statements with arithmetic symbols must NEVER be hijacked as simple arithmetic."""
    proof_prompt = "Prove that for all integers n >= 1: 1 + 2 + 3 + ... + n = n * (n + 1) / 2."
    algebra_prompt = "If x + y = 10 and x - y = 2, calculate x^2 - y^2."

    # Both contain numbers, +, -, *, /, but are NOT deterministic simple arithmetic
    assert is_deterministic_arithmetic(proof_prompt) is False
    assert is_deterministic_arithmetic(algebra_prompt) is False

    router = GuardedRouter()
    dec_proof = router.route_query(proof_prompt, task_type="reasoning")
    # Must NOT be resolved as local deterministic arithmetic
    assert dec_proof.final_route != "local-eligible"
    assert dec_proof.decision_gate != "DETERMINISTIC_SAFETY"

    dec_alg = router.route_query(algebra_prompt, task_type="reasoning")
    assert dec_alg.final_route != "local-eligible"
    assert dec_alg.decision_gate != "DETERMINISTIC_SAFETY"


# =========================================================================
# Category 5: Quoted Text
# =========================================================================

def test_quoted_text_exact_spacing_and_symbols_preserved():
    """Exact whitespace, casing, and punctuation inside double and single quotes must remain byte-for-byte intact."""
    optimizer = QueryOptimizer()

    # Exact spaces inside quotes (e.g. search string)
    exact_search = 'Find the exact string: "  SELECT * FROM users   WHERE active = 1  "'
    res_search = optimizer.optimize(exact_search)
    assert '"  SELECT * FROM users   WHERE active = 1  "' in res_search.optimized_query
    assert res_search.optimized_query == exact_search

    # Single quotes inside dialogue
    dialogue = "He whispered, 'Wait here until midnight, do not move!' before vanishing."
    res_dialogue = optimizer.optimize(dialogue)
    assert "'Wait here until midnight, do not move!'" in res_dialogue.optimized_query
    assert res_dialogue.optimized_query == dialogue

    # Verbatim legal clause
    legal = 'Review clause 4.2: "The Party of the First Part shall not, under any circumstances, be liable for punitive damages."'
    res_legal = optimizer.optimize(legal)
    assert '"The Party of the First Part shall not, under any circumstances, be liable for punitive damages."' in res_legal.optimized_query


# =========================================================================
# Category 6: Multilingual Greetings
# =========================================================================

@pytest.mark.parametrize("greeting_text, language", [
    ("Bonjour! Comment allez-vous aujourd'hui?", "French"),
    ("¡Hola! ¿Cómo estás?", "Spanish"),
    ("Guten Tag! Wie geht es Ihnen?", "German"),
    ("Ciao! Come stai?", "Italian"),
    ("Olá! Tudo bem com você?", "Portuguese"),
    ("こんにちは！お元気ですか？", "Japanese"),
    ("नमस्ते! आप कैसे हैं?", "Hindi"),
    ("你好！最近怎么样？", "Mandarin"),
    ("مرحبا! كيف حالك اليوم؟", "Arabic"),
])
def test_multilingual_greetings_unicode_preservation_and_safe_routing(greeting_text, language):
    """Multilingual greetings must preserve all accents, diacritics, and scripts, and never produce bogus English answers."""
    optimizer = QueryOptimizer()
    res = optimizer.optimize(greeting_text)

    # 1. Zero unicode corruption or character loss
    assert res.optimized_query == greeting_text
    assert res.char_savings == 0

    # 2. English-only deterministic safety gate must NOT trigger on non-English text without explicit task_type
    # It must safely fall through to model routing rather than emitting a bogus English template
    router = GuardedRouter()
    decision = router.route_query(greeting_text)
    # The non-English greeting passes to auxiliary ML routing safely without English greeting override
    assert decision.decision_gate == "ML_CLASSIFIER_AUXILIARY"
    assert decision.safety_override_applied is False
    assert decision.final_route in ("complex-model candidate", "simple-model candidate")


# =========================================================================
# Category 7: Dynamic Information Requests
# =========================================================================

@pytest.mark.parametrize("dynamic_query", [
    "What is the stock price of Apple right now?",
    "What is the current weather in Seattle today?",
    "What are the latest breaking news headlines right now?",
    "What is the current exchange rate from USD to EUR today?",
    "What is the live server status right now?",
])
def test_dynamic_information_requests_temporal_preservation_and_cache_bypass(dynamic_query):
    """Dynamic queries must retain temporal cues ('right now', 'current', 'today') and bypass stale caches."""
    # 1. Optimizer preservation
    optimizer = QueryOptimizer()
    res = optimizer.optimize(dynamic_query)
    assert res.optimized_query == dynamic_query
    for temporal_marker in ["right now", "current", "today", "status"]:
        if temporal_marker in dynamic_query:
            assert temporal_marker in res.optimized_query

    # 2. Centralized TTL Policy must assign NO_CACHE (0s) or DYNAMIC (short TTL)
    ttl_policy = TTLPolicy()
    pkg = NormalizedQueryPackage(
        request_id="req-dyn-001",
        query_text=dynamic_query,
        task_category=TaskCategory.FACTUAL_QUESTION,
        client_metadata=ClientMetadata(extension_version="0.1.0"),
    )
    ttl_seconds, category, rationale = ttl_policy.resolve_ttl(
        package=pkg,
        is_time_sensitive=True,
        allow_time_sensitive=False,
    )
    assert ttl_seconds == 0
    assert category == TTLCategory.NO_CACHE
    assert rationale == "TIME_SENSITIVE_NO_CACHE"

    # 3. Semantic Eligibility Policy must reject dynamic / current event queries
    sem_policy = SemanticEligibilityPolicy()
    sem_decision = sem_policy.evaluate(package=pkg)
    assert sem_decision.is_eligible is False
    assert sem_decision.bypass_reason.startswith("BYPASS_")


# =========================================================================
# Category 8: Ambiguous Follow-ups
# =========================================================================

@pytest.mark.parametrize("ambiguous_query", [
    "How about Python?",
    "What about the second one?",
    "Can you change it?",
    "Yes, please.",
    "Try the other approach.",
    "Make it faster.",
])
def test_ambiguous_followups_prefer_doing_nothing(ambiguous_query):
    """The optimizer MUST PREFER DOING NOTHING over making a risky transformation or guessing intent."""
    optimizer = QueryOptimizer()
    res = optimizer.optimize(ambiguous_query)

    # Must NOT hallucinate expansions, add words, or rewrite ambiguous phrases
    assert res.optimized_query == ambiguous_query
    assert res.is_transformed is False
    assert res.char_savings == 0
    assert res.token_savings == 0
    assert len(res.transformations_applied) == 0

    # Guarded router with explicit context dependency must route to strong tier
    router = GuardedRouter()
    decision = router.route_query(
        query_text=ambiguous_query,
        has_context_dependency=True,
    )
    assert decision.final_route == "complex-model candidate"
    assert decision.recommended_tier == "strong"
    assert decision.context_guard_applied is True


# =========================================================================
# End-to-End API /api/v1/optimize Integration Test with Adversarial Input
# =========================================================================

def test_api_optimize_endpoint_adversarial_integrity():
    """Verifies that POST /api/v1/optimize preserves adversarial inputs without risky mutations."""
    payload = {
        "request_id": "req-adv-001",
        "query_text": 'Review this C++ code: `template<typename T> void fn(T&& val);` and clause: "Party A shall not be liable."',
        "task_category": "coding",
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
        }
    }
    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    # Verify protected code and quoted clause survived without corruption
    q_opt = data["query_optimization"]
    assert "`template<typename T> void fn(T&& val);`" in q_opt["optimized_query"]
    assert '"Party A shall not be liable."' in q_opt["optimized_query"]
    assert q_opt["original_query"] == payload["query_text"]
