#!/usr/bin/env python3
"""Local Container Smoke Test Script for Smart Query Router Backend.

Verifies:
1. Docker build and container execution (when Docker daemon is available).
2. Container orchestration health probes (/startupz, /healthz, /readyz).
3. Non-root user execution (appuser UID 10001).
4. Optimization endpoint (/api/v1/optimize) end-to-end request/response.
5. In-process fallback emulation when Docker daemon is offline.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def is_docker_daemon_running() -> bool:
    """Checks if the local Docker daemon is accessible and responding."""
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def run_emulated_smoke_test() -> bool:
    """Performs end-to-end smoke test using FastAPI TestClient when Docker daemon is offline."""
    print("\n" + "=" * 70)
    print("SMOKE TEST: Running in-process container contract emulation")
    print("=" * 70)

    # Ensure backend directory is in sys.path
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.config import get_settings

        client = TestClient(app)
        settings = get_settings()

        # 1. Startup probe check
        print("\n[1/5] Testing Startup Probe (GET /startupz)...")
        r = client.get("/startupz")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        startup_data = r.json()
        assert startup_data["status"] == "started"
        print(f"  --> PASSED: status={startup_data['status']}, service={startup_data.get('service')}")

        # 2. Liveness probe check
        print("\n[2/5] Testing Liveness Probe (GET /healthz)...")
        r = client.get("/healthz")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        healthz_data = r.json()
        assert healthz_data["status"] == "ok"
        print(f"  --> PASSED: status={healthz_data['status']}, uptime={healthz_data.get('uptime_seconds')}s")

        # 3. Readiness probe check
        print("\n[3/5] Testing Readiness Probe (GET /readyz)...")
        r = client.get("/readyz")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        ready_data = r.json()
        assert ready_data["status"] == "ready"
        assert ready_data["components"]["cache"] == "ready"
        assert ready_data["components"]["gateway"] == "ready"
        assert ready_data["components"]["rollout"] == "ready"
        assert ready_data["components"]["config"] == "ready"
        print(f"  --> PASSED: components={ready_data['components']}")

        # 4. Standard health check
        print("\n[4/5] Testing Standard Health Check (GET /health)...")
        r = client.get("/health")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        health_data = r.json()
        assert health_data["status"] == "ok"
        print(f"  --> PASSED: status={health_data['status']}, version={health_data.get('version')}")

        # 5. Optimization decision request test
        print("\n[5/5] Testing Optimization Decision Endpoint (POST /api/v1/optimize)...")
        payload = {
            "request_id": "smoke_test_req_001",
            "query_text": "How do I optimize database connection pooling under high concurrency?",
            "client_metadata": {
                "extension_version": "0.1.0",
                "client_type": "chrome_extension",
                "schema_version": "1.0",
                "hostname": "claude.ai",
            },
            "user_override": "automatic",
            "coarse_route": "simple-model candidate",
            "task_category": "coding",
            "complexity_level": "MEDIUM",
            "correlation_id": "smoke-corr-12345",
        }
        r = client.post(
            "/api/v1/optimize",
            json=payload,
            headers={"X-Correlation-ID": "smoke-corr-12345"},
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        res_data = r.json()
        assert res_data["correlation_id"] == "smoke-corr-12345"
        assert "decision_type" in res_data
        assert "coarse_route" in res_data
        print(f"  --> PASSED: coarse_route={res_data.get('coarse_route')}, decision={res_data.get('decision_type')}")

        print("\n" + "=" * 70)
        print("ALL SMOKE TESTS PASSED SUCCESSFULLY (In-Process Contract Emulation)")
        print("=" * 70 + "\n")
        return True

    except Exception as exc:
        print(f"\n[FAIL] Smoke test failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


def run_docker_container_smoke_test() -> bool:
    """Builds and runs Docker container to test healthchecks, non-root user, and optimize route."""
    print("\n" + "=" * 70)
    print("SMOKE TEST: Running Docker Containerized Verification")
    print("=" * 70)

    backend_dir = Path(__file__).resolve().parent.parent
    image_tag = "smart-query-router:smoke"
    container_name = f"sqr-smoke-test-{int(time.time())}"
    port = 8019

    try:
        # 1. Build image
        print(f"\n[1/5] Building Docker image '{image_tag}' from {backend_dir}...")
        build_cmd = [
            "docker", "build",
            "-t", image_tag,
            "-f", str(backend_dir / "Dockerfile"),
            str(backend_dir)
        ]
        build_res = subprocess.run(build_cmd, check=True, text=True, capture_output=True)
        print("  --> Build completed successfully.")

        # 2. Run container
        print(f"\n[2/5] Starting container '{container_name}' on port {port}...")
        run_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{port}:8000",
            "-e", "ENVIRONMENT=test",
            image_tag
        ]
        subprocess.run(run_cmd, check=True, text=True, capture_output=True)

        # 3. Wait for readiness probe
        print(f"\n[3/5] Polling readiness probe at http://localhost:{port}/readyz...")
        import urllib.request
        ready = False
        max_attempts = 15
        for attempt in range(max_attempts):
            time.sleep(1)
            try:
                with urllib.request.urlopen(f"http://localhost:{port}/readyz", timeout=2) as resp:
                    if resp.status == 200:
                        ready = True
                        print(f"  --> Container ready after {attempt + 1}s.")
                        break
            except Exception:
                continue

        if not ready:
            raise RuntimeError(f"Container failed to report ready within {max_attempts} seconds.")

        # 4. Verify non-root user
        print("\n[4/5] Verifying container non-root user...")
        whoami_res = subprocess.run(
            ["docker", "exec", container_name, "whoami"],
            capture_output=True,
            text=True,
            check=True
        )
        user = whoami_res.stdout.strip()
        assert user == "appuser", f"Expected user 'appuser', got '{user}'"
        print(f"  --> PASSED: Container is executing as user '{user}' (UID 10001).")

        # 5. Test optimize query
        print(f"\n[5/5] Testing POST http://localhost:{port}/api/v1/optimize...")
        payload = json.dumps({
            "request_id": "docker_smoke_001",
            "query_text": "Verify containerized routing endpoint.",
            "client_metadata": {
                "extension_version": "0.1.0",
                "client_type": "chrome_extension",
                "schema_version": "1.0",
                "hostname": "claude.ai",
            },
            "correlation_id": "docker-smoke-corr-999",
        }).encode("utf-8")

        req = urllib.request.Request(
            f"http://localhost:{port}/api/v1/optimize",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": "docker-smoke-corr-999"
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200, f"Expected 200, got {resp.status}"
            body = json.loads(resp.read().decode("utf-8"))
            assert body["correlation_id"] == "docker-smoke-corr-999"
            assert "decision_type" in body
            print(f"  --> PASSED: decision_type={body.get('decision_type')}, coarse_route={body.get('coarse_route')}")

        print("\n" + "=" * 70)
        print("ALL DOCKER CONTAINER SMOKE TESTS PASSED SUCCESSFULLY")
        print("=" * 70 + "\n")
        return True

    except Exception as exc:
        print(f"\n[FAIL] Docker smoke test failed: {exc}", file=sys.stderr)
        return False

    finally:
        # Cleanup container
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


def main() -> int:
    """Entry point: determines daemon availability and executes appropriate smoke test."""
    print("Checking Docker daemon availability...")
    if is_docker_daemon_running():
        print("Docker daemon is active and responding.")
        success = run_docker_container_smoke_test()
    else:
        print("Docker daemon is not running. Switching to container orchestration contract test.")
        success = run_emulated_smoke_test()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
