"""Cache eligibility policy for semantic caching.

Enforces strict exclusion guardrails:
- Enabled only for a small set of low-risk, relatively static informational requests.
- Excludes current events and real-time temporal queries.
- Excludes personal data (PII, credentials, self-referential user attributes).
- Excludes private documents, confidential notes, attachments, and proprietary text.
- Excludes context-heavy conversation turns (only standalone queries eligible).
- Assigns experimentally-validated similarity threshold (0.88).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
import re

from ...schemas.contract import NormalizedQueryPackage, TaskCategory
from ..policy import CachePolicy


@dataclass
class SemanticEligibilityDecision:
    """Decision regarding whether a query is eligible for semantic cache evaluation."""
    is_eligible: bool
    bypass_reason: str | None = None
    required_similarity_threshold: float = 0.88


@dataclass
class SemanticPolicyConfig:
    """Configuration governing selective semantic cache eligibility."""
    enabled: bool = True  # Enabled for eligible static informational requests
    min_prompt_length: int = 10
    max_prompt_length: int = 5_000
    default_threshold: float = 0.85
    high_precision_threshold: float = 0.92
    static_informational_threshold: float = 0.88
    allowed_categories: set[TaskCategory] | None = field(
        default_factory=lambda: {
            TaskCategory.FACTUAL_QUESTION,
            TaskCategory.TRANSLATION,
        }
    )


class BaseSemanticEligibilityPolicy(ABC):
    """Abstract interface for evaluating semantic cache eligibility."""

    @abstractmethod
    def evaluate(self, package: NormalizedQueryPackage) -> SemanticEligibilityDecision:
        """Determines whether package is eligible for semantic vector lookup."""
        pass


class SemanticEligibilityPolicy(BaseSemanticEligibilityPolicy):
    """Evaluates query eligibility for semantic caching under strict safety guardrails.
    
    Guarantees:
    - Rejects current events and dynamic inquiries.
    - Rejects personal data and self-referential PII.
    - Rejects private documents, attachments, and proprietary text.
    - Rejects context-heavy conversation turns.
    - Permits only low-risk, static informational task categories.
    """

    # 1. Current events patterns
    CURRENT_EVENTS_PATTERNS = [
        re.compile(r"\b(?:breaking\s+news|latest\s+news|today'?s\s+news|recent\s+news)\b", re.IGNORECASE),
        re.compile(r"\b(?:current\s+events?|what\s+happened\s+(?:today|this\s+week|recently))\b", re.IGNORECASE),
        re.compile(r"\b(?:stock\s+price|crypto\s+price|market\s+price|exchange\s+rate|forex)\b", re.IGNORECASE),
        re.compile(r"\b(?:weather\s+(?:today|forecast|tomorrow)|temperature\s+today)\b", re.IGNORECASE),
        re.compile(r"\b(?:election\s+results?|sports?\s+scores?|live\s+scores?|match\s+scores?)\b", re.IGNORECASE),
        re.compile(r"\b(?:who\s+(?:won|is\s+winning)\s+(?:yesterday|today|the\s+match))\b", re.IGNORECASE),
        re.compile(r"\b(?:latest\s+updates?|right\s+now|at\s+the\s+moment|as\s+of\s+today)\b", re.IGNORECASE),
    ]

    # 2. Personal data (PII) patterns
    PERSONAL_DATA_PATTERNS = [
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # Email
        re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),  # Phone
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
        re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),  # Credit Card
        re.compile(r"\bmy\s+(?:name|email|password|account|ssn|credit\s+card|phone|address|salary|income|doctor|patient|medical|prescription|diagnosis|order|balance)\b", re.IGNORECASE),
        re.compile(r"\b(?:i\s+live\s+in|i\s+was\s+born\s+in|my\s+birthday\s+is|my\s+social\s+security)\b", re.IGNORECASE),
    ]

    # 3. Private documents and proprietary text patterns
    PRIVATE_DOC_PATTERNS = [
        re.compile(r"\b(?:attached\s+(?:file|document|pdf|spreadsheet|notes|report|image|csv)|this\s+attachment|uploaded\s+(?:file|document|csv|data))\b", re.IGNORECASE),
        re.compile(r"\b(?:internal\s+(?:report|document|memo|policy|roadmap)|confidential|proprietary|nda|trade\s+secret)\b", re.IGNORECASE),
        re.compile(r"\b(?:our\s+company|our\s+team|our\s+client|our\s+product|meeting\s+notes|meeting\s+transcript)\b", re.IGNORECASE),
        re.compile(r"\b(?:source\s+code\s+below|the\s+following\s+repo|internal\s+api)\b", re.IGNORECASE),
    ]

    def __init__(self, config: SemanticPolicyConfig | None = None, cache_policy: CachePolicy | None = None):
        if config is not None:
            self.config = config
        else:
            env_enabled = os.environ.get("ROUTER_SEMANTIC_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
            self.config = SemanticPolicyConfig(enabled=env_enabled)
        self._cache_policy = cache_policy or CachePolicy()

    def check_current_events(self, text: str) -> tuple[bool, str | None]:
        """Detect current events, breaking news, and real-time temporal inquiries."""
        for p in self.CURRENT_EVENTS_PATTERNS:
            match = p.search(text)
            if match:
                return True, match.group(0)
        return False, None

    def check_personal_data(self, text: str) -> tuple[bool, str | None]:
        """Detect personal data, PII markers, credentials, and sensitive self-references."""
        for p in self.PERSONAL_DATA_PATTERNS:
            match = p.search(text)
            if match:
                return True, match.group(0)
        return False, None

    def check_private_documents(self, text: str) -> tuple[bool, str | None]:
        """Detect references to attachments, uploaded documents, or proprietary business text."""
        for p in self.PRIVATE_DOC_PATTERNS:
            match = p.search(text)
            if match:
                return True, match.group(0)
        return False, None

    def evaluate(self, package: NormalizedQueryPackage) -> SemanticEligibilityDecision:
        # 1. Global toggle
        if not self.config.enabled:
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason="SEMANTIC_CACHE_DISABLED",
                required_similarity_threshold=self.config.static_informational_threshold,
            )

        # Dynamic threshold assignment based on task sensitivity
        if package.task_category in (TaskCategory.CODING, TaskCategory.DEBUGGING):
            threshold = self.config.high_precision_threshold
        elif package.task_category in (TaskCategory.FACTUAL_QUESTION, TaskCategory.TRANSLATION):
            threshold = self.config.static_informational_threshold
        else:
            threshold = self.config.default_threshold

        query = package.query_text.strip()

        # 2. Length limits
        if len(query) < self.config.min_prompt_length:
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason="QUERY_TOO_SHORT_FOR_SEMANTIC_LOOKUP",
                required_similarity_threshold=threshold,
            )
        if len(query) > self.config.max_prompt_length:
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason="QUERY_EXCEEDS_MAX_SEMANTIC_LENGTH",
                required_similarity_threshold=threshold,
            )

        # 3. Context-heavy conversation turn exclusion
        if package.context_candidates and len(package.context_candidates) > 0:
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason="BYPASS_CONTEXT_HEAVY_CONVERSATION",
                required_similarity_threshold=threshold,
            )

        # 4. Low-risk static informational category restriction
        if self.config.allowed_categories is not None and package.task_category not in self.config.allowed_categories:
            cat_name = package.task_category.value if package.task_category else "none"
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason=f"BYPASS_UNSUPPORTED_TASK_TYPE ({cat_name})",
                required_similarity_threshold=threshold,
            )

        # 5. Temporal / Time-sensitivity check
        is_ts, ts_reason = self._cache_policy.is_time_sensitive(package)
        if is_ts:
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason=f"BYPASS_TIME_SENSITIVE ({ts_reason})",
                required_similarity_threshold=threshold,
            )

        # 6. Current events check
        is_ce, ce_match = self.check_current_events(query)
        if is_ce:
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason=f"BYPASS_CURRENT_EVENTS ({ce_match})",
                required_similarity_threshold=threshold,
            )

        # 7. Personal data (PII) check
        is_pii, pii_match = self.check_personal_data(query)
        if is_pii:
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason=f"BYPASS_PERSONAL_DATA ({pii_match})",
                required_similarity_threshold=threshold,
            )

        # 8. Private documents and proprietary data check
        is_doc, doc_match = self.check_private_documents(query)
        if is_doc:
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason=f"BYPASS_PRIVATE_DOCUMENTS ({doc_match})",
                required_similarity_threshold=threshold,
            )

        # 9. Rich content / attachments check
        if package.local_features and (
            package.local_features.has_rich_input
            or package.local_features.has_attachments
            or package.local_features.has_images
            or package.local_features.has_files
            or package.local_features.has_tables
        ):
            return SemanticEligibilityDecision(
                is_eligible=False,
                bypass_reason="BYPASS_RICH_CONTENT_ATTACHMENTS",
                required_similarity_threshold=threshold,
            )

        # All criteria satisfied: eligible static informational query!
        return SemanticEligibilityDecision(
            is_eligible=True,
            bypass_reason=None,
            required_similarity_threshold=threshold,
        )
