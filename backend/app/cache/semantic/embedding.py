"""Embedding generation interface and deterministic mock implementation for semantic caching."""

from abc import ABC, abstractmethod
import hashlib
import math
from typing import Sequence


class BaseEmbeddingGenerator(ABC):
    """Abstract interface for generating vector embeddings."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the generated vector embeddings."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier of the embedding model."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Version string of the embedding model."""
        pass

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """Generate a single embedding vector for the provided text."""
        pass

    @abstractmethod
    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of text strings."""
        pass


class DeterministicMockEmbeddingGenerator(BaseEmbeddingGenerator):
    """Deterministic, zero-inference mock embedding generator.
    
    Generates normalized unit vectors of fixed dimension from text tokens
    and character n-grams.
    
    Guarantees:
    - Same text always produces the exact same embedding vector.
    - Texts with substantial word overlap produce high cosine similarity.
    - Zero external network or ML dependencies.
    """

    STOPWORDS = {
        "what", "is", "the", "of", "in", "to", "a", "an", "and", "tell",
        "me", "which", "how", "do", "i", "can", "you", "please", "explain",
        "describe", "give", "some", "for", "with", "about",
    }

    def __init__(self, dimension: int = 128, model_name: str = "mock-embed-v1", version: str = "1.0"):
        self._dimension = dimension
        self._model_name = model_name
        self._version = version

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def version(self) -> str:
        return self._version

    def _generate_vector(self, text: str) -> list[float]:
        """Computes a normalized unit vector deterministically from text."""
        clean_text = text.strip().lower()
        for ch in "?!.,;:\"'()[]{}":
            clean_text = clean_text.replace(ch, " ")
        words = clean_text.split()
        if not words:
            return [1.0 / math.sqrt(self._dimension)] * self._dimension

        raw_vector = [0.0] * self._dimension

        for word in words:
            # Downweight generic conversational stopwords, emphasize substantive content
            weight = 0.2 if word in self.STOPWORDS else (2.0 + len(word) * 0.3)
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            raw_vector[bucket] += sign * weight

            # Add character trigrams for content words to capture morphological roots
            if word not in self.STOPWORDS and len(word) >= 4:
                for i in range(len(word) - 2):
                    ng = word[i:i+3]
                    digest_ng = hashlib.sha256(ng.encode("utf-8")).digest()
                    bucket_ng = int.from_bytes(digest_ng[:4], "big") % self._dimension
                    sign_ng = 1.0 if digest_ng[4] % 2 == 0 else -1.0
                    raw_vector[bucket_ng] += sign_ng * 0.4

        # L2 Normalize
        norm_sq = sum(v * v for v in raw_vector)
        if norm_sq < 1e-12:
            return [1.0 / math.sqrt(self._dimension)] * self._dimension

        norm = math.sqrt(norm_sq)
        return [round(v / norm, 6) for v in raw_vector]

    async def embed_text(self, text: str) -> list[float]:
        """Generate a single embedding vector asynchronously."""
        return self._generate_vector(text)

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of strings asynchronously."""
        return [self._generate_vector(t) for t in texts]
