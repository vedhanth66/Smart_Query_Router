"""Sanitizer module for training and evaluation candidate records.

Provides strict privacy scrubbing for metadata and minimal, bounded text snippets
with PII redaction and explicit authorization gating.
"""

from __future__ import annotations

import re
from typing import Any

# Forbidden metadata keys matching sensitive auth, credentials, or raw conversational data
FORBIDDEN_KEY_PATTERN = re.compile(
    r"(?i)(cookie|session|token|auth|bearer|jwt|apikey|api_key|secret|password|"
    r"prompt|raw_prompt|query_text|response_text|turn_content|conversation)"
)

# Regex patterns for redacting PII and credentials from bounded snippets
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
IPV4_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
BEARER_REGEX = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-\._~\+\/]+=*")
API_KEY_REGEX = re.compile(r"(?i)\b(?:sk-[a-zA-Z0-9]{16,}|(?:api[_-]?key|secret|token)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{8,}['\"]?)")
CREDENTIAL_URL_REGEX = re.compile(r"https?://[^:\s]+:[^@\s]+@[^\s]+")


def sanitize_metadata(data: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively purges forbidden credential, session, and raw conversational keys.
    
    Args:
        data: Arbitrary metadata dictionary.
        
    Returns:
        New dictionary stripped of all sensitive keys and nested fields.
    """
    if not isinstance(data, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for k, v in data.items():
        if FORBIDDEN_KEY_PATTERN.search(str(k)):
            continue
        if isinstance(v, dict):
            cleaned[k] = sanitize_metadata(v)
        elif isinstance(v, list):
            cleaned[k] = [
                sanitize_metadata(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            cleaned[k] = v
    return cleaned


def sanitize_text_snippet(
    text: str | None,
    max_chars: int = 500,
    authorized: bool = False
) -> tuple[str | None, bool]:
    """Bounds and redacts minimal text necessary for authorized offline evaluation.
    
    GUARANTEES:
    1. Authorization gate: If authorized is False, text is NEVER retained (returns None).
    2. Bounded length: Truncated to max_chars (default: 500).
    3. PII & credential redaction: Masks emails, IPs, API tokens, and URLs with credentials.
    
    Args:
        text: Raw candidate text or prompt snippet.
        max_chars: Maximum character bound for evaluation snippet.
        authorized: Explicit flag indicating whether the user/caller authorized text storage.
        
    Returns:
        Tuple of (sanitized_snippet, is_redacted).
    """
    if not authorized or text is None:
        return None, False

    clean = str(text).strip()
    if not clean:
        return None, False

    # Bounded ceiling
    if len(clean) > max_chars:
        clean = clean[:max_chars].strip()

    is_redacted = False

    # 1. Bearer tokens
    if BEARER_REGEX.search(clean):
        clean = BEARER_REGEX.sub("Bearer [REDACTED_SECRET]", clean)
        is_redacted = True

    # 2. API keys and secrets
    if API_KEY_REGEX.search(clean):
        clean = API_KEY_REGEX.sub("[REDACTED_SECRET]", clean)
        is_redacted = True

    # 3. Credential URLs
    if CREDENTIAL_URL_REGEX.search(clean):
        clean = CREDENTIAL_URL_REGEX.sub("[REDACTED_URL]", clean)
        is_redacted = True

    # 4. Email addresses
    if EMAIL_REGEX.search(clean):
        clean = EMAIL_REGEX.sub("[EMAIL]", clean)
        is_redacted = True

    # 5. IPv4 addresses
    if IPV4_REGEX.search(clean):
        clean = IPV4_REGEX.sub("[IP_ADDRESS]", clean)
        is_redacted = True

    return clean, is_redacted
