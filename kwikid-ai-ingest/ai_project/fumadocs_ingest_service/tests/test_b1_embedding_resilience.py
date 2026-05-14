"""
tests/test_b1_embedding_resilience.py

Tests for embedding pipeline resilience:
  - ConnectError retry (the crash root cause)
  - Timeout retry
  - 429 rate-limit retry with Retry-After
  - 5xx server error retry
  - Circuit breaker engagement and cooldown
  - Batch-level fault isolation (one bad batch does not crash the run)
  - on_batch_embedded callback (streaming upsert)
  - Resume: already-embedded chunks are skipped

All tests use mocks/stubs — no real API or DB calls.
"""
from __future__ import annotations

import time
from typing import Optional
from unittest.mock import MagicMock, patch, call
import pytest

import httpx

from rag_engine.embedding.base import EmbeddingError, EmbeddingResult
from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
from rag_engine.schemas.chunk_schema import TicketChunk
from rag_engine.schemas.ticket_document import AutomationLabel, ChunkType


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_chunk(
    ticket_id: str = "ticket-001",
    chunk_index: int = 0,
    content: str = "Sample content for testing",
    embedding: Optional[list[float]] = None,
) -> TicketChunk:
    chunk = TicketChunk.build(
        ticket_id=ticket_id,
        chunk_index=chunk_index,
        chunk_type=ChunkType.QUERY_BODY,
        chunk_total=1,
        content=content,
        client="unity_bank",
        automation_label=AutomationLabel.AUTO_REPLY,
    )
    chunk.embedding = embedding
    return chunk


def _make_chunks(n: int) -> list[TicketChunk]:
    return [_make_chunk(f"ticket-{i:03}", i, f"Content for chunk {i}") for i in range(n)]


def _mock_provider(
    embeddings: Optional[list[list[float]]] = None,
    fail_with: Optional[Exception] = None,
    call_count: int = 0,
) -> MagicMock:
    """Create a mock EmbeddingProvider."""
    provider = MagicMock()
    call_counter = {"n": 0}

    def _embed_batch(texts: list[str]) -> EmbeddingResult:
        call_counter["n"] += 1
        if fail_with is not None:
            raise fail_with
        embs = embeddings or [[0.1] * 1536 for _ in texts]
        return EmbeddingResult(
            texts=texts,
            embeddings=embs[: len(texts)],
            model="text-embedding-3-small",
            total_tokens=len(texts) * 10,
            api_calls=1,
        )

    provider.embed_batch.side_effect = _embed_batch
    provider._call_counter = call_counter
    return provider


# ─── OpenAIEmbeddingProvider unit tests ──────────────────────────────────────

