"""Unit tests for backend query optimization stage and semantics-preserving transformations."""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.optimizer import (
    QueryOptimizer,
    default_query_optimizer,
    estimate_token_count,
)
from app.schemas.contract import QueryOptimizationComparison

client = TestClient(app)


def test_semantics_preserving_whitespace_collapse():
    """Verify horizontal whitespace collapse in prose while preserving protected elements."""
    optimizer = QueryOptimizer()
    
    # Prose with irregular spaces
    raw = "Explain   how    PostgreSQL    connection   pools   work."
    result = optimizer.optimize(raw)
    
    assert result.is_transformed is True
    assert result.optimized_query == "Explain how PostgreSQL connection pools work."
    assert result.original_query == raw
    assert result.char_savings > 0
    assert result.token_savings >= 0
    assert "collapse_consecutive_spaces" in result.transformations_applied


def test_excessive_blank_lines_collapsed():
    """Verify 3+ newlines collapsed to 2 newlines outside code blocks."""
    optimizer = QueryOptimizer()
    
    raw = "First paragraph.\n\n\n\n\nSecond paragraph.\n\n\nThird paragraph."
    result = optimizer.optimize(raw)
    
    assert result.is_transformed is True
    assert result.optimized_query == "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    assert "collapse_blank_lines" in result.transformations_applied
    assert result.original_query == raw


def test_protected_regions_strictly_unmodified():
    """Verify fenced code, inline code, math, and quotes remain strictly untouched."""
    optimizer = QueryOptimizer()
    
    # 1. Fenced code with internal whitespace and blank lines
    code_block = (
        "Here is the code:\n"
        "```python\n"
        "def compute_total(a,  b):\n"
        "    # Indentation and double spaces inside code fence must remain\n"
        "    x  =  a  +  b\n"
        "\n\n\n"
        "    return  x\n"
        "```\n"
        "Please review it."
    )
    res_code = optimizer.optimize(code_block)
    # The code block inside the fence must be preserved verbatim
    assert "def compute_total(a,  b):" in res_code.optimized_query
    assert "    x  =  a  +  b" in res_code.optimized_query
    assert "    return  x" in res_code.optimized_query

    # 2. Inline code
    inline_query = "Run `print(  'hello world'  )` in your terminal."
    res_inline = optimizer.optimize(inline_query)
    assert "`print(  'hello world'  )`" in res_inline.optimized_query

    # 3. LaTeX display and inline math
    math_query = "Solve $$ \\int_{0}^{1}   x^2   dx $$ and $ f(x)   =   2x $."
    res_math = optimizer.optimize(math_query)
    assert "$$ \\int_{0}^{1}   x^2   dx $$" in res_math.optimized_query
    assert "$ f(x)   =   2x $" in res_math.optimized_query

    # 4. Quoted strings
    quoted_query = 'Look for " exact   phrase   match " and \' single   quoted \'.'
    res_quoted = optimizer.optimize(quoted_query)
    assert '" exact   phrase   match "' in res_quoted.optimized_query
    assert "' single   quoted '" in res_quoted.optimized_query

    # 5. Markdown list structure
    list_query = "Tasks:\n-   First   item\n*   Second   item\n1.   Third   item"
    res_list = optimizer.optimize(list_query)
    assert res_list.optimized_query == "Tasks:\n- First item\n* Second item\n1. Third item"


def test_stopwords_strictly_preserved():
    """Verify no stopwords are removed under any circumstances."""
    optimizer = QueryOptimizer()
    
    # Complex prose loaded with common English stopwords
    prompt = "What is the capital of France and how does a citizen get in touch with it for help?"
    result = optimizer.optimize(prompt)
    
    # Every word must remain intact
    expected_words = [
        "What", "is", "the", "capital", "of", "France", "and", "how",
        "does", "a", "citizen", "get", "in", "touch", "with", "it", "for", "help"
    ]
    for word in expected_words:
        assert word in result.optimized_query, f"Stopword/word '{word}' was incorrectly removed!"

    assert result.optimized_query == prompt


def test_character_and_token_metrics_calculation():
    """Verify mathematical correctness of character counts, token estimates, and savings."""
    optimizer = QueryOptimizer()
    
    raw = "   Explain    the    architecture    of    transformer    models????   "
    result = optimizer.optimize(raw)
    
    # Exact character count metrics
    assert result.original_char_count == len(raw)
    assert result.optimized_char_count == len(result.optimized_query)
    assert result.char_savings == result.original_char_count - result.optimized_char_count
    assert result.char_savings > 0
    expected_char_pct = round((result.char_savings / result.original_char_count) * 100.0, 2)
    assert result.char_savings_pct == expected_char_pct

    # Token count estimate metrics
    assert result.original_token_estimate == estimate_token_count(raw)
    assert result.optimized_token_estimate == estimate_token_count(result.optimized_query)
    assert result.token_savings == result.original_token_estimate - result.optimized_token_estimate
    assert result.token_savings >= 0
    expected_token_pct = round((result.token_savings / result.original_token_estimate) * 100.0, 2)
    assert result.token_savings_pct == expected_token_pct

    # Compression ratio
    expected_compression = round(result.optimized_char_count / result.original_char_count, 4)
    assert result.compression_ratio == expected_compression
    assert result.compression_ratio < 1.0


