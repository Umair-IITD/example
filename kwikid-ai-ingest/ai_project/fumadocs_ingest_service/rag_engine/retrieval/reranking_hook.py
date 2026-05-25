"""
rag_engine/retrieval/reranking_hook.py

Reranking hook interface for Phase B2+.

Implementations:
  NullReranker       — identity, no-op (default in B1)
  LexicalReranker    — BM25/TF-IDF rescoring (lightweight fallback)
  CrossEncoderReranker — sentence-transformers cross-encoder (NOT enabled by default)

To activate CrossEncoderReranker:
  1. pip install sentence-transformers
  2. Set RERANK_CROSS_ENCODER_ENABLED=true in .env
  3. Optionally tune RERANK_CROSS_ENCODER_MODEL, RERANK_CROSS_ENCODER_TIMEOUT_S,
     RERANK_CROSS_ENCODER_LIMIT

CrossEncoderReranker safety guarantees:
  - Lazy model load: no blocking at import time
  - Circuit breaker: opens after N consecutive failures, auto-resets after cooldown
  - GPU auto-detection: uses CUDA when available, falls back to CPU
  - ThreadPoolExecutor timeout: inference capped at RERANK_CROSS_ENCODER_TIMEOUT_S
  - Fallback: degrades to LexicalReranker on any failure (ImportError, timeout, CB open)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rag_engine.retrieval.ticket_retriever import RetrievedChunk

LOGGER = logging.getLogger(__name__)


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


# ── CrossEncoder reranker ─────────────────────────────────────────────────────
# Controlled by RERANK_CROSS_ENCODER_ENABLED (default: false).
# DO NOT enable by default — requires pip install sentence-transformers.

_CE_ENABLED: bool = os.getenv("RERANK_CROSS_ENCODER_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
_CE_MODEL: str = os.getenv(
    "RERANK_CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
)
_CE_TIMEOUT_S: float = float(os.getenv("RERANK_CROSS_ENCODER_TIMEOUT_S", "5.0"))
_CE_LIMIT: int = int(os.getenv("RERANK_CROSS_ENCODER_LIMIT", "20"))
_CE_CB_THRESHOLD: int = 3     # consecutive failures before circuit opens
_CE_CB_COOLDOWN_S: float = 60.0


class CrossEncoderReranker:
    """
    Production-grade CrossEncoder reranker.

    NOT enabled by default. Requires:
      pip install sentence-transformers
      RERANK_CROSS_ENCODER_ENABLED=true

    Safety guarantees:
      - Lazy model load — no blocking at import; first rerank() call triggers load
      - Load lock — only one thread loads the model; others wait or skip if timed out
      - Circuit breaker — opens after _CE_CB_THRESHOLD consecutive failures,
        auto-resets after _CE_CB_COOLDOWN_S seconds
      - ThreadPoolExecutor timeout — inference capped at RERANK_CROSS_ENCODER_TIMEOUT_S
      - Fallback to LexicalReranker on any failure path

    GPU:
      Uses CUDA when torch.cuda.is_available(), otherwise CPU.
      Override with RERANK_CROSS_ENCODER_DEVICE=cpu|cuda.
    """

    def __init__(self) -> None:
        self._model: Optional[Any] = None
        self._load_error: Optional[str] = None
        self._load_lock = threading.Lock()
        self._loaded = False
        self._fallback = LexicalReranker()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cross_enc")

        # Circuit breaker state
        self._cb_lock = threading.Lock()
        self._cb_failures = 0
        self._cb_open_until: float = 0.0

        self._device: str = os.getenv("RERANK_CROSS_ENCODER_DEVICE", "").strip().lower()

    def _load_model(self) -> bool:
        """Attempt to load the model once. Returns True on success."""
        if self._loaded:
            return self._model is not None

        with self._load_lock:
            if self._loaded:  # double-checked
                return self._model is not None
            try:
                from sentence_transformers import CrossEncoder  # noqa: PLC0415
                import torch  # noqa: PLC0415

                if not self._device:
                    self._device = "cuda" if torch.cuda.is_available() else "cpu"

                LOGGER.info(
                    "CrossEncoderReranker: loading model=%s device=%s",
                    _CE_MODEL, self._device,
                )
                self._model = CrossEncoder(_CE_MODEL, device=self._device)
                LOGGER.info("CrossEncoderReranker: model loaded successfully")
            except ImportError:
                self._load_error = (
                    "sentence-transformers not installed. "
                    "Run: pip install sentence-transformers. Falling back to LexicalReranker."
                )
                LOGGER.warning("CrossEncoderReranker: %s", self._load_error)
            except Exception as exc:
                self._load_error = f"Model load failed: {exc}"
                LOGGER.warning("CrossEncoderReranker: %s", self._load_error)
            finally:
                self._loaded = True

        return self._model is not None

    def _circuit_open(self) -> bool:
        with self._cb_lock:
            if self._cb_open_until > 0 and time.monotonic() < self._cb_open_until:
                return True
            return False

    def _record_success(self) -> None:
        with self._cb_lock:
            self._cb_failures = 0
            self._cb_open_until = 0.0

    def _record_failure(self) -> None:
        with self._cb_lock:
            self._cb_failures += 1
            if self._cb_failures >= _CE_CB_THRESHOLD:
                self._cb_open_until = time.monotonic() + _CE_CB_COOLDOWN_S
                LOGGER.warning(
                    "CrossEncoderReranker: circuit opened after %d consecutive failures. "
                    "Will retry after %.0fs.",
                    self._cb_failures, _CE_CB_COOLDOWN_S,
                )

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not _CE_ENABLED or not chunks:
            return self._fallback.rerank(query, chunks, top_k)

        if self._circuit_open():
            LOGGER.debug("CrossEncoderReranker: circuit open — using LexicalReranker fallback")
            return self._fallback.rerank(query, chunks, top_k)

        if not self._load_model():
            return self._fallback.rerank(query, chunks, top_k)

        candidates = chunks[:_CE_LIMIT]
        pairs = [(query, c.content) for c in candidates]

        def _score() -> list[float]:
            return self._model.predict(pairs).tolist()  # type: ignore[union-attr]

        try:
            future = self._executor.submit(_score)
            scores: list[float] = future.result(timeout=_CE_TIMEOUT_S)
            self._record_success()
        except FuturesTimeoutError:
            self._record_failure()
            LOGGER.warning(
                "CrossEncoderReranker: inference timed out (%.1fs) — falling back",
                _CE_TIMEOUT_S,
            )
            return self._fallback.rerank(query, chunks, top_k)
        except Exception as exc:
            self._record_failure()
            LOGGER.warning("CrossEncoderReranker: inference failed (%s) — falling back", exc)
            return self._fallback.rerank(query, chunks, top_k)

        ranked = sorted(
            zip(candidates, scores),
            key=lambda t: t[1],
            reverse=True,
        )

        # Append any chunks beyond _CE_LIMIT at the end (already vector-ranked)
        overflow = chunks[_CE_LIMIT:]
        reranked = [c for c, _ in ranked] + overflow
        return reranked[:top_k]

    def health(self) -> dict:
        """Return current health state for diagnostics."""
        with self._cb_lock:
            cb_failures = self._cb_failures
            cb_open = self._cb_open_until > 0 and time.monotonic() < self._cb_open_until

        return {
            "enabled": _CE_ENABLED,
            "model": _CE_MODEL,
            "device": self._device or "not_loaded",
            "model_loaded": self._loaded and self._model is not None,
            "load_error": self._load_error,
            "circuit_open": cb_open,
            "consecutive_failures": cb_failures,
            "timeout_s": _CE_TIMEOUT_S,
        }
