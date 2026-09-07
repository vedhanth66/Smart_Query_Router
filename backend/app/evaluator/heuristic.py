"""Heuristic evaluator for obvious incompleteness and failure patterns.

DESIGN & SCOPE:
- Identifies obvious, structural, and syntactic failure patterns:
  1. Empty or whitespace-only answers, or answers lacking alphanumeric content.
  2. Premature truncation (unclosed code fences, dangling connector words or incomplete punctuation).
  3. Mismatch with explicit requested item count (e.g. asked for 5 reasons, provided 2).
  4. Omission of explicitly required sections in structured tasks (e.g. 'Pros and Cons').
- Strict Non-Claim:
  Does NOT verify factual correctness or reasoning validity. Its sole purpose is to
  detect obvious incompleteness. Explanations and metadata explicitly state that
  factual correctness is unverified.
- False-Positive Prevention:
  Safeguards against falsely flagging dates, HTTP status codes, math expressions,
  intentional ellipses ('...'), properly closed code fences, or valid concise answers.
"""

import re
import time
from typing import Any

from ..schemas.evaluator import (
    EvaluatorType,
    EscalationRecommendation,
    IssueSeverity,
    IssueCategory,
    EvaluationIssue,
    EvaluationRequest,
    EvaluationResult,
)
from .base import BaseEvaluator