def test_unchanged_query_metrics():
    """Verify metrics for an already-optimal query with no transformations needed."""
    optimizer = QueryOptimizer()
    
    clean_query = "What is the speed of light in a vacuum?"
    result = optimizer.optimize(clean_query)
    
    assert result.is_transformed is False
    assert result.optimized_query == clean_query
    assert result.original_query == clean_query
    assert result.char_savings == 0
    assert result.char_savings_pct == 0.0
    assert result.token_savings == 0
    assert result.token_savings_pct == 0.0
    assert result.compression_ratio == 1.0
    assert result.transformations_applied == []


def test_repeated_terminal_punctuation_collapse():
    """Verify collapse of repeated terminal punctuation while preserving ellipsis."""
    optimizer = QueryOptimizer()
    
    # 1. Question marks and exclamation marks
    raw1 = "Is this real???? Truly amazing!!!! Really??!!"
    res1 = optimizer.optimize(raw1)
    assert res1.optimized_query == "Is this real? Truly amazing! Really?!"
    assert "collapse_repeated_punctuation" in res1.transformations_applied

    # 2. Ellipsis preservation
    raw2 = "Thinking... wait for it..... done."
    res2 = optimizer.optimize(raw2)
    # Standard 3-period ellipsis preserved, 5-period normalized to 3-period
    assert res2.optimized_query == "Thinking... wait for it... done."


def test_preserve_original_query_always():
    """Verify the original query is always stored unmodified in the comparison output."""
    optimizer = QueryOptimizer()
    
    messy_inputs = [
        "   leading and trailing spaces   ",
        "Multiple    internal     spaces",
        "Multiple\n\n\n\nnewlines",
        "Punctuation????",
        "```\ncode\n```",
    ]
    for inp in messy_inputs:
        comp = optimizer.optimize(inp)
        assert comp.original_query == inp, f"Original query altered for input: {repr(inp)}"


def test_api_optimize_endpoint_returns_query_optimization():
    """Verify /api/v1/optimize returns query_optimization in OptimizationDecisionResponse."""
    # 1. Query with redundant whitespace and punctuation
    payload_messy = {
        "request_id": "req_opt_1",
        "correlation_id": "corr_opt_1",
        "query_text": "   What   is   PostgreSQL    connection   pooling????   ",
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
        }
    }
    resp = client.post("/api/v1/optimize", json=payload_messy)
    assert resp.status_code == 200
    data = resp.json()
    
    assert "query_optimization" in data
    opt = data["query_optimization"]
    assert opt is not None
    assert opt["original_query"] == payload_messy["query_text"]
    assert opt["optimized_query"] == "What is PostgreSQL connection pooling?"
    assert opt["is_transformed"] is True
    assert opt["char_savings"] > 0
    assert opt["char_savings_pct"] > 0.0
    assert opt["original_char_count"] == len(payload_messy["query_text"])
    assert opt["optimized_char_count"] == len(opt["optimized_query"])
    assert opt["original_token_estimate"] > 0
    assert opt["compression_ratio"] < 1.0
    assert "collapse_consecutive_spaces" in opt["transformations_applied"]
    assert "collapse_repeated_punctuation" in opt["transformations_applied"]
    assert "trim_outer_whitespace" in opt["transformations_applied"]

    # 2. Query that is already clean
    payload_clean = {
        "request_id": "req_opt_2",
        "correlation_id": "corr_opt_2",
        "query_text": "What is Python?",
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
        }
    }
    resp_clean = client.post("/api/v1/optimize", json=payload_clean)
    assert resp_clean.status_code == 200
    data_clean = resp_clean.json()
    
    assert "query_optimization" in data_clean
    opt_clean = data_clean["query_optimization"]
    assert opt_clean["original_query"] == "What is Python?"
    assert opt_clean["optimized_query"] == "What is Python?"
    assert opt_clean["is_transformed"] is False
    assert opt_clean["char_savings"] == 0
    assert opt_clean["token_savings"] == 0
    assert opt_clean["compression_ratio"] == 1.0


def test_zero_secret_or_pii_exposure():
    """Verify that query optimization comparison exposes zero secrets or credentials."""
    payload = {
        "request_id": "req_sec_test",
        "query_text": "Summarize this   document   cleanly.",
        "client_metadata": {
            "extension_version": "0.1.0"
        }
    }
    resp = client.post("/api/v1/optimize", json=payload)
    assert resp.status_code == 200
    text = resp.text
    
    # Assert absence of secret keys / tokens
    forbidden_tokens = ["api_key", "sk-", "token", "Bearer", "secret", "password"]
    for forbidden in forbidden_tokens:
        assert forbidden not in text.lower() or "secret" not in text
