"""
tests/test_sprint2301_rag_adapter.py

Regression tests for HybridRAGProvider field completeness fix (Sprint 2.30.1).

Verifies that every field required by downstream consumers
(KnowledgeOrchestrator -> RAGEvidence, ReasoningService, response assembler)
is present in chunk dicts returned by HybridRAGProvider.retrieve().

Previously dropped fields: knowledge_class, quality_score, ticket_id, sop_id,
automation_label, query_type, similarity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest


# ── Minimal stubs (no external imports needed) ────────────────────────────────

@dataclass
class _FakeChunk:
    """Mirrors RetrievedChunk field set from ticket_retriever.py."""
    chunk_id:         str
    ticket_id:        Optional[str]
    sop_id:           Optional[str]
    chunk_type:       str
    content:          str
    similarity:       float
    boosted_score:    float
    source_table:     str
    has_rca:          bool                 = False
    has_sop:          bool                 = False
    automation_label: Optional[str]        = None
    query_type:       Optional[str]        = None
    extra_metadata:   dict                 = field(default_factory=dict)
    knowledge_class:  Optional[str]        = None
    quality_score:    Optional[float]      = None


@dataclass
class _FakeResponse:
    chunks:            list[_FakeChunk]
    total_candidates:  int   = 0
    semantic_latency_ms: float = 10.0
    total_latency_ms:  float  = 12.0
    client:            str    = "unity"
    query_hash:        str    = "deadbeef"
    retrieval_metadata: dict  = field(default_factory=dict)


def _make_retriever(chunks: list[_FakeChunk]) -> Any:
    """Build a minimal mock retriever that returns _FakeResponse."""
    retriever = MagicMock()

    def _retrieve(request):
        return _FakeResponse(chunks=chunks)

    retriever.retrieve.side_effect = _retrieve
    return retriever


# ── Import adapter under test ─────────────────────────────────────────────────

def _import_adapter():
    from case_engine.knowledge.rag_adapter import HybridRAGProvider
    return HybridRAGProvider


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestHybridRAGProviderFieldCompleteness:
    """Verify all chunk dict fields are present after the fix."""

    REQUIRED_FIELDS = {
        "content",
        "score",
        "source",
        "chunk_type",
        "chunk_id",
        "has_rca",
        "has_sop",
        "knowledge_class",
        "quality_score",
        "ticket_id",
        "sop_id",
        "automation_label",
        "query_type",
        "similarity",
    }

    def _make_ticket_chunk(self) -> _FakeChunk:
        return _FakeChunk(
            chunk_id="c1",
            ticket_id="T-123",
            sop_id=None,
            chunk_type="RESOLUTION_RCA",
            content="Reset the OTP flow from the admin portal.",
            similarity=0.72,
            boosted_score=0.72,
            source_table="rag_ticket_chunks",
            has_rca=True,
            has_sop=False,
            automation_label="AUTO_REPLY",
            query_type="VKYC_Session_Failure",
            knowledge_class=None,
            quality_score=None,
        )

    def _make_knowledge_chunk(self) -> _FakeChunk:
        return _FakeChunk(
            chunk_id="k1",
            ticket_id=None,
            sop_id="so_123",
            chunk_type="FAQ_ANSWER",
            content="To enable VKYC without OTP, remove &d=1&s=0 from the user link.",
            similarity=0.68,
            boosted_score=0.76,          # 0.68 + 0.08
            source_table="rag_knowledge_chunks",
            has_rca=False,
            has_sop=False,
            automation_label=None,
            query_type=None,
            knowledge_class="FAQ",
            quality_score=0.82,
        )

    def _make_sop_chunk(self) -> _FakeChunk:
        return _FakeChunk(
            chunk_id="s1",
            ticket_id=None,
            sop_id="sop-vkyc-001",
            chunk_type="SOP_PROCEDURE",
            content="Step 1: Navigate to Admin > Sessions.",
            similarity=0.61,
            boosted_score=0.76,          # 0.61 + 0.15
            source_table="rag_sop_chunks",
            has_rca=False,
            has_sop=True,
            automation_label=None,
            query_type=None,
            knowledge_class=None,
            quality_score=None,
        )

    def test_all_required_fields_present_ticket_chunk(self):
        HybridRAGProvider = _import_adapter()
        retriever = _make_retriever([self._make_ticket_chunk()])
        provider = HybridRAGProvider(retriever=retriever, default_tenant="unity")

        result = provider.retrieve("OTP not received", "VKYC_Session_Failure")

        assert result["chunks"], "Expected at least one chunk"
        chunk = result["chunks"][0]
        missing = self.REQUIRED_FIELDS - set(chunk.keys())
        assert not missing, f"Chunk dict missing required fields: {missing}"

    def test_all_required_fields_present_knowledge_chunk(self):
        HybridRAGProvider = _import_adapter()
        retriever = _make_retriever([self._make_knowledge_chunk()])
        provider = HybridRAGProvider(retriever=retriever, default_tenant="unity")

        result = provider.retrieve("start session without OTP", topic="VKYC")

        chunk = result["chunks"][0]
        missing = self.REQUIRED_FIELDS - set(chunk.keys())
        assert not missing, f"Knowledge chunk dict missing required fields: {missing}"

    def test_all_required_fields_present_sop_chunk(self):
        HybridRAGProvider = _import_adapter()
        retriever = _make_retriever([self._make_sop_chunk()])
        provider = HybridRAGProvider(retriever=retriever, default_tenant="unity")

        result = provider.retrieve("vkyc session procedure", topic="VKYC")

        chunk = result["chunks"][0]
        missing = self.REQUIRED_FIELDS - set(chunk.keys())
        assert not missing, f"SOP chunk dict missing required fields: {missing}"


class TestHybridRAGProviderFieldValues:
    """Verify field values are correctly mapped from RetrievedChunk."""

    def test_knowledge_class_populated_for_knowledge_chunk(self):
        HybridRAGProvider = _import_adapter()
        chunk = _FakeChunk(
            chunk_id="k2", ticket_id=None, sop_id="so_99",
            chunk_type="FAQ_ANSWER", content="FAQ content",
            similarity=0.70, boosted_score=0.78,
            source_table="rag_knowledge_chunks",
            knowledge_class="TROUBLESHOOTING", quality_score=0.75,
        )
        retriever = _make_retriever([chunk])
        provider = HybridRAGProvider(retriever=retriever)

        result = provider.retrieve("some query")
        c = result["chunks"][0]

        assert c["knowledge_class"] == "TROUBLESHOOTING"
        assert c["quality_score"] == 0.75
        assert c["sop_id"] == "so_99"
        assert c["ticket_id"] is None

    def test_knowledge_class_none_for_ticket_chunk(self):
        HybridRAGProvider = _import_adapter()
        chunk = _FakeChunk(
            chunk_id="t1", ticket_id="T-001", sop_id=None,
            chunk_type="RESOLUTION_RCA", content="Resolution text",
            similarity=0.80, boosted_score=0.80,
            source_table="rag_ticket_chunks",
            knowledge_class=None, quality_score=None,
        )
        retriever = _make_retriever([chunk])
        provider = HybridRAGProvider(retriever=retriever)

        result = provider.retrieve("ticket query")
        c = result["chunks"][0]

        assert c["knowledge_class"] is None
        assert c["quality_score"] is None
        assert c["ticket_id"] == "T-001"
        assert c["sop_id"] is None

    def test_similarity_distinct_from_boosted_score(self):
        """similarity and score must be separate — boosted_score != similarity for knowledge chunks."""
        HybridRAGProvider = _import_adapter()
        chunk = _FakeChunk(
            chunk_id="k3", ticket_id=None, sop_id="so_55",
            chunk_type="FAQ_ANSWER", content="FAQ",
            similarity=0.60, boosted_score=0.73,   # 0.60 + 0.08 + 0.05*1.0
            source_table="rag_knowledge_chunks",
            knowledge_class="FAQ", quality_score=1.0,
        )
        retriever = _make_retriever([chunk])
        provider = HybridRAGProvider(retriever=retriever)

        result = provider.retrieve("q")
        c = result["chunks"][0]

        assert c["similarity"] == 0.60,         f"Expected similarity=0.60 got {c['similarity']}"
        assert c["score"] == 0.73,               f"Expected score=0.73 got {c['score']}"
        assert c["similarity"] != c["score"],    "similarity and score must differ for boosted chunks"

    def test_automation_label_preserved(self):
        HybridRAGProvider = _import_adapter()
        chunk = _FakeChunk(
            chunk_id="t2", ticket_id="T-500", sop_id=None,
            chunk_type="RESOLUTION_RCA", content="Escalate to L2",
            similarity=0.55, boosted_score=0.55,
            source_table="rag_ticket_chunks",
            automation_label="ESCALATION", query_type="ACCOUNT_FREEZE",
        )
        retriever = _make_retriever([chunk])
        provider = HybridRAGProvider(retriever=retriever)

        result = provider.retrieve("account frozen")
        c = result["chunks"][0]

        assert c["automation_label"] == "ESCALATION"
        assert c["query_type"] == "ACCOUNT_FREEZE"

    def test_has_rca_and_has_sop_flags(self):
        HybridRAGProvider = _import_adapter()
        chunk = _FakeChunk(
            chunk_id="r1", ticket_id="T-200", sop_id=None,
            chunk_type="RESOLUTION_RCA", content="RCA content",
            similarity=0.75, boosted_score=0.75,
            source_table="rag_ticket_chunks",
            has_rca=True, has_sop=False,
        )
        retriever = _make_retriever([chunk])
        provider = HybridRAGProvider(retriever=retriever)

        result = provider.retrieve("rca lookup")
        c = result["chunks"][0]

        assert c["has_rca"] is True
        assert c["has_sop"] is False


class TestHybridRAGProviderErrorHandling:
    """Verify the adapter never raises and returns safe fallback."""

    def test_returns_empty_chunks_on_retriever_exception(self):
        HybridRAGProvider = _import_adapter()
        retriever = MagicMock()
        retriever.retrieve.side_effect = RuntimeError("DB connection failed")
        provider = HybridRAGProvider(retriever=retriever)

        result = provider.retrieve("any query")

        assert result == {"chunks": [], "confidence": 0.0}

    def test_confidence_is_zero_when_no_chunks(self):
        HybridRAGProvider = _import_adapter()
        retriever = _make_retriever([])   # empty result
        provider = HybridRAGProvider(retriever=retriever)

        result = provider.retrieve("query with no hits")

        assert result["confidence"] == 0.0
        assert result["chunks"] == []

    def test_confidence_is_max_boosted_score(self):
        HybridRAGProvider = _import_adapter()
        chunks = [
            _FakeChunk(
                chunk_id="x1", ticket_id=None, sop_id=None,
                chunk_type="FAQ_ANSWER", content="A",
                similarity=0.60, boosted_score=0.71,
                source_table="rag_knowledge_chunks",
            ),
            _FakeChunk(
                chunk_id="x2", ticket_id="T-9", sop_id=None,
                chunk_type="RESOLUTION_RCA", content="B",
                similarity=0.80, boosted_score=0.80,
                source_table="rag_ticket_chunks",
            ),
        ]
        retriever = _make_retriever(chunks)
        provider = HybridRAGProvider(retriever=retriever)

        result = provider.retrieve("multi-chunk query")

        assert result["confidence"] == 0.80   # max(0.71, 0.80)
        assert len(result["chunks"]) == 2


class TestRAGAdapterKnowledgeOrchestratorIntegration:
    """
    Verify that the adapter output is compatible with KnowledgeOrchestrator._query_rag().

    This is the critical integration point: the orchestrator builds RAGEvidence from
    the dict returned by HybridRAGProvider.retrieve(). All chunk dicts are stored
    verbatim in RAGEvidence.chunks — the downstream consumer must find
    knowledge_class, quality_score, source, etc. in each chunk dict.
    """

    def test_chunks_compatible_with_rag_evidence_construction(self):
        """
        RAGEvidence stores chunks as-is. Verify fields that ReasoningService reads
        are present in each chunk.
        """
        HybridRAGProvider = _import_adapter()
        chunk = _FakeChunk(
            chunk_id="ke1", ticket_id=None, sop_id="so_42",
            chunk_type="FAQ_ANSWER",
            content="How to handle OTP expiry: regenerate the session link.",
            similarity=0.69, boosted_score=0.77,
            source_table="rag_knowledge_chunks",
            knowledge_class="FAQ", quality_score=0.88,
        )
        retriever = _make_retriever([chunk])
        provider = HybridRAGProvider(retriever=retriever)

        raw = provider.retrieve("OTP expired")
        # Simulate what KnowledgeOrchestrator does:
        from case_engine.knowledge.unified_bundle import RAGEvidence
        evidence = RAGEvidence(
            evidence_id="e1",
            query="OTP expired",
            chunks=tuple(raw["chunks"]),
            source="hybrid_rag",
            retrieval_confidence=raw["confidence"],
            retrieved_at="2026-06-29T00:00:00+00:00",
            placeholder=False,
        )

        assert len(evidence.chunks) == 1
        c = evidence.chunks[0]
        # Fields the ReasoningService and assembler need:
        assert c["knowledge_class"] == "FAQ"
        assert c["quality_score"] == 0.88
        assert c["source"] == "rag_knowledge_chunks"
        assert c["content"] == "How to handle OTP expiry: regenerate the session link."
        assert c["chunk_type"] == "FAQ_ANSWER"
        assert c["has_rca"] is False
        assert c["has_sop"] is False

    def test_to_dict_roundtrip_preserves_chunks(self):
        """RAGEvidence.to_dict() must include chunks with all fields intact."""
        HybridRAGProvider = _import_adapter()
        chunk = _FakeChunk(
            chunk_id="r9", ticket_id="T-77", sop_id=None,
            chunk_type="RESOLUTION_RCA", content="Retry the session.",
            similarity=0.74, boosted_score=0.74,
            source_table="rag_ticket_chunks",
            has_rca=True,
        )
        retriever = _make_retriever([chunk])
        provider = HybridRAGProvider(retriever=retriever)

        raw = provider.retrieve("session retry")
        from case_engine.knowledge.unified_bundle import RAGEvidence
        evidence = RAGEvidence(
            evidence_id="e2",
            query="session retry",
            chunks=tuple(raw["chunks"]),
            source="hybrid_rag",
            retrieval_confidence=raw["confidence"],
            retrieved_at="2026-06-29T00:00:00+00:00",
            placeholder=False,
        )

        d = evidence.to_dict()
        assert isinstance(d["chunks"], list)
        assert len(d["chunks"]) == 1
        assert d["chunks"][0]["has_rca"] is True
        assert d["chunks"][0]["ticket_id"] == "T-77"
