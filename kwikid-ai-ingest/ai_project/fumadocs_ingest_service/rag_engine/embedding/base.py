"""
rag_engine/embedding/base.py

Abstract EmbeddingProvider protocol.

Swap embedding providers by implementing this protocol.
Current implementations: OpenAIEmbeddingProvider
Future: LocalEmbeddingProvider (sentence-transformers), CohereEmbeddingProvider
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class EmbeddingResult:
    """Result from a single embedding call."""
    texts: list[str]
    embeddings: list[list[float]]       # Parallel to texts
    model: str
    total_tokens: int
    api_calls: int


@runtime_checkable
class EmbeddingProvider(Protocol):
    """
    Protocol for embedding providers.
    All implementations must be safe to call from multiple threads
    (or clearly document thread-safety limitations).
    """

    def embed_batch(self, texts: list[str]) -> EmbeddingResult:
        """
        Embed a batch of texts.
        The batch size must respect the provider's per-call limits.
        Raises EmbeddingError on unrecoverable failure after retries.
        """
        ...

    def embed_single(self, text: str) -> list[float]:
        """
        Embed a single text. Convenience wrapper around embed_batch.
        Used for query embedding at retrieval time.
        """
        ...

    @property
    def dimensions(self) -> int:
        """Number of dimensions in output embeddings."""
        ...

    @property
    def model_name(self) -> str:
        """Name of the embedding model."""
        ...


class EmbeddingError(RuntimeError):
    """Raised when embedding fails after all retries."""
    pass
