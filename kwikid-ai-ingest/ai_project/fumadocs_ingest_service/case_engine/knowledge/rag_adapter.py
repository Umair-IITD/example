"""
case_engine/knowledge/rag_adapter.py — Sprint 2.30.1

HybridRAGProvider: adapter that wraps rag_engine retrieval into the
KnowledgeOrchestrator rag_provider protocol.

KnowledgeOrchestrator._query_rag() expects:
    rag_provider.retrieve(query: str, topic: str) -> dict
    Return value: {"chunks": list[dict], "confidence": float}

TicketRetriever / HybridTicketRetriever requires:
    retrieve(request: RetrievalRequest) -> RetrievalResponse

This adapter bridges the two interfaces without modifying either side.

Injection (done in app/main.py lifespan after singletons are warmed):
    from case_engine.knowledge.rag_adapter import HybridRAGProvider
    rag_adapter = HybridRAGProvider(retriever=generator._retriever)
    knowledge_orchestrator._rag_provider = rag_adapter

Chunk dict contract (consumed by KnowledgeOrchestrator -> RAGEvidence.chunks):
    content         — chunk text (required)
    score           — boosted_score (similarity + type boost)
    source          — source_table identifier
    chunk_type      — ISSUE_HEADER | RESOLUTION_RCA | QUERY_BODY | SOP_PROCEDURE | FAQ_ANSWER
    chunk_id        — opaque identifier
    has_rca         — bool: chunk contains root-cause analysis
    has_sop         — bool: chunk originates from SOP table
    knowledge_class — str | None: Phase B3 knowledge classification (FAQ, TROUBLESHOOTING, etc.)
    quality_score   — float | None: Phase B3 quality gate score [0.0–1.0]
    ticket_id       — str | None: parent ticket id (ticket chunks only)
    sop_id          — str | None: parent SOP id (sop/knowledge chunks only)
    automation_label— str | None: ESCALATION | AUTO_REPLY | MANUAL_REVIEW
    query_type      — str | None: query category hint
    image_metadata  — list[dict]: OCR image metadata from parent article ([] for non-knowledge)
"""
from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)

_DEFAULT_TENANT = "unity"
_DEFAULT_TOP_K  = 8


class HybridRAGProvider:
    """
    Adapts TicketRetriever (or HybridTicketRetriever) as a rag_provider.

    Thread-safe: the wrapped retriever serialises its own concurrent calls.
    Never raises: errors produce an empty {"chunks": [], "confidence": 0.0} dict.
    """

    def __init__(
        self,
        retriever:      Any,
        top_k:          int = _DEFAULT_TOP_K,
        default_tenant: str = _DEFAULT_TENANT,
        index_version:  str = "v2",
    ) -> None:
        self._retriever      = retriever
        self._top_k          = top_k
        self._default_tenant = default_tenant
        self._index_version  = index_version

    def retrieve(self, query: str, topic: str = "") -> dict[str, Any]:
        """
        Run retrieval and return normalised chunk list.

        All fields from RetrievedChunk are included so downstream consumers
        (KnowledgeOrchestrator, ReasoningService, response assembler) have
        access to the full context without round-tripping back to the retriever.

        Never raises.
        """
        try:
            from rag_engine.retrieval.ticket_retriever import RetrievalRequest  # noqa: PLC0415
            request = RetrievalRequest(
                query_text=query,
                client=self._default_tenant,
                top_k=self._top_k,
                include_sop=True,
                exclude_escalation=True,
                index_version=self._index_version,
            )
            response = self._retriever.retrieve(request)
            chunks: list[dict[str, Any]] = [
                {
                    # Core content fields
                    "content":          c.content,
                    "score":            c.boosted_score,
                    "source":           c.source_table,
                    "chunk_type":       c.chunk_type,
                    "chunk_id":         c.chunk_id,
                    # Provenance flags
                    "has_rca":          c.has_rca,
                    "has_sop":          c.has_sop,
                    # Phase B3 knowledge fields (None for ticket/SOP chunks)
                    "knowledge_class":  c.knowledge_class,
                    "quality_score":    c.quality_score,
                    # Cross-table identifiers
                    "ticket_id":        c.ticket_id,
                    "sop_id":           c.sop_id,
                    # Routing / classification labels
                    "automation_label": c.automation_label,
                    "query_type":       c.query_type,
                    # Raw similarity score (before boost) — useful for reranking
                    "similarity":       c.similarity,
                    # Phase B3 Part 2: OCR image metadata ([] for non-knowledge chunks)
                    "image_metadata":   getattr(c, "image_metadata", []),
                }
                for c in response.chunks
            ]
            confidence = (
                max(c.boosted_score for c in response.chunks)
                if response.chunks else 0.0
            )
            LOGGER.info(
                "rag_adapter.retrieved query_prefix=%r topic=%r chunks=%d confidence=%.3f latency_ms=%.0f",
                query[:50],
                topic[:30],
                len(chunks),
                confidence,
                response.total_latency_ms,
            )
            return {"chunks": chunks, "confidence": confidence}
        except Exception as exc:
            LOGGER.warning(
                "rag_adapter.retrieve_failed query_prefix=%r error=%s — returning empty",
                query[:50],
                exc,
            )
            return {"chunks": [], "confidence": 0.0}
