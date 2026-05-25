"""
retrieval/reranker.py

Modular reranking layer for the hybrid retrieval pipeline.

Architecture:
  - BaseReranker: pluggable interface (score / rerank)
  - BM25Reranker: IDF-weighted BM25 (default) — no external deps, fast
  - CrossEncoderReranker: dense reranking via sentence-transformers (optional)

BM25Reranker:
  k1=1.5 (TF saturation), b=0.75 (length normalization)
  Smoothed IDF: log((n - df + 0.5) / (df + 0.5) + 1)
  Phrase match bonus: +0.5 * |query_tokens| when exact phrase found in content
  Title token bonus: +0.3 per shared token with document title

CrossEncoderReranker production safeguards:
  - Worker-safe: uses a threading.Lock + module-level singleton so the model
    is loaded once per process (not per request, not per class instantiation).
    With uvicorn --workers 2+, each worker process loads independently (expected).
  - Circuit breaker: after CB_THRESHOLD consecutive inference failures, the
    reranker enters open state and falls back to BM25 for CB_COOLDOWN_S seconds.
    Prevents cascading failures from keeping requests in a broken state.
  - GPU detection: uses CUDA if available, falls back to CPU (no configuration needed).
  - Timeout: ThreadPoolExecutor with configurable timeout. BM25 fallback on timeout.
  - Candidate cap: only scores top RERANK_CROSS_ENCODER_LIMIT candidates (default 20)
    to control latency; overflow scored with BM25.
  - Model warmup: dummy inference call at load time to JIT-compile and warm CUDA.

Environment:
  RERANK_CROSS_ENCODER_ENABLED=false            — master switch (default: off)
  RERANK_CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
  RERANK_CROSS_ENCODER_TIMEOUT_S=5.0            — per-request inference timeout
  RERANK_CROSS_ENCODER_LIMIT=20                 — max candidates fed to cross-encoder
  RERANK_CROSS_ENCODER_CB_THRESHOLD=3           — consecutive failures to open circuit
  RERANK_CROSS_ENCODER_CB_COOLDOWN_S=60         — seconds circuit stays open
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from abc import ABC, abstractmethod

from retrieval.models import FusedCandidate
from retrieval.config import RetrievalConfig

LOGGER = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


# ── Base interface ────────────────────────────────────────────────────────────

class BaseReranker(ABC):
    """Pluggable reranker interface."""

    @abstractmethod
    def score(self, query: str, candidate_text: str) -> float:
        """Score a single candidate. Not all implementations support this meaningfully."""
        ...

    def rerank(
        self,
        query: str,
        candidates: list[FusedCandidate],
        top_k: int,
    ) -> tuple[list[FusedCandidate], float]:
        """Score all candidates, sort descending, return top_k with final_rank assigned."""
        t_start = time.perf_counter()
        for c in candidates:
            c.rerank_score = self.score(query, c.content)
        candidates.sort(key=lambda c: (c.rerank_score, c.rrf_score), reverse=True)
        result = candidates[:top_k]
        for idx, c in enumerate(result, start=1):
            c.final_rank = idx
        return result, (time.perf_counter() - t_start) * 1000


# ── BM25 reranker ─────────────────────────────────────────────────────────────

class BM25Reranker(BaseReranker):
    """
    In-batch BM25 reranker.

    Uses the full candidate set to compute in-batch IDF — more accurate than
    single-candidate scoring because IDF reflects how rare terms are in the
    retrieved set, not the full corpus.

    Matches the _bm25_scores() implementation in app/query.py exactly for
    consistency across legacy and hybrid retrieval paths.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def score(self, query: str, candidate_text: str) -> float:
        """Single-candidate score — TF-only (no IDF). Use rerank() for batch."""
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
            return candidates[:top_k], (time.perf_counter() - t_start) * 1000

        contents = [c.content for c in candidates]
        doc_token_sets = [_tokenize(ct) for ct in contents]
        doc_lengths = [len(dt) for dt in doc_token_sets]
        avg_dl = sum(doc_lengths) / n

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

            if len(query_lower) > 4 and query_lower in content_lower:
                score += len(q_tokens) * 0.5

            title = str(candidate.metadata.get("title", "") if candidate.metadata else "")
            title_overlap = len(q_tokens & _tokenize(title))
            if title_overlap:
                score += title_overlap * 0.3

            candidate.rerank_score = round(score, 6)

        candidates.sort(key=lambda c: (c.rerank_score, c.rrf_score), reverse=True)
        result = candidates[:top_k]
        for idx, c in enumerate(result, start=1):
            c.final_rank = idx

        return result, (time.perf_counter() - t_start) * 1000


