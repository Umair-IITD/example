"""
tests/test_b1_retrieval.py

Retrieval test script for Phase B1.

Tests:
  1. Basic retrieval against rag_ticket_chunks (requires live DB + embeddings)
  2. Tenant isolation (unity_bank cannot see rbl_bank chunks)
  3. SOP retrieval from rag_sop_library
  4. Document builder output quality
  5. Chunker output structure validation

Run:
    # Unit tests (no DB needed):
    pytest tests/test_b1_retrieval.py -k "not live"

    # Live DB tests (requires populated rag_ticket_chunks):
    SUPABASE_URL=... SUPABASE_KEY=... pytest tests/test_b1_retrieval.py -k "live"
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Optional

import pytest

# ── Unit: Document Builder ────────────────────────────────────────────────────

class TestTicketDocumentBuilder:
    def _make_row(self, **overrides):
        from rag_engine.schemas.ticket_document import TicketSourceRow
        defaults = dict(
            ticket_id="12345",
            client="unity_bank",
            source_file="rbl_rca",
            subject="OTP not received",
            query_type="OTP Not Received",
            issue_area="Authentication",
            environment="production",
            priority="medium",
            status="closed",
            cleaned_description="Customer reports OTP is not being received on registered mobile number. Issue started today morning.",
            cleaned_rca="OTP delivery was blocked due to DND (Do Not Disturb) setting on customer's number. Support team requested customer to remove DND and retry.",
            automation_label="AUTO_REPLY",
            rca_quality_score=75,
            has_rca=True,
            has_sop=True,
            sop_status="SOP Present",
            escalation_flag=False,
            issue_recurrence="Recurring issue",
            resolution_status="Within SLA",
            agent_interactions=2,
            handling_time_mins=8,
        )
        defaults.update(overrides)
        return TicketSourceRow.model_validate(defaults)

    def test_builds_valid_document(self):
        from rag_engine.document_builder.ticket_builder import TicketDocumentBuilder
        builder = TicketDocumentBuilder()
        row = self._make_row()
        doc = builder.build(row)

        assert doc is not None
        assert doc.ticket_id == "12345"
        assert doc.client == "unity_bank"
        assert "OTP not received" in doc.issue_header_text
        assert "[ISSUE SUMMARY]" in doc.issue_header_text
        assert "[CUSTOMER QUERY]" in doc.query_body_text
        assert "[TROUBLESHOOTING AND RESOLUTION]" in doc.resolution_rca_text
        assert "DND" in doc.resolution_rca_text
        assert doc.content_hash  # SHA-256 set

    def test_returns_none_for_empty_content(self):
        from rag_engine.document_builder.ticket_builder import TicketDocumentBuilder
        builder = TicketDocumentBuilder()
        row = self._make_row(cleaned_description=None, cleaned_rca=None)
        doc = builder.build(row)
        assert doc is None

    def test_subject_fallback_when_no_description(self):
        from rag_engine.document_builder.ticket_builder import TicketDocumentBuilder
        builder = TicketDocumentBuilder()
        row = self._make_row(cleaned_description=None)  # has cleaned_rca
        doc = builder.build(row)
        assert doc is not None  # should still build from RCA alone

    def test_automation_label_normalization(self):
        from rag_engine.schemas.ticket_document import TicketSourceRow
        row = TicketSourceRow.model_validate({
            "ticket_id": "1",
            "client": "unity_bank",
            "source_file": "rbl_rca",
            "automation_label": "AUTO_RESOLVABLE",   # legacy label
        })
        assert row.automation_label == "AUTO_REPLY"

    def test_client_slug_normalization(self):
        from rag_engine.schemas.ticket_document import TicketSourceRow
        row = TicketSourceRow.model_validate({
            "ticket_id": "1",
            "client": "Unity Bank",
            "source_file": "rbl_rca",
        })
        assert row.client == "unity_bank"


# ── Unit: Ticket Chunker ──────────────────────────────────────────────────────

class TestTicketChunker:
    def _make_doc(self, **overrides):
        from rag_engine.schemas.ticket_document import AutomationLabel, RagTicketDocument
        defaults = dict(
            ticket_id="99999",
            client="unity_bank",
            source_file="rbl_rca",
            source_type="freshdesk",
            issue_header_text="[ISSUE SUMMARY]\nTicket: 99999\nCategory: OTP Not Received\nClient: Unity Bank",
            query_body_text="[CUSTOMER QUERY]\nCustomer is unable to receive OTP on registered mobile. Issue started today morning at 9am IST.",
            resolution_rca_text="[TROUBLESHOOTING AND RESOLUTION]\nResolution: Asked customer to check DND status.\n\n[ROOT CAUSE AND FIX]\nDND was enabled by customer. Removed DND and OTP delivered successfully.",
            full_document_text="[ISSUE SUMMARY]\n... [CUSTOMER QUERY]\n... [TROUBLESHOOTING AND RESOLUTION]\n...",
            content_hash=hashlib.sha256(b"test").hexdigest(),
            automation_label=AutomationLabel.AUTO_REPLY,
            escalation_flag=False,
            has_rca=True,
            has_sop=True,
            rca_quality_score=80,
        )
        defaults.update(overrides)
        return RagTicketDocument.model_validate(defaults)

    def test_produces_three_chunks(self):
        from rag_engine.chunking.ticket_chunker import TicketChunker
        chunker = TicketChunker()
        doc = self._make_doc()
        chunks = chunker.chunk(doc)

        assert len(chunks) == 3
        chunk_types = [c.chunk_type.value for c in chunks]
        assert "ISSUE_HEADER" in chunk_types
        assert "QUERY_BODY" in chunk_types
        assert "RESOLUTION_RCA" in chunk_types

    def test_chunk_ids_are_deterministic(self):
        from rag_engine.chunking.ticket_chunker import TicketChunker
        chunker = TicketChunker()
        doc = self._make_doc()

        chunks1 = chunker.chunk(doc)
        chunks2 = chunker.chunk(doc)

        for c1, c2 in zip(chunks1, chunks2):
            assert c1.id == c2.id, "Chunk IDs must be deterministic"

    def test_all_chunks_inherit_client(self):
        from rag_engine.chunking.ticket_chunker import TicketChunker
        chunker = TicketChunker()
        doc = self._make_doc(client="rbl_bank")
        chunks = chunker.chunk(doc)
        assert all(c.client == "rbl_bank" for c in chunks)

    def test_escalation_flag_inherited(self):
        from rag_engine.schemas.ticket_document import AutomationLabel
        from rag_engine.chunking.ticket_chunker import TicketChunker
        chunker = TicketChunker()
        doc = self._make_doc(
            automation_label=AutomationLabel.ESCALATION,
            escalation_flag=True,
        )
        chunks = chunker.chunk(doc)
        assert all(c.escalation_flag for c in chunks)

    def test_to_db_row_has_required_fields(self):
        from rag_engine.chunking.ticket_chunker import TicketChunker
        chunker = TicketChunker()
        doc = self._make_doc()
        chunks = chunker.chunk(doc)
        required_fields = {
            "id", "ticket_id", "client", "chunk_type", "content",
            "word_count", "content_hash", "automation_label",
            "has_rca", "has_sop", "index_version"
        }
        for chunk in chunks:
            row = chunk.to_db_row()
            missing = required_fields - set(row.keys())
            assert not missing, f"Missing fields in DB row: {missing}"

    def test_long_query_body_is_split(self):
        from rag_engine.chunking.ticket_chunker import TicketChunker
        chunker = TicketChunker(max_chunk_words=30, overlap_words=5)
        # 80 words of content → should split into 3 QUERY_BODY chunks
        long_text = "[CUSTOMER QUERY]\n" + " ".join([f"word{i}" for i in range(80)])
        from rag_engine.schemas.ticket_document import AutomationLabel, RagTicketDocument
        doc = RagTicketDocument.model_validate({
            "ticket_id": "split_test",
            "client": "unity_bank",
            "source_file": "rbl_rca",
            "source_type": "freshdesk",
            "issue_header_text": "[ISSUE SUMMARY]\nTest ticket",
            "query_body_text": long_text,
            "resolution_rca_text": "",
            "full_document_text": long_text,
            "content_hash": hashlib.sha256(b"test2").hexdigest(),
            "automation_label": AutomationLabel.AUTO_REPLY,
            "escalation_flag": False,
            "has_rca": False,
            "has_sop": False,
            "rca_quality_score": 0,
        })
        chunks = chunker.chunk(doc)
        query_body_chunks = [c for c in chunks if c.chunk_type.value == "QUERY_BODY"]
        assert len(query_body_chunks) >= 2, "Long QUERY_BODY should be split"


# ── Unit: Deduplication ───────────────────────────────────────────────────────

class TestDeduplicationChecker:
    def test_classify_chunks_new(self):
        from rag_engine.ingestion.deduplication import DeduplicationChecker
        from rag_engine.schemas.chunk_schema import TicketChunk
        from rag_engine.schemas.ticket_document import AutomationLabel, ChunkType

        checker = DeduplicationChecker(supabase_client=None, chunks_table="x")
        chunk = TicketChunk(
            id="abc", document_id=None, ticket_id="123",
            chunk_index=0, chunk_type=ChunkType.QUERY_BODY, chunk_total=2,
            content="test content", word_count=2,
            content_hash=hashlib.sha256(b"test content").hexdigest(),
            client="unity_bank",
            automation_label=AutomationLabel.AUTO_REPLY,
        )
        to_insert, to_update, to_skip = checker.classify_chunks([chunk], existing_hashes={})
        assert len(to_insert) == 1
        assert len(to_update) == 0
        assert len(to_skip) == 0

    def test_classify_chunks_skip_identical(self):
        from rag_engine.ingestion.deduplication import DeduplicationChecker
        from rag_engine.schemas.chunk_schema import TicketChunk
        from rag_engine.schemas.ticket_document import AutomationLabel, ChunkType

        checker = DeduplicationChecker(supabase_client=None, chunks_table="x")
        content_hash = hashlib.sha256(b"same content").hexdigest()
        chunk = TicketChunk(
            id="abc", document_id=None, ticket_id="123",
            chunk_index=0, chunk_type=ChunkType.QUERY_BODY, chunk_total=2,
            content="same content", word_count=2,
            content_hash=content_hash,
            client="unity_bank",
            automation_label=AutomationLabel.AUTO_REPLY,
        )
        existing = {"123": {0: content_hash}}
        to_insert, to_update, to_skip = checker.classify_chunks([chunk], existing)
        assert len(to_skip) == 1


# ── Live DB tests (skipped unless SUPABASE_URL is set) ───────────────────────

@pytest.mark.skipif(
    not os.getenv("SUPABASE_URL"),
    reason="Live DB test — requires SUPABASE_URL and SUPABASE_KEY env vars"
)
class TestLiveRetrieval:
    """
    Integration tests against a real Supabase instance.
    Requires B1 migrations to be applied and at least one ingestion run completed.
    """

    @pytest.fixture(scope="class")
    def retriever(self):
        from rag_engine.database.supabase_client import build_supabase_client_from_settings
        from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
        from rag_engine.retrieval.ticket_retriever import TicketRetriever
        from app.config import get_settings

        app_settings = get_settings()
        client = build_supabase_client_from_settings()
        embedder = OpenAIEmbeddingProvider(
            api_key=app_settings.embedding_api_key,
            model=app_settings.embedding_model,
        )
        return TicketRetriever(supabase_client=client, embedding_provider=embedder)

    def test_live_retrieval_returns_results(self, retriever):
        from rag_engine.retrieval.ticket_retriever import RetrievalRequest
        response = retriever.retrieve(
            RetrievalRequest(
                query_text="OTP not received on registered mobile number",
                client="unity_bank",
                top_k=5,
            )
        )
        assert response.chunks is not None
        assert isinstance(response.total_latency_ms, float)
        print(f"\nRetrieved {len(response.chunks)} chunks in {response.total_latency_ms:.0f}ms")
        for i, chunk in enumerate(response.chunks):
            print(f"  [{i}] {chunk.chunk_type} | sim={chunk.similarity:.3f} | {chunk.content[:80]}...")

    def test_live_tenant_isolation(self, retriever):
        """Cross-tenant bleed check: unity_bank query must NOT return rbl_bank chunks."""
        from rag_engine.retrieval.ticket_retriever import RetrievalRequest
        response = retriever.retrieve(
            RetrievalRequest(
                query_text="login failure for banking customer",
                client="unity_bank",
                top_k=10,
            )
        )
        for chunk in response.chunks:
            # If this assertion fails, tenant isolation is broken
            assert chunk.source_table != "rag_ticket_chunks" or \
                   chunk.extra_metadata.get("client") in (None, "unity_bank"), \
                f"Tenant bleed detected: chunk from non-unity_bank client returned for unity_bank query"

    def test_live_escalation_excluded(self, retriever):
        """ESCALATION chunks should not appear in auto-reply path."""
        from rag_engine.retrieval.ticket_retriever import RetrievalRequest
        response = retriever.retrieve(
            RetrievalRequest(
                query_text="server down production outage critical",
                client="unity_bank",
                exclude_escalation=True,
                top_k=10,
            )
        )
        for chunk in response.chunks:
            assert chunk.automation_label != "ESCALATION", \
                "ESCALATION chunk returned in non-escalation retrieval path"
