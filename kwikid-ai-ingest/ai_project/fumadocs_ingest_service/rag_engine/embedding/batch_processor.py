"""
rag_engine/embedding/batch_processor.py

Production-grade batch embedding processor for the ingestion pipeline.

Key features:
  - Pre-embed validation: empty content, oversized tokens, excessive repetition,
    and whitespace payloads are detected and skipped before any API call.
  - Catches ALL exceptions per batch (not just EmbeddingError), so one bad batch
    cannot crash the entire run.
  - on_batch_embedded callback: caller can upsert each batch immediately,
    enabling free resume-on-crash via the deduplication checker.
  - Circuit breaker: after N consecutive batch failures, pause for cooldown_s
    before retrying. Prevents hammering a degraded API for hours.
  - Structured per-batch metrics: batch_id, attempt count, latency, tokens.
  - Progress % logged at each checkpoint.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from rag_engine.embedding.base import EmbeddingProvider
from rag_engine.schemas.chunk_schema import TicketChunk
from rag_engine.utils.tokens import count_tokens

# Patterns for encoded-blob detection (base64 / hex long tokens with no spaces)
_BASE64_CHARS = re.compile(r'^[A-Za-z0-9+/=]{50,}$')
_HEX_CHARS    = re.compile(r'^[0-9a-fA-F]{50,}$')

LOGGER = logging.getLogger(__name__)


@dataclass
class BatchEmbeddingMetrics:
    chunks_submitted:  int   = 0
    chunks_embedded:   int   = 0
    chunks_failed:     int   = 0
    chunks_invalid:    int   = 0   # failed pre-embed validation (skipped, not retried)
    batches_total:     int   = 0
    batches_succeeded: int   = 0
    batches_failed:    int   = 0
    batches_skipped:   int   = 0   # skipped by circuit breaker
    api_calls:         int   = 0
    total_tokens:      int   = 0
    total_latency_ms:  float = 0.0
    retry_count:       int   = 0   # total extra attempts across all batches
    circuit_breaks:    int   = 0   # number of times circuit breaker engaged


@dataclass
class _BatchRecord:
    """Internal record for one batch attempt."""
    batch_num:   int
    chunk_ids:   list[str]
    size:        int
    latency_ms:  float = 0.0
    succeeded:   bool  = False
    failure_msg: str   = ""


class BatchEmbeddingProcessor:
    """
    Processes a list of TicketChunks through the embedding pipeline in batches.

    Mutates chunks in-place by setting chunk.embedding.

    Pre-embed validation:
      Each chunk is validated before batching. Chunks that fail validation are
      skipped (not sent to the API) and counted in metrics.chunks_invalid.
      One invalid chunk never contaminates the rest of the batch.

    Circuit breaker:
      After `circuit_breaker_threshold` consecutive failed batches, pauses for
      `circuit_breaker_cooldown_s` seconds. Resets on first success.
      After `circuit_breaker_max_breaks` total breaks, aborts the embedding phase.
    """

    # Thresholds for the repetition check
    _REPETITION_MIN_WORDS = 20
    _REPETITION_MAX_RATIO = 0.5
    _WHITESPACE_MAX_RATIO = 0.9
    _MIN_CONTENT_CHARS    = 10

    def __init__(
        self,
        provider: EmbeddingProvider,
        batch_size: int = 64,
        log_every_n_batches: int = 5,
        # Circuit breaker
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown_s: float = 60.0,
        circuit_breaker_max_breaks: int = 5,
        # Validation
        max_input_tokens: int = 7000,
        embedding_model: str = "text-embedding-3-small",
    ) -> None:
        self._provider = provider
        self._batch_size = min(batch_size, 2048)
        self._log_every = log_every_n_batches
        self._cb_threshold = circuit_breaker_threshold
        self._cb_cooldown_s = circuit_breaker_cooldown_s
        self._cb_max_breaks = circuit_breaker_max_breaks
        self._max_input_tokens = max_input_tokens
        self._embedding_model = embedding_model

    def embed_chunks(
        self,
        chunks: list[TicketChunk],
        *,
        skip_already_embedded: bool = True,
        on_batch_embedded: Optional[Callable[[list[TicketChunk]], None]] = None,
    ) -> BatchEmbeddingMetrics:
        """
        Embed all provided chunks in-place. Returns aggregated metrics.

        Args:
            chunks:               Chunks to embed. Mutated in-place (chunk.embedding set).
            skip_already_embedded: If True, skip chunks that already have embeddings.
            on_batch_embedded:    Optional callback invoked after each successful batch.
                                  Receives the list of just-embedded chunks.
                                  Use this to upsert batches immediately (resume support).
        """
        metrics = BatchEmbeddingMetrics()

        # Filter already-embedded and run pre-embed validation
        to_embed: list[TicketChunk] = []
        for chunk in chunks:
            if skip_already_embedded and chunk.embedding:
                continue
            reason = self._validate_chunk(chunk)
            if reason is not None:
                LOGGER.warning(
                    "Skipping chunk %s (ticket=%s, index=%d): %s",
                    chunk.id[:8], chunk.ticket_id, chunk.chunk_index, reason,
                )
                metrics.chunks_invalid += 1
                continue
            to_embed.append(chunk)

        metrics.chunks_submitted = len(to_embed)
        metrics.batches_total = (
            max(1, (len(to_embed) + self._batch_size - 1) // self._batch_size)
            if to_embed else 0
        )

        if not to_embed:
            skipped_str = f"; {len(chunks) - len(to_embed)} already embedded or invalid" if chunks else ""
            LOGGER.debug(
                "No chunks to embed%s.", skipped_str
            )
            return metrics

        if metrics.chunks_invalid:
            LOGGER.warning(
                "%d chunk(s) failed pre-embed validation and will be skipped.",
                metrics.chunks_invalid,
            )

        LOGGER.info(
            "Starting embedding: %d chunks in %d batches (batch_size=%d, max_tokens=%d)",
            len(to_embed), metrics.batches_total, self._batch_size, self._max_input_tokens,
        )

        consecutive_failures = 0
        total_breaks = 0

        for batch_num, batch in enumerate(self._batches(to_embed), start=1):
            # ── Circuit breaker: open → cooldown ────────────────────────────
            if consecutive_failures >= self._cb_threshold:
                total_breaks += 1
                metrics.circuit_breaks += 1

                if total_breaks > self._cb_max_breaks:
                    LOGGER.error(
                        "Circuit breaker tripped %d times (max %d). "
                        "Embedding service appears persistently unavailable. "
                        "Aborting embedding phase. %d chunks will have no embedding.",
                        total_breaks, self._cb_max_breaks,
                        metrics.chunks_submitted - metrics.chunks_embedded,
                    )
                    break

                LOGGER.warning(
                    "Circuit breaker: %d consecutive failures — pausing %.0fs before retry. "
                    "(Trip %d/%d)",
                    consecutive_failures, self._cb_cooldown_s,
                    total_breaks, self._cb_max_breaks,
                )
                time.sleep(self._cb_cooldown_s)
                consecutive_failures = 0  # reset after cooldown

            texts = [c.content for c in batch]
            t_start = time.perf_counter()

            try:
                result = self._provider.embed_batch(texts)
                latency_ms = (time.perf_counter() - t_start) * 1000

                for chunk, embedding in zip(batch, result.embeddings, strict=True):
                    chunk.embedding = embedding

                metrics.chunks_embedded   += len(batch)
                metrics.batches_succeeded += 1
                metrics.api_calls         += result.api_calls
                metrics.total_tokens      += result.total_tokens
                metrics.total_latency_ms  += latency_ms
                consecutive_failures = 0  # reset on success

                pct = (metrics.chunks_embedded / metrics.chunks_submitted) * 100
                if (
                    batch_num == 1
                    or batch_num % self._log_every == 0
                    or batch_num == metrics.batches_total
                ):
                    LOGGER.info(
                        "Embedding batch %d/%d | %d/%d chunks (%.1f%%) | "
                        "%.0fms | %d tokens",
                        batch_num, metrics.batches_total,
                        metrics.chunks_embedded, metrics.chunks_submitted,
                        pct, latency_ms, result.total_tokens,
                    )

                # ── Streaming upsert callback ────────────────────────────────
                if on_batch_embedded is not None:
                    try:
                        on_batch_embedded(batch)
                    except Exception as cb_exc:  # noqa: BLE001
                        LOGGER.error(
                            "on_batch_embedded callback failed for batch %d: %s "
                            "(embeddings retained in memory; upsert will be retried on re-run)",
                            batch_num, cb_exc,
                        )

            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.perf_counter() - t_start) * 1000
                consecutive_failures += 1
                metrics.chunks_failed    += len(batch)
                metrics.batches_failed   += 1
                metrics.total_latency_ms += latency_ms

                chunk_ids = [c.id[:8] for c in batch[:3]]
                LOGGER.error(
                    "Embedding batch %d/%d FAILED after %.0fms: %s: %s "
                    "— %d chunks will have no embedding. "
                    "Consecutive failures: %d/%d. First chunk IDs: %s",
                    batch_num, metrics.batches_total,
                    latency_ms, type(exc).__name__, str(exc)[:200],
                    len(batch),
                    consecutive_failures, self._cb_threshold,
                    chunk_ids,
                )

        avg_latency = metrics.total_latency_ms / max(metrics.batches_succeeded, 1)
        throughput = (
            metrics.chunks_embedded / max(metrics.total_latency_ms / 1000, 0.001)
        )

        LOGGER.info(
            "Embedding complete: %d embedded, %d failed, %d invalid | "
            "%d/%d batches OK | %d API calls, %d tokens | "
            "avg_batch_latency=%.0fms, throughput=%.1f chunks/s | "
            "%d circuit breaks",
            metrics.chunks_embedded, metrics.chunks_failed, metrics.chunks_invalid,
            metrics.batches_succeeded, metrics.batches_total,
            metrics.api_calls, metrics.total_tokens,
            avg_latency, throughput,
            metrics.circuit_breaks,
        )

        return metrics

    def _validate_chunk(self, chunk: TicketChunk) -> Optional[str]:
        """
        Validate a chunk before embedding.

        Returns a human-readable reason string if the chunk should be skipped,
        or None if the chunk is valid.
        """
        content = chunk.content

        # Empty or whitespace-only
        if not content or not content.strip():
            return "empty content"

        stripped = content.strip()

        # Too short to be meaningful
        if len(stripped) < self._MIN_CONTENT_CHARS:
            return f"content too short ({len(stripped)} chars; min {self._MIN_CONTENT_CHARS})"

        # Absurd whitespace ratio (e.g. "\n\n\n  word  \n\n\n")
        non_ws = len(stripped.replace(" ", "").replace("\n", "").replace("\t", ""))
        if non_ws == 0:
            return "no non-whitespace characters"
        whitespace_ratio = 1.0 - (non_ws / len(content))
        if whitespace_ratio > self._WHITESPACE_MAX_RATIO:
            return f"whitespace ratio {whitespace_ratio:.1%} (max {self._WHITESPACE_MAX_RATIO:.0%})"

        # Excessive word repetition (detects repeated error messages, stack trace dumps)
        words = content.lower().split()
        if len(words) >= self._REPETITION_MIN_WORDS:
            most_common_word, most_common_count = Counter(words).most_common(1)[0]
            ratio = most_common_count / len(words)
            if ratio > self._REPETITION_MAX_RATIO:
                return (
                    f"excessive repetition: word {most_common_word!r} appears "
                    f"{most_common_count}/{len(words)} times ({ratio:.1%})"
                )

        # Malformed unicode: excessive non-printable / surrogate characters
        control_chars = sum(
            1 for c in stripped
            if unicodedata.category(c) in {"Cc", "Cs"} and c not in {"\n", "\r", "\t"}
        )
        control_ratio = control_chars / max(len(stripped), 1)
        if control_ratio > 0.10:
            return (
                f"excessive control/surrogate characters: "
                f"{control_chars}/{len(stripped)} ({control_ratio:.1%})"
            )

        # Encoded-blob detection: words that look like base64 or hex dumps
        words = stripped.split()
        long_words = [w for w in words if len(w) >= 50]
        if long_words:
            encoded = sum(
                1 for w in long_words
                if _BASE64_CHARS.match(w) or _HEX_CHARS.match(w)
            )
            if encoded / len(long_words) > 0.5:
                return (
                    f"suspected encoded blob: {encoded}/{len(long_words)} "
                    "long tokens look like base64 or hex data"
                )

        # Token count exceeds hard limit (safety net — chunker should prevent this)
        token_count = count_tokens(content, self._embedding_model)
        if token_count > self._max_input_tokens:
            return (
                f"token count {token_count} exceeds max_input_tokens {self._max_input_tokens}. "
                "This chunk was not split by the chunker — it will be skipped. "
                "Check your chunk_target_tokens setting."
            )

        return None  # valid

    def _batches(self, chunks: list[TicketChunk]) -> Iterator[list[TicketChunk]]:
        """Yield successive sub-lists of size batch_size."""
        for i in range(0, len(chunks), self._batch_size):
            yield chunks[i : i + self._batch_size]
