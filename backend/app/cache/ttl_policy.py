"""Centralized category-based TTL policy for response and semantic caches.

DISCLAIMER & STALENESS GUARANTEES:
Time-To-Live (TTL) is one operational mitigation layer designed to balance cache
freshness against backend latency and compute costs. TTL does NOT make stale
information impossible. Real-world facts, package versions, and external states
can change at any point before a TTL expires. TTL operates in conjunction with
explicit cache invalidation, client-side fresh overrides, and strict time-sensitivity
bypass detection.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import os
import re
from typing import Any

from ..schemas.contract import NormalizedQueryPackage, TaskCategory


class TTLCategory(str, Enum):
    """Broad time-sensitivity and stability categories governing TTL duration."""
    STATIC_FACTS = "static_facts"      # Reference facts, definitions, math constants, translations
    STANDARD = "standard"              # General tasks (coding concepts, summarization, rewriting)
    DYNAMIC = "dynamic"                # Fast-moving topics, status queries, allowed time-sensitive
    NO_CACHE = "no_cache"              # Real-time inquiries, current events, strict bypass


@dataclass
class TTLPolicyConfig:
    """Configuration governing category-based TTL assignment.
    
    All TTL values are in seconds. Configurable via constructor or environment variables.
    """
    static_facts_ttl_seconds: int = 86_400   # 24 hours
    standard_ttl_seconds: int = 3_600        # 1 hour
    dynamic_ttl_seconds: int = 300           # 5 minutes
    
    # Granular overrides for specific TaskCategory values
    category_overrides: dict[TaskCategory | str, int] = field(default_factory=dict)
    
    # Custom keywords indicating dynamic or rapidly changing state
    dynamic_keywords: list[str] = field(default_factory=lambda: [
        "status", "health", "uptime", "current version", "latest release",
        "changelog", "recent updates", "active build", "current price",
        "metrics", "server load", "git commit", "head revision"
    ])

    @classmethod
    def from_env(cls) -> "TTLPolicyConfig":
        """Builds configuration from environment variables with safe fallbacks."""
        def _get_int(key: str, default: int) -> int:
            val = os.environ.get(key)
            if val is not None:
                try:
                    return int(val)
                except ValueError:
                    pass
            return default

        return cls(
            static_facts_ttl_seconds=_get_int("ROUTER_TTL_STATIC_FACTS_SECONDS", 86_400),
            standard_ttl_seconds=_get_int("ROUTER_TTL_STANDARD_SECONDS", 3_600),
            dynamic_ttl_seconds=_get_int("ROUTER_TTL_DYNAMIC_SECONDS", 300),
        )


class BaseTTLPolicy(ABC):
    """Abstract interface for centralized TTL resolution."""

    @abstractmethod
    def resolve_ttl(
        self,
        package: NormalizedQueryPackage,
        is_time_sensitive: bool = False,
        allow_time_sensitive: bool = False,
    ) -> tuple[int, TTLCategory, str]:
        """Resolves the TTL duration, category tier, and rationale for a query package.
        
        Returns:
            (ttl_seconds, ttl_category, rationale)
        """
        pass


class TTLPolicy(BaseTTLPolicy):
    """Centralized, configurable TTL policy mapping task and temporal cues to cache durations.
    
    Design Guarantees:
    - Static facts (geography, language translation, math constants, greetings) receive longer TTLs (24h).
    - Standard categories (coding, summarization, analysis, reasoning) receive standard TTLs (1h).
    - Dynamic topics (status, version cues, debugging, or time-sensitive queries when allowed) receive short TTLs (5m).
    - Strict time-sensitive tasks when caching is disallowed receive 0s TTL and NO_CACHE category.
    """

    # Categories considered relatively static and factual
    STATIC_FACT_CATEGORIES: set[TaskCategory] = {
        TaskCategory.FACTUAL_QUESTION,
        TaskCategory.TRANSLATION,
        TaskCategory.ARITHMETIC,
        TaskCategory.GREETING,
    }

    # Categories considered dynamic and fast-moving
    DYNAMIC_CATEGORIES: set[TaskCategory] = {
        TaskCategory.DEBUGGING,
    }

    def __init__(self, config: TTLPolicyConfig | None = None):
        self.config = config or TTLPolicyConfig.from_env()
        # Precompile dynamic keyword regexes
        self._dynamic_patterns = [
            re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
            for kw in self.config.dynamic_keywords
        ]

    def has_dynamic_keywords(self, text: str) -> tuple[bool, str | None]:
        """Checks if query contains keywords indicating dynamic/rapidly changing state."""
        for pattern in self._dynamic_patterns:
            match = pattern.search(text)
            if match:
                return True, match.group(0)
        return False, None

    def resolve_ttl(
        self,
        package: NormalizedQueryPackage,
        is_time_sensitive: bool = False,
        allow_time_sensitive: bool = False,
    ) -> tuple[int, TTLCategory, str]:
        """Determines appropriate TTL based on category, dynamic keywords, and time sensitivity."""
        # 1. Real-time / Temporal queries
        if is_time_sensitive:
            if not allow_time_sensitive:
                return 0, TTLCategory.NO_CACHE, "TIME_SENSITIVE_NO_CACHE"
            # Explicitly allowed time-sensitive query receives short dynamic TTL
            return (
                self.config.dynamic_ttl_seconds,
                TTLCategory.DYNAMIC,
                "TIME_SENSITIVE_ALLOWED_DYNAMIC_TTL",
            )

        # 2. Explicit granular category overrides in config
        cat_key = package.task_category.value if package.task_category else "none"
        if package.task_category in self.config.category_overrides:
            override_ttl = self.config.category_overrides[package.task_category]
            category = self._classify_ttl_duration(override_ttl)
            return override_ttl, category, f"CATEGORY_OVERRIDE ({cat_key})"
        elif cat_key in self.config.category_overrides:
            override_ttl = self.config.category_overrides[cat_key]
            category = self._classify_ttl_duration(override_ttl)
            return override_ttl, category, f"CATEGORY_OVERRIDE ({cat_key})"

        # 3. Dynamic keywords in query text (e.g., status, version, uptime)
        has_dyn, matched_kw = self.has_dynamic_keywords(package.query_text)
        if has_dyn:
            return (
                self.config.dynamic_ttl_seconds,
                TTLCategory.DYNAMIC,
                f"DYNAMIC_KEYWORD_MATCH ({matched_kw})",
            )

        # 4. TaskCategory classification
        task_cat = package.task_category
        if task_cat in self.STATIC_FACT_CATEGORIES:
            return (
                self.config.static_facts_ttl_seconds,
                TTLCategory.STATIC_FACTS,
                f"STATIC_FACTS_CATEGORY ({task_cat.value})",
            )
        elif task_cat in self.DYNAMIC_CATEGORIES:
            return (
                self.config.dynamic_ttl_seconds,
                TTLCategory.DYNAMIC,
                f"DYNAMIC_CATEGORY ({task_cat.value})",
            )

        # 5. Standard fallback for general reasoning, coding, summarization, etc.
        cat_name = task_cat.value if task_cat else "unknown"
        return (
            self.config.standard_ttl_seconds,
            TTLCategory.STANDARD,
            f"STANDARD_CATEGORY ({cat_name})",
        )

    def _classify_ttl_duration(self, ttl: int) -> TTLCategory:
        """Helper to classify an arbitrary overridden TTL into a broad tier."""
        if ttl <= 0:
            return TTLCategory.NO_CACHE
        elif ttl <= self.config.dynamic_ttl_seconds * 2:
            return TTLCategory.DYNAMIC
        elif ttl >= self.config.static_facts_ttl_seconds // 2:
            return TTLCategory.STATIC_FACTS
        return TTLCategory.STANDARD
