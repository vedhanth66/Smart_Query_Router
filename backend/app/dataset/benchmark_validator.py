"""Validation and coverage utilities for the Benchmark Dataset Format.

Guarantees:
1. Enforces provenance integrity: subjective/creative tasks cannot be falsely marked
   as DETERMINISTIC_VERIFIED.
2. Enforces sensitivity consistency: credential-bearing or high-PII items require redaction.
3. Enforces context dependency integrity: context-dependent follow-ups must provide
   preceding conversation turns.
4. Verifies complete 13-category task coverage across benchmark datasets.
5. Provides bidirectional JSON and JSONL serialization/deserialization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas.benchmark import (
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkTaskType,
    LabelVerificationMethod,
    PiiRiskLevel,
)
from app.schemas.contract import CoarseRoute


# Categories that are inherently subjective, creative, or architectural and cannot
# be proven via deterministic rules alone
SUBJECTIVE_TASK_CATEGORIES: set[BenchmarkTaskType] = {
    BenchmarkTaskType.CREATIVE_WRITING,
    BenchmarkTaskType.ANALYSIS,
    BenchmarkTaskType.REASONING,
}


def validate_benchmark_item(item: BenchmarkItem) -> list[str]:
    """Validates an individual benchmark item against architectural integrity rules.
    
    Returns a list of validation error strings. An empty list signifies a valid item.
    """
    errors: list[str] = []

    # 1. Provenance Guardrail: Subjective categories cannot be labeled via deterministic rules alone
    if item.task_type in SUBJECTIVE_TASK_CATEGORIES:
        if item.route_provenance.method == LabelVerificationMethod.DETERMINISTIC_VERIFIED:
            errors.append(
                f"Provenance violation for '{item.id}': Subjective category '{item.task_type.value}' "
                "cannot have DETERMINISTIC_VERIFIED route. Requires REFERENCE_MODEL_EVALUATED or "
                "HUMAN_REVIEW_REQUIRED."
            )
        if item.complexity_provenance.method == LabelVerificationMethod.DETERMINISTIC_VERIFIED:
            errors.append(
                f"Provenance violation for '{item.id}': Subjective category '{item.task_type.value}' "
                "cannot have DETERMINISTIC_VERIFIED complexity. Requires REFERENCE_MODEL_EVALUATED or "
                "HUMAN_REVIEW_REQUIRED."
            )

    # 2. Human Review notes requirement
    for prov_name, prov in [
        ("route_provenance", item.route_provenance),
        ("complexity_provenance", item.complexity_provenance),
        ("task_provenance", item.task_provenance),
    ]:
        if (
            prov.method == LabelVerificationMethod.HUMAN_REVIEW_REQUIRED
            and not (prov.review_notes and prov.review_notes.strip())
        ):
            errors.append(
                f"Missing rationale for '{item.id}': {prov_name} marked as HUMAN_REVIEW_REQUIRED "
                "must provide review_notes documenting the human evaluation criteria."
            )

    # 3. Context-Dependent Follow-up integrity
    if item.task_type == BenchmarkTaskType.CONTEXT_DEPENDENT:
        if not item.has_context_dependency:
            errors.append(
                f"Consistency error for '{item.id}': Task type '{item.task_type.value}' "
                "must have has_context_dependency=True."
            )
        if len(item.context_turns) == 0:
            errors.append(
                f"Missing context turns for '{item.id}': Context-dependent follow-up items "
                "must provide at least one preceding conversation turn in context_turns."
            )

    # 4. Sensitivity & Redaction requirements
    if item.sensitivity_flags.contains_credentials and not item.sensitivity_flags.requires_redaction:
        errors.append(
            f"Security flag error for '{item.id}': Items containing credentials "
            "must set requires_redaction=True."
        )

    if (
        item.sensitivity_flags.pii_level == PiiRiskLevel.HIGH
        and not item.sensitivity_flags.requires_redaction
    ):
        errors.append(
            f"Privacy flag error for '{item.id}': High PII risk items "
            "must set requires_redaction=True."
        )

    # 5. Local-Eligible route verification
    if item.expected_route == CoarseRoute.LOCAL_ELIGIBLE:
        if item.task_type not in (
            BenchmarkTaskType.GREETING,
            BenchmarkTaskType.ARITHMETIC,
        ):
            errors.append(
                f"Routing mismatch for '{item.id}': Route 'local-eligible' is only permitted "
                f"for greeting or arithmetic tasks, got '{item.task_type.value}'."
            )

    return errors


def validate_benchmark_coverage(dataset: BenchmarkDataset) -> dict[str, Any]:
    """Inspects a benchmark dataset for complete 13-category coverage and statistical distribution."""
    by_task_type: dict[str, int] = {cat.value: 0 for cat in BenchmarkTaskType}
    by_expected_route: dict[str, int] = {}
    by_complexity: dict[str, int] = {}
    by_model_tier: dict[str, int] = {}
    by_provenance_method: dict[str, int] = {
        method.value: 0 for method in LabelVerificationMethod
    }

    item_errors: dict[str, list[str]] = {}

    for item in dataset.items:
        # Validate individual item
        errors = validate_benchmark_item(item)
        if errors:
            item_errors[item.id] = errors

        # Accumulate task type counts
        task_val = item.task_type.value
        by_task_type[task_val] = by_task_type.get(task_val, 0) + 1

        # Accumulate route counts
        route_val = item.expected_route.value
        by_expected_route[route_val] = by_expected_route.get(route_val, 0) + 1

        # Accumulate complexity counts
        comp_val = item.complexity_label.value
        by_complexity[comp_val] = by_complexity.get(comp_val, 0) + 1

        # Accumulate tier counts
        tier_val = item.target_model_tier.value
        by_model_tier[tier_val] = by_model_tier.get(tier_val, 0) + 1

        # Accumulate provenance counts (route provenance primary)
        prov_val = item.route_provenance.method.value
        by_provenance_method[prov_val] = by_provenance_method.get(prov_val, 0) + 1

    missing_categories = [cat for cat, count in by_task_type.items() if count == 0]
    all_covered = len(missing_categories) == 0

    return {
        "dataset_id": dataset.dataset_id,
        "version": dataset.version,
        "total_items": len(dataset.items),
        "all_categories_covered": all_covered,
        "missing_categories": missing_categories,
        "by_task_type": by_task_type,
        "by_expected_route": by_expected_route,
        "by_complexity": by_complexity,
        "by_model_tier": by_model_tier,
        "by_provenance_method": by_provenance_method,
        "item_errors": item_errors,
        "is_valid": len(item_errors) == 0 and all_covered,
    }


def load_benchmark_dataset(file_path: Path | str) -> BenchmarkDataset:
    """Loads a BenchmarkDataset from a JSON or JSONL file path."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Benchmark file not found: {path}")

    suffix = path.suffix.lower()

    if suffix == ".jsonl":
        items: list[BenchmarkItem] = []
        dataset_id = path.stem
        version = "1.0.0"
        description = f"Loaded from {path.name}"
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean = line.strip()
                if not clean or clean.startswith("#"):
                    continue
                try:
                    data = json.loads(clean)
                    if isinstance(data, dict) and data.get("__metadata__"):
                        dataset_id = data.get("dataset_id", dataset_id)
                        version = data.get("version", version)
                        description = data.get("description", description)
                        continue
                    items.append(BenchmarkItem.model_validate(data))
                except Exception as exc:
                    raise ValueError(f"Failed to parse JSONL line {line_no} in {path}: {exc}") from exc

        return BenchmarkDataset(
            dataset_id=dataset_id,
            version=version,
            description=description,
            items=items,
        )

    # Standard JSON format
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return BenchmarkDataset.model_validate(data)


def save_benchmark_dataset(
    dataset: BenchmarkDataset,
    file_path: Path | str,
    format_type: str = "json",
) -> None:
    """Serializes a BenchmarkDataset to JSON or JSON Lines."""
    fmt = format_type.lower()
    if fmt not in ("json", "jsonl"):
        raise ValueError(f"Unsupported format_type: '{format_type}'. Supported types: 'json', 'jsonl'.")

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "jsonl":
        with open(path, "w", encoding="utf-8") as f:
            header = {
                "__metadata__": True,
                "dataset_id": dataset.dataset_id,
                "version": dataset.version,
                "description": dataset.description,
            }
            f.write(json.dumps(header) + "\n")
            for item in dataset.items:
                f.write(item.model_dump_json() + "\n")
        return

    # Standard pretty-printed JSON
    with open(path, "w", encoding="utf-8") as f:
        f.write(dataset.model_dump_json(indent=2) + "\n")