class TestOpenAIEmbeddingProviderRetry:
    """Tests for retry logic in OpenAIEmbeddingProvider."""

    def _provider(self, **kwargs) -> OpenAIEmbeddingProvider:
        defaults = {
            "api_key": "sk-test-key-1234",
            "max_retries": 3,
            "retry_base_delay_s": 0.01,   # Fast for tests
            "retry_max_delay_s": 0.05,
            "connect_timeout_s": 5.0,
            "read_timeout_s": 10.0,
            "write_timeout_s": 5.0,
            "pool_timeout_s": 5.0,
        }
        defaults.update(kwargs)
        return OpenAIEmbeddingProvider(**defaults)

    def test_connect_error_retried_then_succeeds(self):
        """ConnectError (DNS failure / getaddrinfo failed) must be retried."""
        provider = self._provider()
        texts = ["Hello world"]

        call_count = {"n": 0}

        def _patched_post(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise httpx.ConnectError("getaddrinfo failed")
            # Third call succeeds
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "data": [{"embedding": [0.1] * 1536, "index": 0}],
                "usage": {"total_tokens": 5},
            }
            return resp

        with patch.object(provider._client, "post", side_effect=_patched_post):
            result = provider.embed_batch(texts)

        assert result.embeddings[0] == [0.1] * 1536
        assert call_count["n"] == 3  # Failed twice, succeeded on third

    def test_connect_error_exhausts_retries(self):
        """After all retries exhausted on ConnectError, raise EmbeddingError."""
        provider = self._provider(max_retries=2)
        texts = ["Hello world"]

        with patch.object(
            provider._client, "post",
            side_effect=httpx.ConnectError("getaddrinfo failed"),
        ):
            with pytest.raises(EmbeddingError) as exc_info:
                provider.embed_batch(texts)

        assert "getaddrinfo" in str(exc_info.value).lower() or "failed" in str(exc_info.value).lower()

    def test_network_error_retried(self):
        """NetworkError (connection reset) must be retried."""
        provider = self._provider(max_retries=2)
        texts = ["Hello"]

        call_count = {"n": 0}

        def _patched_post(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.RemoteProtocolError("Server disconnected", request=None)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "data": [{"embedding": [0.5] * 1536, "index": 0}],
                "usage": {"total_tokens": 3},
            }
            return resp

        with patch.object(provider._client, "post", side_effect=_patched_post):
            result = provider.embed_batch(texts)

        assert len(result.embeddings) == 1
        assert call_count["n"] == 2

    def test_timeout_error_retried(self):
        """Timeout must be retried."""
        provider = self._provider(max_retries=2)

        call_count = {"n": 0}

        def _patched_post(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.ReadTimeout("Request timed out")
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "data": [{"embedding": [0.2] * 1536, "index": 0}],
                "usage": {"total_tokens": 4},
            }
            return resp

        with patch.object(provider._client, "post", side_effect=_patched_post):
            result = provider.embed_batch(["Hello"])

        assert result.embeddings[0] == [0.2] * 1536
        assert call_count["n"] == 2

    def test_429_rate_limit_retried(self):
        """HTTP 429 must be retried after waiting."""
        provider = self._provider(max_retries=2)

        call_count = {"n": 0}

        def _patched_post(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                resp = MagicMock()
                resp.status_code = 429
                resp.headers = {"Retry-After": "0.01"}
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "data": [{"embedding": [0.3] * 1536, "index": 0}],
                "usage": {"total_tokens": 5},
            }
            return resp

        with patch.object(provider._client, "post", side_effect=_patched_post):
            result = provider.embed_batch(["test"])

        assert len(result.embeddings) == 1
        assert call_count["n"] == 2

    def test_503_server_error_retried(self):
        """HTTP 503 must be retried."""
        provider = self._provider(max_retries=2)

        call_count = {"n": 0}

        def _patched_post(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                resp = MagicMock()
                resp.status_code = 503
                resp.headers = {}
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "data": [{"embedding": [0.7] * 1536, "index": 0}],
                "usage": {"total_tokens": 5},
            }
            return resp

        with patch.object(provider._client, "post", side_effect=_patched_post):
            result = provider.embed_batch(["test"])

        assert result.embeddings[0] == [0.7] * 1536

    def test_401_not_retried(self):
        """HTTP 401 (bad API key) must NOT be retried — raise immediately."""
        provider = self._provider()

        def _patched_post(*args, **kwargs):
            resp = MagicMock()
            resp.status_code = 401
            resp.text = "Unauthorized"
            return resp

        with patch.object(provider._client, "post", side_effect=_patched_post):
            with pytest.raises(EmbeddingError) as exc_info:
                provider.embed_batch(["test"])

        assert "401" in str(exc_info.value)

    def test_invalid_api_key_raises_on_init(self):
        """Empty or placeholder API key must raise ValueError on construction."""
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            OpenAIEmbeddingProvider(api_key="")

        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            OpenAIEmbeddingProvider(api_key="your-placeholder-key")

    def test_backoff_has_jitter(self):
        """_backoff_with_jitter should not return exactly the same value twice."""
        provider = self._provider()
        delays = [provider._backoff_with_jitter(1) for _ in range(20)]
        # At least some variation expected from jitter
        assert len(set(round(d, 3) for d in delays)) > 1


# ─── BatchEmbeddingProcessor tests ───────────────────────────────────────────