# ── CrossEncoder: worker-safe singleton ───────────────────────────────────────

# Module-level state shared within a single worker process.
# Each uvicorn worker has its own copy (process-level isolation).
_CE_MODEL = None
_CE_LOCK = threading.Lock()
_CE_LOAD_ATTEMPTED = False
_CE_DEVICE: str | None = None


def _load_cross_encoder_model(model_name: str) -> tuple[Any, str] | tuple[None, None]:
    """
    Load a CrossEncoder model once per process. Thread-safe via module lock.
    Returns (model, device) or (None, None) on failure.
    """
    global _CE_MODEL, _CE_LOAD_ATTEMPTED, _CE_DEVICE

    with _CE_LOCK:
        if _CE_LOAD_ATTEMPTED:
            return _CE_MODEL, _CE_DEVICE

        _CE_LOAD_ATTEMPTED = True
        try:
            from sentence_transformers import CrossEncoder  # noqa: PLC0415
        except ImportError:
            LOGGER.warning(
                "CrossEncoderReranker: sentence-transformers not installed. "
                "Install with: pip install sentence-transformers. "
                "Falling back to BM25Reranker."
            )
            return None, None

        # GPU detection
        try:
            import torch  # noqa: PLC0415
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

        try:
            model = CrossEncoder(model_name, device=device)

            # Model warmup — JIT-compile and warm CUDA kernels
            _ = model.predict([["warmup", "warmup"]])
            LOGGER.info(
                "CrossEncoderReranker: loaded model=%s device=%s",
                model_name, device,
            )
            _CE_MODEL = model
            _CE_DEVICE = device
            return model, device
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "CrossEncoderReranker: failed to load model=%s on device=%s: %s. "
                "Falling back to BM25Reranker.",
                model_name, device, exc,
            )
            return None, None


# Type alias for type-checker only (sentence_transformers may not be installed)
from typing import Any


