"""Unit and integration tests for standardized Benchmark Dataset Format.

Guarantees Verified:
1. 13-Category Task Representation:
   Greetings, arithmetic, factual questions, summarization, rewriting, translation,
   creative writing, coding, debugging, comparison, analysis, reasoning, and context-dependent follow-ups.
2. Label Provenance Guardrail:
   No hard-coding labels from intuition alone. Strict attribution with LabelVerificationMethod,
   confidence scores, and review notes.
3. Subjective task rules:
   Creative writing, analysis, reasoning cannot be falsely marked DETERMINISTIC_VERIFIED.
4. Sensitivity & Redaction flags:
   Credentials or HIGH PII mandate requires_redaction=True.
5. Context-dependent validation:
   Must provide prior context_turns with valid roles and utterances, and has_context_dependency=True.
6. Canonical benchmark validation & serialization:
   canonical_benchmark.json and canonical_benchmark.jsonl load, pass all validations,
   cover all 13 categories, and support loss-less roundtripping.
"""

import json
import os
import tempfile
import pytest
from pydantic import ValidationError

from app.schemas.contract import CoarseRoute, ComplexityLevel
from app.schemas.benchmark import (
    BenchmarkDataset,
    BenchmarkItem,
    BenchmarkModelTier,
    BenchmarkTaskType,
    ConversationTurn,
    CorrectnessRequirement,
    HallucinationTolerance,
    LabelProvenance,
    LabelVerificationMethod,
    PiiRiskLevel,
    QualityCriteria,
    SensitivityFlags,
)
from app.dataset.benchmark_validator import (
    load_benchmark_dataset,
    save_benchmark_dataset,
    validate_benchmark_coverage,
    validate_benchmark_item,
)


# -----------------------------------------------------------------------------
# Fixtures & Helpers
# -----------------------------------------------------------------------------

def make_valid_provenance(
    method: LabelVerificationMethod = LabelVerificationMethod.REFERENCE_MODEL_EVALUATED,
    confidence: float = 0.95,
    evaluator: str = "claude-3-5-sonnet",
    notes: str | None = None,
) -> LabelProvenance:
    return LabelProvenance(
        method=method,
        confidence=confidence,
        evaluator_id=evaluator,
        review_notes=notes,
    )


def make_valid_quality() -> QualityCriteria:
    return QualityCriteria(
        correctness=CorrectnessRequirement.FUNCTIONAL_EQUIVALENCE,
        completeness="FULL_COVERAGE",
        conciseness="BALANCED",
        context_adherence=False,
        format_compliance=None,
        hallucination_tolerance=HallucinationTolerance.LOW_TOLERANCE,
        rubric_description="Valid rubric description.",
    )


def make_valid_sensitivity() -> SensitivityFlags:
    return SensitivityFlags(
        pii_level=PiiRiskLevel.NONE,
        contains_credentials=False,
        contains_proprietary_code=False,
        safety_sensitive=False,
        requires_redaction=False,
        authorized_for_benchmark=True,
    )


def make_base_item(**overrides) -> BenchmarkItem:
    defaults = {
        "id": "bench_test_001",
        "query": "What is the boiling point of water at sea level?",
        "context_turns": [],
        "has_context_dependency": False,
        "task_type": BenchmarkTaskType.FACTUAL_QUESTION,
        "expected_route": CoarseRoute.SIMPLE_MODEL_CANDIDATE,
        "complexity_label": ComplexityLevel.LOW,
        "target_model_tier": BenchmarkModelTier.FAST_CHEAP,
        "quality_criteria": make_valid_quality(),
        "sensitivity_flags": make_valid_sensitivity(),
        "route_provenance": make_valid_provenance(),
        "complexity_provenance": make_valid_provenance(),
        "task_provenance": make_valid_provenance(),
        "tags": ["test"],
    }
    defaults.update(overrides)
    return BenchmarkItem(**defaults)


# -----------------------------------------------------------------------------
# 1. Schema Validation Tests
# -----------------------------------------------------------------------------