class TestBatchEmbeddingProcessorResilience:

    def _processor(self, provider, batch_size=10, **kwargs) -> BatchEmbeddingProcessor:
        defaults = {
            "circuit_breaker_threshold": 3,
            "circuit_breaker_cooldown_s": 0.01,  # Fast for tests
        }
        defaults.update(kwargs)
        return BatchEmbeddingProcessor(provider=provider, batch_size=batch_size, **defaults)

    def test_successful_embedding_sets_chunk_embeddings(self):
        """All chunks should have embeddings after a successful run."""
        provider = _mock_provider()
        processor = self._processor(provider)
        chunks = _make_chunks(5)

        metrics = processor.embed_chunks(chunks)

        assert metrics.chunks_embedded == 5
        assert metrics.chunks_failed == 0
        assert all(c.embedding is not None for c in chunks)
        assert all(len(c.embedding) == 1536 for c in chunks)

    def test_one_bad_batch_does_not_crash_run(self):
        """If one batch fails, remaining batches should still be embedded."""
        call_count = {"n": 0}

        def _embed(texts):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.ConnectError("DNS failed")
            return EmbeddingResult(
                texts=texts,
                embeddings=[[0.1] * 1536 for _ in texts],
                model="test",
                total_tokens=len(texts),
                api_calls=1,
            )

        provider = MagicMock()
        provider.embed_batch.side_effect = _embed
        processor = self._processor(provider, batch_size=5)
        chunks = _make_chunks(15)  # 3 batches of 5

        metrics = processor.embed_chunks(chunks)

        # First batch (5) failed, remaining (10) embedded
        assert metrics.chunks_failed == 5
        assert metrics.chunks_embedded == 10
        assert metrics.batches_failed == 1
        assert metrics.batches_succeeded == 2
        # First 5 chunks have no embedding; rest do
        assert all(c.embedding is None for c in chunks[:5])
        assert all(c.embedding is not None for c in chunks[5:])

    def test_circuit_breaker_engages_after_threshold(self):
        """After N consecutive failures, circuit breaker should engage."""
        provider = _mock_provider(fail_with=httpx.ConnectError("DNS fail"))
        processor = self._processor(
            provider,
            batch_size=3,
            circuit_breaker_threshold=2,
            circuit_breaker_cooldown_s=0.001,
        )
        chunks = _make_chunks(30)  # 10 batches of 3

        # Should engage circuit breaker, circuit_breaks > 0
        metrics = processor.embed_chunks(chunks)
        assert metrics.circuit_breaks > 0
        assert metrics.chunks_embedded == 0  # Nothing succeeded

    def test_skip_already_embedded_chunks(self):
        """Chunks with existing embeddings should be skipped."""
        provider = _mock_provider()
        processor = self._processor(provider, batch_size=10)

        chunks = _make_chunks(6)
        # Pre-embed first 3
        for c in chunks[:3]:
            c.embedding = [0.9] * 1536

        metrics = processor.embed_chunks(chunks, skip_already_embedded=True)

        # Only 3 new embeddings should be requested
        assert metrics.chunks_submitted == 3
        assert metrics.chunks_embedded == 3
        # Original embeddings preserved
        assert all(c.embedding == [0.9] * 1536 for c in chunks[:3])

    def test_on_batch_embedded_callback_called_per_batch(self):
        """The on_batch_embedded callback should be invoked once per successful batch."""
        provider = _mock_provider()
        processor = self._processor(provider, batch_size=3)
        chunks = _make_chunks(9)  # 3 batches of 3

        batch_calls = []

        def _callback(batch):
            batch_calls.append([c.ticket_id for c in batch])

        metrics = processor.embed_chunks(chunks, on_batch_embedded=_callback)

        assert len(batch_calls) == 3
        assert metrics.chunks_embedded == 9
        assert sum(len(b) for b in batch_calls) == 9

    def test_on_batch_embedded_callback_failure_does_not_abort(self):
        """A failing on_batch_embedded callback must NOT abort the embedding run."""
        provider = _mock_provider()
        processor = self._processor(provider, batch_size=5)
        chunks = _make_chunks(10)

        def _bad_callback(batch):
            raise RuntimeError("Simulated upsert failure")

        # Should still complete embedding; callback errors are logged and swallowed
        metrics = processor.embed_chunks(chunks, on_batch_embedded=_bad_callback)

        assert metrics.chunks_embedded == 10  # Embedding still worked
        assert all(c.embedding is not None for c in chunks)

    def test_empty_chunks_returns_zero_metrics(self):
        """Passing an empty list should return immediately with zero metrics."""
        provider = _mock_provider()
        processor = self._processor(provider)

        metrics = processor.embed_chunks([])

        assert metrics.chunks_submitted == 0
        assert metrics.chunks_embedded == 0
        provider.embed_batch.assert_not_called()

    def test_metrics_track_api_calls_and_tokens(self):
        """Metrics should aggregate api_calls and tokens across all batches."""
        provider = _mock_provider()
        processor = self._processor(provider, batch_size=3)
        chunks = _make_chunks(9)  # 3 batches

        metrics = processor.embed_chunks(chunks)

        assert metrics.api_calls == 3  # One per batch
        assert metrics.total_tokens == 9 * 10  # 10 tokens per chunk (from mock)
        assert metrics.total_latency_ms >= 0


# ─── Resume behavior ──────────────────────────────────────────────────────────

class TestResumeOnCrash:
    """
    Tests that verify the streaming embed+upsert design gives free resume.
    The test simulates what happens when:
      1. First run embeds batches 1-2, upserts them, then crashes on batch 3
      2. Second run detects batches 1-2 already in DB (dedup skip) and only embeds batch 3
    """

    def test_already_embedded_chunks_skipped_on_resume(self):
        """
        Chunks pre-loaded with embeddings (simulating already-upserted state)
        should be skipped without calling embed_batch.
        """
        provider = _mock_provider()
        processor = BatchEmbeddingProcessor(provider=provider, batch_size=5)

        # Simulate 3 chunks already embedded (they'd be dedup-skipped in real pipeline,
        # but here we test the skip_already_embedded flag directly)
        chunks = _make_chunks(8)
        for c in chunks[:3]:
            c.embedding = [0.42] * 1536  # "Already embedded" from prior run

        metrics = processor.embed_chunks(chunks, skip_already_embedded=True)

        # Only 5 new chunks should be embedded (8 - 3 = 5)
        assert metrics.chunks_submitted == 5
        assert metrics.chunks_embedded == 5

        # The 3 pre-embedded chunks keep their original embedding
        for c in chunks[:3]:
            assert c.embedding == [0.42] * 1536

    def test_callback_not_called_for_skipped_chunks(self):
        """
        The on_batch_embedded callback should only receive newly-embedded chunks,
        not pre-existing ones.
        """
        provider = _mock_provider()
        processor = BatchEmbeddingProcessor(provider=provider, batch_size=10)

        chunks = _make_chunks(5)
        # Pre-embed all chunks
        for c in chunks:
            c.embedding = [0.7] * 1536

        callback_invocations = []

        metrics = processor.embed_chunks(
            chunks,
            skip_already_embedded=True,
            on_batch_embedded=lambda b: callback_invocations.append(len(b)),
        )

        assert metrics.chunks_submitted == 0
        assert len(callback_invocations) == 0
        provider.embed_batch.assert_not_called()
