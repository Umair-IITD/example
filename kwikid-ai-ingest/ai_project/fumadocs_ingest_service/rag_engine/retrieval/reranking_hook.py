"""
rag_engine/retrieval/reranking_hook.py

Reranking hook interface for Phase B2+.

Currently ships with NullReranker (identity, no-op).
Future implementations:
  - LexicalReranker: BM25/TF-IDF rescoring
  - CrossEncoderReranker: sentence-transformers cross-encoder
  - LLMReranker: GPT-4o-mini as judge (expensive, high precision)

To activate a reranker:
  1. Implement the RerankingHook protocol
  2. Pass it to TicketRetriever(reranker=YourReranker())
  3. Set RERANK_ENABLED=true + RERANK_PROVIDER=your_provider in env
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rag_engine.retrieval.ticket_retriever import RetrievedChunk


@runtime_checkable
class RerankingHook(Protocol):
    """
    Protocol for reranking implementations.
    Takes a list of RetrievedChunks and a query, returns reranked top-k.
    """

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """
        Rerank candidates and return top_k.
        Must preserve boosted_score semantics: higher = more relevant.
        """
        ...


class NullReranker:
    """
    Identity reranker — sorts by boosted_score (already done by SQL) and returns top_k.
    Zero latency, zero cost. Default in Phase B1.
    """

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        return sorted(chunks, key=lambda c: c.boosted_score, reverse=True)[:top_k]


class LexicalReranker:
    """
    Simple lexical rescoring using query term overlap.
    Boosts chunks that contain more query terms verbatim.
    Suitable when cross-encoder is unavailable.
    """

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        query_terms = set(query.lower().split())

        def _lexical_score(chunk: RetrievedChunk) -> float:
            content_terms = set(chunk.content.lower().split())
            overlap = len(query_terms & content_terms)
            lexical = overlap / (len(query_terms) + 1)
            # Combine: 80% vector score + 20% lexical score
            return 0.8 * chunk.boosted_score + 0.2 * lexical

        rescored = sorted(chunks, key=_lexical_score, reverse=True)
        return rescored[:top_k]
