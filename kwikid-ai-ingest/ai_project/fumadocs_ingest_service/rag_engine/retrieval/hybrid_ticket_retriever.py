"""
rag_engine/retrieval/hybrid_ticket_retriever.py

Hybrid retrieval for the B1/B2/B3 RAG tables (rag_ticket_chunks,
rag_sop_chunks, rag_knowledge_chunks).

Extends the pure-semantic TicketRetriever with a keyword FTS leg and
Reciprocal Rank Fusion, matching the architecture used for the legacy
`documents` table in retrieval/hybrid_search.py.

Pipeline:
  1. Embed query (via EmbeddingProvider)
  2. Semantic search: match_all_b1_sources RPC (existing)
  3. Keyword search: search_b1_sources_fts RPC (NEW — requires B1_008 migration)
  4. RRF fusion (adaptive k when RETRIEVAL_ADAPTIVE_RRF_ENABLED=true)
  5. BM25 / CrossEncoder reranking
  6. Return RetrievalResponse

Design:
  - Inherits all tenant isolation, fallback safety, and observability from TicketRetriever
  - Keyword leg is fully optional — if the FTS RPC is unavailable, degrades gracefully
    to semantic-only (same behavior as the base TicketRetriever)
  - All retrieval modes are exposed in retrieval_metadata diagnostics
  - The B1/B2/B3 hybrid path is activated by setting:
      B1_HYBRID_RETRIEVAL_ENABLED=true
    The existing TicketRetriever is used when this is false (default).

SQL prerequisite:
  Run sql/b1_migrations/B1_007_fts_setup.sql and B1_008_fts_rpc.sql in Supabase
  before enabling B1_HYBRID_RETRIEVAL_ENABLED=true.

Usage:
  Instantiate HybridTicketRetriever the same way as TicketRetriever.
  The app/main.py factory selects the right class based on B1_HYBRID_RETRIEVAL_ENABLED.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from typing import Any, Optional

from rag_engine.embedding.base import EmbeddingProvider
from rag_engine.retrieval.reranking_hook import RerankingHook, NullReranker
from rag_engine.retrieval.ticket_retriever import (
    RetrievalRequest,
    RetrievalResponse,
    RetrievedChunk,
    TicketRetriever,
)

LOGGER = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")
_RRF_K_MIN = int(os.getenv("RETRIEVAL_RRF_K_MIN", "20"))
_RRF_K_MAX = int(os.getenv("RETRIEVAL_RRF_K_MAX", "100"))
_RRF_K_BASE = int(os.getenv("B1_RRF_K", "60"))
_ADAPTIVE_RRF = os.getenv("RETRIEVAL_ADAPTIVE_RRF_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _bm25_score_single(query: str, content: str, k1: float = 1.5) -> float:
    """Lightweight TF-only BM25 approximation for ranking keyword results."""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0
    content_lower = content.lower()
    score = 0.0
    for term in q_tokens:
        tf = content_lower.count(term)
        if tf:
            score += tf * (k1 + 1.0) / (tf + k1)
    return round(score, 6)


def _compute_adaptive_k_b1(
    query_text: str,
    sem_count: int,
    kw_count: int,
    overlap_count: int,
) -> int:
    """Adaptive RRF k for B1 tables — same logic as retrieval/fusion.py."""
    tokens = re.findall(r"[a-z0-9]+", query_text.lower())
    query_len = len(tokens)
    union = max(1, sem_count + kw_count - overlap_count)
    overlap_ratio = overlap_count / union

    delta = 0
    if query_len <= 2:
        delta -= 15
    elif query_len <= 4:
        delta -= 10
    elif query_len >= 12:
        delta += 10

    if overlap_ratio >= 0.5:
        delta -= 15
    elif overlap_ratio <= 0.1 and union > 5:
        delta += 10

    if union <= 3:
        delta += 10

    return max(_RRF_K_MIN, min(_RRF_K_MAX, _RRF_K_BASE + delta))


class HybridTicketRetriever(TicketRetriever):
    """
    Hybrid semantic + FTS retriever for B1/B2/B3 RAG tables.

    Extends TicketRetriever by adding a keyword FTS search leg (via the
    search_b1_sources_fts RPC) and RRF fusion. Falls back to semantic-only
    if the FTS RPC is unavailable.
    """

    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        if not request.client:
            raise ValueError(
                "client (tenant) is required for all retrieval calls. "
                "This is a security requirement — cross-tenant retrieval is not allowed."
            )

        t_start = time.perf_counter()
        query_hash = self._hash_query(request.query_text)

        # ── Step 1: Embed query ────────────────────────────────────────────────
        query_embedding = self._embedder.embed_single(request.query_text)
        embedding_latency_ms = (time.perf_counter() - t_start) * 1000

        # ── Step 2: Semantic search (match_all_b1_sources RPC) ────────────────
        t_search = time.perf_counter()
        # Fetch more candidates than top_k for fusion + reranking headroom
        fetch_count = request.top_k * 4
        semantic_raw = self._search_with_fetch(request, query_embedding, fetch_count)
        semantic_latency_ms = (time.perf_counter() - t_search) * 1000

        # ── Step 3: Keyword FTS search ─────────────────────────────────────────
        t_kw = time.perf_counter()
        keyword_raw = self._search_keyword(request, fetch_count)
        keyword_latency_ms = (time.perf_counter() - t_kw) * 1000

        # ── Step 4: RRF Fusion ─────────────────────────────────────────────────
        t_fusion = time.perf_counter()
        fused_raw, retrieval_mode, rrf_diagnostics = self._rrf_fuse(
            semantic_raw, keyword_raw, request
        )
        fusion_latency_ms = (time.perf_counter() - t_fusion) * 1000

        # ── Step 5: Convert + filter ───────────────────────────────────────────
        chunks = [self._row_to_chunk(r) for r in fused_raw]
        total_candidates = len(chunks)

        if request.chunk_types:
            allowed = set(request.chunk_types)
            chunks = [c for c in chunks if c.chunk_type in allowed]

        # ── Step 6: Reranking ──────────────────────────────────────────────────
        chunks = self._reranker.rerank(
            query=request.query_text,
            chunks=chunks,
            top_k=request.top_k,
        )

        total_latency_ms = (time.perf_counter() - t_start) * 1000
        used_fallback = any(r.get("_fallback") for r in semantic_raw)

        LOGGER.info(
            "HybridRetrieval: client=%s query_hash=%s sem=%d kw=%d fused=%d "
            "returned=%d mode=%s fallback=%s latency=%.1fms",
            request.client, query_hash[:8],
            len(semantic_raw), len(keyword_raw), len(fused_raw),
            len(chunks), retrieval_mode, used_fallback, total_latency_ms,
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
                "keyword_latency_ms": round(keyword_latency_ms, 1),
                "fusion_latency_ms": round(fusion_latency_ms, 1),
                "threshold": request.similarity_threshold,
                "top_k": request.top_k,
                "index_version": request.index_version,
                "used_fallback": used_fallback,
                "retrieval_mode": retrieval_mode,
                "semantic_candidates": len(semantic_raw),
                "keyword_candidates": len(keyword_raw),
                "fused_candidates": len(fused_raw),
                # Adaptive RRF diagnostics (always present, even in semantic-only mode)
                "selected_rrf_k": rrf_diagnostics["selected_rrf_k"],
                "overlap_count": rrf_diagnostics["overlap_count"],
                "overlap_ratio": rrf_diagnostics["overlap_ratio"],
                "retrieval_confidence": rrf_diagnostics["retrieval_confidence"],
            },
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _search_with_fetch(
        self,
        request: RetrievalRequest,
        embedding: list[float],
        fetch_count: int,
    ) -> list[dict]:
        """Run match_all_b1_sources with a larger fetch_count for fusion headroom."""
        try:
            response = self._client.rpc(
                "match_all_b1_sources",
                {
                    "p_query_embedding": embedding,
                    "p_client": request.client,
                    "p_match_count": fetch_count,
                    "p_match_threshold": request.similarity_threshold,
                    "p_index_version": request.index_version,
                }
            ).execute()
            return response.data or []
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "HybridTicketRetriever: match_all_b1_sources failed: %s. Falling back.", exc
            )
            return self._fallback_search(request, embedding)

    def _search_keyword(
        self,
        request: RetrievalRequest,
        fetch_count: int,
    ) -> list[dict]:
        """
        Call the search_b1_sources_fts RPC for keyword results.
        Returns empty list (gracefully) if the RPC doesn't exist yet.
        """
        try:
            response = self._client.rpc(
                "search_b1_sources_fts",
                {
                    "p_query_text": request.query_text,
                    "p_client": request.client,
                    "p_match_count": fetch_count,
                    "p_index_version": request.index_version,
                }
            ).execute()
            rows = response.data or []
            # Inject approximated ts_rank if not present
            for rank, row in enumerate(rows, start=1):
                if "ts_rank" not in row:
                    row["ts_rank"] = _bm25_score_single(request.query_text, row.get("content", ""))
                row.setdefault("_keyword_rank", rank)
            return rows
        except Exception as exc:  # noqa: BLE001
            # B1_008 migration not yet applied — degrade silently to semantic-only
            LOGGER.debug(
                "HybridTicketRetriever: search_b1_sources_fts unavailable (%s). "
                "Running semantic-only. Apply sql/b1_migrations/B1_008_fts_rpc.sql to enable.",
                type(exc).__name__,
            )
            return []

    def _rrf_fuse(
        self,
        semantic_raw: list[dict],
        keyword_raw: list[dict],
        request: RetrievalRequest,
    ) -> tuple[list[dict], str, dict]:
        """
        Fuse semantic and keyword results using RRF.

        Returns:
            (fused_list, retrieval_mode_string, rrf_diagnostics)
            rrf_diagnostics keys: selected_rrf_k, overlap_count, overlap_ratio,
                                  retrieval_confidence
        """
        _empty_diag: dict = {"selected_rrf_k": _RRF_K_BASE, "overlap_count": 0,
                             "overlap_ratio": 0.0, "retrieval_confidence": "low"}

        if not semantic_raw and not keyword_raw:
            return [], "none", _empty_diag

        if not keyword_raw:
            diag = {**_empty_diag, "retrieval_confidence": "medium" if len(semantic_raw) >= 3 else "low"}
            return semantic_raw, "semantic_only", diag

        if not semantic_raw:
            diag = {**_empty_diag, "retrieval_confidence": "low"}
            return keyword_raw, "keyword_only", diag

        # Build lookup by chunk ID
        sem_map: dict[str, dict] = {}
        for rank, row in enumerate(semantic_raw, start=1):
            row_id = str(row.get("id", ""))
            if row_id:
                sem_map[row_id] = {**row, "_sem_rank": rank}

        kw_map: dict[str, dict] = {}
        for rank, row in enumerate(keyword_raw, start=1):
            row_id = str(row.get("id", ""))
            if row_id:
                kw_map[row_id] = {**row, "_kw_rank": rank}

        all_ids = set(sem_map.keys()) | set(kw_map.keys())

        # Adaptive k
        overlap = len(set(sem_map.keys()) & set(kw_map.keys()))
        union = max(1, len(sem_map) + len(kw_map) - overlap)
        overlap_ratio = round(overlap / union, 4)

        if _ADAPTIVE_RRF:
            k = _compute_adaptive_k_b1(
                request.query_text,
                len(sem_map), len(kw_map), overlap,
            )
        else:
            k = _RRF_K_BASE

        fused: list[dict] = []
        for doc_id in all_ids:
            sem = sem_map.get(doc_id)
            kw = kw_map.get(doc_id)

            sem_rank = sem["_sem_rank"] if sem else 0
            kw_rank = kw["_kw_rank"] if kw else 0

            rrf = (1.0 / (k + sem_rank) if sem_rank else 0.0) + \
                  (1.0 / (k + kw_rank) if kw_rank else 0.0)

            base = sem or kw
            fused.append({
                **base,
                "_rrf_score": rrf,
                "_sem_rank": sem_rank,
                "_kw_rank": kw_rank,
            })

        fused.sort(key=lambda r: r["_rrf_score"], reverse=True)

        # BM25 rerank within fused set
        self._bm25_rerank_inplace(request.query_text, fused)
        fused.sort(key=lambda r: (r.get("_bm25_score", 0.0), r["_rrf_score"]), reverse=True)

        # Retrieval confidence: both legs agree on ≥30% of candidates → high
        if overlap_ratio >= 0.30 and len(fused) >= 3:
            retrieval_confidence = "high"
        elif overlap_ratio >= 0.10 or len(fused) >= 5:
            retrieval_confidence = "medium"
        else:
            retrieval_confidence = "low"

        rrf_diagnostics: dict = {
            "selected_rrf_k": k,
            "overlap_count": overlap,
            "overlap_ratio": overlap_ratio,
            "retrieval_confidence": retrieval_confidence,
        }
        return fused, "hybrid", rrf_diagnostics

    @staticmethod
    def _bm25_rerank_inplace(query: str, rows: list[dict]) -> None:
        """In-place BM25 scoring on fused rows for post-fusion reranking."""
        q_tokens = _tokenize(query)
        if not q_tokens or not rows:
            return
        n = len(rows)
        contents = [str(r.get("content", "")) for r in rows]
        doc_token_sets = [_tokenize(c) for c in contents]
        doc_lengths = [len(dt) for dt in doc_token_sets]
        avg_dl = sum(doc_lengths) / n if n else 1.0
        df = {t: sum(1 for dt in doc_token_sets if t in dt) for t in q_tokens}
        k1, b = 1.5, 0.75
        for row, content, dl in zip(rows, contents, doc_lengths):
            content_lower = content.lower()
            score = 0.0
            for term in q_tokens:
                tf = content_lower.count(term)
                if tf == 0:
                    continue
                idf = math.log((n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1.0)
                tf_norm = tf * (k1 + 1.0) / (tf + k1 * (1.0 - b + b * dl / max(1.0, avg_dl)))
                score += idf * tf_norm
            row["_bm25_score"] = round(score, 6)
