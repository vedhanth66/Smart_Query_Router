"""Smart Query Router - Task-Aware Output Length Guidance Policy.

Provides a decoupled policy layer for the Model Gateway:
- Configures task-aware output length limits upfront (pre-generation).
- Simple utility / factual tasks: concise token limits.
- Coding tasks: expansive limits preserving code formatting, indentation, and syntax.
- Structured requests: dynamically scaled limits respecting requested item/bullet counts.
- Open-ended reasoning tasks: generous limits allowing multi-step deliberation.
- Decoupled from model adapters: adapters only receive configured max_tokens and system prompt.
- Explicit non-strategy: avoids post-hoc response truncation as primary length control.
"""

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from ..schemas.contract import TaskCategory
from .base import GatewayRequest


class OutputLengthTier(str, Enum):
    """Output length tiers reflecting task complexity and structure."""

    CONCISE_UTILITY = "concise_utility"
    CODING_EXPANSIVE = "coding_expansive"
    STRUCTURED_COUNT = "structured_count"
    OPEN_REASONING = "open_reasoning"
    STANDARD_BALANCED = "standard_balanced"


@dataclass
class OutputLengthPolicyConfig:
    """Configurable token limits and guidance options."""

    concise_max_tokens: int = 256
    coding_max_tokens: int = 3584
    open_reasoning_max_tokens: int = 4096
    standard_max_tokens: int = 1024
    structured_base_tokens: int = 150
    tokens_per_structured_item: int = 120
    structured_min_tokens: int = 256
    structured_max_tokens: int = 3584
    inject_system_guidance: bool = True

    @classmethod
    def from_env(cls) -> "OutputLengthPolicyConfig":
        """Loads length policy configuration with environment overrides."""
        def _get_int(key: str, default: int) -> int:
            try:
                val = os.environ.get(key)
                return int(val) if val else default
            except (ValueError, TypeError):
                return default

        return cls(
            concise_max_tokens=_get_int("ROUTER_LENGTH_CONCISE_MAX", 256),
            coding_max_tokens=_get_int("ROUTER_LENGTH_CODING_MAX", 3584),
            open_reasoning_max_tokens=_get_int("ROUTER_LENGTH_REASONING_MAX", 4096),
            standard_max_tokens=_get_int("ROUTER_LENGTH_STANDARD_MAX", 1024),
            structured_base_tokens=_get_int("ROUTER_LENGTH_STRUCTURED_BASE", 150),
            tokens_per_structured_item=_get_int("ROUTER_LENGTH_STRUCTURED_PER_ITEM", 120),
            structured_min_tokens=_get_int("ROUTER_LENGTH_STRUCTURED_MIN", 256),
            structured_max_tokens=_get_int("ROUTER_LENGTH_STRUCTURED_MAX", 3584),
            inject_system_guidance=os.environ.get("ROUTER_LENGTH_INJECT_GUIDANCE", "true").lower() in ("true", "1", "yes"),
        )


@dataclass
class LengthGuidance:
    """Resolved length guidance for a gateway request."""

    max_tokens: int
    length_tier: OutputLengthTier
    system_guidance: str | None
    detected_requested_count: int | None = None
    detected_structure_type: str | None = None
    rationale: str = ""

    @property
    def tier(self) -> OutputLengthTier:
        return self.length_tier

    @property
    def system_directive(self) -> str | None:
        return self.system_guidance

    def to_dict(self) -> dict[str, Any]:
        """Serializes guidance to dictionary."""
        return {
            "max_tokens": self.max_tokens,
            "length_tier": self.length_tier.value,
            "system_guidance": self.system_guidance,
            "detected_requested_count": self.detected_requested_count,
            "detected_structure_type": self.detected_structure_type,
            "rationale": self.rationale,
        }


# Mapping number words to integer values
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
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
}

STRUCTURE_NOUNS_PATTERN = (
    r"(?:examples|reasons|options|tips|ideas|steps|ways|items|points|"
    r"functions|questions|bullet points|bullets|sections|fields|keys|"
    r"methods|recommendations|approaches|alternatives|benefits|features|"
    r"principles|components|advantages|disadvantages|drawbacks)"
)


