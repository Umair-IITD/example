"""
tests/test_b1_token_chunking.py

Tests for token-aware chunking, pre-embed validation, document persistence,
and idempotent reruns.

Coverage:
  - Token utility functions (count_tokens, truncate, safe_split)
  - Oversized chunk splitting and truncation fallback
  - Pathological inputs: JSON blobs, log dumps, empty, whitespace-only
  - ISSUE_HEADER safety limit
  - Pre-embed validation layer (invalid chunk detection)
  - Document persistence (rag_ticket_documents upserted before chunks)
  - document_id propagated to all chunks
  - Idempotent reruns (same document_id across runs)
  - Batch isolation (invalid chunk does not kill rest of batch)

All tests use mocks — no real API or DB calls.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Optional
from unittest.mock import MagicMock, call, patch

import pytest

from rag_engine.chunking.ticket_chunker import TicketChunker, _deterministic_id
from rag_engine.embedding.base import EmbeddingResult
from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor
from rag_engine.schemas.chunk_schema import TicketChunk
from rag_engine.schemas.ticket_document import (
    AutomationLabel,
    ChunkType,
    RagTicketDocument,
)
from rag_engine.utils.tokens import (
    count_tokens,
    safe_split_by_tokens,
    truncate_to_token_limit,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_doc(
    ticket_id: str = "T001",
    query_body: str = "Customer cannot log in.",
    resolution: str = "Reset password and cleared cache.",
    client: str = "unity_bank",
) -> RagTicketDocument:
    return RagTicketDocument(
        ticket_id=ticket_id,
        client=client,
        source_file="test.parquet",
        source_type="freshdesk",
        issue_header_text=(
            f"[ISSUE SUMMARY]\nTicket ID: {ticket_id}\nSubject: Login issue\n"
            f"Client: Unity Bank | Environment: Production | Priority: High\n"
            f"Automation Classification: Requires agent review"
        ),
        query_body_text=query_body,
        resolution_rca_text=resolution,
        full_document_text=f"{query_body}\n\n{resolution}",
        content_hash=hashlib.sha256(query_body.encode()).hexdigest(),
        automation_label=AutomationLabel.HUMAN_REVIEW,
        escalation_flag=False,
        has_rca=True,
        has_sop=False,
    )


def _make_chunker(**kwargs) -> TicketChunker:
    defaults = dict(
        chunk_target_tokens=50,    # tiny target so tests can trigger splitting
        chunk_overlap_tokens=5,
        max_input_tokens=100,
        embedding_model="text-embedding-3-small",
        min_query_body_chars=10,
    )
    defaults.update(kwargs)
    return TicketChunker(**defaults)


def _make_chunk(
    content: str,
    ticket_id: str = "T001",
    chunk_index: int = 0,
    embedding: Optional[list[float]] = None,
) -> TicketChunk:
    chunk = TicketChunk.build(
        ticket_id=ticket_id,
        chunk_index=chunk_index,
        chunk_type=ChunkType.QUERY_BODY,
        chunk_total=1,
        content=content,
        client="unity_bank",
        automation_label=AutomationLabel.HUMAN_REVIEW,
    )
    chunk.embedding = embedding
    return chunk


def _mock_provider(embedding_dim: int = 4) -> MagicMock:
    provider = MagicMock()
    provider.embed_batch.side_effect = lambda texts: EmbeddingResult(
        texts=texts,
        embeddings=[[0.1] * embedding_dim for _ in texts],
        model="text-embedding-3-small",
        total_tokens=len(texts) * 10,
        api_calls=1,
    )
    return provider


# ─── Task 2: Token utility tests ──────────────────────────────────────────────

class TestTokenUtilities:
    def test_count_tokens_short_text(self):
        n = count_tokens("Hello world", model="text-embedding-3-small")
        # "Hello world" is 2 tokens in cl100k_base
        assert n >= 1

    def test_count_tokens_empty(self):
        assert count_tokens("", model="text-embedding-3-small") == 0

    def test_count_tokens_is_deterministic(self):
        text = "The customer reported that OTP was not received."
        assert count_tokens(text) == count_tokens(text)

    def test_truncate_preserves_short_text(self):
        text = "Short text"
        result = truncate_to_token_limit(text, max_tokens=1000)
        assert result == text

    def test_truncate_shortens_long_text(self):
        # Create text that is definitely > 5 tokens
        long_text = "word " * 100
        result = truncate_to_token_limit(long_text, max_tokens=5)
        assert len(result) < len(long_text)
        assert count_tokens(result) <= 5

    def test_truncate_empty(self):
        assert truncate_to_token_limit("", 100) == ""

    def test_safe_split_short_text_returns_single_chunk(self):
        text = "Short text."
        parts = safe_split_by_tokens(text, max_tokens=1000)
        assert parts == [text]

    def test_safe_split_long_text_splits(self):
        # 200 words ≈ 150–250 tokens; split at 50 tokens → multiple chunks
        text = "This is a support ticket description. " * 30
        parts = safe_split_by_tokens(text, max_tokens=50, overlap_tokens=5)
        assert len(parts) > 1
        # Each part must be within the limit
        for part in parts:
            assert count_tokens(part) <= 50

    def test_safe_split_overlap_causes_content_repetition(self):
        # With overlap, the tail of chunk N should appear at the start of chunk N+1
        text = "alpha beta gamma delta epsilon zeta eta theta iota kappa " * 10
        parts = safe_split_by_tokens(text, max_tokens=20, overlap_tokens=5)
        assert len(parts) >= 2
        # Check that total tokens > text tokens (overlap is repeated)
        total_tokens = sum(count_tokens(p) for p in parts)
        original_tokens = count_tokens(text)
        assert total_tokens > original_tokens

    def test_safe_split_empty_returns_empty_list(self):
        assert safe_split_by_tokens("", max_tokens=100) == []
        assert safe_split_by_tokens("   ", max_tokens=100) == []

    def test_safe_split_overlap_geq_max_clamps_safely(self):
        # overlap >= max should not infinite loop
        parts = safe_split_by_tokens("word " * 50, max_tokens=10, overlap_tokens=15)
        assert len(parts) >= 1
        for part in parts:
            assert count_tokens(part) <= 10


# ─── Task 2+3: Token-aware chunker tests ──────────────────────────────────────

class TestTicketChunkerTokenAware:
    def test_normal_doc_produces_chunks(self):
        chunker = _make_chunker(chunk_target_tokens=500, max_input_tokens=7000)
        doc = _make_doc()
        chunks = chunker.chunk(doc)
        assert len(chunks) >= 2  # at least ISSUE_HEADER + QUERY_BODY

    def test_chunk_token_counts_stored_in_extra_metadata(self):
        chunker = _make_chunker(chunk_target_tokens=500, max_input_tokens=7000)
        doc = _make_doc()
        chunks = chunker.chunk(doc)
        for chunk in chunks:
            assert "token_count" in chunk.extra_metadata
            assert isinstance(chunk.extra_metadata["token_count"], int)
            assert chunk.extra_metadata["token_count"] > 0

    def test_no_chunk_exceeds_max_input_tokens(self):
        chunker = _make_chunker(chunk_target_tokens=50, max_input_tokens=100)
        # Long query body: many repetitions to force splitting
        long_query = "The customer is unable to complete the KYC process. " * 40
        doc = _make_doc(query_body=long_query)
        chunks = chunker.chunk(doc)
        assert len(chunks) > 0
        for chunk in chunks:
            tc = count_tokens(chunk.content)
            assert tc <= 100, f"Chunk has {tc} tokens, exceeds limit of 100"

    def test_oversized_header_is_truncated(self):
        chunker = _make_chunker(max_input_tokens=20)
        # Force a very large header by monkeypatching the doc
        doc = _make_doc()
        # Replace header with something definitely > 20 tokens
        doc = doc.model_copy(update={
            "issue_header_text": "word " * 200
        })
        chunks = chunker.chunk(doc)
        header_chunks = [c for c in chunks if c.chunk_type == ChunkType.ISSUE_HEADER]
        assert len(header_chunks) == 1
        assert count_tokens(header_chunks[0].content) <= 20
        assert header_chunks[0].extra_metadata.get("truncated") is True

    def test_long_resolution_is_split_not_truncated(self):
        chunker = _make_chunker(chunk_target_tokens=30, max_input_tokens=50)
        # Resolution that's definitely > 30 tokens
        long_rca = "The root cause was identified as a DNS misconfiguration. " * 20
        doc = _make_doc(resolution=long_rca)
        rca_chunks = [
            c for c in chunker.chunk(doc)
            if c.chunk_type == ChunkType.RESOLUTION_RCA
        ]
        assert len(rca_chunks) > 1, "Long resolution should be split into multiple chunks"
        for c in rca_chunks:
            assert count_tokens(c.content) <= 50

    def test_pathological_json_blob_is_split_safely(self):
        """A JSON dump embedded in a ticket body must not exceed token limit."""
        json_blob = (
            '{"error":"TOKEN_EXPIRED","trace":"' + "x" * 1000 + '",'
            '"code":12345,"timestamp":"2026-01-01T00:00:00Z"}'
        )
        chunker = _make_chunker(chunk_target_tokens=100, max_input_tokens=200)
        doc = _make_doc(query_body=json_blob)
        chunks = chunker.chunk(doc)
        for chunk in chunks:
            assert count_tokens(chunk.content) <= 200

    def test_deterministic_chunk_ids(self):
        chunker = _make_chunker()
        doc = _make_doc(ticket_id="T999")
        chunks_run1 = chunker.chunk(doc)
        chunks_run2 = chunker.chunk(doc)
        ids_run1 = [c.id for c in chunks_run1]
        ids_run2 = [c.id for c in chunks_run2]
        assert ids_run1 == ids_run2

    def test_chunk_ids_differ_across_tickets(self):
        chunker = _make_chunker()
        doc_a = _make_doc(ticket_id="T001")
        doc_b = _make_doc(ticket_id="T002")
        ids_a = {c.id for c in chunker.chunk(doc_a)}
        ids_b = {c.id for c in chunker.chunk(doc_b)}
        assert ids_a.isdisjoint(ids_b)

    def test_document_id_propagated_to_chunks(self):
        chunker = _make_chunker()
        doc = _make_doc()
        doc_id = "test-document-id-123"
        chunks = chunker.chunk(doc, document_id=doc_id)
        for chunk in chunks:
            assert chunk.document_id == doc_id

    def test_empty_query_body_below_min_chars_skipped(self):
        chunker = _make_chunker(min_query_body_chars=100)
        doc = _make_doc(query_body="Too short")
        chunks = chunker.chunk(doc)
        # QUERY_BODY should be skipped since "Too short" < 100 chars
        query_chunks = [c for c in chunks if c.chunk_type == ChunkType.QUERY_BODY]
        assert len(query_chunks) == 0


# ─── Task 4: Pre-embed validation tests ───────────────────────────────────────

class TestPreEmbedValidation:
    def _make_processor(self, max_input_tokens=7000) -> BatchEmbeddingProcessor:
        provider = _mock_provider()
        return BatchEmbeddingProcessor(
            provider=provider,
            batch_size=10,
            max_input_tokens=max_input_tokens,
            embedding_model="text-embedding-3-small",
        )

    def test_empty_chunk_is_invalid(self):
        proc = self._make_processor()
        chunk = _make_chunk("")
        reason = proc._validate_chunk(chunk)
        assert reason is not None
        assert "empty" in reason.lower()

    def test_whitespace_only_chunk_is_invalid(self):
        proc = self._make_processor()
        chunk = _make_chunk("   \n\n\t   ")
        reason = proc._validate_chunk(chunk)
        assert reason is not None

    def test_very_short_chunk_is_invalid(self):
        proc = self._make_processor()
        chunk = _make_chunk("Hi")
        reason = proc._validate_chunk(chunk)
        assert reason is not None

    def test_valid_chunk_passes(self):
        proc = self._make_processor()
        chunk = _make_chunk("The customer reports that the OTP was not delivered to their phone.")
        reason = proc._validate_chunk(chunk)
        assert reason is None

    def test_oversized_chunk_fails_validation(self):
        proc = self._make_processor(max_input_tokens=5)
        # Use unique words so repetition check is NOT triggered; only token limit fires
        oversized = " ".join(f"uniqueterm{i}" for i in range(60))
        chunk = _make_chunk(oversized)
        reason = proc._validate_chunk(chunk)
        assert reason is not None
        assert "token" in reason.lower()

    def test_repetitive_chunk_fails_validation(self):
        proc = self._make_processor()
        # One word repeated 100 times — 100% repetition
        chunk = _make_chunk("error " * 100)
        reason = proc._validate_chunk(chunk)
        assert reason is not None
        assert "repetition" in reason.lower()

    def test_invalid_chunk_skipped_rest_of_batch_embedded(self):
        """One invalid chunk in a batch must not prevent the others from embedding."""
        provider = _mock_provider()
        # max_input_tokens=20 — valid chunks (< 20 tokens) pass, oversized one fails
        proc = BatchEmbeddingProcessor(
            provider=provider,
            batch_size=10,
            max_input_tokens=20,
            embedding_model="text-embedding-3-small",
        )
        valid = _make_chunk("The customer is unable to log in to the portal.", chunk_index=0)
        # Unique words so repetition check is skipped; only token count fires
        oversized = " ".join(f"uniqueterm{i}" for i in range(60))
        invalid = _make_chunk(oversized, chunk_index=1)
        another_valid = _make_chunk("OTP was not delivered to the registered mobile number.", chunk_index=2)

        metrics = proc.embed_chunks([valid, invalid, another_valid])

        assert metrics.chunks_invalid == 1
        assert metrics.chunks_embedded == 2
        assert valid.embedding is not None
        assert another_valid.embedding is not None
        assert invalid.embedding is None  # skipped

    def test_metrics_chunks_invalid_counter(self):
        proc = self._make_processor(max_input_tokens=5)
        # All 3 chunks will fail validation (too many tokens)
        chunks = [_make_chunk("word " * 30, chunk_index=i) for i in range(3)]
        metrics = proc.embed_chunks(chunks)
        assert metrics.chunks_invalid == 3
        assert metrics.chunks_embedded == 0

    def test_high_whitespace_chunk_is_invalid(self):
        proc = self._make_processor()
        # Many newlines surrounding a single word
        chunk = _make_chunk("\n" * 200 + "word" + "\n" * 200)
        reason = proc._validate_chunk(chunk)
        assert reason is not None


# ─── Task 5: Document persistence tests ───────────────────────────────────────

class TestDocumentPersistence:
    def _make_pipeline(self):
        from rag_engine.ingestion.pipeline import IngestionPipeline, _deterministic_document_id
        from rag_engine.config.rag_settings import get_rag_settings

        settings = get_rag_settings()
        supabase = MagicMock()
        provider = _mock_provider()
        pipeline = IngestionPipeline(
            settings=settings,
            supabase_client=supabase,
            embedding_provider=provider,
        )
        return pipeline, supabase, settings

    def test_deterministic_document_id_is_stable(self):
        from rag_engine.ingestion.pipeline import _deterministic_document_id
        id1 = _deterministic_document_id("T001", "v1")
        id2 = _deterministic_document_id("T001", "v1")
        assert id1 == id2
        assert isinstance(id1, str)
        assert len(id1) == 36  # UUID format

    def test_deterministic_document_id_differs_by_ticket(self):
        from rag_engine.ingestion.pipeline import _deterministic_document_id
        id_a = _deterministic_document_id("T001", "v1")
        id_b = _deterministic_document_id("T002", "v1")
        assert id_a != id_b

    def test_deterministic_document_id_differs_by_version(self):
        from rag_engine.ingestion.pipeline import _deterministic_document_id
        id_v1 = _deterministic_document_id("T001", "v1")
        id_v2 = _deterministic_document_id("T001", "v2")
        assert id_v1 != id_v2

    def test_document_id_propagated_to_chunks(self):
        from rag_engine.ingestion.pipeline import _deterministic_document_id
        chunker = TicketChunker(
            chunk_target_tokens=1200,
            chunk_overlap_tokens=150,
            max_input_tokens=7000,
        )
        doc = _make_doc(ticket_id="T001")
        expected_doc_id = _deterministic_document_id("T001", "v1")
        chunks = chunker.chunk(doc, document_id=expected_doc_id)
        for chunk in chunks:
            assert chunk.document_id == expected_doc_id, (
                f"Chunk {chunk.chunk_index} has document_id={chunk.document_id!r}, "
                f"expected {expected_doc_id!r}"
            )

    def test_doc_to_db_row_has_required_fields(self):
        pipeline, supabase, settings = self._make_pipeline()
        from rag_engine.schemas.ingestion_record import IngestionRunRecord
        run = IngestionRunRecord(run_id="test-run-id")

        doc = _make_doc(ticket_id="T001")
        from rag_engine.ingestion.pipeline import _deterministic_document_id
        doc_id = _deterministic_document_id("T001", settings.index_version)
        row = pipeline._doc_to_db_row(doc, doc_id, run)

        required_fields = [
            "id", "ticket_id", "source_file", "source_type", "client",
            "automation_label", "escalation_flag", "has_rca", "has_sop",
            "document_text", "content_hash", "index_version", "ingestion_run_id",
        ]
        for field in required_fields:
            assert field in row, f"Missing field: {field}"

        assert row["id"] == doc_id
        assert row["ticket_id"] == "T001"
        assert row["client"] == "unity_bank"
        assert row["ingestion_run_id"] == "test-run-id"

    def test_upsert_documents_called_with_correct_table(self):
        pipeline, supabase, settings = self._make_pipeline()
        from rag_engine.schemas.ingestion_record import IngestionRunRecord
        run = IngestionRunRecord(run_id="test-run-id")

        doc_rows = [{"id": "fake-id", "ticket_id": "T001", "source_file": "test.parquet"}]
        pipeline._upsert_documents(doc_rows, run)

        supabase.table.assert_called_with(settings.ticket_documents_table)

    def test_full_run_calls_both_document_and_chunk_tables(self):
        """End-to-end: a full run must write to both tables."""
        import pandas as pd
        from rag_engine.ingestion.pipeline import IngestionPipeline
        from rag_engine.config.rag_settings import get_rag_settings

        settings = get_rag_settings()
        provider = _mock_provider()

        # Track which tables are upserted
        upserted_tables: list[str] = []

        def _make_table_mock(table_name: str) -> MagicMock:
            m = MagicMock()
            m.upsert.return_value.execute.return_value = MagicMock(data=[])
            m.select.return_value.in_.return_value.eq.return_value.execute.return_value = (
                MagicMock(data=[])
            )
            m.insert.return_value.execute.return_value = MagicMock(data=[])
            upserted_tables.append(table_name)
            return m

        supabase = MagicMock()
        supabase.table.side_effect = _make_table_mock

        pipeline = IngestionPipeline(
            settings=settings,
            supabase_client=supabase,
            embedding_provider=provider,
        )

        # Minimal source DataFrame with one ticket
        df = pd.DataFrame([{
            "ticket_id": "T001",
            "tenant_id": "unity_bank",
            "source_file": "test.parquet",
            "subject": "OTP not received",
            "cleaned_description": "Customer reports OTP is not delivered to registered mobile number.",
            "cleaned_rca": "SIM card was inactive; customer was asked to restart device.",
            "automation_label": "HUMAN_REVIEW",
            "rca_quality_score": 75,
            "has_rca": True,
            "has_sop": False,
            "escalation_flag": False,
        }])

        with patch.object(pipeline, "_load_source", return_value=df), \
             patch.object(pipeline, "_delta_tracker") as mock_dt:
            mock_dt.get_last_completed_run_time.return_value = None
            mock_dt.filter_delta.return_value = df
            run = pipeline.run(mode="full")

        # Both tables must have been written
        tables_called = [str(c.args[0]) for c in supabase.table.call_args_list]
        assert any(settings.ticket_documents_table in t for t in tables_called), (
            f"ticket_documents_table not written. Tables called: {tables_called}"
        )
        assert any(settings.ticket_chunks_table in t for t in tables_called), (
            f"ticket_chunks_table not written. Tables called: {tables_called}"
        )


# ─── Task 6: Dedup + resume safety ────────────────────────────────────────────

class TestDedupAndResumeSafety:
    def test_already_embedded_chunks_not_reembedded(self):
        provider = _mock_provider()
        proc = BatchEmbeddingProcessor(
            provider=provider,
            batch_size=10,
            max_input_tokens=7000,
        )
        pre_embedded = _make_chunk("Already embedded chunk", chunk_index=0)
        pre_embedded.embedding = [0.5, 0.5, 0.5, 0.5]
        fresh = _make_chunk("Needs embedding", chunk_index=1)

        proc.embed_chunks([pre_embedded, fresh], skip_already_embedded=True)

        # Only 1 API call for the 1 unembedded chunk
        assert provider.embed_batch.call_count == 1
        call_texts = provider.embed_batch.call_args[0][0]
        assert "Needs embedding" in call_texts
        assert "Already embedded chunk" not in call_texts

    def test_idempotent_chunk_ids_after_rerun(self):
        """Same doc → same chunk IDs regardless of how many times we chunk it."""
        chunker = _make_chunker(chunk_target_tokens=1200, max_input_tokens=7000)
        doc = _make_doc(ticket_id="IDEM-001")
        run1_ids = [c.id for c in chunker.chunk(doc, document_id="doc-1")]
        run2_ids = [c.id for c in chunker.chunk(doc, document_id="doc-1")]
        assert run1_ids == run2_ids

    def test_callback_not_called_for_skipped_chunks(self):
        provider = _mock_provider()
        proc = BatchEmbeddingProcessor(
            provider=provider,
            batch_size=10,
            max_input_tokens=7000,
        )
        already_embedded = _make_chunk("Already done", chunk_index=0)
        already_embedded.embedding = [0.1, 0.2, 0.3, 0.4]

        callback = MagicMock()
        proc.embed_chunks(
            [already_embedded],
            skip_already_embedded=True,
            on_batch_embedded=callback,
        )

        # No API call, no callback invocation
        provider.embed_batch.assert_not_called()
        callback.assert_not_called()

    def test_failed_chunks_have_no_embedding(self):
        from rag_engine.embedding.base import EmbeddingError
        failing_provider = MagicMock()
        failing_provider.embed_batch.side_effect = EmbeddingError("API down")

        proc = BatchEmbeddingProcessor(
            provider=failing_provider,
            batch_size=10,
            max_input_tokens=7000,
            circuit_breaker_threshold=99,
        )
        chunks = [
            _make_chunk(f"The customer reported issue number {i} with the OTP delivery.", chunk_index=i)
            for i in range(3)
        ]
        metrics = proc.embed_chunks(chunks)

        assert metrics.chunks_embedded == 0
        assert metrics.chunks_failed == 3
        for c in chunks:
            assert c.embedding is None

    def test_content_hash_is_stable(self):
        """Same content → same content_hash across runs (dedup correctness)."""
        chunker = _make_chunker()
        doc = _make_doc()
        chunks_run1 = chunker.chunk(doc)
        chunks_run2 = chunker.chunk(doc)
        hashes_run1 = [c.content_hash for c in chunks_run1]
        hashes_run2 = [c.content_hash for c in chunks_run2]
        assert hashes_run1 == hashes_run2


# ─── Task 7 extras: Batch isolation ───────────────────────────────────────────

class TestBatchIsolation:
    def _make_valid_chunks(self, n: int) -> list[TicketChunk]:
        """Helper: chunks with content long enough to pass pre-embed validation."""
        return [
            _make_chunk(
                f"Customer reported an issue with OTP delivery on attempt number {i}.",
                chunk_index=i,
            )
            for i in range(n)
        ]

    def test_one_api_failure_leaves_other_batches_embedded(self):
        from rag_engine.embedding.base import EmbeddingError

        fail_count = 0

        def side_effect(texts):
            nonlocal fail_count
            if fail_count == 0 and len(texts) == 2:  # only fail first batch of 2
                fail_count += 1
                raise EmbeddingError("First batch failed")
            return EmbeddingResult(
                texts=texts,
                embeddings=[[0.1, 0.2] for _ in texts],
                model="m",
                total_tokens=10,
                api_calls=1,
            )

        provider = MagicMock()
        provider.embed_batch.side_effect = side_effect

        proc = BatchEmbeddingProcessor(
            provider=provider,
            batch_size=2,           # 2 per batch
            circuit_breaker_threshold=5,
            max_input_tokens=7000,
        )
        chunks = self._make_valid_chunks(4)  # 2 batches of 2

        metrics = proc.embed_chunks(chunks)

        # First batch failed, second should succeed
        assert metrics.batches_failed == 1
        assert metrics.batches_succeeded == 1
        assert metrics.chunks_embedded == 2   # second batch
        assert metrics.chunks_failed == 2     # first batch

    def test_on_batch_embedded_called_per_successful_batch(self):
        provider = _mock_provider()
        proc = BatchEmbeddingProcessor(
            provider=provider,
            batch_size=2,
            max_input_tokens=7000,
        )
        chunks = self._make_valid_chunks(6)
        callback_calls: list[list[TicketChunk]] = []
        proc.embed_chunks(chunks, on_batch_embedded=lambda b: callback_calls.append(b))
        assert len(callback_calls) == 3  # 6 chunks / batch_size 2

    def test_on_batch_embedded_failure_does_not_abort_run(self):
        provider = _mock_provider()
        proc = BatchEmbeddingProcessor(
            provider=provider,
            batch_size=2,
            max_input_tokens=7000,
        )
        chunks = self._make_valid_chunks(4)

        def bad_callback(batch):
            raise RuntimeError("DB connection lost")

        metrics = proc.embed_chunks(chunks, on_batch_embedded=bad_callback)
        # All chunks are still embedded despite callback failures
        assert metrics.chunks_embedded == 4
        for c in chunks:
            assert c.embedding is not None
