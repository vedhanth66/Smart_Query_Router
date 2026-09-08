#!/usr/bin/env python3
"""Kubernetes Manifest Validation Script.

Validates the minimal Kubernetes deployment suite:
1. Validates manifest bundle generation via kubectl kustomize.
2. Validates YAML schema integrity and required fields across all resource documents.
3. Enforces deployment safety policies:
   - Fixed minimal replicas (no advanced autoscaling).
   - Zero-downtime rolling update configuration (maxUnavailable: 0, maxSurge: 1).
   - Container orchestration probes (startupProbe /startupz, livenessProbe /healthz, readinessProbe /readyz).
   - Resource requests and limits for CPU and Memory.
   - Non-root user execution (appuser UID 10001).
   - Service selector and port alignment.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
import yaml


def validate_with_kubectl(k8s_dir: Path) -> list[str]:
    """Uses kubectl kustomize to validate bundle compilation."""
    errors: list[str] = []
    kubectl_bin = shutil.which("kubectl")
    if not kubectl_bin:
        print("  [WARN] kubectl binary not found; skipping kubectl kustomize check.")
        return errors

    print("Running 'kubectl kustomize' on k8s/ manifests...")
    try:
        res = subprocess.run(
            [kubectl_bin, "kustomize", str(k8s_dir)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode != 0:
            errors.append(f"kubectl kustomize failed with exit code {res.returncode}:\n{res.stderr}")
        else:
            print("  --> kubectl kustomize rendered all manifests cleanly.")
    except Exception as exc:
        errors.append(f"Error executing kubectl: {exc}")

    return errors


def validate_manifest_semantics(k8s_dir: Path) -> list[str]:
    """Performs deep semantic and policy checks across individual manifest files."""
    errors: list[str] = []

    # 1. Namespace
    ns_file = k8s_dir / "namespace.yaml"
    if not ns_file.is_file():
        errors.append("namespace.yaml missing")
    else:
        docs = yaml.safe_load(ns_file.read_text(encoding="utf-8"))
        if docs.get("kind") != "Namespace" or docs.get("metadata", {}).get("name") != "smart-query-router":
            errors.append(f"Invalid namespace document in {ns_file.name}")

    # 2. ConfigMap
    cm_file = k8s_dir / "configmap.yaml"
    if not cm_file.is_file():
        errors.append("configmap.yaml missing")
    else:
        cm_data = yaml.safe_load(cm_file.read_text(encoding="utf-8"))
        if cm_data.get("kind") != "ConfigMap":
            errors.append(f"Expected kind ConfigMap in {cm_file.name}")
        data_keys = cm_data.get("data", {})
        required_keys = ["ENVIRONMENT", "HOST", "PORT", "ROUTER_CACHE_ENABLED", "ROUTER_ROLLOUT_PERCENTAGE"]
        for k in required_keys:
            if k not in data_keys:
                errors.append(f"ConfigMap missing required key: {k}")

    # 3. Secret
    sec_file = k8s_dir / "secret.yaml"
    if not sec_file.is_file():
        errors.append("secret.yaml missing")
    else:
        sec_data = yaml.safe_load(sec_file.read_text(encoding="utf-8"))
        if sec_data.get("kind") != "Secret":
            errors.append(f"Expected kind Secret in {sec_file.name}")
        if sec_data.get("type") != "Opaque":
            errors.append("Secret must be of type Opaque")

    # 4. Service
    svc_file = k8s_dir / "service.yaml"
    if not svc_file.is_file():
        errors.append("service.yaml missing")
    else:
        svc_data = yaml.safe_load(svc_file.read_text(encoding="utf-8"))
        if svc_data.get("kind") != "Service":
            errors.append("Expected kind Service")
        spec = svc_data.get("spec", {})
        if spec.get("type") != "ClusterIP":
            errors.append(f"Expected Service type ClusterIP, got {spec.get('type')}")
        ports = spec.get("ports", [])
        if not ports or ports[0].get("port") != 8000:
            errors.append("Service must expose port 8000")
        if spec.get("selector", {}).get("app") != "smart-query-router-backend":
            errors.append("Service selector must match 'app: smart-query-router-backend'")

    # 5. Deployment
    dep_file = k8s_dir / "deployment.yaml"
    if not dep_file.is_file():
        errors.append("deployment.yaml missing")
        return errors

    dep = yaml.safe_load(dep_file.read_text(encoding="utf-8"))
    if dep.get("kind") != "Deployment":
        errors.append("Expected kind Deployment")
    
    spec = dep.get("spec", {})
    replicas = spec.get("replicas")
    if replicas != 2:
        errors.append(f"Expected fixed replicas: 2 (no advanced autoscaling), got {replicas}")

    # Safe Rolling Update check
    strategy = spec.get("strategy", {})
    if strategy.get("type") != "RollingUpdate":
        errors.append(f"Expected strategy.type RollingUpdate, got {strategy.get('type')}")
    ru = strategy.get("rollingUpdate", {})
    if ru.get("maxUnavailable") != 0:
        errors.append(f"Safe zero-downtime requires maxUnavailable: 0, found {ru.get('maxUnavailable')}")
    if ru.get("maxSurge") != 1:
        errors.append(f"Expected maxSurge: 1, found {ru.get('maxSurge')}")

    # Pod Template
    template = spec.get("template", {})
    pod_sec = template.get("spec", {}).get("securityContext", {})
    if not pod_sec.get("runAsNonRoot") or pod_sec.get("runAsUser") != 10001:
        errors.append("Pod securityContext must enforce runAsNonRoot: true and runAsUser: 10001")

    containers = template.get("spec", {}).get("containers", [])
    if not containers:
        errors.append("Deployment has no containers defined")
        return errors

    main_c = containers[0]

    # Probes check
    startup = main_c.get("startupProbe", {}).get("httpGet", {})
    if startup.get("path") != "/startupz":
        errors.append(f"startupProbe must target /startupz, found {startup.get('path')}")

    liveness = main_c.get("livenessProbe", {}).get("httpGet", {})
    if liveness.get("path") != "/healthz":
        errors.append(f"livenessProbe must target /healthz, found {liveness.get('path')}")

    readiness = main_c.get("readinessProbe", {}).get("httpGet", {})
    if readiness.get("path") != "/readyz":
        errors.append(f"readinessProbe must target /readyz, found {readiness.get('path')}")

    # Resources check
    res = main_c.get("resources", {})
    reqs = res.get("requests", {})
    limits = res.get("limits", {})
    if not reqs.get("cpu") or not reqs.get("memory"):
        errors.append("Container must declare resource requests for both cpu and memory")
    if not limits.get("cpu") or not limits.get("memory"):
        errors.append("Container must declare resource limits for both cpu and memory")

    # 6. HorizontalPodAutoscaler (HPA v2)
    hpa_file = k8s_dir / "hpa.yaml"
    if not hpa_file.is_file():
        errors.append("hpa.yaml missing")
    else:
        hpa_data = yaml.safe_load(hpa_file.read_text(encoding="utf-8"))
        if hpa_data.get("kind") != "HorizontalPodAutoscaler":
            errors.append("Expected kind HorizontalPodAutoscaler in hpa.yaml")
        if hpa_data.get("apiVersion") != "autoscaling/v2":
            errors.append(f"Expected apiVersion autoscaling/v2, got {hpa_data.get('apiVersion')}")
        
        hpa_spec = hpa_data.get("spec", {})
        target_ref = hpa_spec.get("scaleTargetRef", {})
        if target_ref.get("name") != "smart-query-router-backend":
            errors.append("HPA scaleTargetRef must target 'smart-query-router-backend'")
        
        min_reps = hpa_spec.get("minReplicas")
        max_reps = hpa_spec.get("maxReplicas")
        if min_reps != 2:
            errors.append(f"HPA minReplicas must be 2, got {min_reps}")
        if max_reps < 5:
            errors.append(f"HPA maxReplicas must be at least 5, got {max_reps}")
        
        metrics = hpa_spec.get("metrics", [])
        metric_names = []
        for m in metrics:
            m_type = m.get("type")
            if m_type == "Pods":
                metric_names.append(m.get("pods", {}).get("metric", {}).get("name"))
            elif m_type == "Resource":
                metric_names.append(m.get("resource", {}).get("name"))

        if "http_requests_in_flight" not in metric_names:
            errors.append("HPA must include primary queue-aware metric 'http_requests_in_flight'")
        if "memory" not in metric_names:
            errors.append("HPA must include secondary memory headroom protection metric")
        if "cpu" not in metric_names:
            errors.append("HPA must include auxiliary fallback cpu metric")

        behavior = hpa_spec.get("behavior", {})
        down_window = behavior.get("scaleDown", {}).get("stabilizationWindowSeconds", 0)
        if down_window < 180:
            errors.append(f"HPA scaleDown stabilizationWindowSeconds must be >= 180s to prevent flapping, got {down_window}s")

    # 7. Actionable Alerting Rules (PrometheusRule)
    alerts_file = k8s_dir / "alerts.yaml"
    if alerts_file.is_file():
        rule_data = yaml.safe_load(alerts_file.read_text(encoding="utf-8"))
        if rule_data.get("kind") != "PrometheusRule":
            errors.append("alerts.yaml must be of kind PrometheusRule")
        groups = rule_data.get("spec", {}).get("groups", [])
        if not groups:
            errors.append("PrometheusRule must define at least one alerting group")
        else:
            alert_names = [r.get("alert") for r in groups[0].get("rules", [])]
            for req_alert in [
                "HighErrorRate",
                "HighEscalationRate",
                "HighP95Latency",
                "ServiceReadinessDegraded",
                "CostSavingsInversion",
            ]:
                if req_alert not in alert_names:
                    errors.append(f"alerts.yaml missing required actionable alert: {req_alert}")

    return errors


def main() -> int:
    print("=" * 70)
    print("KUBERNETES MANIFEST & POLICY VALIDATION")
    print("=" * 70)

    repo_root = Path(__file__).resolve().parent.parent.parent
    k8s_dir = repo_root / "k8s"

    if not k8s_dir.is_dir():
        print(f"Error: k8s directory not found at {k8s_dir}", file=sys.stderr)
        return 1

    print(f"Validating Kubernetes manifests in: {k8s_dir}")
    kubectl_errors = validate_with_kubectl(k8s_dir)
    semantic_errors = validate_manifest_semantics(k8s_dir)

    all_errors = kubectl_errors + semantic_errors

    print("-" * 70)
    if all_errors:
        print(f"FAILED: {len(all_errors)} Kubernetes manifest policy errors detected:")
        for idx, err in enumerate(all_errors, start=1):
            print(f"  [{idx}] {err}")
        print("=" * 70)
        return 1

    print("ALL KUBERNETES MANIFESTS & POLICIES VALIDATED SUCCESSFULLY")
    print("Zero-downtime rolling update, probes, security, and resource limits verified.")
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