class TestBenchmarkSchemaValidation:
    """Test structural constraints on individual benchmark components."""

    def test_valid_benchmark_item_creation(self):
        item = make_base_item()
        assert item.id == "bench_test_001"
        assert item.query == "What is the boiling point of water at sea level?"
        assert item.task_type == BenchmarkTaskType.FACTUAL_QUESTION
        assert item.expected_route == CoarseRoute.SIMPLE_MODEL_CANDIDATE
        assert item.target_model_tier == BenchmarkModelTier.FAST_CHEAP

    def test_empty_query_rejected(self):
        with pytest.raises(ValidationError):
            make_base_item(query="")

    def test_provenance_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            make_valid_provenance(confidence=1.5)
        with pytest.raises(ValidationError):
            make_valid_provenance(confidence=-0.1)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            QualityCriteria(
                correctness=CorrectnessRequirement.EXACT_MATCH,
                unknown_extra_field=123,
            )


# -----------------------------------------------------------------------------
# 2. Label Provenance Guardrail Tests
# -----------------------------------------------------------------------------

class TestLabelProvenanceGuardrails:
    """Verify labels cannot be assigned from unverified intuition alone."""

    def test_deterministic_verified_allowed_for_arithmetic(self):
        item = make_base_item(
            task_type=BenchmarkTaskType.ARITHMETIC,
            query="What is 123 * 456?",
            route_provenance=make_valid_provenance(
                method=LabelVerificationMethod.DETERMINISTIC_VERIFIED,
                confidence=1.0,
                evaluator="python_eval",
            ),
            complexity_provenance=make_valid_provenance(
                method=LabelVerificationMethod.DETERMINISTIC_VERIFIED,
                confidence=1.0,
                evaluator="rule_based",
            ),
        )
        errors = validate_benchmark_item(item)
        assert len(errors) == 0

    def test_subjective_tasks_reject_deterministic_verified_route(self):
        subjective_types = [
            BenchmarkTaskType.CREATIVE_WRITING,
            BenchmarkTaskType.ANALYSIS,
            BenchmarkTaskType.REASONING,
        ]
        for st in subjective_types:
            item = make_base_item(
                task_type=st,
                query="Analyze the macroeconomic effects of quantitative tightening.",
                route_provenance=make_valid_provenance(
                    method=LabelVerificationMethod.DETERMINISTIC_VERIFIED
                ),
            )
            errors = validate_benchmark_item(item)
            assert any("cannot have DETERMINISTIC_VERIFIED route" in err for err in errors), (
                f"Expected error for subjective task {st} with DETERMINISTIC_VERIFIED route"
            )

    def test_subjective_tasks_reject_deterministic_verified_complexity(self):
        item = make_base_item(
            task_type=BenchmarkTaskType.CREATIVE_WRITING,
            query="Write a poem about a fading star.",
            complexity_provenance=make_valid_provenance(
                method=LabelVerificationMethod.DETERMINISTIC_VERIFIED
            ),
        )
        errors = validate_benchmark_item(item)
        assert any("cannot have DETERMINISTIC_VERIFIED complexity" in err for err in errors)

    def test_human_review_required_requires_notes(self):
        item = make_base_item(
            route_provenance=LabelProvenance(
                method=LabelVerificationMethod.HUMAN_REVIEW_REQUIRED,
                confidence=0.8,
                evaluator_id="annotator_group",
                review_notes=None,  # Missing notes
            )
        )
        errors = validate_benchmark_item(item)
        assert any("must provide review_notes" in err for err in errors)

    def test_human_review_with_notes_is_valid(self):
        item = make_base_item(
            route_provenance=LabelProvenance(
                method=LabelVerificationMethod.HUMAN_REVIEW_REQUIRED,
                confidence=0.85,
                evaluator_id="expert_annotator_panel",
                review_notes="Borderline complexity between medium and high; consensus agreed on strong model.",
            )
        )
        errors = validate_benchmark_item(item)
        assert len(errors) == 0


# -----------------------------------------------------------------------------
# 3. Sensitivity & Privacy Guardrails
# -----------------------------------------------------------------------------

