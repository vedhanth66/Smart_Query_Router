"""Smart Query Router - Experimental Stronger Prompt Compression.

Provides a disabled-by-default experimental path for stronger prompt compression:
- Disabled by default with a strict production safety lock.
- Preserves the original input query verbatim under all circumstances.
- Records compression ratio and token savings metrics objectively.
- Allows A/B comparison routing between uncompressed Variant A and compressed Variant B.
- Strictly bypasses compression if high-sensitivity structures are detected:
  - Code (fenced blocks, inline code, programming syntax/keywords)
  - Mathematical notation (LaTeX display/inline math, formulas, equations)
  - Quoted legal text / quotations (quotes, terms of service, copyright, licenses)
  - URLs and URIs
  - PII and credentials
- Strictly refrains from making quality claims:
  Quality parity is unvalidated pending empirical evaluation benchmarks.
"""

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from .query_optimizer import estimate_token_count
from ..schemas.contract import (
    LocalFeatures,
    QualityEvaluationStatus,
    ExperimentalCompressionResult,
    CompressionABComparisonResult,
    ModelTier,
)
from ..gateway.base import GatewayRequest, GatewayResponse


# High-sensitivity detection patterns
CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]+`")
CODE_KEYWORD_PATTERN = re.compile(
    r"\b(?:def|class|import|from\s+\w+\s+import|function|const|let|var|return|SELECT\s+.+\s+FROM|npm\s+install|pip\s+install|curl\s+)\b",
    re.IGNORECASE,
)

MATH_DISPLAY_PATTERN = re.compile(r"\$\$[\s\S]*?\$\$")
MATH_INLINE_PATTERN = re.compile(r"\$[^\$\n]+\$")
MATH_SYMBOLS_PATTERN = re.compile(
    r"(?:\\[a-zA-Z]+|\b[a-zA-Z]\s*=\s*[-+]?\d+|\bf\s*\(\s*[a-zA-Z]\s*\)\s*=|[\^_{}\\]|[\u2200-\u22FF])"
)

QUOTED_TEXT_PATTERN = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
LEGAL_TERMS_PATTERN = re.compile(
    r"\b(?:terms\s+of\s+service|privacy\s+policy|license\s+agreement|copyright\b|all\s+rights\s+reserved|pursuant\s+to|hereby|governed\s+by\s+the\s+laws|disclaimer\s+of\s+warranties|limitation\s+of\s+liability|indemnification|non-disclosure|nda)\b",
    re.IGNORECASE,
)

URL_PATTERN = re.compile(
    r"\b(?:https?://|ftp://|file://|chrome-extension://|www\.)[^\s/$.?#].[^\s]*",
    re.IGNORECASE,
)

PII_CREDENTIAL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b|\b(?:sk-[a-zA-Z0-9]{20,}|bearer\s+[a-zA-Z0-9_\-\.]+)\b",
    re.IGNORECASE,
)

# Discourse marker and verbose phrase replacements
DISCOURSE_REPLACEMENTS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\bin order to\b", re.IGNORECASE), "to", "replace_in_order_to"),
    (re.compile(r"\bat this point in time\b", re.IGNORECASE), "now", "replace_at_this_point_in_time"),
    (re.compile(r"\bat the present time\b", re.IGNORECASE), "now", "replace_at_the_present_time"),
    (re.compile(r"\bdue to the fact that\b", re.IGNORECASE), "because", "replace_due_to_fact_that"),
    (re.compile(r"\bwith regard to\b", re.IGNORECASE), "regarding", "replace_with_regard_to"),
    (re.compile(r"\bwith reference to\b", re.IGNORECASE), "regarding", "replace_with_reference_to"),
    (re.compile(r"\bin reference to\b", re.IGNORECASE), "regarding", "replace_in_reference_to"),
    (re.compile(r"\bfor the purpose of\b", re.IGNORECASE), "to", "replace_for_the_purpose_of"),
    (re.compile(r"\bin the event that\b", re.IGNORECASE), "if", "replace_in_the_event_that"),
    (re.compile(r"\bprior to\b", re.IGNORECASE), "before", "replace_prior_to"),
    (re.compile(r"\bsubsequent to\b", re.IGNORECASE), "after", "replace_subsequent_to"),
    (re.compile(r"\ba large number of\b", re.IGNORECASE), "many", "replace_a_large_number_of"),
    (re.compile(r"\bis able to\b", re.IGNORECASE), "can", "replace_is_able_to"),
    (re.compile(r"\bhas the capability to\b", re.IGNORECASE), "can", "replace_has_capability_to"),
    (re.compile(r"\bas a consequence of\b", re.IGNORECASE), "because", "replace_as_consequence_of"),
    (re.compile(r"\bin spite of the fact that\b", re.IGNORECASE), "although", "replace_in_spite_of_fact"),
    (re.compile(r"\bin close proximity to\b", re.IGNORECASE), "near", "replace_in_close_proximity_to"),
    (re.compile(r"\btake into consideration\b", re.IGNORECASE), "consider", "replace_take_into_consideration"),
]

# Preamble padding trimming patterns
PREAMBLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"^(?:(?:can|could)\s+you\s+(?:please\s+)?(?:tell|explain|help\s+me\s+understand|clarify)\s+(?:to\s+me\s+)?|please\s+(?:tell|explain|clarify)\s+(?:to\s+me\s+)?|i\s+(?:would\s+like|want)\s+to\s+(?:know|understand)\s+(?:if\s+you\s+can\s+)?|i\s+was\s+wondering\s+if\s+you\s+could\s+)",
            re.IGNORECASE,
        ),
        "prune_conversational_preamble",
    ),
    (
        re.compile(
            r"^(?:hi(?:\s+there)?|hello(?:\s+there)?|hey(?:\s+claude)?)[,!.]*\s*",
            re.IGNORECASE,
        ),
        "prune_greeting_preamble",
    ),
]


@dataclass
class ExperimentalCompressionConfig:
    """Configuration for experimental stronger prompt compression."""

    enabled: bool = False
    environment: str = "production"
    allow_high_sensitivity: bool = False
    target_compression_ratio: float = 0.70

    @classmethod
    def from_env(cls) -> "ExperimentalCompressionConfig":
        """Loads configuration from environment variables with safe production defaults."""
        enabled_val = os.environ.get("ROUTER_EXPERIMENTAL_COMPRESSION_ENABLED", "false").strip().lower()
        enabled = enabled_val in ("true", "1", "yes")
        env_val = os.environ.get("ROUTER_ENVIRONMENT", "production").strip().lower()
        allow_sens_val = os.environ.get("ROUTER_EXPERIMENTAL_ALLOW_SENSITIVITY", "false").strip().lower()
        allow_sens = allow_sens_val in ("true", "1", "yes")
        return cls(
            enabled=enabled,
            environment=env_val,
            allow_high_sensitivity=allow_sens,
        )


def is_experimental_compression_allowed(config: ExperimentalCompressionConfig | None = None) -> bool:
    """Evaluates whether experimental compression is permitted.
    
    Guarantees:
    - Disabled by default.
    - Production lock: unconditionally disabled if environment is 'production'.
    - Only permitted when enabled: True and environment is non-production (test/experimental/development).
    """
    cfg = config or ExperimentalCompressionConfig.from_env()
    if not cfg.enabled:
        return False
    if cfg.environment.lower() == "production":
        return False
    return True


class SensitivityValidator:
    """Validates whether prompt contains high-sensitivity structures requiring compression bypass."""

    def check_sensitivity(
        self,
        text: str,
        features: LocalFeatures | None = None,
    ) -> tuple[bool, list[str]]:
        """Checks for code, math, quoted legal text, URLs, or PII.
        
        Returns (is_sensitive, detected_reasons).
        """
        reasons: list[str] = []

        if not text:
            return False, []

        # 1. Code detection
        if (
            (features and features.has_code)
            or CODE_BLOCK_PATTERN.search(text)
            or INLINE_CODE_PATTERN.search(text)
            or CODE_KEYWORD_PATTERN.search(text)
        ):
            reasons.append("HIGH_SENSITIVITY_CODE")

        # 2. Mathematical notation detection
        if (
            (features and features.has_math)
            or MATH_DISPLAY_PATTERN.search(text)
            or MATH_INLINE_PATTERN.search(text)
            or MATH_SYMBOLS_PATTERN.search(text)
        ):
            reasons.append("HIGH_SENSITIVITY_MATH")

        # 3. Quoted legal text / quotations
        if (
            QUOTED_TEXT_PATTERN.search(text)
            or LEGAL_TERMS_PATTERN.search(text)
        ):
            reasons.append("HIGH_SENSITIVITY_QUOTED_LEGAL_TEXT")

        # 4. URLs and URIs
        if (
            (features and features.has_urls)
            or URL_PATTERN.search(text)
        ):
            reasons.append("HIGH_SENSITIVITY_URL")

        # 5. PII and credentials
        if PII_CREDENTIAL_PATTERN.search(text):
            reasons.append("HIGH_SENSITIVITY_PII")

        is_sensitive = len(reasons) > 0
        return is_sensitive, reasons


class StrongerPromptCompressor:
    """Experimental stronger prompt compressor with sensitivity guardrails and quality non-claims."""

    def __init__(
        self,
        config: ExperimentalCompressionConfig | None = None,
        validator: SensitivityValidator | None = None,
    ):
        self._config = config or ExperimentalCompressionConfig.from_env()
        self._validator = validator or SensitivityValidator()

    @property
    def config(self) -> ExperimentalCompressionConfig:
        return self._config

    def compress(
        self,
        text: str,
        features: LocalFeatures | None = None,
        override_config: ExperimentalCompressionConfig | None = None,
    ) -> ExperimentalCompressionResult:
        """Compresses prompt text if allowed and non-sensitive.
        
        Guarantees:
        - Preserves original input verbatim under all paths.
        - Bypasses if disabled or in production mode.
        - Bypasses if code, math, quoted legal text, or URLs are detected.
        - Refrains from claiming quality parity: status is UNVALIDATED_PENDING_EMPIRICAL_EVALUATION.
        """
        cfg = override_config or self._config
        original_input = text
        orig_char_count = len(text)
        orig_tokens = estimate_token_count(text)

        # Check 1: Feature gating & production lock
        if not is_experimental_compression_allowed(cfg):
            bypass_reason = (
                "FEATURE_LOCKED_IN_PRODUCTION"
                if cfg.environment.lower() == "production"
                else "FEATURE_DISABLED_BY_DEFAULT"
            )
            return ExperimentalCompressionResult(
                original_input=original_input,
                compressed_input=original_input,
                is_compressed=False,
                bypass_reason=bypass_reason,
                detected_sensitivities=[],
                original_char_count=orig_char_count,
                compressed_char_count=orig_char_count,
                char_savings=0,
                compression_ratio=1.0,
                original_token_estimate=orig_tokens,
                compressed_token_estimate=orig_tokens,
                token_savings=0,
                applied_transformations=[],
                quality_evaluation_status=QualityEvaluationStatus.UNVALIDATED,
            )

        # Check 2: High-sensitivity structures
        is_sensitive, sensitivities = self._validator.check_sensitivity(text, features)
        if is_sensitive and not cfg.allow_high_sensitivity:
            return ExperimentalCompressionResult(
                original_input=original_input,
                compressed_input=original_input,
                is_compressed=False,
                bypass_reason=f"BYPASS_{sensitivities[0]}",
                detected_sensitivities=sensitivities,
                original_char_count=orig_char_count,
                compressed_char_count=orig_char_count,
                char_savings=0,
                compression_ratio=1.0,
                original_token_estimate=orig_tokens,
                compressed_token_estimate=orig_tokens,
                token_savings=0,
                applied_transformations=[],
                quality_evaluation_status=QualityEvaluationStatus.UNVALIDATED,
            )

        # Check 3: Empty / trivial prompt
        if not text or not text.strip():
            return ExperimentalCompressionResult(
                original_input=original_input,
                compressed_input=original_input,
                is_compressed=False,
                bypass_reason="TRIVIAL_EMPTY_PROMPT",
                detected_sensitivities=[],
                original_char_count=orig_char_count,
                compressed_char_count=orig_char_count,
                char_savings=0,
                compression_ratio=1.0,
                original_token_estimate=orig_tokens,
                compressed_token_estimate=orig_tokens,
                token_savings=0,
                applied_transformations=[],
                quality_evaluation_status=QualityEvaluationStatus.UNVALIDATED,
            )

        # Apply stronger prompt compression transformations
        applied_rules: list[str] = []
        working = text.strip()

        # Step A: Preamble padding pruning
        for pattern, rule_name in PREAMBLE_PATTERNS:
            before_len = len(working)
            working = pattern.sub("", working).strip()
            if len(working) != before_len:
                applied_rules.append(rule_name)
                # Ensure first character is capitalized if sentence remains
                if working and working[0].islower():
                    working = working[0].upper() + working[1:]

        # Step B: Verbose discourse marker replacements
        for pattern, replacement, rule_name in DISCOURSE_REPLACEMENTS:
            before_len = len(working)
            working = pattern.sub(replacement, working)
            if len(working) != before_len:
                applied_rules.append(rule_name)

        # Step C: Whitespace cleanup after substitutions
        cleaned = re.sub(r"[ \t]+", " ", working)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if cleaned != working:
            applied_rules.append("whitespace_normalization")
        working = cleaned

        compressed_char_count = len(working)
        char_savings = max(0, orig_char_count - compressed_char_count)
        compression_ratio = (
            round(compressed_char_count / orig_char_count, 4)
            if orig_char_count > 0
            else 1.0
        )
        compressed_tokens = estimate_token_count(working)
        token_savings = max(0, orig_tokens - compressed_tokens)
        is_compressed = working != original_input

        return ExperimentalCompressionResult(
            original_input=original_input,
            compressed_input=working,
            is_compressed=is_compressed,
            bypass_reason=None if is_compressed else "NO_REDUNDANCIES_IDENTIFIED",
            detected_sensitivities=sensitivities,
            original_char_count=orig_char_count,
            compressed_char_count=compressed_char_count,
            char_savings=char_savings,
            compression_ratio=compression_ratio,
            original_token_estimate=orig_tokens,
            compressed_token_estimate=compressed_tokens,
            token_savings=token_savings,
            applied_transformations=applied_rules,
            quality_evaluation_status=QualityEvaluationStatus.UNVALIDATED,
        )


def _compute_lexical_jaccard(text_a: str | None, text_b: str | None) -> float:
    """Computes token-level Jaccard lexical overlap between two responses."""
    if not text_a or not text_b:
        return 0.0
    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a.intersection(tokens_b))
    union = len(tokens_a.union(tokens_b))
    return round(intersection / union, 4) if union > 0 else 0.0


async def compare_ab_compression_routing(
    gateway: Any,
    prompt: str,
    tier: ModelTier = ModelTier.FAST_CHEAP,
    provider: str | None = None,
    compressor: StrongerPromptCompressor | None = None,
    override_config: ExperimentalCompressionConfig | None = None,
    correlation_id: str | None = None,
) -> CompressionABComparisonResult:
    """Executes A/B comparison routing between uncompressed Variant A and compressed Variant B.
    
    Guarantees:
    - Variant A uses the uncompressed original input prompt.
    - Variant B uses the compressed prompt if compression was applicable.
    - Executes both variants via the model gateway concurrently.
    - Computes compression ratio, token savings delta, latency delta, and lexical overlap.
    - Explicitly records quality_evaluation_status as UNVALIDATED_PENDING_EMPIRICAL_EVALUATION.
    """
    comp_engine = compressor or default_experimental_compressor
    comp_id = correlation_id or f"ab_{int(time.time() * 1000)}"

    # 1. Generate Variant B via experimental compression
    comp_res = comp_engine.compress(prompt, override_config=override_config)
    uncompressed_prompt = comp_res.original_input
    compressed_prompt = comp_res.compressed_input

    # 2. Build requests for Variant A and Variant B
    req_a = GatewayRequest(
        prompt=uncompressed_prompt,
        tier=tier,
        provider=provider,
        correlation_id=f"{comp_id}_variant_a",
    )
    req_b = GatewayRequest(
        prompt=compressed_prompt,
        tier=tier,
        provider=provider,
        correlation_id=f"{comp_id}_variant_b",
    )

    # 3. Execute concurrently via gateway
    start_a = time.perf_counter()
    start_b = time.perf_counter()

    task_a = asyncio.create_task(gateway.execute(req_a))
    task_b = asyncio.create_task(gateway.execute(req_b))

    res_a, res_b = await asyncio.gather(task_a, task_b, return_exceptions=True)

    lat_a = round((time.perf_counter() - start_a) * 1000.0, 2)
    lat_b = round((time.perf_counter() - start_b) * 1000.0, 2)

    content_a = res_a.content if isinstance(res_a, GatewayResponse) else None
    content_b = res_b.content if isinstance(res_b, GatewayResponse) else None

    overlap = _compute_lexical_jaccard(content_a, content_b)

    return CompressionABComparisonResult(
        comparison_id=comp_id,
        variant_a_prompt=uncompressed_prompt,
        variant_b_prompt=compressed_prompt,
        is_compressed=comp_res.is_compressed,
        compression_ratio=comp_res.compression_ratio,
        estimated_token_savings=comp_res.token_savings,
        variant_a_latency_ms=lat_a,
        variant_b_latency_ms=lat_b,
        latency_delta_ms=round(lat_a - lat_b, 2),
        variant_a_content=content_a,
        variant_b_content=content_b,
        lexical_overlap=overlap,
        quality_evaluation_status=QualityEvaluationStatus.UNVALIDATED,
    )


# Default singleton instance (configured from environment, disabled by default in production)
default_experimental_compressor = StrongerPromptCompressor()
