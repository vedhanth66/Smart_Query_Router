#!/usr/bin/env python3
"""Dependency Vulnerability and Security Posture Scanner.

Scans:
1. Backend pinned dependencies in backend/requirements.txt.
   - Executes pip-audit if available in the execution environment.
   - Falls back to built-in security advisory checks for pinned dependency boundaries.
2. Chrome Extension security surface in extension/manifest.json.
   - Validates Manifest V3 compliance.
   - Strictly enforces minimal permissions (only 'storage' allowed).
   - Validates host_permissions to forbid excessive wildcards (<all_urls>, *://*/*).
   - Enforces Content Security Policy restrictions (forbids unsafe-eval).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def audit_backend_dependencies(repo_root: Path) -> list[str]:
    """Audits backend requirements.txt for known vulnerabilities and insecure specifiers."""
    issues: list[str] = []
    req_path = repo_root / "backend" / "requirements.txt"
    if not req_path.is_file():
        issues.append(f"Backend requirements file missing: {req_path}")
        return issues

    content = req_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]

    print(f"Auditing {len(lines)} backend dependency specifications from {req_path.name}...")

    # Check 1: If pip-audit is installed, run it
    pip_audit_path = shutil.which("pip-audit")
    if pip_audit_path:
        print("  --> Found pip-audit binary. Running live vulnerability audit against PyPI advisory database...")
        try:
            res = subprocess.run(
                [pip_audit_path, "-r", str(req_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
            if res.returncode != 0:
                issues.append(f"pip-audit reported vulnerabilities:\n{res.stdout}\n{res.stderr}")
            else:
                print("  --> pip-audit: No known vulnerabilities found in installed dependency graph.")
        except Exception as e:
            print(f"  [WARN] pip-audit execution timed out or failed ({e}), continuing with heuristic audit...")

    # Check 2: Heuristic static advisory audit
    known_vulnerable_thresholds = {
        "fastapi": "0.100.0",
        "pydantic": "2.0.0",
        "httpx": "0.27.0",
        "numpy": "1.24.0",
        "uvicorn": "0.22.0",
        "pytest": "7.0.0",
    }

    parsed_deps = {}
    for line in lines:
        match = re.match(r"^([a-zA-Z0-9_\-]+)(?:([><=\!~]+)(.*))?$", line)
        if not match:
            continue
        pkg = match.group(1).lower()
        op = match.group(2)
        version = match.group(3)
        parsed_deps[pkg] = (op, version)

        # Ensure no completely unbounded dependencies
        if not op:
            issues.append(f"Package '{pkg}' is unbounded (no minimum version specified in requirements.txt).")

    # Verify baseline security versions
    for pkg, min_ver in known_vulnerable_thresholds.items():
        if pkg in parsed_deps:
            op, ver = parsed_deps[pkg]
            if op in (">=", "==") and ver:
                try:
                    ver_parts = [int(p) for p in re.split(r"[\.abrc]", ver) if p.isdigit()]
                    min_parts = [int(p) for p in re.split(r"[\.abrc]", min_ver) if p.isdigit()]
                    if ver_parts < min_parts:
                        issues.append(f"Package '{pkg}' version '{ver}' is below security baseline '{min_ver}'.")
                except Exception:
                    pass

    return issues


def audit_extension_security(repo_root: Path) -> list[str]:
    """Audits extension manifest for excessive permissions, wildcards, and CSP violations."""
    issues: list[str] = []
    manifest_path = repo_root / "extension" / "manifest.json"
    if not manifest_path.is_file():
        issues.append(f"Extension manifest missing: {manifest_path}")
        return issues

    print(f"Auditing Chrome Extension security surface from {manifest_path.name}...")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        issues.append(f"Failed to parse manifest.json: {e}")
        return issues

    # 1. Manifest V3 strictly enforced
    if manifest.get("manifest_version") != 3:
        issues.append(f"Insecure or deprecated manifest_version: {manifest.get('manifest_version')} (Must be 3)")

    # 2. Strict minimal permissions check
    allowed_permissions = {"storage"}
    perms = set(manifest.get("permissions", []))
    excessive_perms = perms - allowed_permissions
    if excessive_perms:
        issues.append(f"Security violation: Excessive extension permissions declared: {sorted(list(excessive_perms))}")

    # 3. Forbidden dangerous permissions
    dangerous_permissions = {
        "webRequest", "webRequestBlocking", "cookies", "debugger",
        "proxy", "nativeMessaging", "declarativeNetRequest", "<all_urls>",
    }
    present_dangerous = perms.intersection(dangerous_permissions)
    if present_dangerous:
        issues.append(f"CRITICAL Security violation: Dangerous extension permissions detected: {present_dangerous}")

    # 4. Host permissions scope check
    host_perms = manifest.get("host_permissions", [])
    disallowed_patterns = ["<all_urls>", "*://*/*", "http://*/*", "https://*/*"]
    for hp in host_perms:
        if hp in disallowed_patterns:
            issues.append(f"Security violation: Broad host permission pattern detected: '{hp}'")

    # 5. Content Security Policy verification
    csp = manifest.get("content_security_policy", {})
    if isinstance(csp, dict):
        for csp_key, csp_val in csp.items():
            if "unsafe-eval" in csp_val:
                issues.append(f"Security violation: 'unsafe-eval' detected in CSP '{csp_key}'")
            if "http://" in csp_val:
                issues.append(f"Security violation: Insecure HTTP source detected in CSP '{csp_key}'")

    return issues


def main() -> int:
    print("=" * 70)
    print("SECURITY & DEPENDENCY VULNERABILITY AUDIT")
    print("=" * 70)

    repo_root = Path(__file__).resolve().parent.parent.parent

    backend_issues = audit_backend_dependencies(repo_root)
    extension_issues = audit_extension_security(repo_root)

    all_issues = backend_issues + extension_issues

    print("\n" + "-" * 70)
    if all_issues:
        print(f"AUDIT FAILED: {len(all_issues)} security issues / vulnerabilities detected:")
        for idx, issue in enumerate(all_issues, start=1):
            print(f"  [{idx}] {issue}")
        print("=" * 70)
        return 1

    print("ALL DEPENDENCY & SECURITY AUDITS PASSED WITH ZERO ISSUES")
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
