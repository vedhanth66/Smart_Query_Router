"""Cacheability policies and time-sensitivity detection for response cache."""

import os
import re
from dataclasses import dataclass
from typing import Any
from ..schemas.contract import NormalizedQueryPackage, TaskCategory
from .ttl_policy import TTLCategory, TTLPolicyConfig, TTLPolicy


@dataclass
class CachePolicyConfig:
    """Configuration options governing cache decisions."""
    enabled: bool = True
    default_ttl_seconds: int = 3600  # Fallback standard TTL
    allow_time_sensitive: bool = False  # Strict default: never cache time-sensitive tasks
    max_cache_prompt_length: int = 50_000  # Cap on cacheable prompt size
    ttl_policy_config: TTLPolicyConfig | None = None


@dataclass(frozen=True)
class CacheDecision:
    """Outcome of evaluating cacheability for a query package.
    
    NOTE: TTL is one operational mitigation layer to manage freshness; it does not
    guarantee that stale information is impossible.
    """
    is_cacheable: bool
    bypass_reason: str | None = None
    ttl_seconds: int = 3600
    ttl_category: TTLCategory = TTLCategory.STANDARD
    ttl_reason: str = "DEFAULT_POLICY"


class CachePolicy:
    """Evaluates whether a request can be safely cached or served from cache.
    
    GUARANTEES:
    - Never caches time-sensitive tasks unless allow_time_sensitive is explicitly enabled.
    - Prevents caching of oversized prompts.
    - Respects global cache enabled/disabled state.
    """

    # Temporal patterns that identify time-sensitive queries
    TIME_PATTERNS = [
        # Current time queries
        re.compile(r"\b(?:what\s+(?:is\s+)?(?:the\s+)?time|what's\s+the\s+time|current\s+time|tell\s+(?:me\s+)?the\s+time)\b", re.IGNORECASE),
        # Current date / day queries
        re.compile(r"\b(?:what\s+is\s+today'?s\s+date|what'?s\s+today'?s\s+date|what\s+(?:is\s+)?the\s+date\s+today|today'?s\s+date|current\s+date)\b", re.IGNORECASE),
        re.compile(r"\b(?:what\s+day\s+is\s+it\s+today|what\s+day\s+is\s+today|current\s+day)\b", re.IGNORECASE),
        # Ephemeral / real-time inquiries
        re.compile(r"\b(?:right\s+now|at\s+this\s+moment|current\s+timestamp|live\s+timestamp)\b", re.IGNORECASE),
        re.compile(r"\b(?:weather\s+today|current\s+weather|breaking\s+news|latest\s+headlines|latest\s+stock\s+price|current\s+stock\s+price)\b", re.IGNORECASE),
    ]

    def __init__(self, config: CachePolicyConfig | None = None, ttl_policy: TTLPolicy | None = None):
        if config is not None:
            self.config = config
        else:
            # Check environment variables
            env_enabled = os.environ.get("ROUTER_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
            env_allow_ts = os.environ.get("ROUTER_CACHE_ALLOW_TIME_SENSITIVE", "false").lower() in ("true", "1", "yes")
            try:
                env_ttl = int(os.environ.get("ROUTER_CACHE_DEFAULT_TTL_SECONDS", "3600"))
            except ValueError:
                env_ttl = 3600

            self.config = CachePolicyConfig(
                enabled=env_enabled,
                default_ttl_seconds=env_ttl,
                allow_time_sensitive=env_allow_ts,
            )

        if ttl_policy is not None:
            self.ttl_policy = ttl_policy
        else:
            ttl_cfg = self.config.ttl_policy_config or TTLPolicyConfig.from_env()
            if self.config.default_ttl_seconds != 3600 and self.config.ttl_policy_config is None:
                ttl_cfg.standard_ttl_seconds = self.config.default_ttl_seconds
            self.ttl_policy = TTLPolicy(config=ttl_cfg)

    def is_time_sensitive(self, package: NormalizedQueryPackage) -> tuple[bool, str | None]:
        """Inspects query package to determine if it is time-sensitive."""
        # 1. Explicit task category check
        if package.task_category and "datetime" in str(package.task_category.value).lower():
            return True, f"TaskCategory.{package.task_category.value}"

        # 2. Pattern matching on normalized query text
        query = package.query_text.strip()
        for pattern in self.TIME_PATTERNS:
            if pattern.search(query):
                return True, f"temporal_pattern_match ({pattern.pattern[:30]}...)"

        return False, None

    def evaluate(self, package: NormalizedQueryPackage) -> CacheDecision:
        """Evaluates whether the incoming query package is eligible for caching."""
        # 1. Check if cache is enabled globally
        if not self.config.enabled:
            return CacheDecision(
                is_cacheable=False,
                bypass_reason="CACHE_DISABLED",
                ttl_seconds=0,
                ttl_category=TTLCategory.NO_CACHE,
                ttl_reason="CACHE_DISABLED",
            )

        # 2. Check prompt length
        if len(package.query_text) > self.config.max_cache_prompt_length:
            return CacheDecision(
                is_cacheable=False,
                bypass_reason="PROMPT_EXCEEDS_MAX_CACHEABLE_LENGTH",
                ttl_seconds=0,
                ttl_category=TTLCategory.NO_CACHE,
                ttl_reason="PROMPT_EXCEEDS_MAX_CACHEABLE_LENGTH",
            )

        # 3. Check for time-sensitivity
        is_ts, ts_reason = self.is_time_sensitive(package)
        if is_ts and not self.config.allow_time_sensitive:
            return CacheDecision(
                is_cacheable=False,
                bypass_reason=f"TIME_SENSITIVE_TASK ({ts_reason})",
                ttl_seconds=0,
                ttl_category=TTLCategory.NO_CACHE,
                ttl_reason=f"TIME_SENSITIVE_TASK ({ts_reason})",
            )

        # 4. Resolve category-based TTL via centralized TTLPolicy
        ttl_seconds, ttl_category, ttl_reason = self.ttl_policy.resolve_ttl(
            package=package,
            is_time_sensitive=is_ts,
            allow_time_sensitive=self.config.allow_time_sensitive,
        )

        # Eligible for caching
        return CacheDecision(
            is_cacheable=True,
            bypass_reason=None,
            ttl_seconds=ttl_seconds,
            ttl_category=ttl_category,
            ttl_reason=ttl_reason,
        )
