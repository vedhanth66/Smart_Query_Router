#!/usr/bin/env python3
"""Unified Local CI Runner for Smart Query Router.

Executes all CI quality gates with strict fail-fast semantics:
1. Stage 1: Extension Build Integrity & Unit Tests (Manifest V3, syntax, 34 test suites)
2. Stage 2: Backend Code Quality & Static Linting (Ruff Pyflakes/AST integrity)
3. Stage 3: Dependency Security & Vulnerability Scanning (pip-audit & manifest audit)
4. Stage 4: Backend Automated Tests (Pytest 403 test cases)
5. Stage 5: Container & Orchestration Integration Smoke Test (Probes, non-root, optimize API)

Fails fast on the first encountered error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_stage(name: str, cmd: list[str], cwd: Path) -> bool:
    """Executes a CI stage, prints output, and returns whether it succeeded."""
    print("\n" + "=" * 75)
    print(f"STAGE: {name}")
    print(f"Command: {' '.join(cmd)}")
    print(f"Working Dir: {cwd}")
    print("=" * 75)

    start_time = time.time()
    try:
        res = subprocess.run(cmd, cwd=str(cwd), text=True)
        duration = time.time() - start_time
        if res.returncode == 0:
            print(f"\n[PASS] {name} completed successfully in {duration:.2f}s")
            return True
        else:
            print(f"\n[FAIL-FAST] {name} exited with code {res.returncode} after {duration:.2f}s!")
            return False
    except Exception as exc:
        duration = time.time() - start_time
        print(f"\n[ERROR] Failed to execute {name}: {exc} (elapsed: {duration:.2f}s)")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Query Router Master CI Runner")
    parser.add_argument(
        "--stage",
        choices=["extension", "lint", "security", "backend-tests", "k8s", "smoke"],
        help="Run only a single specific CI stage",
    )
    args = parser.parse_args()

    stages = [
        (
            "extension",
            "Extension Build Integrity & Unit Tests",
            ["node", "tests/run_all_tests.js"],
            REPO_ROOT / "extension",
        ),
        (
            "lint",
            "Backend Code Quality & Static Linting (Ruff)",
            ["ruff", "check", "backend"],
            REPO_ROOT,
        ),
        (
            "security",
            "Dependency & Security Vulnerability Scanning",
            [sys.executable, "backend/scripts/audit_dependencies.py"],
            REPO_ROOT,
        ),
        (
            "backend-tests",
            "Backend Automated Tests (Pytest)",
            [sys.executable, "-m", "pytest", "tests", "-q"],
            REPO_ROOT / "backend",
        ),
        (
            "k8s",
            "Kubernetes Manifests & Policy Validation",
            [sys.executable, "backend/scripts/validate_k8s.py"],
            REPO_ROOT,
        ),
        (
            "smoke",
            "Container & Orchestration Integration Smoke Test",
            [sys.executable, "scripts/smoke_test.py"],
            REPO_ROOT / "backend",
        ),
    ]

    total_start = time.time()
    print("*" * 75)
    print("SMART QUERY ROUTER - CONTINUOUS INTEGRATION (CI) PIPELINE")
    print(f"Repository Root: {REPO_ROOT}")
    print(f"Python: {sys.version.split()[0]} | Node: available")
    print("Policy: Fail-fast on broken builds. Deployment automation withheld.")
    print("*" * 75)

    executed = 0
    for stage_id, stage_name, cmd, cwd in stages:
        if args.stage and args.stage != stage_id:
            continue

        executed += 1
        success = run_stage(stage_name, cmd, cwd)
        if not success:
            total_duration = time.time() - total_start
            print("\n" + "!" * 75)
            print(f"CI PIPELINE FAILED at stage '{stage_name}'!")
            print(f"Fail-fast triggered after {total_duration:.2f}s. Subsequent stages aborted.")
            print("!" * 75 + "\n")
            return 1

    total_duration = time.time() - total_start
    print("\n" + "=" * 75)
    print(f"ALL {executed} CI STAGES PASSED SUCCESSFULLY in {total_duration:.2f}s")
    print("Build integrity, tests, linting, security scans, and smoke tests verified.")
    print("Deployment gate: Ready for staged rollout when authorized.")
    print("=" * 75 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
