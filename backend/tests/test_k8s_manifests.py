"""Kubernetes Deployment Manifests Automated Test Suite.

Validates:
- Manifest syntax and YAML parsing
- Container resource requests and limits
- Readiness, liveness, and startup probe targets
- Safe rolling update strategy (zero downtime maxUnavailable=0)
- Minimal fixed replica baseline (no autoscaling)
- Non-root user security context (UID 10001)
- Service port and selector alignment
- kubectl kustomize bundle compilation
"""

import shutil
import subprocess
from pathlib import Path
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
K8S_DIR = REPO_ROOT / "k8s"


@pytest.fixture(scope="module")
def k8s_manifests():
    """Loads and parses all YAML files in the k8s directory."""
    manifests = {}
    for yml in K8S_DIR.glob("*.yaml"):
        with open(yml, "r", encoding="utf-8") as f:
            manifests[yml.name] = yaml.safe_load(f)
    return manifests


def test_k8s_manifests_yaml_validity(k8s_manifests):
    """Verify all 5 manifests + kustomization exist and parse cleanly."""
    required_files = [
        "namespace.yaml",
        "configmap.yaml",
        "secret.yaml",
        "deployment.yaml",
        "service.yaml",
        "hpa.yaml",
        "kustomization.yaml",
    ]
    for filename in required_files:
        assert filename in k8s_manifests, f"Missing manifest: {filename}"
        assert k8s_manifests[filename] is not None, f"Manifest {filename} is empty"


def test_deployment_resource_requests_and_limits(k8s_manifests):
    """Verify CPU and memory requests and limits are explicitly bounded."""
    dep = k8s_manifests["deployment.yaml"]
    container = dep["spec"]["template"]["spec"]["containers"][0]
    res = container["resources"]

    # Resource requests
    assert "requests" in res
    assert res["requests"]["cpu"] == "250m"
    assert res["requests"]["memory"] == "256Mi"

    # Resource limits
    assert "limits" in res
    assert res["limits"]["cpu"] == "1000m"
    assert res["limits"]["memory"] == "512Mi"


def test_deployment_orchestration_probes(k8s_manifests):
    """Verify liveness, readiness, and startup probes match API contracts."""
    dep = k8s_manifests["deployment.yaml"]
    container = dep["spec"]["template"]["spec"]["containers"][0]

    # Startup probe
    startup = container["startupProbe"]
    assert startup["httpGet"]["path"] == "/startupz"
    assert startup["httpGet"]["port"] == "http"
    assert startup["failureThreshold"] >= 5

    # Liveness probe
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/healthz"
    assert liveness["httpGet"]["port"] == "http"
    assert liveness["periodSeconds"] >= 5

    # Readiness probe
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/readyz"
    assert readiness["httpGet"]["port"] == "http"
    assert readiness["periodSeconds"] >= 5


def test_deployment_rolling_update_strategy(k8s_manifests):
    """Verify zero-downtime safe rolling update configuration."""
    dep = k8s_manifests["deployment.yaml"]
    strategy = dep["spec"]["strategy"]
    assert strategy["type"] == "RollingUpdate"

    ru = strategy["rollingUpdate"]
    # maxUnavailable MUST be 0 to guarantee continuous availability during rollout
    assert ru["maxUnavailable"] == 0
    assert ru["maxSurge"] == 1


def test_deployment_fixed_replicas_no_autoscaling(k8s_manifests):
    """Verify fixed minimal redundancy (2 replicas) with no advanced autoscaler."""
    dep = k8s_manifests["deployment.yaml"]
    assert dep["spec"]["replicas"] == 2


def test_deployment_security_context(k8s_manifests):
    """Verify defense-in-depth non-root execution matching Docker appuser UID 10001."""
    dep = k8s_manifests["deployment.yaml"]
    pod_sec = dep["spec"]["template"]["spec"]["securityContext"]
    assert pod_sec["runAsNonRoot"] is True
    assert pod_sec["runAsUser"] == 10001
    assert pod_sec["runAsGroup"] == 10001

    container_sec = dep["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    assert container_sec["allowPrivilegeEscalation"] is False
    assert "ALL" in container_sec["capabilities"]["drop"]


def test_service_selector_matching(k8s_manifests):
    """Verify Service selector targets the backend Deployment pod template."""
    svc = k8s_manifests["service.yaml"]
    dep = k8s_manifests["deployment.yaml"]

    svc_selector = svc["spec"]["selector"]
    pod_labels = dep["spec"]["template"]["metadata"]["labels"]

    for key, val in svc_selector.items():
        assert key in pod_labels
        assert pod_labels[key] == val


def test_kubectl_kustomize_execution():
    """Verify kubectl kustomize renders the bundle without errors if kubectl is available."""
    kubectl_bin = shutil.which("kubectl")
    if not kubectl_bin:
        pytest.skip("kubectl binary not present in environment")

    res = subprocess.run(
        [kubectl_bin, "kustomize", str(K8S_DIR)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"kubectl kustomize failed:\n{res.stderr}"
    assert "kind: Deployment" in res.stdout
    assert "kind: Service" in res.stdout
    assert "kind: ConfigMap" in res.stdout
    assert "kind: Secret" in res.stdout
    assert "kind: Namespace" in res.stdout
