"""Dataset curation and offline evaluation staging package."""

from .sanitizer import sanitize_metadata, sanitize_text_snippet
from .repository import (
    CandidateRepository,
    CandidateNotFoundError,
    InvalidReviewStateTransitionError,
)
from .pipeline import DatasetCurationPipeline
from .ml_dataset_generator import (
    MLFeatureDatasetGenerator,
    save_ml_dataset_json,
    save_ml_dataset_jsonl,
    save_ml_dataset_csv,
    load_ml_dataset_json,
    extract_local_signals,
    extract_context_signals,
    derive_optimal_target_label,
)

# Default module-level singletons
default_candidate_repository = CandidateRepository()
default_dataset_pipeline = DatasetCurationPipeline(repository=default_candidate_repository)

__all__ = [
    "sanitize_metadata",
    "sanitize_text_snippet",
    "CandidateRepository",
    "CandidateNotFoundError",
    "InvalidReviewStateTransitionError",
    "DatasetCurationPipeline",
    "default_candidate_repository",
    "default_dataset_pipeline",
    "MLFeatureDatasetGenerator",
    "save_ml_dataset_json",
    "save_ml_dataset_jsonl",
    "save_ml_dataset_csv",
    "load_ml_dataset_json",
    "extract_local_signals",
    "extract_context_signals",
    "derive_optimal_target_label",
]
