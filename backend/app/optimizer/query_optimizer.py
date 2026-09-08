"""Smart Query Router - Backend Query Optimizer.

Provides semantics-preserving query optimization that:
- Preserves the original query always alongside the optimized query.
- Employs strictly semantics-preserving transformations:
  - Collapsing excessive horizontal whitespace in non-code, non-quoted prose
  - Collapsing 3+ consecutive newlines down to 2 outside code fences
  - Trimming trailing whitespace on lines
  - Collapsing redundant repeated terminal punctuation (e.g. ???? -> ?, !!!! -> !)
  - Preserving standard ellipsis (...)
- Strictly protects:
  - Fenced code blocks (```...``` and ~~~...~~~)
  - Inline code (`...`)
  - LaTeX display and inline math ($$...$$ and $...$)
  - Quoted strings ("..." and '...')
  - Markdown list item markers and indentation
- Strictly avoids:
  - Broad stopword removal (never strips "the", "is", "a", "of", "and", etc.)
  - Aggressive compression, semantic loss, or syntactic truncation
- Computes measured metrics:
  - Character counts (original, optimized, savings, savings percentage)
  - Token count estimates (original, optimized, savings, savings percentage)
  - Compression ratio (<=1.0 indicates reduction)
  - Applied transformation rule names
"""

import math
import re
from typing import Any
from ..schemas.contract import QueryOptimizationComparison


# Matches code fence opening or closing lines (e.g. ```python, ```, ~~~)
CODE_FENCE_PATTERN = re.compile(r"^\s*(?:```|~~~)")

# Matches markdown list item prefix (ordered or unordered) with leading indentation
LIST_ITEM_PATTERN = re.compile(r"^(\s*)([-*+]|\d+\.)[ \t]+(.*)$")

# Matches markdown table rows: e.g. | col 1 | col 2 |
TABLE_ROW_PATTERN = re.compile(r"^\s*\|.+?\|\s*$")

# Matches ASCII/markdown table border or separator rows: e.g. +---+---+ or |---|---|
ASCII_TABLE_PATTERN = re.compile(r"^\s*[\+\|][-+=]+[\+\|]\s*$")

# Matches protected inline structures:
# 1. Inline code: `...`
# 2. Display math: $$...$$
# 3. Inline math: $...$
# 4. Double-quoted strings: "..."
# 5. Single-quoted strings: '...'
INLINE_PROTECTED_PATTERN = re.compile(
    r"(`[^`\n]+`)|"
    r"(\$\$[^\$\n]+\$\$)|"
    r"(\$[^\$\n]+\$)|"
    r'("(?:[^"\\]|\\.)*")|'
    r"('(?:[^'\\]|\\.)*')"
)


def estimate_token_count(text: str) -> int:
    """Deterministic token count estimation for text.
    
    Uses word boundaries, subword splitting for longer identifiers (~4 chars/token),
    whitespace segments, and punctuation boundaries.
    
    Provides a consistent, transparent metric without requiring external model dependencies.
    """
    if not text:
        return 0
    
    # Matches words/identifiers, non-whitespace punctuation, and whitespace sequences
    tokens = re.findall(r"\w+|[^\w\s]|\s+", text)
    count = 0
    for t in tokens:
        if t.isalnum() or "_" in t:
            # Words longer than 4 chars typically split into subwords
            count += max(1, math.ceil(len(t) / 4))
        elif t.isspace():
            # Indentation and newlines: ~1 token per 4 spaces or per newline sequence
            count += max(1, math.ceil(len(t) / 4))
        else:
            # Punctuation / symbols: 1 token per 1-2 chars
            count += max(1, math.ceil(len(t) / 2))
    return count