class HeuristicIncompletenessEvaluator(BaseEvaluator):
    """Evaluates candidate responses for obvious structural incompleteness."""

    DEFAULT_EVALUATOR_ID = "heuristic-incompleteness-v1"

    WORD_TO_NUM: dict[str, int] = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }

    COUNT_ITEMS_PATTERN = (
        r"(?:examples|reasons|options|tips|ideas|steps|ways|items|points|"
        r"functions|questions|bullet points|bullets|sections|fields|keys|"
        r"methods|recommendations|approaches|alternatives|benefits|features|"
        r"principles|components|advantages|disadvantages|drawbacks)"
    )

    def __init__(self, evaluator_id: str = DEFAULT_EVALUATOR_ID):
        self._evaluator_id = evaluator_id

    @property
    def evaluator_id(self) -> str:
        return self._evaluator_id

    @property
    def evaluator_type(self) -> EvaluatorType:
        return EvaluatorType.HEURISTIC

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        """Execute deterministic heuristic incompleteness and structural consistency checks."""
        start_time = time.perf_counter()
        issues: list[EvaluationIssue] = []

        prompt = request.prompt.strip()
        response = request.candidate_response.strip()

        # 1. Check for empty or trivial response
        empty_issue = self._check_empty_or_trivial(response, prompt)
        if empty_issue:
            issues.append(empty_issue)
            elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 3)
            return self._build_result(
                request=request,
                completeness=0.0,
                confidence=0.99,
                issues=issues,
                elapsed_ms=elapsed_ms,
                summary="Candidate response is empty or contains no alphanumeric content."
            )

        # 2. Check for premature truncation
        trunc_issue = self._check_premature_truncation(response, prompt)
        if trunc_issue:
            issues.append(trunc_issue)

        # 3. Check for explicit requested count mismatch (bullets, items, sections)
        count_issue, count_completeness = self._check_requested_count_mismatch(response, prompt)
        if count_issue:
            issues.append(count_issue)

        # 4. Check for bullet structure presence when bullets requested without count
        bullet_issue, bullet_completeness = self._check_bullet_structure_presence(response, prompt)
        if bullet_issue:
            issues.append(bullet_issue)

        # 5. Check for missing required sections (named sections: Pros and Cons, sections: A, B, C)
        sec_issue, sec_completeness = self._check_missing_required_sections(response, prompt)
        if sec_issue:
            issues.append(sec_issue)

        # 6. Check for missing required output fields (fields: a, b, c)
        field_issue, field_completeness = self._check_missing_required_fields(response, prompt)
        if field_issue:
            issues.append(field_issue)

        # Determine overall completeness and escalation recommendation
        if issues:
            completeness = 1.0
            if count_completeness is not None:
                completeness = min(completeness, count_completeness)
            if bullet_completeness is not None:
                completeness = min(completeness, bullet_completeness)
            if sec_completeness is not None:
                completeness = min(completeness, sec_completeness)
            if field_completeness is not None:
                completeness = min(completeness, field_completeness)
            if any(i.issue_code == "PREMATURE_TRUNCATION" for i in issues):
                completeness = min(completeness, 0.4)

            # Cap completeness if structural or incompleteness issues exist
            completeness = min(completeness, 0.7)
            recommendation = EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
            summary = f"Detected {len(issues)} structural or incompleteness issue(s)."
        else:
            completeness = 1.0
            recommendation = EscalationRecommendation.NO_ESCALATION
            summary = "No obvious incompleteness detected."

        elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 3)
        return self._build_result(
            request=request,
            completeness=completeness,
            confidence=0.85 if issues else 0.80,
            issues=issues,
            elapsed_ms=elapsed_ms,
            summary=summary,
        )

    def _check_empty_or_trivial(self, response: str, prompt: str) -> EvaluationIssue | None:
        """Flag completely empty responses or responses lacking any alphanumeric content."""
        if not response:
            return EvaluationIssue(
                issue_code="EMPTY_OR_TRIVIAL_RESPONSE",
                message="Candidate response is completely empty or contains only whitespace.",
                severity=IssueSeverity.CRITICAL,
                category=IssueCategory.COMPLETENESS,
                metadata={"response_length": 0}
            )

        # Check if response has any alphanumeric character
        if not re.search(r"[a-zA-Z0-9]", response):
            return EvaluationIssue(
                issue_code="EMPTY_OR_TRIVIAL_RESPONSE",
                message="Candidate response contains no alphanumeric content (only punctuation or whitespace).",
                severity=IssueSeverity.CRITICAL,
                category=IssueCategory.COMPLETENESS,
                metadata={"response_preview": response[:30]}
            )

        return None

    def _check_premature_truncation(self, response: str, prompt: str) -> EvaluationIssue | None:
        """Flag responses that cut off mid-thought or mid-block."""
        # 1. Check for unclosed markdown code fence (odd count of ```)
        if response.count("```") % 2 != 0:
            return EvaluationIssue(
                issue_code="PREMATURE_TRUNCATION",
                message="Candidate response ended with an unclosed markdown code fence, indicating premature cutoff.",
                severity=IssueSeverity.HIGH,
                category=IssueCategory.COMPLETENESS,
                metadata={"reason": "unclosed_code_fence", "fence_count": response.count("```")}
            )

        # False-positive guard: intentional ellipsis at end is not truncation
        if response.endswith("...") or response.endswith("…"):
            return None

        # False-positive guard: properly closed fences or quotes
        if response.endswith("```") or response.endswith("'''") or response.endswith('"""'):
            return None

        # 2. Check for trailing dangling connector words (e.g. 'because', 'such as', 'and', 'including')
        trailing_connector = re.search(
            r"\b(and|or|because|with|such as|for example|including|the|a|an|to|of|in|that|which|is|are|was|were)\s*$",
            response,
            re.IGNORECASE
        )
        if trailing_connector:
            word = trailing_connector.group(1)
            return EvaluationIssue(
                issue_code="PREMATURE_TRUNCATION",
                message=f"Candidate response terminated abruptly with trailing connector word '{word}'.",
                severity=IssueSeverity.HIGH,
                category=IssueCategory.COMPLETENESS,
                metadata={"reason": "trailing_connector", "token": word}
            )

        # 3. Check for trailing dangling punctuation indicating an incomplete sentence (trailing comma or semicolon)
        trailing_dangling_punct = re.search(r"[,;]\s*$", response)
        if trailing_dangling_punct:
            return EvaluationIssue(
                issue_code="PREMATURE_TRUNCATION",
                message="Candidate response terminated abruptly with trailing comma or semicolon.",
                severity=IssueSeverity.HIGH,
                category=IssueCategory.COMPLETENESS,
                metadata={"reason": "dangling_punctuation"}
            )

        return None

    def _check_requested_count_mismatch(
        self,
        response: str,
        prompt: str
    ) -> tuple[EvaluationIssue | None, float | None]:
        """Detect when prompt explicitly requests a specific item/bullet/section count but response provides fewer."""
        # 1. Extract requested count and structure type from prompt
        count_info = self._extract_requested_structure_count(prompt)
        if not count_info:
            return None, None
        expected_count, structure_type = count_info
        if expected_count < 2:
            return None, None

        # 2. Count items provided in response for that structure type
        actual_count = self._count_response_items(response, structure_type)

        # If response provides fewer items than requested
        if actual_count < expected_count:
            completeness = round(actual_count / expected_count, 2) if expected_count > 0 else 0.0
            noun_desc = "bullet points" if structure_type == "bullets" else ("sections" if structure_type == "sections" else "items")
            issue = EvaluationIssue(
                issue_code="REQUESTED_COUNT_MISMATCH",
                message=(
                    f"Prompt explicitly requested {expected_count} {noun_desc}, but response only "
                    f"provided {actual_count}."
                ),
                severity=IssueSeverity.HIGH,
                category=IssueCategory.COMPLETENESS,
                metadata={
                    "expected_count": expected_count,
                    "actual_count": actual_count,
                    "structure_type": structure_type,
                }
            )
            return issue, completeness

        return None, None

    def _extract_requested_structure_count(self, prompt: str) -> tuple[int, str] | None:
        """Extract explicit requested count and structure type from prompt with false-positive guards."""
        # False-positive guard: math expressions like '5 + 3' or '10 / 2'
        if re.search(r"\b\d+\s*[\+\-\*\/]\s*\d+\b", prompt):
            return None

        # False-positive guard: HTTP codes (e.g. HTTP 404, status 500)
        if re.search(r"\b(?:http|status|error|code)\s+\d{3}\b", prompt, re.IGNORECASE):
            return None

        # False-positive guard: Years/dates (e.g. in 1999, in 2024, between 1990 and 2000)
        if re.search(r"\b(?:in|since|until|during|year)\s+(?:19|20)\d{2}\b", prompt, re.IGNORECASE):
            pass

        # Match: "give me 5 examples", "list 3 reasons", "provide 4 bullets", "10 tips", etc.
        pattern1 = (
            r"\b(?:give|provide|list|name|write|show|suggest|generate)\s+(?:me\s+)?"
            r"(?:at\s+least\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+("
            + self.COUNT_ITEMS_PATTERN
            + r")\b"
        )
        match = re.search(pattern1, prompt, re.IGNORECASE)

        # Match: "in 3 bullets", "in 4 sections", "divide into 3 sections"
        if not match:
            pattern2 = (
                r"\b(?:in|using|with|divide\s+(?:this\s+)?into)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+("
                + self.COUNT_ITEMS_PATTERN
                + r")\b"
            )
            match = re.search(pattern2, prompt, re.IGNORECASE)

        # Fallback: "5 tips to ...", "3 reasons why ..."
        if not match:
            pattern3 = (
                r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+("
                + self.COUNT_ITEMS_PATTERN
                + r")\b"
            )
            match = re.search(pattern3, prompt, re.IGNORECASE)

        if match:
            raw_val = match.group(1).lower()
            matched_noun = match.group(2).lower()
            num = int(raw_val) if raw_val.isdigit() else self.WORD_TO_NUM.get(raw_val)
            if num and 2 <= num <= 50:
                if "bullet" in matched_noun:
                    struct_type = "bullets"
                elif "section" in matched_noun:
                    struct_type = "sections"
                else:
                    struct_type = "items"
                return num, struct_type

        return None

    def _extract_requested_count(self, prompt: str) -> int | None:
        """Extract requested item count from prompt."""
        res = self._extract_requested_structure_count(prompt)
        return res[0] if res else None

    def _count_response_items(self, response: str, structure_type: str = "items") -> int:
        """Count list items, numbered items, sections, or ordinal narrative markers in response."""
        # 1. Numbered lists (e.g. '1.', '2)', '(1)')
        numbered_matches = re.findall(r"^\s*(?:\d+[\.\)]|\(\d+\))\s+", response, re.MULTILINE)
        num_count = len(numbered_matches) if numbered_matches else 0

        # 2. Bullet points (e.g. '-', '*', '•')
        bullet_matches = re.findall(r"^\s*[-*•]\s+", response, re.MULTILINE)
        bullet_count = len(bullet_matches) if bullet_matches else 0

        # 3. Headings denoting itemized sections (e.g. '### 1.', '### Option 1')
        heading_matches = re.findall(r"^\s*#{1,4}\s+(?:\d+[\.\)]|[A-Z])", response, re.MULTILINE)
        heading_count = len(heading_matches) if heading_matches else 0

        # General markdown headings
        all_headings = re.findall(r"^\s*#{1,4}\s+\S+", response, re.MULTILINE)
        all_heading_count = len(all_headings) if all_headings else 0

        # Bold section markers at line start (e.g. '**Section 1:**', '__Introduction:__')
        bold_sections = re.findall(r"^\s*(?:\*\*[^*]+\*\*|__[^_]+__):?", response, re.MULTILINE)
        bold_count = len(bold_sections) if bold_sections else 0

        if structure_type == "bullets":
            if bullet_count > 0:
                return bullet_count
            # Stylistic leniency: if user asked for bullets and model formatted as numbered list
            if num_count > 0:
                return num_count
            return 0

        if structure_type == "sections":
            return max(all_heading_count, bold_count, heading_count)

        # 4. Ordinal narrative enumeration ('First, ... Second, ... Third, ...')
        ordinals = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
        found_ordinals = set()
        for ord_word in ordinals:
            if re.search(rf"\b{ord_word}\b", response, re.IGNORECASE):
                found_ordinals.add(ord_word)

        ordinal_count = len(found_ordinals) if len(found_ordinals) >= 2 else 0

        return max(num_count, bullet_count, heading_count, ordinal_count)

    def _check_bullet_structure_presence(
        self,
        response: str,
        prompt: str
    ) -> tuple[EvaluationIssue | None, float | None]:
        """Detect when prompt explicitly requested bullet points (without a count) and response has none."""
        # Only check if prompt explicitly asks for bullet formatting
        bullet_request_pattern = (
            r"\b(?:in|as|using|with)\s+bullet\s+(?:points?|items?)\b|"
            r"\b(?:in|as|use)\s+bullets\b|"
            r"\busing\s+bullets\b"
        )
        if not re.search(bullet_request_pattern, prompt, re.IGNORECASE):
            return None, None

        # Check if response contains any bullet points (-, *, •) or numbered bullets (1., 2.)
        has_bullets = bool(re.search(r"^\s*[-*•]\s+", response, re.MULTILINE))
        has_numbered = bool(re.search(r"^\s*(?:\d+[\.\)]|\(\d+\))\s+", response, re.MULTILINE))

        if not has_bullets and not has_numbered:
            issue = EvaluationIssue(
                issue_code="MISSING_REQUIRED_BULLETS",
                message="Prompt explicitly requested bullet points, but candidate response did not contain any bullet points.",
                severity=IssueSeverity.HIGH,
                category=IssueCategory.COMPLETENESS,
                metadata={"structure_type": "bullets"}
            )
            return issue, 0.5

        return None, None

    def _check_missing_required_sections(
        self,
        response: str,
        prompt: str
    ) -> tuple[EvaluationIssue | None, float | None]:
        """Detect when prompt explicitly requires specific titled sections and response omits them."""
        required_sections = self._extract_required_sections(prompt)
        if not required_sections:
            return None, None

        missing_sections = []
        for sec in required_sections:
            # Check if section title exists in response as heading, bold label, or key
            escaped_sec = re.escape(sec)
            pattern = rf"(?:^#{1,4}\s+.*?\b{escaped_sec}\b|\*\*{escaped_sec}:?\*\*|^\s*{escaped_sec}:)"
            if not re.search(pattern, response, re.IGNORECASE | re.MULTILINE):
                # Fallback: if section is 'Code', accept presence of markdown code block
                if sec.lower() == "code" and "```" in response:
                    continue
                missing_sections.append(sec)

        if missing_sections:
            completeness = round(
                (len(required_sections) - len(missing_sections)) / len(required_sections),
                2
            )
            issue = EvaluationIssue(
                issue_code="MISSING_REQUIRED_SECTION",
                message=(
                    f"Prompt explicitly required structured sections ({', '.join(required_sections)}), "
                    f"but response omitted: {', '.join(missing_sections)}."
                ),
                severity=IssueSeverity.HIGH,
                category=IssueCategory.COMPLETENESS,
                metadata={
                    "required_sections": required_sections,
                    "missing_sections": missing_sections,
                }
            )
            return issue, completeness

        return None, None

    def _extract_required_sections(self, prompt: str) -> list[str]:
        """Extract explicit required section titles from prompt with false-positive guards."""
        # Check for explicit 'Pros and Cons'
        if re.search(r"\bpros\s+(?:and|&)\s+cons\b", prompt, re.IGNORECASE):
            return ["Pros", "Cons"]

        # Check for explicit 'Explanation and Code'
        if re.search(r"\b(?:both\s+)?explanation\s+(?:and|&)\s+code\b", prompt, re.IGNORECASE):
            return ["Explanation", "Code"]

        # Check for 'sections: [A, B, C]' or 'include sections [A, B, and C]'
        pattern = r"\b(?:include|provide|with|create)\s+(?:the\s+)?(?:following\s+)?sections?:?\s*([A-Za-z0-9,\s\-&/]+?)(?:\.|$|\n)"
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            raw_secs = match.group(1)
            # Split on commas or 'and'
            parts = [s.strip() for s in re.split(r",|\band\b", raw_secs) if s.strip()]
            valid_secs = [p for p in parts if len(p) <= 30 and re.match(r"^[A-Za-z0-9\s\-]+$", p)]
            if len(valid_secs) >= 2:
                return valid_secs

        return []

    def _check_missing_required_fields(
        self,
        response: str,
        prompt: str
    ) -> tuple[EvaluationIssue | None, float | None]:
        """Detect when prompt explicitly requires specific output fields and response omits them."""
        required_fields = self._extract_required_fields(prompt)
        if not required_fields:
            return None, None

        missing_fields = [
            f for f in required_fields
            if not self._is_field_present_in_response(f, response)
        ]

        if missing_fields:
            completeness = round(
                (len(required_fields) - len(missing_fields)) / len(required_fields),
                2
            )
            issue = EvaluationIssue(
                issue_code="MISSING_REQUIRED_FIELD",
                message=(
                    f"Prompt explicitly requested output fields ({', '.join(required_fields)}), "
                    f"but response omitted: {', '.join(missing_fields)}."
                ),
                severity=IssueSeverity.HIGH,
                category=IssueCategory.COMPLETENESS,
                metadata={
                    "required_fields": required_fields,
                    "missing_fields": missing_fields,
                    "structure_type": "output_fields",
                }
            )
            return issue, completeness

        return None, None

    def _extract_required_fields(self, prompt: str) -> list[str]:
        """Extract explicit required field/key names from prompt with false-positive guards."""
        # False-positive guard 1: "in the field of ...", "field of study", "across the field"
        if re.search(r"\b(?:in|of|across|within)\s+(?:the\s+)?field\s+of\b", prompt, re.IGNORECASE):
            return []

        # False-positive guard 2: physical sports or science fields
        if re.search(r"\b(?:football|baseball|soccer|playing|sports|battle|magnetic|electric|gravitational)\s+fields?\b", prompt, re.IGNORECASE):
            return []
        if re.search(r"\bfield\s+(?:hockey|trip|goals?|work|test|guide)\b|\btrack\s+and\s+field\b", prompt, re.IGNORECASE):
            return []

        patterns = [
            r"\b(?:output\s+)?(?:fields|keys|attributes):\s*\[?([A-Za-z0-9_,\s\-&/]+?)\]?(?:\.|$|\n)",
            r"\b(?:with|include|containing|provide|return|format as|having)\s+(?:the\s+)?(?:following\s+)?(?:fields|keys|attributes):?\s*\[?([A-Za-z0-9_,\s\-&/]+?)\]?(?:\.|$|\n)",
            r"\b(?:json|yaml|object|dictionary|record)\s+(?:with|containing)\s+(?:the\s+)?(?:fields|keys|attributes):?\s*\[?([A-Za-z0-9_,\s\-&/]+?)\]?(?:\.|$|\n)",
        ]

        for pat in patterns:
            match = re.search(pat, prompt, re.IGNORECASE)
            if match:
                raw = match.group(1).strip()
                tokens = re.split(r",|\band\b", raw, flags=re.IGNORECASE)
                clean_fields = []
                for t in tokens:
                    cleaned = re.sub(r"[`'\"\[\]:]", "", t).strip()
                    if cleaned and cleaned.lower() not in {"and", "or", "the", "a", "an", "with", "fields", "keys", "attributes"}:
                        if re.match(r"^[A-Za-z0-9_]{1,32}$", cleaned):
                            clean_fields.append(cleaned)
                if len(clean_fields) >= 2 or (clean_fields and ":" in match.group(0)):
                    seen = set()
                    unique_fields = []
                    for f in clean_fields:
                        fl = f.lower()
                        if fl not in seen:
                            seen.add(fl)
                            unique_fields.append(f)
                    if unique_fields:
                        return unique_fields

        return []

    def _is_field_present_in_response(self, field: str, response: str) -> bool:
        """Check if a field is present in response without judging style preferences (JSON/YAML/markdown)."""
        escaped = re.escape(field)
        # Handle flexible separators: 'first_name' matches 'First Name', 'first-name', 'first_name'
        escaped_flexible = re.sub(r"[_\-\s]+", r"[\\s_-]+", escaped)

        # Match JSON keys ("field": / 'field':), markdown headers (### field),
        # bold labels (**field:** / **field**:), bullet keys (- field:), or assignments (field =)
        pattern = (
            rf"""(?i)(?:["']{escaped_flexible}["']\s*:|"""
            rf"""^\s*#{1,4}\s+.*?\b{escaped_flexible}\b|"""
            rf"""\b{escaped_flexible}\b\s*:|"""
            rf"""\*\*{escaped_flexible}:?\*\*|"""
            rf"""^\s*[-*•]\s+.*?{escaped_flexible}\b\s*:|"""
            rf"""\b{escaped_flexible}\s*=)"""
        )
        return bool(re.search(pattern, response, re.MULTILINE))

    def _build_result(
        self,
        request: EvaluationRequest,
        completeness: float,
        confidence: float,
        issues: list[EvaluationIssue],
        elapsed_ms: float,
        summary: str,
    ) -> EvaluationResult:
        """Construct standardized EvaluationResult with strict non-factual disclaimer."""
        eval_id = f"eval_{int(time.time() * 1000)}_{self.evaluator_id}"
        if request.correlation_id:
            eval_id = f"{request.correlation_id}_{eval_id}"

        recommendation = (
            EscalationRecommendation.ESCALATE_TO_STRONGER_MODEL
            if issues
            else EscalationRecommendation.NO_ESCALATION
        )

        full_explanation = (
            f"{summary} (Heuristic incompleteness check only; does not verify factual accuracy.)"
        )

        return EvaluationResult(
            evaluation_id=eval_id,
            completeness=completeness,
            confidence=confidence,
            detected_issues=issues,
            escalation_recommendation=recommendation,
            evaluator_id=self.evaluator_id,
            evaluator_type=self.evaluator_type,
            latency_ms=elapsed_ms,
            explanation=full_explanation,
            metadata={
                "factual_correctness_verified": False,
                "incompleteness_heuristics_applied": [
                    "empty_or_trivial",
                    "premature_truncation",
                    "requested_count_mismatch",
                    "missing_required_sections",
                    "missing_required_fields",
                    "bullet_structure_consistency",
                    "structural_consistency_checks",
                ],
                "issues_count": len(issues),
            }
        )
