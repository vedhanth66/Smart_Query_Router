"""Unit tests for HeuristicIncompletenessEvaluator.

Verifies:
1. Detection of obvious incompleteness failure patterns:
   - Empty or whitespace-only answers.
   - Non-alphanumeric trivial responses.
   - Premature truncation via unclosed code fences.
   - Premature truncation via dangling trailing connector words or trailing punctuation.
   - Explicit requested count mismatch (e.g. requested 5, provided 2).
   - Omission of explicitly required sections in structured tasks (e.g. 'Pros and Cons').
2. False-positive prevention:
   - Numbers that are dates, years, HTTP codes, or math equations do NOT trigger count mismatch.
   - Valid concise answers (e.g. "Paris") do NOT trigger empty/trivial.
   - Intentional ellipses ('...') do NOT trigger premature truncation.
   - Properly closed code fences do NOT trigger premature truncation.
   - Narrative ordinal enumeration satisfies count requests without rigid lists.
   - Conversational or passing mentions of "section" do NOT trigger section omission issues.
3. Strict non-claim guarantee:
   - Evaluator does NOT claim to verify factual correctness.
   - factual_correctness_verified is strictly False across all outcomes.
"""

import pytest

from app.schemas.evaluator import (
    EscalationRecommendation,
    IssueSeverity,
    IssueCategory,
    EvaluationRequest,
    EvaluatorType,
)
from app.evaluator.heuristic import HeuristicIncompletenessEvaluator


@pytest.fixture
def evaluator():
    return HeuristicIncompletenessEvaluator()


# --- 1. Obvious Failure Pattern Tests ---

@pytest.mark.anyio
async def test_empty_response_detection(evaluator):
    """Verify completely empty response triggers EMPTY_OR_TRIVIAL_RESPONSE."""
    req = EvaluationRequest(
        prompt="Explain the difference between TCP and UDP",
        candidate_response="",
    )
    res = await evaluator.evaluate(req)

    assert res.completeness == 0.0
    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert len(res.detected_issues) == 1
    assert res.detected_issues[0].issue_code == "EMPTY_OR_TRIVIAL_RESPONSE"
    assert res.detected_issues[0].severity == IssueSeverity.CRITICAL


@pytest.mark.anyio
async def test_whitespace_and_non_alphanumeric_response(evaluator):
    """Verify whitespace and punctuation-only responses are detected as trivial."""
    req_ws = EvaluationRequest(
        prompt="Write a quicksort implementation",
        candidate_response="    \n\t  \n  ",
    )
    res_ws = await evaluator.evaluate(req_ws)
    assert res_ws.completeness == 0.0
    assert res_ws.detected_issues[0].issue_code == "EMPTY_OR_TRIVIAL_RESPONSE"

    req_punct = EvaluationRequest(
        prompt="Explain quantum mechanics",
        candidate_response="... --- // ??",
    )
    res_punct = await evaluator.evaluate(req_punct)
    assert res_punct.completeness == 0.0
    assert res_punct.detected_issues[0].issue_code == "EMPTY_OR_TRIVIAL_RESPONSE"


@pytest.mark.anyio
async def test_premature_truncation_unclosed_code_fence(evaluator):
    """Verify unclosed markdown code fence triggers PREMATURE_TRUNCATION."""
    req = EvaluationRequest(
        prompt="Write a Python function to read JSON",
        candidate_response="Here is the function:\n```python\nimport json\ndef read_json(fp):\n    return json.load(fp)",
    )
    res = await evaluator.evaluate(req)

    assert res.completeness <= 0.4
    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "PREMATURE_TRUNCATION" for i in res.detected_issues)
    trunc_issue = next(i for i in res.detected_issues if i.issue_code == "PREMATURE_TRUNCATION")
    assert trunc_issue.metadata["reason"] == "unclosed_code_fence"


@pytest.mark.anyio
async def test_premature_truncation_trailing_connector(evaluator):
    """Verify trailing connector words trigger PREMATURE_TRUNCATION."""
    req = EvaluationRequest(
        prompt="Why did the system crash?",
        candidate_response="The primary root cause of the system failure occurred because",
    )
    res = await evaluator.evaluate(req)

    assert res.completeness <= 0.4
    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "PREMATURE_TRUNCATION" for i in res.detected_issues)
    trunc_issue = next(i for i in res.detected_issues if i.issue_code == "PREMATURE_TRUNCATION")
    assert trunc_issue.metadata["reason"] == "trailing_connector"
    assert trunc_issue.metadata["token"].lower() == "because"