class CrossEncoderReranker(BaseReranker):
    """
    Dense reranker using sentence-transformers CrossEncoder.

    Production safeguards:
      - Worker-safe singleton model (loaded once per process, not per request)
      - Circuit breaker: open after CB_THRESHOLD consecutive failures
      - Timeout: ThreadPoolExecutor with configurable hard limit
      - Candidate cap: only top-N candidates scored for latency control
      - Overflow candidates scored with BM25
      - Graceful BM25 fallback on any failure

    Recommended model: cross-encoder/ms-marco-MiniLM-L-6-v2
      22MB, ~10ms/pair CPU | ~2ms/pair GPU | excellent precision for support retrieval
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self.model_name = model_name
        self._fallback = BM25Reranker()
        self._timeout_s = float(os.getenv("RERANK_CROSS_ENCODER_TIMEOUT_S", "5.0"))
        self._candidate_limit = int(os.getenv("RERANK_CROSS_ENCODER_LIMIT", "20"))
        self._cb_threshold = int(os.getenv("RERANK_CROSS_ENCODER_CB_THRESHOLD", "3"))
        self._cb_cooldown_s = float(os.getenv("RERANK_CROSS_ENCODER_CB_COOLDOWN_S", "60.0"))

        # Circuit breaker state (per-instance, which is per-process)
        self._cb_failures = 0
        self._cb_open_until = 0.0
        self._cb_lock = threading.Lock()

        # Eagerly load model at construction (happens during startup, not first request)
        self._model, self._device = _load_cross_encoder_model(model_name)

    def _is_circuit_open(self) -> bool:
        """Return True if circuit breaker is in open (tripped) state."""
        with self._cb_lock:
            if self._cb_failures < self._cb_threshold:
                return False
            if time.monotonic() >= self._cb_open_until:
                # Cooldown expired — half-open: allow one trial
                self._cb_failures = 0
                LOGGER.info(
                    "CrossEncoderReranker: circuit breaker half-open after cooldown"
                )
                return False
            return True

    def _record_failure(self) -> None:
        with self._cb_lock:
            self._cb_failures += 1
            if self._cb_failures >= self._cb_threshold:
                self._cb_open_until = time.monotonic() + self._cb_cooldown_s
                LOGGER.warning(
                    "CrossEncoderReranker: circuit breaker OPEN after %d failures "
                    "(cooldown=%.0fs until %.0f)",
                    self._cb_failures, self._cb_cooldown_s, self._cb_open_until,
                )

    def _record_success(self) -> None:
        with self._cb_lock:
            self._cb_failures = 0

    def score(self, query: str, candidate_text: str) -> float:
        """Single-candidate scoring — falls back to BM25 if model unavailable."""
        if self._model is None or self._is_circuit_open():
            return self._fallback.score(query, candidate_text)
        try:
            scores = self._model.predict([[query, candidate_text]])
            self._record_success()
            return float(scores[0])
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("CrossEncoder.score failed: %s", exc)
            self._record_failure()
            return self._fallback.score(query, candidate_text)

    def rerank(
        self,
        query: str,
        candidates: list[FusedCandidate],
        top_k: int,
    ) -> tuple[list[FusedCandidate], float]:
        """
        Batch reranking with circuit breaker, timeout, and BM25 fallback.
        """
        if self._model is None or self._is_circuit_open():
            LOGGER.debug("CrossEncoderReranker: using BM25 fallback (model=%s circuit_open=%s)",
                         self._model is None, self._is_circuit_open())
            return self._fallback.rerank(query, candidates, top_k)

        t_start = time.perf_counter()
        to_rerank = candidates[: self._candidate_limit]
        overflow = candidates[self._candidate_limit:]

        import concurrent.futures  # noqa: PLC0415

        def _infer() -> list[float]:
            pairs = [[query, c.content] for c in to_rerank]
            return self._model.predict(pairs).tolist()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_infer)
                scores = future.result(timeout=self._timeout_s)

            self._record_success()
            for candidate, score in zip(to_rerank, scores):
                candidate.rerank_score = round(float(score), 6)

            # Score overflow with BM25
            for c in overflow:
                c.rerank_score = self._fallback.score(query, c.content)

            all_candidates = to_rerank + overflow
            all_candidates.sort(key=lambda c: (c.rerank_score, c.rrf_score), reverse=True)
            result = all_candidates[:top_k]
            for idx, c in enumerate(result, start=1):
                c.final_rank = idx

            latency_ms = (time.perf_counter() - t_start) * 1000
            LOGGER.debug(
                "CrossEncoderReranker: scored %d candidates (device=%s) in %.1fms",
                len(to_rerank), self._device, latency_ms,
            )
            return result, latency_ms

        except concurrent.futures.TimeoutError:
            latency_ms = (time.perf_counter() - t_start) * 1000
            LOGGER.warning(
                "CrossEncoderReranker: timed out after %.1fs — BM25 fallback",
                latency_ms / 1000,
            )
            self._record_failure()
            return self._fallback.rerank(query, candidates, top_k)

        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t_start) * 1000
            LOGGER.warning(
                "CrossEncoderReranker: inference failed (%s) — BM25 fallback",
                type(exc).__name__,
            )
            self._record_failure()
            return self._fallback.rerank(query, candidates, top_k)

    @property
    def circuit_state(self) -> str:
        if self._is_circuit_open():
            return "open"
        if self._cb_failures > 0:
            return "degraded"
        return "closed"

    @property
    def backend(self) -> str:
        if self._model is None:
            return "bm25_fallback_no_model"
        if self.circuit_state == "open":
            return "bm25_fallback_circuit_open"
        return f"cross_encoder({self._device})"


# ── Factory ───────────────────────────────────────────────────────────────────

# Module-level singleton to avoid repeated model instantiation
_RERANKER_SINGLETON: BaseReranker | None = None
_RERANKER_SINGLETON_LOCK = threading.Lock()


def get_reranker(config: RetrievalConfig) -> BaseReranker:
    """
    Return the appropriate reranker based on RERANK_CROSS_ENCODER_ENABLED env var.
    The CrossEncoderReranker is a singleton (loaded once per process).
    BM25Reranker is lightweight and can be instantiated per-call.
    """
    global _RERANKER_SINGLETON

    cross_encoder_enabled = os.getenv(
        "RERANK_CROSS_ENCODER_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}

    if not cross_encoder_enabled:
        return BM25Reranker()

    # CrossEncoder: use singleton to avoid loading model multiple times
    if _RERANKER_SINGLETON is not None:
        return _RERANKER_SINGLETON

    with _RERANKER_SINGLETON_LOCK:
        if _RERANKER_SINGLETON is None:
            model_name = os.getenv(
                "RERANK_CROSS_ENCODER_MODEL",
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
            )
            LOGGER.info("Initializing CrossEncoderReranker model=%s", model_name)
            _RERANKER_SINGLETON = CrossEncoderReranker(model_name=model_name)

    return _RERANKER_SINGLETON
