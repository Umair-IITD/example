"""
rag_engine/retrieval/ticket_retriever.py

Tenant-isolated vector retrieval from rag_ticket_chunks and rag_sop_chunks.

This is the Phase B1 retrieval API used by the chat/generation layer (Phase B2).

Key behaviors:
  1. client (tenant) is a REQUIRED parameter — raises ValueError if omitted
  2. ESCALATION chunks are excluded by default from auto-reply path
  3. SOP chunks are fetched in parallel and ranked with +0.15 similarity boost
  4. All results include full metadata for context assembly
  5. Future reranking plugs in via RerankingHook
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from rag_engine.embedding.base import EmbeddingProvider
from rag_engine.retrieval.reranking_hook import RerankingHook, NullReranker

LOGGER = logging.getLogger(__name__)


@dataclass
class RetrievalRequest:
    """Input specification for a retrieval call."""
    query_text: str
    client: str                         # REQUIRED: tenant slug (unity_bank, rbl_bank, etc.)
    top_k: int = 10
    similarity_threshold: float = 0.27
    include_sop: bool = True
    exclude_escalation: bool = True
    chunk_types: Optional[list[str]] = None     # None = all chunk types
    query_type_hint: Optional[str] = None       # Boost same-category results
    index_version: str = "v1"


@dataclass
class RetrievedChunk:
    """A single chunk returned from retrieval."""
    chunk_id: str
    ticket_id: Optional[str]
    sop_id: Optional[str]
    chunk_type: str
    content: str
    similarity: float
    boosted_score: float
    source_table: str                           # rag_ticket_chunks | rag_sop_chunks | rag_knowledge_chunks
    has_rca: bool = False
    has_sop: bool = False
    automation_label: Optional[str] = None
    query_type: Optional[str] = None
    extra_metadata: dict = field(default_factory=dict)
    # Phase B3: knowledge chunk fields (None for ticket/SOP chunks)
    knowledge_class: Optional[str] = None
    quality_score: Optional[float] = None


@dataclass
class RetrievalResponse:
    """Full retrieval result including trace for observability."""
    chunks: list[RetrievedChunk]
    total_candidates: int
    semantic_latency_ms: float
    total_latency_ms: float
    client: str
    query_hash: str
    retrieval_metadata: dict = field(default_factory=dict)

    @property
    def has_sop_context(self) -> bool:
        return any(c.source_table == "rag_sop_chunks" for c in self.chunks)

    @property
    def has_rca_context(self) -> bool:
        return any(c.has_rca for c in self.chunks)

    @property
    def has_knowledge_context(self) -> bool:
        """True if any knowledge chunk (Phase B3) is present in results."""
        return any(c.source_table == "rag_knowledge_chunks" for c in self.chunks)

    def top_chunk_ids(self) -> list[str]:
        return [c.chunk_id for c in self.chunks]

    def to_context_dict_list(self) -> list[dict]:
        """Serializable form for logging and context assembly."""
        return [
            {
                "chunk_id": c.chunk_id,
                "ticket_id": c.ticket_id,
                "sop_id": c.sop_id,
                "chunk_type": c.chunk_type,
                "similarity": round(c.similarity, 4),
                "has_rca": c.has_rca,
                "has_sop": c.has_sop,
            }
            for c in self.chunks
        ]


class TicketRetriever:
    """
    Main retrieval API for Phase B2.
    Uses the Supabase RPC functions defined in B1_005_rpc_functions.sql.
    """

    def __init__(
        self,
        supabase_client: Any,
        embedding_provider: EmbeddingProvider,
        ticket_chunks_table: str = "rag_ticket_chunks",
        sop_chunks_table: str = "rag_sop_chunks",
        reranker: Optional[RerankingHook] = None,
    ) -> None:
        self._client = supabase_client
        self._embedder = embedding_provider
        self._ticket_table = ticket_chunks_table
        self._sop_table = sop_chunks_table
        self._reranker = reranker or NullReranker()

    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        """
        Execute retrieval for a support query.

        Steps:
          1. Embed the query
          2. Call match_all_b1_sources RPC (unified ticket + SOP search)
          3. Apply reranker (NullReranker by default → no-op)
          4. Return top-k results with full metadata
        """
        if not request.client:
            raise ValueError(
                "client (tenant) is required for all retrieval calls. "
                "This is a security requirement — cross-tenant retrieval is not allowed."
            )

        t_start = time.perf_counter()
        query_hash = self._hash_query(request.query_text)

        # ── Step 1: Embed query ─────────────────────────────────────────────
        query_embedding = self._embedder.embed_single(request.query_text)
        embedding_latency_ms = (time.perf_counter() - t_start) * 1000

        # ── Step 2: Vector search ───────────────────────────────────────────
        t_search = time.perf_counter()
        raw_results = self._search(request, query_embedding)
        semantic_latency_ms = (time.perf_counter() - t_search) * 1000

        # ── Step 3: Convert to RetrievedChunk objects ───────────────────────
        chunks = [self._row_to_chunk(r) for r in raw_results]
        total_candidates = len(chunks)

        # ── Step 3b: chunk_type filter ──────────────────────────────────────
        # match_all_b1_sources returns all chunk types; filter here when requested.
        # total_candidates above reflects the pre-filter RPC count for observability.
        if request.chunk_types:
            allowed = set(request.chunk_types)
            chunks = [c for c in chunks if c.chunk_type in allowed]

        # ── Step 4: Reranking (NullReranker by default) ─────────────────────
        chunks = self._reranker.rerank(
            query=request.query_text,
            chunks=chunks,
            top_k=request.top_k,
        )

        total_latency_ms = (time.perf_counter() - t_start) * 1000

        # Detect if fallback was used (raw_results carries _fallback=True marker)
        used_fallback = any(r.get("_fallback") for r in raw_results)

        LOGGER.debug(
            "Retrieval: client=%s query_hash=%s candidates=%d returned=%d fallback=%s latency=%.1fms",
            request.client, query_hash[:8], total_candidates, len(chunks), used_fallback, total_latency_ms
        )

        return RetrievalResponse(
            chunks=chunks,
            total_candidates=total_candidates,
            semantic_latency_ms=semantic_latency_ms,
            total_latency_ms=total_latency_ms,
            client=request.client,
            query_hash=query_hash,
            retrieval_metadata={
                "embedding_latency_ms": round(embedding_latency_ms, 1),
                "threshold": request.similarity_threshold,
                "top_k": request.top_k,
                "index_version": request.index_version,
                "used_fallback": used_fallback,
                "retrieval_mode": "fallback_non_semantic" if used_fallback else "semantic_rpc",
            },
        )

    def retrieve_query_body_only(
        self,
        query_text: str,
        client: str,
        top_k: int = 5,
        threshold: float = 0.27,
        index_version: str = "v1",
    ) -> RetrievalResponse:
        """
        Retrieve only QUERY_BODY chunks — finds the most similar past customer complaints.
        Used when you want to surface similar cases, not their resolutions.
        """
        return self.retrieve(
            RetrievalRequest(
                query_text=query_text,
                client=client,
                top_k=top_k,
                similarity_threshold=threshold,
                include_sop=False,
                chunk_types=["QUERY_BODY"],
                index_version=index_version,
            )
        )

    def retrieve_resolution_for_ticket(
        self,
        ticket_id: str,
        client: str,
        index_version: str = "v1",
    ) -> list[dict]:
        """
        Fetch the RESOLUTION_RCA chunks for a specific known ticket.
        Used in the feedback loop to retrieve existing resolutions.
        """
        try:
            response = (
                self._client.table(self._ticket_table)
                .select("id, ticket_id, chunk_type, content, has_rca, extra_metadata")
                .eq("ticket_id", ticket_id)
                .eq("client", client)
                .eq("chunk_type", "RESOLUTION_RCA")
                .eq("index_version", index_version)
                .execute()
            )
            return response.data or []
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to retrieve resolution for ticket %s: %s", ticket_id, exc)
            return []

    # ── Private ────────────────────────────────────────────────────────────────

    def _search(self, request: RetrievalRequest, embedding: list[float]) -> list[dict]:
        """Call the unified match_all_b1_sources RPC."""
        try:
            response = self._client.rpc(
                "match_all_b1_sources",
                {
                    "p_query_embedding": embedding,
                    "p_client": request.client,
                    "p_match_count": request.top_k * 3,    # Over-fetch for reranking
                    "p_match_threshold": request.similarity_threshold,
                    "p_index_version": request.index_version,
                }
            ).execute()
            return response.data or []
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("RPC match_all_b1_sources failed: %s. Falling back to table scan.", exc)
            return self._fallback_search(request, embedding)

    def _fallback_search(self, request: RetrievalRequest, embedding: list[float]) -> list[dict]:
        """
        Direct table query fallback when the match_all_b1_sources RPC is unavailable.

        SAFETY CONSTRAINTS:
          - Returns ONLY RESOLUTION_RCA chunks with has_rca=True (never raw customer queries)
          - Hard-capped at top_k * 2 rows (never a full-table scan)
          - Injects similarity=0.01 so downstream confidence logic stays in "low" territory
          - Sets _fallback=True so callers and response metadata can surface this fact
          - Results are NOT ranked by semantic relevance — do not treat as authoritative

        This fallback exists as a graceful degradation path only.
        Restore the match_all_b1_sources RPC to re-enable proper retrieval.
        """
        # Hard cap: never scan more than this many rows even under fallback
        _FALLBACK_SCAN_LIMIT = min(request.top_k * 2, 20)

        LOGGER.warning(
            "FALLBACK_RETRIEVAL activated: match_all_b1_sources RPC unavailable. "
            "client=%s top_k=%d scan_limit=%d. "
            "Results are NOT ranked by semantic relevance — confidence will be LOW. "
            "Restore the RPC to return to production-quality retrieval.",
            request.client,
            request.top_k,
            _FALLBACK_SCAN_LIMIT,
        )
        try:
            response = (
                self._client.table(self._ticket_table)
                .select(
                    "id, ticket_id, chunk_type, content, automation_label, "
                    "query_type, has_rca, has_sop, extra_metadata"
                )
                .eq("client", request.client)
                .eq("index_version", request.index_version)
                .neq("automation_label", "ESCALATION")
                .eq("chunk_type", "RESOLUTION_RCA")
                .not_.is_("has_rca", "null")
                .eq("has_rca", True)
                .limit(_FALLBACK_SCAN_LIMIT)
                .execute()
            )
            rows = response.data or []
            for row in rows:
                # Near-zero similarity ensures downstream confidence scoring stays "low"
                row.setdefault("similarity", 0.01)
                row.setdefault("boosted_score", 0.01)
                row.setdefault("source_table", self._ticket_table)
                row["_fallback"] = True
                row["_fallback_reason"] = "rpc_unavailable"
            LOGGER.warning(
                "FALLBACK_RETRIEVAL: returned %d rows for client=%s",
                len(rows),
                request.client,
            )
            return rows
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "FALLBACK_RETRIEVAL also failed: client=%s error=%s. "
                "Returning empty result — response will show insufficient_context=True.",
                request.client,
                exc,
            )
            return []

    @staticmethod
    def _row_to_chunk(row: dict) -> RetrievedChunk:
        source_table = str(row.get("source_table", "rag_ticket_chunks"))
        extra_metadata = row.get("extra_metadata") or {}

        # Phase B3: extract knowledge chunk fields from extra_metadata
        knowledge_class: Optional[str] = None
        quality_score: Optional[float] = None
        if source_table == "rag_knowledge_chunks":
            knowledge_class = extra_metadata.get("knowledge_class")
            raw_qs = extra_metadata.get("quality_score")
            if raw_qs is not None:
                try:
                    quality_score = float(raw_qs)
                except (TypeError, ValueError):
                    quality_score = None

        return RetrievedChunk(
            chunk_id=str(row.get("id", "")),
            ticket_id=row.get("ticket_id"),
            sop_id=row.get("sop_id"),
            chunk_type=str(row.get("chunk_type", "UNKNOWN")),
            content=str(row.get("content", "")),
            similarity=float(row.get("similarity", 0.0)),
            boosted_score=float(row.get("boosted_score", row.get("similarity", 0.0))),
            source_table=source_table,
            has_rca=bool(row.get("has_rca", False)),
            has_sop=bool(row.get("has_sop", False)),
            automation_label=row.get("automation_label"),
            query_type=row.get("query_type"),
            extra_metadata=extra_metadata,
            knowledge_class=knowledge_class,
            quality_score=quality_score,
        )

    @staticmethod
    def _hash_query(text: str) -> str:
        import hashlib
        return hashlib.md5(text.encode()).hexdigest()