@pytest.mark.anyio
async def test_premature_truncation_trailing_comma(evaluator):
    """Verify trailing dangling comma triggers PREMATURE_TRUNCATION."""
    req = EvaluationRequest(
        prompt="List the steps in the CI/CD pipeline",
        candidate_response="1. Lint the code, 2. Run unit tests, 3. Build container,",
    )
    res = await evaluator.evaluate(req)

    assert any(i.issue_code == "PREMATURE_TRUNCATION" for i in res.detected_issues)


@pytest.mark.anyio
async def test_requested_count_mismatch_numbered_list(evaluator):
    """Verify mismatch when prompt requested 5 items and response only provided 2."""
    prompt = "Give me 5 reasons to adopt TypeScript in our frontend codebase"
    response = """Here are the reasons:
1. Static typing catches runtime errors early.
2. IDE autocompletion and refactoring tools are vastly superior.
"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    count_issue = next(i for i in res.detected_issues if i.issue_code == "REQUESTED_COUNT_MISMATCH")
    assert count_issue.metadata["expected_count"] == 5
    assert count_issue.metadata["actual_count"] == 2
    assert res.completeness == 0.4


@pytest.mark.anyio
async def test_requested_count_mismatch_word_numeral(evaluator):
    """Verify word numeral count requests (e.g. 'four options')."""
    prompt = "Suggest four options for message queue backends"
    response = """- RabbitMQ: Traditional AMQP broker.
- Redis: Fast in-memory pub/sub."""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    count_issue = next(i for i in res.detected_issues if i.issue_code == "REQUESTED_COUNT_MISMATCH")
    assert count_issue.metadata["expected_count"] == 4
    assert count_issue.metadata["actual_count"] == 2


@pytest.mark.anyio
async def test_missing_required_sections_pros_and_cons(evaluator):
    """Verify missing required sections when prompt demands Pros and Cons but response only gives Pros."""
    prompt = "Analyze migrating to GraphQL. Provide Pros and Cons."
    response = """### Pros
- Eliminates over-fetching and under-fetching.
- Strongly-typed schema serves as living API documentation.
"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "MISSING_REQUIRED_SECTION" for i in res.detected_issues)
    sec_issue = next(i for i in res.detected_issues if i.issue_code == "MISSING_REQUIRED_SECTION")
    assert "Cons" in sec_issue.metadata["missing_sections"]


@pytest.mark.anyio
async def test_missing_required_named_sections(evaluator):
    """Verify missing required sections when prompt demands explicit sections."""
    prompt = "Write a technical proposal. Include sections: Summary, Architecture, Conclusion."
    response = """## Summary
This proposal describes the caching layer.

## Architecture
We use Redis cluster with master-replica replication.
"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert any(i.issue_code == "MISSING_REQUIRED_SECTION" for i in res.detected_issues)
    sec_issue = next(i for i in res.detected_issues if i.issue_code == "MISSING_REQUIRED_SECTION")
    assert "Conclusion" in sec_issue.metadata["missing_sections"]


# --- 2. False-Positive Prevention Tests ---

@pytest.mark.anyio
async def test_false_positive_date_in_query(evaluator):
    """Verify dates in query (e.g. 1999) do NOT trigger count mismatch."""
    req = EvaluationRequest(
        prompt="What notable events occurred in 1999 regarding the dot-com bubble?",
        candidate_response="In 1999, venture capital poured into internet startups at unprecedented valuations.",
    )
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    assert res.completeness == 1.0
    assert res.escalation_recommendation == EscalationRecommendation.NO_ESCALATION


@pytest.mark.anyio
async def test_false_positive_http_code_in_query(evaluator):
    """Verify HTTP status codes (e.g. HTTP 404, status 500) do NOT trigger count mismatch."""
    req = EvaluationRequest(
        prompt="Explain what an HTTP 404 Not Found error means and how to handle it",
        candidate_response="An HTTP 404 error indicates that the requested resource could not be found on the server.",
    )
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    assert res.completeness == 1.0


@pytest.mark.anyio
async def test_false_positive_arithmetic_in_query(evaluator):
    """Verify math formulas (e.g. 5 + 3) do NOT trigger count mismatch."""
    req = EvaluationRequest(
        prompt="Calculate 5 + 3 and explain why",
        candidate_response="The answer is 8. In standard Peano arithmetic, adding 3 to 5 yields 8.",
    )
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    assert res.completeness == 1.0


@pytest.mark.anyio
async def test_false_positive_concise_valid_answer(evaluator):
    """Verify legitimate concise single-word answer does NOT trigger EMPTY_OR_TRIVIAL_RESPONSE."""
    req = EvaluationRequest(
        prompt="What is the capital of France?",
        candidate_response="Paris",
    )
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "EMPTY_OR_TRIVIAL_RESPONSE" for i in res.detected_issues)
    assert res.completeness == 1.0


@pytest.mark.anyio
async def test_false_positive_intentional_ellipsis(evaluator):
    """Verify intentional ellipsis at the end does NOT trigger premature truncation."""
    req = EvaluationRequest(
        prompt="Write a cliffhanger sentence for a mystery novel",
        candidate_response="She slowly opened the cellar door, but nothing could prepare her for what lay in the shadows...",
    )
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "PREMATURE_TRUNCATION" for i in res.detected_issues)
    assert res.completeness == 1.0


@pytest.mark.anyio
async def test_false_positive_properly_closed_code_fence(evaluator):
    """Verify properly closed markdown code block does NOT trigger premature truncation."""
    req = EvaluationRequest(
        prompt="Write a python hello world function",
        candidate_response="Here is your function:\n```python\ndef hello():\n    print('Hello, world!')\n```",
    )
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "PREMATURE_TRUNCATION" for i in res.detected_issues)
    assert res.completeness == 1.0


@pytest.mark.anyio
async def test_false_positive_exact_requested_count_satisfied(evaluator):
    """Verify prompt asking for 3 items with 3 provided items produces zero issues."""
    prompt = "List 3 methods for handling missing data in pandas"
    response = """1. dropna(): Discard rows with null values.