class QueryOptimizer:
    """Semantics-preserving backend query optimizer."""

    def _tokenize_inline(self, text: str) -> list[tuple[str, str]]:
        """Splits an inline line into PROTECTED and PROSE segments."""
        segments: list[tuple[str, str]] = []
        last_idx = 0
        for match in INLINE_PROTECTED_PATTERN.finditer(text):
            start, end = match.span()
            if start > last_idx:
                segments.append(("PROSE", text[last_idx:start]))
            segments.append(("PROTECTED", match.group(0)))
            last_idx = end
        if last_idx < len(text):
            segments.append(("PROSE", text[last_idx:]))
        return segments

    def _transform_prose(self, prose: str, applied_rules: set[str]) -> str:
        """Applies semantics-preserving transformations to a prose segment."""
        orig = prose
        # 1. Collapse multiple consecutive spaces and tabs down to a single space
        transformed = re.sub(r"[ \t]+", " ", prose)
        if transformed != orig:
            applied_rules.add("collapse_consecutive_spaces")

        # 2. Collapse excessive repeated terminal punctuation (e.g. ???? -> ?, !!!! -> !)
        before_punct = transformed
        # Collapse mixed ? and ! runs (e.g. ??!!, !?!?) to ?!
        transformed = re.sub(r"(?:\?+!+|!+\?+)[!?]*", "?!", transformed)
        # Collapse 2+ question marks to a single ?
        transformed = re.sub(r"\?{2,}", "?", transformed)
        # Collapse 2+ exclamation marks to a single !
        transformed = re.sub(r"!{2,}", "!", transformed)
        # Collapse 4+ periods down to standard 3-period ellipsis (...)
        transformed = re.sub(r"\.{4,}", "...", transformed)
        if transformed != before_punct:
            applied_rules.add("collapse_repeated_punctuation")

        return transformed

    def _optimize_line(self, line: str, applied_rules: set[str]) -> str:
        """Optimizes a single line outside of a code fence."""
        if not line or not line.strip():
            return ""

        # Preserve leading indentation for indented code blocks (4+ spaces or tabs)
        leading_indent_match = re.match(r"^[ \t]*", line)
        leading_indent = leading_indent_match.group(0) if leading_indent_match else ""
        remainder = line[len(leading_indent):]

        # Check if line is indented code
        if leading_indent.startswith("    ") or leading_indent.startswith("\t"):
            # Indented code block: preserve internal spacing, only strip trailing whitespace
            if line.rstrip() != line:
                applied_rules.add("trim_line_whitespace")
            return leading_indent + remainder.rstrip()

        # Check if line is a table row or border (preserve internal column spacing)
        if TABLE_ROW_PATTERN.match(line) or ASCII_TABLE_PATTERN.match(line):
            if line.rstrip() != line:
                applied_rules.add("trim_line_whitespace")
            return line.rstrip()

        # Check for markdown list item
        list_match = LIST_ITEM_PATTERN.match(line)
        if list_match:
            indent, bullet, list_body = list_match.groups()
            segments = self._tokenize_inline(list_body)
            processed_segments = []
            for kind, content in segments:
                if kind == "PROTECTED":
                    processed_segments.append(content)
                else:
                    processed_segments.append(self._transform_prose(content, applied_rules))
            normalized_body = "".join(processed_segments).rstrip()
            result_line = f"{indent}{bullet} {normalized_body}"
            if result_line != line.rstrip():
                applied_rules.add("collapse_consecutive_spaces")
            return result_line

        # General prose line: tokenize protected vs prose spans
        segments = self._tokenize_inline(remainder)
        processed_segments = []
        for kind, content in segments:
            if kind == "PROTECTED":
                processed_segments.append(content)
            else:
                processed_segments.append(self._transform_prose(content, applied_rules))

        result_remainder = "".join(processed_segments).rstrip()
        if line.rstrip() != line:
            applied_rules.add("trim_line_whitespace")

        return leading_indent + result_remainder

    def optimize(self, query_text: str) -> QueryOptimizationComparison:
        """Optimizes query text using semantics-preserving transformations.
        
        Preserves original query always and measures exact character and estimated token savings.
        """
        original_query = query_text
        applied_rules: set[str] = set()

        if not query_text or not query_text.strip():
            # Trivial or empty query: return identity comparison
            orig_len = len(query_text)
            orig_tokens = estimate_token_count(query_text)
            return QueryOptimizationComparison(
                original_query=original_query,
                optimized_query=query_text,
                is_transformed=False,
                original_char_count=orig_len,
                optimized_char_count=orig_len,
                char_savings=0,
                char_savings_pct=0.0,
                original_token_estimate=orig_tokens,
                optimized_token_estimate=orig_tokens,
                token_savings=0,
                token_savings_pct=0.0,
                compression_ratio=1.0,
                transformations_applied=[],
            )

        # 1. Normalize line endings (CRLF / CR -> LF)
        text = query_text.replace("\r\n", "\n").replace("\r", "\n")

        # 2. Handle outer whitespace trimming
        trimmed_text = text.strip()
        if trimmed_text != text:
            applied_rules.add("trim_outer_whitespace")

        # 3. Process line-by-line while tracking code fences
        lines = trimmed_text.split("\n")
        processed_lines: list[str] = []
        in_code_fence = False

        for line in lines:
            if CODE_FENCE_PATTERN.match(line):
                in_code_fence = not in_code_fence
                # Strip trailing whitespace on fence delimiter itself
                trimmed_fence = line.rstrip()
                if trimmed_fence != line:
                    applied_rules.add("trim_line_whitespace")
                processed_lines.append(trimmed_fence)
                continue

            if in_code_fence:
                # Inside code block: strictly preserve exact indentation and content
                processed_lines.append(line)
            else:
                # Outside code block: apply semantics-preserving line optimization
                processed_lines.append(self._optimize_line(line, applied_rules))

        optimized = "\n".join(processed_lines)

        # 4. Collapse excessive consecutive blank lines (3+ newlines -> 2 newlines)
        if "```" not in optimized and "~~~" not in optimized:
            before_collapse = optimized
            optimized = re.sub(r"\n{3,}", "\n\n", optimized)
            if optimized != before_collapse:
                applied_rules.add("collapse_blank_lines")
        else:
            # Safe multi-newline collapse outside code fences
            parts = re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)", optimized)
            rebuilt_parts = []
            for part in parts:
                if part.startswith("```") or part.startswith("~~~"):
                    rebuilt_parts.append(part)
                else:
                    collapsed = re.sub(r"\n{3,}", "\n\n", part)
                    if collapsed != part:
                        applied_rules.add("collapse_blank_lines")
                    rebuilt_parts.append(collapsed)
            optimized = "".join(rebuilt_parts)

        # 5. Final outer trim
        final_optimized = optimized.strip()
        if final_optimized != optimized:
            applied_rules.add("trim_outer_whitespace")
        optimized = final_optimized

        # 6. Calculate metrics
        orig_char_count = len(original_query)
        opt_char_count = len(optimized)
        char_savings = max(0, orig_char_count - opt_char_count)
        char_savings_pct = (
            round((char_savings / orig_char_count) * 100.0, 2)
            if orig_char_count > 0
            else 0.0
        )

        orig_token_est = estimate_token_count(original_query)
        opt_token_est = estimate_token_count(optimized)
        token_savings = max(0, orig_token_est - opt_token_est)
        token_savings_pct = (
            round((token_savings / orig_token_est) * 100.0, 2)
            if orig_token_est > 0
            else 0.0
        )

        compression_ratio = (
            round(opt_char_count / orig_char_count, 4)
            if orig_char_count > 0
            else 1.0
        )

        is_transformed = original_query != optimized

        return QueryOptimizationComparison(
            original_query=original_query,
            optimized_query=optimized,
            is_transformed=is_transformed,
            original_char_count=orig_char_count,
            optimized_char_count=opt_char_count,
            char_savings=char_savings,
            char_savings_pct=char_savings_pct,
            original_token_estimate=orig_token_est,
            optimized_token_estimate=opt_token_est,
            token_savings=token_savings,
            token_savings_pct=token_savings_pct,
            compression_ratio=compression_ratio,
            transformations_applied=sorted(list(applied_rules)),
        )


default_query_optimizer = QueryOptimizer()
