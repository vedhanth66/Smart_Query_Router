"""Similarity lookup interface and in-memory cosine index for semantic caching."""

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field
import math
from typing import Any


@dataclass
class SimilarityMatch:
    """Represents a similarity search match retrieved from the vector index."""
    entry_id: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    vector: list[float] | None = None

    @property
    def similarity_score(self) -> float:
        return self.score

    @property
    def query_text(self) -> str:
        return self.metadata.get("query_text", "")

    @property
    def content(self) -> str:
        return self.metadata.get("content", "")

    @property
    def hit_count(self) -> int:
        return self.metadata.get("hit_count", 1)



class BaseSimilarityIndex(ABC):
    """Abstract interface for vector similarity search and index storage."""

    @abstractmethod
    async def add(
        self,
        entry_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        """Add or replace a vector entry with associated metadata."""
        pass

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
        filter_criteria: dict[str, Any] | None = None,
    ) -> list[SimilarityMatch]:
        """Search the index for nearest vectors satisfying the minimum score and metadata filters."""
        pass

    @abstractmethod
    async def delete(self, entry_id: str) -> bool:
        """Delete a vector entry by identifier. Returns True if deleted."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all entries from the index."""
        pass

    @abstractmethod
    async def count(self) -> int:
        """Return the current number of indexed entries."""
        pass

    @abstractmethod
    async def get(self, entry_id: str) -> SimilarityMatch | None:
        """Retrieve entry by identifier."""
        pass


def compute_cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes cosine similarity between two float vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0

    dot = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0

    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a_sq += a * a
        norm_b_sq += b * b

    if norm_a_sq <= 1e-12 or norm_b_sq <= 1e-12:
        return 0.0

    sim = dot / (math.sqrt(norm_a_sq) * math.sqrt(norm_b_sq))
    return max(-1.0, min(1.0, round(sim, 6)))


class InMemoryCosineSimilarityIndex(BaseSimilarityIndex):
    """Asyncio-safe in-memory vector index performing brute-force cosine similarity."""

    def __init__(self):
        self._entries: dict[str, tuple[list[float], dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def add(
        self,
        entry_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        """Adds or updates a vector entry."""
        async with self._lock:
            self._entries[entry_id] = (list(vector), dict(metadata))

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
        filter_criteria: dict[str, Any] | None = None,
    ) -> list[SimilarityMatch]:
        """Search nearest vector matches with optional metadata filtering."""
        async with self._lock:
            candidates: list[SimilarityMatch] = []
            for entry_id, (vec, meta) in self._entries.items():
                # Apply metadata filters if provided
                if filter_criteria:
                    match = True
                    for k, v in filter_criteria.items():
                        if meta.get(k) != v:
                            match = False
                            break
                    if not match:
                        continue

                sim = compute_cosine_similarity(query_vector, vec)
                if sim >= min_score:
                    candidates.append(
                        SimilarityMatch(
                            entry_id=entry_id,
                            score=sim,
                            metadata=dict(meta),
                        )
                    )

            # Sort descending by similarity score
            candidates.sort(key=lambda m: m.score, reverse=True)
            return candidates[:top_k]

    async def delete(self, entry_id: str) -> bool:
        """Delete an entry by ID."""
        async with self._lock:
            return self._entries.pop(entry_id, None) is not None

    async def clear(self) -> None:
        """Clear all entries."""
        async with self._lock:
            self._entries.clear()

    async def count(self) -> int:
        """Return total indexed entries."""
        async with self._lock:
            return len(self._entries)

    async def get(self, entry_id: str) -> SimilarityMatch | None:
        """Retrieve entry by ID."""
        async with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return None
            vec, meta = entry
            return SimilarityMatch(entry_id=entry_id, score=1.0, metadata=dict(meta), vector=list(vec))