class TestSensitivityGuardrails:
    """Verify privacy and redaction rules for benchmark items."""

    def test_credentials_require_redaction(self):
        sens = SensitivityFlags(
            pii_level=PiiRiskLevel.NONE,
            contains_credentials=True,
            contains_proprietary_code=False,
            safety_sensitive=False,
            requires_redaction=False,  # Conflict: credentials must be redacted
        )
        item = make_base_item(sensitivity_flags=sens)
        errors = validate_benchmark_item(item)
        assert any("containing credentials must set requires_redaction=True" in err for err in errors)

    def test_high_pii_requires_redaction(self):
        sens = SensitivityFlags(
            pii_level=PiiRiskLevel.HIGH,
            contains_credentials=False,
            contains_proprietary_code=False,
            safety_sensitive=False,
            requires_redaction=False,  # Conflict: HIGH PII must be redacted
        )
        item = make_base_item(sensitivity_flags=sens)
        errors = validate_benchmark_item(item)
        assert any("High PII risk items must set requires_redaction=True" in err for err in errors)

    def test_credentials_with_redaction_is_valid(self):
        sens = SensitivityFlags(
            pii_level=PiiRiskLevel.LOW,
            contains_credentials=True,
            contains_proprietary_code=False,
            safety_sensitive=False,
            requires_redaction=True,
        )
        item = make_base_item(sensitivity_flags=sens)
        errors = validate_benchmark_item(item)
        assert len(errors) == 0


# -----------------------------------------------------------------------------
# 4. Context-Dependent Follow-Up Validation
# -----------------------------------------------------------------------------

class TestContextDependentValidation:
    """Verify context-dependent items contain necessary conversation history."""

    def test_context_dependent_without_history_fails(self):
        item = make_base_item(
            task_type=BenchmarkTaskType.CONTEXT_DEPENDENT,
            query="Can you rewrite that in Go?",
            has_context_dependency=True,
            context_turns=[],  # Missing history!
        )
        errors = validate_benchmark_item(item)
        assert any("must provide at least one preceding conversation turn" in err for err in errors)

    def test_context_dependent_without_flag_fails(self):
        item = make_base_item(
            task_type=BenchmarkTaskType.CONTEXT_DEPENDENT,
            query="Can you rewrite that in Go?",
            has_context_dependency=False,  # Conflict: must be True
            context_turns=[
                ConversationTurn(role="user", content="Write a quicksort in Python."),
            ],
        )
        errors = validate_benchmark_item(item)
        assert any("must have has_context_dependency=True" in err for err in errors)

    def test_context_dependent_with_valid_history_passes(self):
        item = make_base_item(
            task_type=BenchmarkTaskType.CONTEXT_DEPENDENT,
            query="Can you rewrite that in Go?",
            has_context_dependency=True,
            context_turns=[
                ConversationTurn(role="user", content="Write a quicksort in Python."),
                ConversationTurn(role="assistant", content="def quicksort(arr): ..."),
            ],
        )
        errors = validate_benchmark_item(item)
        assert len(errors) == 0


# -----------------------------------------------------------------------------
# 5. Canonical Dataset Coverage & Integrity Tests
# -----------------------------------------------------------------------------

