"""Dataset curation and offline evaluation staging package."""

from .sanitizer import sanitize_metadata, sanitize_text_snippet
from .repository import (
    CandidateRepository,
    CandidateNotFoundError,
    InvalidReviewStateTransitionError,
)
from .pipeline import DatasetCurationPipeline

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
]