2. fillna(): Impute missing values with a scalar or mean.
3. interpolate(): Fill values using polynomial or linear interpolation."""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert len(res.detected_issues) == 0
    assert res.completeness == 1.0
    assert res.escalation_recommendation == EscalationRecommendation.NO_ESCALATION


@pytest.mark.anyio
async def test_false_positive_narrative_ordinal_enumeration(evaluator):
    """Verify narrative paragraphs using 'First, ... Second, ... Third, ...' satisfy count requests."""
    prompt = "Explain 3 benefits of containerization"
    response = (
        "First, containerization packages application dependencies into an immutable artifact. "
        "Second, it ensures consistency across staging and production environments. "
        "Third, containers start in milliseconds compared to heavy virtual machines."
    )
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    assert res.completeness == 1.0


@pytest.mark.anyio
async def test_false_positive_incidental_mention_of_section(evaluator):
    """Verify incidental mention of 'section' in prompt does not demand structured sections."""
    req = EvaluationRequest(
        prompt="In the legal section of our contract, how is indemnity defined?",
        candidate_response="Indemnity is an obligation by one party to compensate the other for specified loss or damage.",
    )
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "MISSING_REQUIRED_SECTION" for i in res.detected_issues)
    assert res.completeness == 1.0


# --- 3. Strict Non-Claim & Factual Non-Verification Audit ---

@pytest.mark.anyio
async def test_factual_non_claim_audit(evaluator):
    """Verify all results explicitly disclaim factual verification."""
    req = EvaluationRequest(
        prompt="What is the speed of light?",
        candidate_response="The speed of light in vacuum is approximately 299,792,458 meters per second.",
    )
    res = await evaluator.evaluate(req)

    assert res.metadata["factual_correctness_verified"] is False
    assert "does not verify factual accuracy" in res.explanation
    assert "incompleteness_heuristics_applied" in res.metadata


# --- 4. Structural Consistency Checks (Bullets, Sections, Output Fields) ---

@pytest.mark.anyio
async def test_requested_bullet_count_mismatch(evaluator):
    """Verify prompt asking for 5 bullets escalates when response only provides 2."""
    prompt = "Provide 5 bullets explaining why unit testing is important"
    response = """Here are the key points:
- Catches regressions before code hits staging
- Facilitates confident refactoring
"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    issue = next(i for i in res.detected_issues if i.issue_code == "REQUESTED_COUNT_MISMATCH")
    assert issue.metadata["expected_count"] == 5
    assert issue.metadata["actual_count"] == 2
    assert issue.metadata["structure_type"] == "bullets"
    assert res.completeness == 0.40


@pytest.mark.anyio
async def test_requested_bullets_returned_as_pure_prose(evaluator):
    """Verify prompt asking for bullets escalates when response provides pure prose with 0 bullets."""
    prompt = "In 3 bullets, summarize the incident report"
    response = "The database cluster experienced elevated connection latency due to a deadlock in the transaction manager during morning peak traffic."
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    issue = next(i for i in res.detected_issues if i.issue_code == "REQUESTED_COUNT_MISMATCH")
    assert issue.metadata["expected_count"] == 3
    assert issue.metadata["actual_count"] == 0
    assert issue.metadata["structure_type"] == "bullets"
    assert res.completeness == 0.0


