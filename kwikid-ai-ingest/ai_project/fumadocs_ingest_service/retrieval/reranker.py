"""
retrieval/reranker.py
----------------------
Modular reranking layer for the hybrid retrieval pipeline.

Architecture:
  - BaseReranker: pluggable interface for future reranker implementations
  - LexicalReranker: preserves existing query.py reranking behavior
  - CrossEncoderReranker: scaffold for future dense reranking (not implemented)

The current implementation EXACTLY mirrors the existing _rerank_score() logic
in app/query.py to ensure zero behavioral change when plugged in.
"""
from __future__ import annotations

import re
import time
import logging
from abc import ABC, abstractmethod
from typing import Any

from retrieval.models import FusedCandidate
from retrieval.config import RetrievalConfig

LOGGER = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


class BaseReranker(ABC):
    """
    Pluggable reranker interface.
    Subclass and implement `score()` to swap in a new reranking strategy.
    """

    @abstractmethod
    def score(self, query: str, candidate_text: str) -> float:
        """Return a relevance score in [0.0, 1.0] range."""
        ...

    def rerank(
        self,
        query: str,
        candidates: list[FusedCandidate],
        top_k: int,
    ) -> tuple[list[FusedCandidate], float]:
        """
        Score all candidates, sort descending, return top_k.
        Returns (reranked_candidates, latency_ms).
        """
        t_start = time.perf_counter()
        for candidate in candidates:
            candidate.rerank_score = self.score(query, candidate.content)
        candidates.sort(key=lambda c: (c.rerank_score, c.rrf_score), reverse=True)
        result = candidates[:top_k]
        for idx, c in enumerate(result, start=1):
            c.final_rank = idx
        latency_ms = (time.perf_counter() - t_start) * 1000
        return result, latency_ms


class LexicalReranker(BaseReranker):
    """
    Reranker using query-coverage ratio (unigram overlap).

    This exactly mirrors the existing _rerank_score() in app/query.py:
        score = |query_tokens ∩ candidate_tokens| / max(1, |query_tokens|)

    Preserves current behavior while being independently testable.
    """

    def score(self, query: str, candidate_text: str) -> float:
        q = _tokenize(query)
        c = _tokenize(candidate_text)
        if not q or not c:
            return 0.0
        overlap = len(q & c)
        return overlap / max(1, len(q))


class CrossEncoderReranker(BaseReranker):
    """
    Scaffold for future cross-encoder / dense reranker integration.

    When implemented, this would use a sentence-transformers cross-encoder
    or a hosted reranking API (Cohere, VoyageAI, etc.) to produce
    fine-grained relevance scores.

    NOT YET IMPLEMENTED — returns LexicalReranker scores as fallback.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self.model_name = model_name
        self._lexical_fallback = LexicalReranker()
        LOGGER.warning(
            "CrossEncoderReranker is not yet implemented; "
            "falling back to LexicalReranker. model_name=%s",
            model_name,
        )

    def score(self, query: str, candidate_text: str) -> float:
        # TODO: load cross-encoder model and score properly
        return self._lexical_fallback.score(query, candidate_text)


def get_reranker(config: RetrievalConfig) -> BaseReranker:
    """
    Factory function returning the appropriate reranker.
    Extend this to add new reranker types without changing call sites.
    """
    return LexicalReranker()
