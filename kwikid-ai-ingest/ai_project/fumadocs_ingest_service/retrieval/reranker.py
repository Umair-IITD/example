"""
retrieval/reranker.py
----------------------
Modular reranking layer for the hybrid retrieval pipeline.

Architecture:
  - BaseReranker: pluggable interface
  - BM25Reranker: IDF-weighted BM25 (default) — matches app/query.py implementation
  - LexicalReranker: legacy Jaccard overlap (kept for backward compat, not used by default)
  - CrossEncoderReranker: scaffold for future dense reranking (not implemented)
"""
from __future__ import annotations

import math
import re
import time
import logging
from abc import ABC, abstractmethod

from retrieval.models import FusedCandidate
from retrieval.config import RetrievalConfig

LOGGER = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


class BaseReranker(ABC):
    """Pluggable reranker interface."""

    @abstractmethod
    def score(self, query: str, candidate_text: str) -> float:
        """Return a relevance score. Not all subclasses support single-candidate scoring."""
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


class BM25Reranker(BaseReranker):
    """
    BM25 reranker using in-batch IDF.

    Matches the _bm25_scores() implementation in app/query.py exactly:
      - k1=1.5 (TF saturation), b=0.75 (length normalization)
      - Smoothed IDF: log((n - df + 0.5) / (df + 0.5) + 1)
      - Exact phrase match bonus: +0.5 * |query_tokens|
      - Title token overlap bonus: +0.3 per shared token

    BM25 requires the full candidate set to compute IDF, so single-candidate
    scoring via score() is not meaningful — always use rerank() directly.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def score(self, query: str, candidate_text: str) -> float:
        # Single-candidate scoring has no IDF — falls back to TF-only approximation.
        # Prefer rerank() for batch scoring with proper IDF.
        q_tokens = _tokenize(query)
        if not q_tokens:
            return 0.0
        content_lower = candidate_text.lower()
        score = 0.0
        for term in q_tokens:
            tf = content_lower.count(term)
            if tf:
                score += tf * (self.k1 + 1.0) / (tf + self.k1)
        return round(score, 6)

    def rerank(
        self,
        query: str,
        candidates: list[FusedCandidate],
        top_k: int,
    ) -> tuple[list[FusedCandidate], float]:
        t_start = time.perf_counter()

        q_tokens = _tokenize(query)
        n = len(candidates)

        if not q_tokens or n == 0:
            for idx, c in enumerate(candidates[:top_k], start=1):
                c.rerank_score = 0.0
                c.final_rank = idx
            latency_ms = (time.perf_counter() - t_start) * 1000
            return candidates[:top_k], latency_ms

        contents = [c.content for c in candidates]
        doc_token_sets = [_tokenize(ct) for ct in contents]
        doc_lengths = [len(dt) for dt in doc_token_sets]
        avg_dl = sum(doc_lengths) / n

        # In-batch document frequency for each query term
        df: dict[str, int] = {
            term: sum(1 for dt in doc_token_sets if term in dt)
            for term in q_tokens
        }

        query_lower = query.lower().strip()
        k1, b = self.k1, self.b

        for candidate, content, dl in zip(candidates, contents, doc_lengths):
            content_lower = content.lower()
            score = 0.0

            for term in q_tokens:
                tf = content_lower.count(term)
                if tf == 0:
                    continue
                idf = math.log(
                    (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1.0
                )
                tf_norm = tf * (k1 + 1.0) / (tf + k1 * (1.0 - b + b * dl / max(1.0, avg_dl)))
                score += idf * tf_norm

            # Exact phrase match bonus
            if len(query_lower) > 4 and query_lower in content_lower:
                score += len(q_tokens) * 0.5

            # Title token overlap bonus
            title = str(candidate.metadata.get("title", "") if candidate.metadata else "")
            title_tokens = _tokenize(title)
            title_overlap = len(q_tokens & title_tokens)
            if title_overlap:
                score += title_overlap * 0.3

            candidate.rerank_score = round(score, 6)

        candidates.sort(key=lambda c: (c.rerank_score, c.rrf_score), reverse=True)
        result = candidates[:top_k]
        for idx, c in enumerate(result, start=1):
            c.final_rank = idx

        latency_ms = (time.perf_counter() - t_start) * 1000
        return result, latency_ms


class LexicalReranker(BaseReranker):
    """
    Legacy Jaccard overlap reranker — kept for backward compat, not used by default.
    Use BM25Reranker instead; it handles term frequency and rare-term weighting correctly.
    """

    def score(self, query: str, candidate_text: str) -> float:
        q = _tokenize(query)
        c = _tokenize(candidate_text)
        if not q or not c:
            return 0.0
        return len(q & c) / max(1, len(q))


class CrossEncoderReranker(BaseReranker):
    """
    Optional cross-encoder reranker using sentence-transformers.

    Enabled by setting RERANK_CROSS_ENCODER_ENABLED=true in the environment.
    Requires `sentence-transformers` to be installed (not in requirements.txt by default
    — add it manually: pip install sentence-transformers).

    Design:
      - Loads the model lazily on first call, cached per-instance
      - Uses a thread pool executor to avoid blocking the event loop
      - Hard timeout: falls back to BM25 if inference exceeds RERANK_CROSS_ENCODER_TIMEOUT_S
      - Only reranks top-k candidates (RERANK_CROSS_ENCODER_LIMIT, default 20) for cost control
      - Always falls back gracefully to BM25Reranker if the model is unavailable

    Recommended model: cross-encoder/ms-marco-MiniLM-L-6-v2
      - 22MB, ~10ms/pair on CPU, good precision for passage retrieval
      - Alternative: cross-encoder/ms-marco-MiniLM-L-12-v2 (better quality, 2× slower)

    To enable:
      RERANK_CROSS_ENCODER_ENABLED=true
      RERANK_CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
      RERANK_CROSS_ENCODER_TIMEOUT_S=5.0
      RERANK_CROSS_ENCODER_LIMIT=20
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self.model_name = model_name
        self._fallback = BM25Reranker()
        self._model = None  # lazy-loaded
        self._load_attempted = False
        import os
        self._timeout_s = float(os.getenv("RERANK_CROSS_ENCODER_TIMEOUT_S", "5.0"))
        self._candidate_limit = int(os.getenv("RERANK_CROSS_ENCODER_LIMIT", "20"))

    def _load_model(self) -> bool:
        """Attempt to load the cross-encoder model. Returns True on success."""
        if self._load_attempted:
            return self._model is not None
        self._load_attempted = True
        try:
            from sentence_transformers import CrossEncoder  # noqa: PLC0415
            self._model = CrossEncoder(self.model_name)
            LOGGER.info("CrossEncoderReranker loaded model=%s", self.model_name)
            return True
        except ImportError:
            LOGGER.warning(
                "sentence-transformers not installed — CrossEncoderReranker falling back to BM25. "
                "Install with: pip install sentence-transformers"
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "CrossEncoderReranker failed to load model=%s: %s — falling back to BM25",
                self.model_name,
                exc,
            )
        return False

    def score(self, query: str, candidate_text: str) -> float:
        """Single-candidate scoring — loads model if needed, falls back to BM25."""
        if not self._load_model():
            return self._fallback.score(query, candidate_text)
        try:
            scores = self._model.predict([[query, candidate_text]])
            return float(scores[0])
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("CrossEncoder.predict failed: %s", exc)
            return self._fallback.score(query, candidate_text)

    def rerank(
        self,
        query: str,
        candidates: list[FusedCandidate],
        top_k: int,
    ) -> tuple[list[FusedCandidate], float]:
        """
        Batch cross-encoder reranking with timeout protection and BM25 fallback.
        Only scores up to _candidate_limit candidates to control latency.
        """
        if not self._load_model():
            return self._fallback.rerank(query, candidates, top_k)

        t_start = time.perf_counter()

        # Limit the candidates fed to the cross-encoder for cost/latency control
        to_rerank = candidates[: self._candidate_limit]
        remainder = candidates[self._candidate_limit :]

        import concurrent.futures  # noqa: PLC0415

        def _run_inference() -> list[float]:
            pairs = [[query, c.content] for c in to_rerank]
            return self._model.predict(pairs).tolist()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_inference)
                scores = future.result(timeout=self._timeout_s)

            for candidate, score in zip(to_rerank, scores):
                candidate.rerank_score = round(float(score), 6)

            # Remainder candidates still get BM25 scores for the sort
            if remainder:
                # In-place BM25 for the overflow — sets rerank_score
                q_tokens = _tokenize(query)
                for c in remainder:
                    c.rerank_score = self._fallback.score(query, c.content)

            all_candidates = to_rerank + remainder
            all_candidates.sort(key=lambda c: (c.rerank_score, c.rrf_score), reverse=True)
            result = all_candidates[:top_k]
            for idx, c in enumerate(result, start=1):
                c.final_rank = idx

            latency_ms = (time.perf_counter() - t_start) * 1000
            LOGGER.debug(
                "CrossEncoderReranker: scored %d candidates in %.1fms (limit=%d)",
                len(to_rerank),
                latency_ms,
                self._candidate_limit,
            )
            return result, latency_ms

        except concurrent.futures.TimeoutError:
            latency_ms = (time.perf_counter() - t_start) * 1000
            LOGGER.warning(
                "CrossEncoderReranker timed out after %.1fs (limit=%ds) — "
                "falling back to BM25 for this request",
                latency_ms / 1000,
                self._timeout_s,
            )
            return self._fallback.rerank(query, candidates, top_k)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("CrossEncoderReranker inference failed: %s — falling back to BM25", exc)
            return self._fallback.rerank(query, candidates, top_k)


def get_reranker(config: RetrievalConfig) -> BaseReranker:
    """
    Factory returning the appropriate reranker based on environment configuration.

    Default: BM25Reranker (in-batch IDF, no external dependencies)
    Optional: CrossEncoderReranker (RERANK_CROSS_ENCODER_ENABLED=true + sentence-transformers)
    """
    import os  # noqa: PLC0415
    cross_encoder_enabled = os.getenv("RERANK_CROSS_ENCODER_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if cross_encoder_enabled:
        model_name = os.getenv(
            "RERANK_CROSS_ENCODER_MODEL",
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
        )
        LOGGER.info("Using CrossEncoderReranker model=%s", model_name)
        return CrossEncoderReranker(model_name=model_name)
    return BM25Reranker()