class TestCanonicalBenchmarkCoverage:
    """Verify the canonical benchmark dataset files satisfy all requirements."""

    @pytest.fixture
    def canonical_json_path(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "app", "dataset", "canonical_benchmark.json"
        )
        assert os.path.exists(path), f"Canonical benchmark JSON file not found at {path}"
        return path

    @pytest.fixture
    def canonical_jsonl_path(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "app", "dataset", "canonical_benchmark.jsonl"
        )
        assert os.path.exists(path), f"Canonical benchmark JSONL file not found at {path}"
        return path

    def test_canonical_json_covers_all_13_task_types(self, canonical_json_path):
        dataset = load_benchmark_dataset(canonical_json_path)
        stats = validate_benchmark_coverage(dataset)

        assert stats["is_valid"], f"Dataset validation failed: {stats['item_errors']}"
        assert stats["all_categories_covered"], (
            f"Missing task categories: {stats['missing_categories']}"
        )
        assert len(stats["missing_categories"]) == 0

        # Verify all 13 categories are present in by_task_type map
        expected_types = {t.value for t in BenchmarkTaskType}
        covered_types = set(stats["by_task_type"].keys())
        assert expected_types.issubset(covered_types)

    def test_canonical_jsonl_matches_json(self, canonical_json_path, canonical_jsonl_path):
        ds_json = load_benchmark_dataset(canonical_json_path)
        ds_jsonl = load_benchmark_dataset(canonical_jsonl_path)

        assert len(ds_json.items) == len(ds_jsonl.items)
        assert ds_json.dataset_id == ds_jsonl.dataset_id

        for item_json, item_jsonl in zip(ds_json.items, ds_jsonl.items):
            assert item_json.id == item_jsonl.id
            assert item_json.task_type == item_jsonl.task_type
            assert item_json.expected_route == item_jsonl.expected_route
            assert item_json.complexity_label == item_jsonl.complexity_label
            assert item_json.target_model_tier == item_jsonl.target_model_tier
            assert item_json.route_provenance.method == item_jsonl.route_provenance.method

    def test_canonical_dataset_provenance_diversity(self, canonical_json_path):
        dataset = load_benchmark_dataset(canonical_json_path)
        methods = {item.route_provenance.method for item in dataset.items}

        # Must include deterministic, reference-model evaluated, and human review
        assert LabelVerificationMethod.DETERMINISTIC_VERIFIED in methods
        assert LabelVerificationMethod.REFERENCE_MODEL_EVALUATED in methods
        assert LabelVerificationMethod.HUMAN_REVIEW_REQUIRED in methods


# -----------------------------------------------------------------------------
# 6. Serialization & Roundtrip Tests
# -----------------------------------------------------------------------------

class TestBenchmarkSerialization:
    """Verify loading and saving benchmark datasets in JSON and JSONL formats."""

    def test_json_roundtrip(self):
        items = [
            make_base_item(id="item_01", task_type=BenchmarkTaskType.GREETING, query="Hello!"),
            make_base_item(id="item_02", task_type=BenchmarkTaskType.ARITHMETIC, query="2 + 2 = ?"),
        ]
        original_ds = BenchmarkDataset(
            dataset_id="test_roundtrip",
            version="1.0.0",
            description="Roundtrip test",
            items=items,
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_path = tf.name

        try:
            save_benchmark_dataset(original_ds, temp_path, format_type="json")
            loaded_ds = load_benchmark_dataset(temp_path)
            assert loaded_ds.dataset_id == original_ds.dataset_id
            assert len(loaded_ds.items) == 2
            assert loaded_ds.items[0].query == "Hello!"
            assert loaded_ds.items[1].query == "2 + 2 = ?"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_jsonl_roundtrip(self):
        items = [
            make_base_item(id="item_01", task_type=BenchmarkTaskType.CODING, query="print('hi')"),
            make_base_item(id="item_02", task_type=BenchmarkTaskType.DEBUGGING, query="fix null ptr"),
        ]
        original_ds = BenchmarkDataset(
            dataset_id="test_jsonl_roundtrip",
            version="1.0.0",
            description="JSONL roundtrip test",
            items=items,
        )

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
            temp_path = tf.name

        try:
            save_benchmark_dataset(original_ds, temp_path, format_type="jsonl")
            loaded_ds = load_benchmark_dataset(temp_path)
            assert loaded_ds.dataset_id == original_ds.dataset_id
            assert len(loaded_ds.items) == 2
            assert loaded_ds.items[0].task_type == BenchmarkTaskType.CODING
            assert loaded_ds.items[1].task_type == BenchmarkTaskType.DEBUGGING
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_unsupported_format_raises_error(self):
        ds = BenchmarkDataset(
            dataset_id="test_format_err",
            version="1.0.0",
            description="Format error test",
            items=[make_base_item()],
        )
        with pytest.raises(ValueError, match="Unsupported format_type"):
            save_benchmark_dataset(ds, "out.xml", format_type="xml")
