"""Centralized Configuration and Secret Management for Smart Query Router Backend.

Loads environment variables, container secrets, and .env configuration using Pydantic Settings.
Guarantees:
- Secrets are typed as SecretStr to prevent unintentional logging or serialization leaks.
- Supports file-based secret resolution (Docker / Kubernetes secrets mounted at /run/secrets/ or *_FILE).
- Zero hardcoded credentials in source code.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment and secret management."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server runtime configuration
    ENVIRONMENT: str = Field(default="production", description="Environment mode (production, staging, development, test)")
    HOST: str = Field(default="0.0.0.0", description="Bind host address")
    PORT: int = Field(default=8000, description="Listening TCP port")
    LOG_LEVEL: str = Field(default="INFO", description="Standard logging level")

    # Gateway & Model Endpoints
    GATEWAY_PROVIDER: str = Field(default="", description="Active model gateway provider override")
    SMALL_MODEL_BASE_URL: str | None = Field(default=None, description="Fast-cheap small model endpoint URL")
    STRONG_MODEL_BASE_URL: str | None = Field(default=None, description="Frontier strong model endpoint URL")
    SMALL_MODEL_TEST_MODE: str | None = Field(default=None, description="Test mode simulation flag for small model")
    STRONG_MODEL_TEST_MODE: str | None = Field(default=None, description="Test mode simulation flag for strong model")

    # Sensitive Secrets (Masked by SecretStr)
    OPENAI_API_KEY: SecretStr | None = Field(default=None, description="Primary OpenAI provider API key")
    SMALL_MODEL_API_KEY: SecretStr | None = Field(default=None, description="Small model provider API key")
    STRONG_MODEL_API_KEY: SecretStr | None = Field(default=None, description="Strong model provider API key")

    # File-based secret references (Docker/K8s secret mounts)
    OPENAI_API_KEY_FILE: str | None = Field(default=None, description="Path to mounted secret file for OpenAI API key")
    SMALL_MODEL_API_KEY_FILE: str | None = Field(default=None, description="Path to mounted secret file for small model API key")
    STRONG_MODEL_API_KEY_FILE: str | None = Field(default=None, description="Path to mounted secret file for strong model API key")

    # Cache Configuration
    ROUTER_CACHE_ENABLED: bool = Field(default=True, description="Enable response caching")
    ROUTER_CACHE_ALLOW_TIME_SENSITIVE: bool = Field(default=False, description="Permit caching for time-sensitive queries")
    ROUTER_CACHE_DEFAULT_TTL_SECONDS: int = Field(default=3600, description="Default cache TTL in seconds")
    ROUTER_SEMANTIC_CACHE_ENABLED: bool = Field(default=True, description="Enable semantic embedding cache lookup")

    # Evaluator Thresholds
    ROUTER_EVAL_CONFIDENCE_THRESHOLD: float = Field(default=0.70, ge=0.0, le=1.0, description="Minimum confidence for small model escalation check")
    ROUTER_EVAL_COMPLETENESS_THRESHOLD: float = Field(default=0.70, ge=0.0, le=1.0, description="Minimum completeness score for small model escalation check")

    # Experimental Optimization
    ROUTER_EXPERIMENTAL_COMPRESSION_ENABLED: bool = Field(default=False, description="Enable experimental prompt compression")
    ROUTER_EXPERIMENTAL_ALLOW_SENSITIVITY: bool = Field(default=False, description="Allow compression on sensitive queries")

    # Rollout & Safety
    ROUTER_ROLLOUT_PERCENTAGE: float = Field(default=10.0, ge=0.0, le=100.0, description="Default canary rollout percentage")
    ROUTER_KILL_SWITCH_ENGAGED: bool = Field(default=False, description="Default state of emergency kill switch")

    def resolve_secret(self, secret_field: str) -> str | None:
        """Resolves a secret value, preferring mounted secret file if specified."""
        file_var = f"{secret_field}_FILE"
        file_path = getattr(self, file_var, None) or os.environ.get(file_var)
        if file_path:
            p = Path(file_path)
            if p.is_file():
                try:
                    return p.read_text(encoding="utf-8").strip()
                except Exception:
                    pass

        val: SecretStr | None = getattr(self, secret_field, None)
        if val is not None:
            return val.get_secret_value()

        # Fallback to direct os.environ lookup if not loaded
        raw_env = os.environ.get(secret_field)
        return raw_env.strip() if raw_env else None

    @property
    def is_production(self) -> bool:
        """Returns True if the environment is set to production."""
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_testing(self) -> bool:
        """Returns True if the environment is test mode."""
        return self.ENVIRONMENT.lower() in ("test", "testing")


@lru_cache()
def get_settings() -> Settings:
    """Returns the cached global application settings instance."""
    return Settings()