def extract_requested_count(prompt: str) -> tuple[int, str] | None:
    """Extract explicit requested count and structure type from prompt with false-positive guards."""
    if not prompt:
        return None

    # False-positive guard: math expressions like '5 + 3' or '10 / 2'
    if re.search(r"\b\d+\s*[\+\-\*\/]\s*\d+\b", prompt):
        return None

    # False-positive guard: HTTP codes (e.g. HTTP 404, status 500)
    if re.search(r"\b(?:http|status|error|code)\s+\d{3}\b", prompt, re.IGNORECASE):
        return None

    # Pattern 1: "give me 5 examples", "list 3 reasons", "provide 4 bullets", "top 10 tips"
    p1 = (
        r"\b(?:give|provide|list|name|write|show|suggest|generate|top)\s+(?:me\s+)?"
        r"(?:at\s+least\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+("
        + STRUCTURE_NOUNS_PATTERN
        + r")\b"
    )
    m = re.search(p1, prompt, re.IGNORECASE)

    # Pattern 2: "in 3 bullets", "in 4 sections", "divide into 3 sections"
    if not m:
        p2 = (
            r"\b(?:in|using|with|divide\s+(?:this\s+)?into)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+("
            + STRUCTURE_NOUNS_PATTERN
            + r")\b"
        )
        m = re.search(p2, prompt, re.IGNORECASE)

    # Pattern 3: "5 tips for ...", "3 reasons why ..."
    if not m:
        p3 = (
            r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+("
            + STRUCTURE_NOUNS_PATTERN
            + r")\b"
        )
        m = re.search(p3, prompt, re.IGNORECASE)

    if m:
        raw_val = m.group(1).lower()
        matched_noun = m.group(2).lower()
        num = int(raw_val) if raw_val.isdigit() else WORD_TO_NUM.get(raw_val)
        if num and 2 <= num <= 50:
            if "bullet" in matched_noun:
                struct_type = "bullet points"
            elif "section" in matched_noun:
                struct_type = "sections"
            else:
                struct_type = matched_noun
            return num, struct_type

    return None


class OutputLengthPolicy:
    """Task-aware output length guidance policy layer."""

    def __init__(self, config: OutputLengthPolicyConfig | None = None):
        self._config = config or OutputLengthPolicyConfig.from_env()

    @property
    def config(self) -> OutputLengthPolicyConfig:
        return self._config

    def resolve_guidance(
        self,
        query_text: str = "",
        task_category: TaskCategory | str | None = None,
        existing_max_tokens: int | None = None,
        existing_system_prompt: str | None = None,
        *,
        prompt: str | None = None,
    ) -> LengthGuidance:
        """Resolves task-aware output length limit and guidance directive.
        
        Guarantees:
        - Upfront token budgeting and directive generation (no post-hoc slicing).
        - Simple utility / factual: concise limits.
        - Coding tasks: expansive limits preserving full syntax, indentation, and formatting.
        - Structured requests: dynamically scaled budget respecting requested count.
        - Open-ended reasoning: generous budget for multi-step reasoning.
        """
        text = prompt if prompt is not None else query_text
        cat_str = (
            task_category.value
            if isinstance(task_category, TaskCategory)
            else str(task_category).lower().replace("_", " ")
            if task_category
            else ""
        )

        # 1. Check for explicit requested structure count (e.g. "5 reasons", "3 bullets")
        count_info = extract_requested_count(text)
        if count_info and cat_str not in ("coding", "debugging"):
            count, struct_type = count_info
            budget = self._config.structured_base_tokens + (count * self._config.tokens_per_structured_item)
            clamped_budget = max(
                self._config.structured_min_tokens,
                min(self._config.structured_max_tokens, budget),
            )
            return LengthGuidance(
                max_tokens=clamped_budget,
                length_tier=OutputLengthTier.STRUCTURED_COUNT,
                system_guidance=f"Provide exactly {count} {struct_type} as explicitly requested. Maintain clear numbering or bullet markers.",
                detected_requested_count=count,
                detected_structure_type=struct_type,
                rationale=f"Explicit requested count of {count} {struct_type} detected; budget allocated proportionally.",
            )

        # 2. Simple utility or factual tasks
        if cat_str in ("greeting", "arithmetic", "factual question", "translation"):
            return LengthGuidance(
                max_tokens=self._config.concise_max_tokens,
                length_tier=OutputLengthTier.CONCISE_UTILITY,
                system_guidance="Provide a concise, direct answer with minimal preamble or conversational filler.",
                rationale="Simple utility / factual task; concise limit allocated to minimize latency and verbosity.",
            )

        # 3. Coding and debugging tasks
        if cat_str in ("coding", "debugging"):
            return LengthGuidance(
                max_tokens=self._config.coding_max_tokens,
                length_tier=OutputLengthTier.CODING_EXPANSIVE,
                system_guidance="Provide complete, functional code with all necessary formatting, syntax, and indentation. Avoid premature truncation or placeholder omissions.",
                rationale="Coding task; expansive limit allocated to preserve full code structure, formatting, and indentation.",
            )

        # 4. Open-ended reasoning, analysis, comparison, creative writing
        if cat_str in ("reasoning", "analysis", "comparison", "creative writing"):
            return LengthGuidance(
                max_tokens=self._config.open_reasoning_max_tokens,
                length_tier=OutputLengthTier.OPEN_REASONING,
                system_guidance="Provide a thorough, multi-step explanation detailing reasoning, nuances, and trade-offs as necessary.",
                rationale="Open-ended reasoning / analysis task; generous limit allocated to accommodate detailed multi-step explanation.",
            )

        # 5. Standard balanced tasks (summarization, rewriting, unknown)
        return LengthGuidance(
            max_tokens=self._config.standard_max_tokens,
            length_tier=OutputLengthTier.STANDARD_BALANCED,
            system_guidance="Provide a clear, balanced response of appropriate length.",
            rationale="Standard task; balanced limit allocated.",
        )

    def apply_to_request(
        self,
        request: GatewayRequest,
        task_category: TaskCategory | str | None = None,
    ) -> GatewayRequest:
        """Applies task-aware length guidance to a GatewayRequest.
        
        Guarantees:
        - Keeps policy separate from model adapters.
        - Merges system prompt guidance cleanly without clobbering existing custom persona/instructions.
        - Adjusts max_tokens according to task-aware policy tier.
        - Records metadata for observability.
        """
        # Allow caller to explicitly opt out of policy if desired
        if request.metadata.get("apply_length_policy") is False:
            return request

        guidance = self.resolve_guidance(
            query_text=request.prompt,
            task_category=task_category or request.metadata.get("task_category"),
            existing_max_tokens=request.max_tokens,
            existing_system_prompt=request.system_prompt,
        )

        # Merge system prompt directive
        new_sys_prompt = request.system_prompt
        if self._config.inject_system_guidance and guidance.system_guidance:
            directive_text = f"[Length Guidance]: {guidance.system_guidance}"
            if request.system_prompt and request.system_prompt.strip():
                new_sys_prompt = f"{request.system_prompt.strip()}\n\n{directive_text}"
            else:
                new_sys_prompt = directive_text

        # Resolve target max_tokens: if caller set a custom tighter limit (different from default 1024), respect it
        effective_max_tokens = guidance.max_tokens
        if request.max_tokens != 1024 and request.max_tokens < guidance.max_tokens:
            effective_max_tokens = request.max_tokens
            guidance.max_tokens = effective_max_tokens

        # Record metadata
        updated_metadata = dict(request.metadata)
        updated_metadata["length_guidance"] = guidance.to_dict()

        return request.model_copy(
            update={
                "max_tokens": effective_max_tokens,
                "system_prompt": new_sys_prompt,
                "metadata": updated_metadata,
            }
        )


# Default singleton instance
default_length_policy = OutputLengthPolicy()