@pytest.mark.anyio
async def test_requested_bullets_without_count_missing(evaluator):
    """Verify prompt requesting bullet points without explicit count flags missing bullets when pure prose."""
    prompt = "Explain photosynthesis in bullet points"
    response = "Photosynthesis is the biological process used by plants to convert light energy into chemical energy."
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "MISSING_REQUIRED_BULLETS" for i in res.detected_issues)
    assert res.completeness == 0.50


@pytest.mark.anyio
async def test_stylistic_indifference_bullet_markers(evaluator):
    """Verify different bullet markers (*, -, •) and numbered bullets satisfy bullet requests without penalty."""
    prompt = "Provide 3 bullets on code quality"
    response = """Quality checklist:
* Clean naming conventions
• Comprehensive unit tests
- Continuous automated integration
"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert len(res.detected_issues) == 0
    assert res.completeness == 1.0
    assert res.escalation_recommendation == EscalationRecommendation.NO_ESCALATION


@pytest.mark.anyio
async def test_requested_section_count_mismatch(evaluator):
    """Verify prompt requesting a specific number of sections escalates when response provides fewer."""
    prompt = "In 3 sections, explain backend scaling: Caching, Database Sharding, and Load Balancing"
    response = """## 1. Caching
Redis caches hot keys to reduce primary database load.
"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "REQUESTED_COUNT_MISMATCH" for i in res.detected_issues)
    issue = next(i for i in res.detected_issues if i.issue_code == "REQUESTED_COUNT_MISMATCH")
    assert issue.metadata["expected_count"] == 3
    assert issue.metadata["actual_count"] == 1
    assert issue.metadata["structure_type"] == "sections"


@pytest.mark.anyio
async def test_explicit_output_fields_missing(evaluator):
    """Verify prompt requesting specific output fields flags MISSING_REQUIRED_FIELD when fields are omitted."""
    prompt = "Generate a deployment status report with fields: environment, version, deployer, status"
    response = """**Environment:** production
**Version:** v2.4.1
**Status:** success
"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "MISSING_REQUIRED_FIELD" for i in res.detected_issues)
    issue = next(i for i in res.detected_issues if i.issue_code == "MISSING_REQUIRED_FIELD")
    assert issue.metadata["missing_fields"] == ["deployer"]
    assert issue.metadata["required_fields"] == ["environment", "version", "deployer", "status"]
    assert res.completeness == 0.70  # 3 of 4 present capped at 0.7


@pytest.mark.anyio
async def test_explicit_output_fields_json_missing_keys(evaluator):
    """Verify JSON structure requests detect missing keys without stylistic penalties."""
    prompt = "Return a JSON object with fields: id, username, email, is_active"
    response = """```json
{
  "id": 101,
  "username": "alice",
  "is_active": true
}
```"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert res.escalation_recommendation == EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
    assert any(i.issue_code == "MISSING_REQUIRED_FIELD" for i in res.detected_issues)
    issue = next(i for i in res.detected_issues if i.issue_code == "MISSING_REQUIRED_FIELD")
    assert "email" in issue.metadata["missing_fields"]


@pytest.mark.anyio
async def test_stylistic_indifference_output_fields(evaluator):
    """Verify casing differences and markdown formats satisfy field requirements without penalty."""
    prompt = "Provide user info with fields: user_id, email_address, account_status"
    response = """User Record:
- **User Id:** 4242
- Email Address: user@corp.internal
- account_status = "active"
"""
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert len(res.detected_issues) == 0
    assert res.completeness == 1.0
    assert res.escalation_recommendation == EscalationRecommendation.NO_ESCALATION


@pytest.mark.anyio
async def test_false_positive_field_of_study(evaluator):
    """Verify conversational use of 'field of ...' does not trigger false field consistency check."""
    prompt = "Describe recent breakthrough architectures in the field of natural language processing"
    response = "Transformer architectures have largely superseded recurrent neural networks for sequence modeling."
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "MISSING_REQUIRED_FIELD" for i in res.detected_issues)
    assert res.completeness == 1.0


@pytest.mark.anyio
async def test_false_positive_sports_field(evaluator):
    """Verify physical sports mentions like 'football field' do not trigger field consistency check."""
    prompt = "How many yards are marked on a standard football field?"
    response = "A standard American football field is 100 yards long between the goal lines, with two 10-yard end zones."
    req = EvaluationRequest(prompt=prompt, candidate_response=response)
    res = await evaluator.evaluate(req)

    assert not any(i.issue_code == "MISSING_REQUIRED_FIELD" for i in res.detected_issues)
    assert res.completeness == 1.0

