"""Smart Query Router - Backend Rich Content & Attachment Routing Tests.

Verifies:
1. Contract schemas (LocalFeatures, RichContentHandling, OptimizationDecisionResponse).
2. Query optimizer table structure preservation (never collapses table column whitespace).
3. Experimental compressor bypass for attachment references and tables (HIGH_SENSITIVITY_*).
4. Semantic cache eligibility exclusion for rich content and attachments.
5. End-to-end API evaluation via /api/v1/optimize:
   - Queries with attachments/images/files route conservatively to COMPLEX_MODEL_CANDIDATE.
   - Queries with markdown tables route conservatively to COMPLEX_MODEL_CANDIDATE.
   - OptimizationDecisionResponse includes explicit rich_content handling metadata.
   - Clean queries without rich content report has_rich_input: False and preservation_strategy: "NONE".
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.contract import (
    NormalizedQueryPackage,
    LocalFeatures,
    RichContentHandling,
    OptimizationDecisionResponse,
    CoarseRoute,
    DecisionType,
    ClientMetadata,
    TaskCategory,
)
from app.optimizer.query_optimizer import QueryOptimizer
from app.optimizer.experimental_compressor import (
    SensitivityValidator,
    StrongerPromptCompressor,
    ExperimentalCompressionConfig,
)
from app.cache.semantic.eligibility import SemanticEligibilityPolicy, SemanticPolicyConfig


@pytest.fixture
def client() -> TestClient:
    """FastAPI test client."""
    return TestClient(app)


def build_package(
    query_text: str,
    local_features: LocalFeatures | None = None,
    task_category: TaskCategory = TaskCategory.UNKNOWN,
) -> dict:
    """Helper to build a valid NormalizedQueryPackage dictionary."""
    features_dict = local_features.model_dump() if local_features else {
        "character_count": len(query_text),
        "word_count": len(query_text.split()),
        "has_code": False,
        "has_math": False,
        "has_questions": False,
        "has_urls": False,
        "has_rich_input": False,
        "has_attachments": False,
        "has_images": False,
        "has_files": False,
        "has_code_blocks": False,
        "has_tables": False,
        "attachment_types": [],
        "is_normalized": False,
        "detected_cues": [],
    }

    return {
        "request_id": "req_test_rich_123",
        "correlation_id": "corr_test_rich_456",
        "client_metadata": {
            "extension_version": "0.1.0",
            "client_type": "chrome_extension",
            "schema_version": "1.0",
            "hostname": "claude.ai",
        },
        "query_text": query_text,
        "local_features": features_dict,
        "task_category": task_category.value,
    }


# Test 1: Contract Schemas Validation
def test_contract_local_features_and_rich_content_schemas():
    """Verifies LocalFeatures and RichContentHandling models adhere to contract."""
    features = LocalFeatures(
        has_rich_input=True,
        has_attachments=True,
        has_images=True,
        has_files=False,
        has_code_blocks=False,
        has_tables=True,
        attachment_types=["image", "table"],
    )
    assert features.has_rich_input is True
    assert features.has_attachments is True
    assert features.has_tables is True
    assert "table" in features.attachment_types

    rich_handling = RichContentHandling(
        has_rich_input=True,
        detected_types=["image", "table"],
        preservation_strategy="CONSERVATIVE_PRESERVATION",
        notes="Testing rich handling",
    )
    assert rich_handling.has_rich_input is True
    assert rich_handling.preservation_strategy == "CONSERVATIVE_PRESERVATION"
    assert rich_handling.detected_types == ["image", "table"]

    # Forbid extra fields validation
    with pytest.raises(Exception):
        RichContentHandling(
            has_rich_input=True,
            extra_field="disallowed",
        )


# Test 2: Query Optimizer Preserves Table Rows & Borders
def test_query_optimizer_preserves_table_structure():
    """Verifies query optimizer keeps table columns and borders intact."""
    optimizer = QueryOptimizer()
    table_text = (
        "Here is   the   raw dataset:\n\n"
        "| ID   | Item       | Price |\n"
        "|:-----|:-----------|------:|\n"
        "| 1    | Apple      | $1.50 |\n"
        "| 2    | Watermelon | $6.00 |\n\n"
        "Please analyze   the   totals."
    )

    opt_result = optimizer.optimize(table_text)
    optimized = opt_result.optimized_query

    # Prose lines have spaces collapsed
    assert "Here is the raw dataset:" in optimized
    assert "Please analyze the totals." in optimized

    # Table rows retain column spacing
    assert "| ID   | Item       | Price |" in optimized
    assert "|:-----|:-----------|------:|" in optimized
    assert "| 1    | Apple      | $1.50 |" in optimized
    assert "| 2    | Watermelon | $6.00 |" in optimized


# Test 3: Sensitivity Validator in Compressor Detects Attachments & Tables
def test_sensitivity_validator_detects_attachments_and_tables():
    """Verifies SensitivityValidator flags attachments and tables as high sensitivity."""
    validator = SensitivityValidator()

    # Attachment reference in text
    attach_text = "Please examine [Attachment #1: financial_report.xlsx] and give me key insights."
    is_sens, reasons = validator.check_sensitivity(attach_text)
    assert is_sens is True
    assert "HIGH_SENSITIVITY_ATTACHMENT_REFERENCE" in reasons

    # Table in text
    table_text = "| Col A | Col B |\n|---|---|\n| 1 | 2 |"
    is_sens, reasons = validator.check_sensitivity(table_text)
    assert is_sens is True
    assert "HIGH_SENSITIVITY_TABLE" in reasons

    # Via LocalFeatures flags
    clean_text = "What is the result?"
    features_attach = LocalFeatures(has_attachments=True)
    is_sens, reasons = validator.check_sensitivity(clean_text, features=features_attach)
    assert is_sens is True
    assert "HIGH_SENSITIVITY_ATTACHMENT_REFERENCE" in reasons

    features_table = LocalFeatures(has_tables=True)
    is_sens, reasons = validator.check_sensitivity(clean_text, features=features_table)
    assert is_sens is True
    assert "HIGH_SENSITIVITY_TABLE" in reasons


# Test 4: Stronger Compressor Bypasses Compression on Rich Content
def test_compressor_bypasses_on_rich_content():
    """Verifies stronger prompt compressor preserves original text verbatim when attachments exist."""
    config = ExperimentalCompressionConfig(
        enabled=True,
        environment="test",
        allow_high_sensitivity=False,
    )
    compressor = StrongerPromptCompressor(config=config)
    attach_prompt = "In order to understand [Attachment #1: doc.pdf], please explain at this point in time."

    res = compressor.compress(attach_prompt)
    assert res.is_compressed is False
    assert res.compressed_input == attach_prompt
    assert res.bypass_reason == "BYPASS_HIGH_SENSITIVITY_ATTACHMENT_REFERENCE"
    assert "HIGH_SENSITIVITY_ATTACHMENT_REFERENCE" in res.detected_sensitivities


# Test 5: Semantic Cache Rejects Queries With Rich Content
def test_semantic_cache_eligibility_bypasses_rich_content():
    """Verifies semantic cache policy excludes queries containing attachments or tables."""
    policy = SemanticEligibilityPolicy(config=SemanticPolicyConfig(enabled=True))

    pkg = NormalizedQueryPackage(
        request_id="req_sem_1",
        query_text="What is the capital of France?",
        client_metadata=ClientMetadata(extension_version="0.1.0"),
        task_category=TaskCategory.FACTUAL_QUESTION,
        local_features=LocalFeatures(has_attachments=True),
    )

    decision = policy.evaluate(pkg)
    assert decision.is_eligible is False
    assert decision.bypass_reason == "BYPASS_RICH_CONTENT_ATTACHMENTS"


# Test 6: API Evaluation - End-to-End Conservative Routing for Attachments
def test_api_optimize_routes_attachments_conservatively(client: TestClient):
    """Verifies POST /api/v1/optimize routes queries with attachments to COMPLEX_MODEL_CANDIDATE."""
    pkg_data = build_package(
        query_text="Please summarize [Attachment #1: contract.pdf] and highlight risks.",
        local_features=LocalFeatures(
            has_rich_input=True,
            has_attachments=True,
            has_files=True,
            attachment_types=["attachment", "file"],
        ),
        task_category=TaskCategory.SUMMARIZATION,  # Note: summarization would normally be simple
    )

    response = client.post("/api/v1/optimize", json=pkg_data)
    assert response.status_code == 200

    data = response.json()
    # Summarization with attachments MUST route to complex-model candidate, not simple!
    assert data["coarse_route"] == CoarseRoute.COMPLEX_MODEL_CANDIDATE.value
    assert data["decision_type"] == DecisionType.BACKEND_CANDIDATE.value
    assert data["reason_code"] == "RICH_CONTENT_CANDIDATE"

    rich = data.get("rich_content")
    assert rich is not None
    assert rich["has_rich_input"] is True
    assert rich["preservation_strategy"] == "CONSERVATIVE_PRESERVATION"
    assert "ATTACHMENT" in rich["detected_types"]


# Test 7: API Evaluation - End-to-End Conservative Routing for Tables
def test_api_optimize_routes_tables_conservatively(client: TestClient):
    """Verifies POST /api/v1/optimize routes queries with tables to COMPLEX_MODEL_CANDIDATE."""
    table_query = (
        "| Metric | Q1 | Q2 |\n"
        "|--------|----|----|\n"
        "| Sales  | 10 | 25 |\n"
        "What is the growth percentage?"
    )
    # Even without local_features set, query text contains markdown table
    pkg_data = build_package(
        query_text=table_query,
        task_category=TaskCategory.FACTUAL_QUESTION,
    )

    response = client.post("/api/v1/optimize", json=pkg_data)
    assert response.status_code == 200

    data = response.json()
    assert data["coarse_route"] == CoarseRoute.COMPLEX_MODEL_CANDIDATE.value
    assert data["decision_type"] == DecisionType.BACKEND_CANDIDATE.value
    assert data["reason_code"] == "RICH_CONTENT_CANDIDATE"

    rich = data.get("rich_content")
    assert rich is not None
    assert rich["has_rich_input"] is True
    assert "TABLE" in rich["detected_types"]


# Test 8: API Evaluation - Clean Query Reports rich_content: False
def test_api_optimize_clean_query_reports_no_rich_content(client: TestClient):
    """Verifies clean queries report has_rich_input: False and preservation_strategy: NONE."""
    pkg_data = build_package(
        query_text="What is the speed of light in a vacuum?",
        task_category=TaskCategory.FACTUAL_QUESTION,
    )

    response = client.post("/api/v1/optimize", json=pkg_data)
    assert response.status_code == 200

    data = response.json()
    assert data["coarse_route"] == CoarseRoute.SIMPLE_MODEL_CANDIDATE.value

    rich = data.get("rich_content")
    assert rich is not None
    assert rich["has_rich_input"] is False
    assert rich["preservation_strategy"] == "NONE"
    assert rich["detected_types"] == []
