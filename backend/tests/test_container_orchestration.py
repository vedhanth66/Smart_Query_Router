"""Container Orchestration Probes and Secret Management Tests.

Validates:
- Liveness probe (/healthz)
- Readiness probe (/readyz)
- Startup probe (/startupz)
- General health check (/health)
- SecretStr masking and file-based secret resolution
- Unhealthy component reporting on readiness probe
"""

from pathlib import Path
import tempfile
from fastapi.testclient import TestClient
import pytest
from app.main import app
from app.config import Settings, get_settings


client = TestClient(app)


def test_liveness_probe_healthz():
    """Verify GET /healthz returns 200 with service metadata and uptime."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "smart-query-router-backend"
    assert data["version"] == "0.1.0"
    assert "timestamp" in data
    assert "uptime_seconds" in data
    assert data["uptime_seconds"] >= 0


def test_readiness_probe_readyz():
    """Verify GET /readyz returns 200 and all critical subsystems report ready."""
    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "components" in data
    components = data["components"]
    assert components["cache"] == "ready"
    assert components["gateway"] == "ready"
    assert components["rollout"] == "ready"
    assert components["config"] == "ready"
    assert "uptime_seconds" in data


def test_startup_probe_startupz():
    """Verify GET /startupz returns 200 once application has initialized."""
    response = client.get("/startupz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert data["service"] == "smart-query-router-backend"
    assert "timestamp" in data
    assert "uptime_seconds" in data


def test_general_health_endpoint():
    """Verify GET /health remains backwards-compatible with existing contract."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "smart-query-router-backend"
    assert data["version"] == "0.1.0"
    assert "uptime_seconds" in data


def test_secret_masking_in_config():
    """Verify SecretStr prevents credentials from leaking into str/repr/logs."""
    raw_secret = "sk-super-confidential-openai-key-999"
    settings = Settings(OPENAI_API_KEY=raw_secret)
    
    # Secret must never appear in raw string conversions or representations
    assert raw_secret not in str(settings.OPENAI_API_KEY)
    assert raw_secret not in repr(settings)
    assert "**********" in str(settings.OPENAI_API_KEY)
    
    # Must only be accessible via explicit methods
    assert settings.OPENAI_API_KEY.get_secret_value() == raw_secret
    assert settings.resolve_secret("OPENAI_API_KEY") == raw_secret


def test_file_based_secret_resolution():
    """Verify Docker/K8s file-mounted secret resolution and precedence."""
    file_secret = "sk-mounted-from-k8s-secret-volume-123"
    env_secret = "sk-fallback-env-key-456"
    
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
        tmp.write(f"  {file_secret}\n")
        tmp_path = tmp.name

    try:
        settings = Settings(
            SMALL_MODEL_API_KEY=env_secret,
            SMALL_MODEL_API_KEY_FILE=tmp_path,
        )
        # File-based secret should take precedence and be cleanly stripped
        resolved = settings.resolve_secret("SMALL_MODEL_API_KEY")
        assert resolved == file_secret
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_readiness_probe_failure_mode(monkeypatch):
    """Verify GET /readyz returns 503 when a critical subsystem is unavailable."""
    import app.main as main_module
    
    # Temporarily simulate broken cache component
    monkeypatch.setattr(main_module, "default_response_cache", None)
    
    response = client.get("/readyz")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["components"]["cache"] == "unavailable"
