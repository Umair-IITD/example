"""
rag_engine/retrieval/hybrid_ticket_retriever.py

Hybrid retrieval for the B1/B2/B3 RAG tables (rag_ticket_chunks,
rag_sop_chunks, rag_knowledge_chunks).

Extends the pure-semantic TicketRetriever with a keyword FTS leg and
Reciprocal Rank Fusion, matching the architecture used for the legacy
`documents` table in retrieval/hybrid_search.py.

Pipeline:
  1. Embed query (via EmbeddingProvider)
  2. Semantic search: match_b1_ticket_chunks_v2 + match_b1_sop_chunks_v2 (split HNSW, merged in Python)
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
import threading
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

# ── Latency & early-exit controls ────────────────────────────────────────────
# Max wall-clock seconds to wait for the FTS thread before proceeding semantic-only.
_FTS_TIMEOUT_S: float = float(os.getenv("RETRIEVAL_FTS_TIMEOUT_S", "0.40"))

# Skip FTS entirely if semantic already has enough high-confidence candidates.
_EARLY_EXIT_SIM_THRESHOLD: float  = float(os.getenv("RETRIEVAL_EARLY_EXIT_SIM_THRESHOLD", "0.50"))
_EARLY_EXIT_MIN_CANDIDATES: int   = int(os.getenv("RETRIEVAL_EARLY_EXIT_MIN_CANDIDATES", "5"))

# ── Chunk-type score boosts (applied in Python after SQL retrieval) ───────────
# Positive = boost toward top of results; negative = push down.
# RESOLUTION_RCA carries the fix; QUERY_BODY the problem description.
# ISSUE_HEADER is a title-only chunk — useful for recall but not the answer.
_CHUNK_TYPE_BOOSTS: dict[str, float] = {
    "RESOLUTION_RCA": float(os.getenv("RETRIEVAL_BOOST_RESOLUTION_RCA", "0.15")),
    "QUERY_BODY":     float(os.getenv("RETRIEVAL_BOOST_QUERY_BODY",     "0.08")),
    # -0.06: enough to rank ISSUE_HEADER below substantive chunks at equal cosine similarity,
    # but not so aggressive that it eliminates the ticket-context signal entirely.
    "ISSUE_HEADER":   float(os.getenv("RETRIEVAL_BOOST_ISSUE_HEADER",  "-0.06")),
}

# Post-reranking cap: prevents ISSUE_HEADER dominance while allowing 1-3 headers
# to provide ticket context. Combined with the assembly-level cap (2 headers), the
# LLM context always has substantive chunk diversity.
_MAX_ISSUE_HEADERS: int = int(os.getenv("RETRIEVAL_MAX_ISSUE_HEADERS", "3"))

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
        # Fetch more candidates than top_k for fusion + reranking headroom.
        # Computed before FTS thread starts so both legs use the same fetch_count.
        fetch_count = request.top_k * 4

        # ── Step 1+3 parallel: FTS starts immediately; embedding runs in parallel ─
        # FTS (keyword search) has no dependency on the query embedding.
        # Starting it in a daemon thread lets it overlap with the OpenAI embedding
        # round-trip (~300ms), reducing total wall-clock latency by ~50-200ms.
        _fts_state: dict = {"rows": [], "latency_ms": 0.0, "done": False}

        def _run_fts() -> None:
            _t0 = time.perf_counter()
            try:
                _fts_state["rows"] = self._search_keyword(request, fetch_count)
            except Exception:  # noqa: BLE001
                _fts_state["rows"] = []
            _fts_state["latency_ms"] = (time.perf_counter() - _t0) * 1000
            _fts_state["done"] = True

        _fts_thread = threading.Thread(target=_run_fts, daemon=True, name="hybrid_fts")
        _fts_thread.start()

        # ── Step 1: Embed query (runs while FTS thread is active) ─────────────
        t_embed = time.perf_counter()
        query_embedding = self._embedder.embed_single(request.query_text)
        embedding_latency_ms = (time.perf_counter() - t_embed) * 1000

        # ── Step 2: Semantic search (needs embedding result) ──────────────────
        t_search = time.perf_counter()
        semantic_raw, v2_timing = self._search_with_fetch(request, query_embedding, fetch_count)
        semantic_latency_ms = (time.perf_counter() - t_search) * 1000

        LOGGER.debug(
            "HybridRetrieval timing: client=%s embed=%.0fms semantic=%.0fms "
            "(ticket=%.0fms sop=%.0fms merge=%.0fms)",
            request.client,
            embedding_latency_ms,
            semantic_latency_ms,
            v2_timing.get("ticket_latency_ms") or 0.0,
            v2_timing.get("sop_latency_ms") or 0.0,
            v2_timing.get("merge_latency_ms") or 0.0,
        )

        # ── Early-exit: skip FTS when semantic confidence is already high ──────
        _top_sim = max((r.get("similarity", 0.0) for r in semantic_raw), default=0.0)
        _skip_fts = (
            _top_sim >= _EARLY_EXIT_SIM_THRESHOLD
            and len(semantic_raw) >= _EARLY_EXIT_MIN_CANDIDATES
        )
        if _skip_fts:
            LOGGER.debug(
                "HybridRetrieval: semantic early-exit "
                "(top_sim=%.3f candidates=%d) — skipping FTS for client=%s",
                _top_sim, len(semantic_raw), request.client,
            )
            keyword_raw: list[dict] = []
            keyword_latency_ms: float = 0.0
        else:
            # ── Collect FTS results (short timeout — fall back to semantic-only) ─
            _fts_thread.join(timeout=_FTS_TIMEOUT_S)
            if not _fts_state["done"]:
                LOGGER.warning(
                    "HybridRetrieval: FTS timed out after %.0fms — "
                    "continuing semantic-only for client=%s",
                    _FTS_TIMEOUT_S * 1000, request.client,
                )
            keyword_raw = _fts_state["rows"]
            keyword_latency_ms = _fts_state["latency_ms"]

        LOGGER.debug(
            "HybridRetrieval timing: client=%s keyword=%.0fms candidates=%d skip_fts=%s",
            request.client, keyword_latency_ms, len(keyword_raw), _skip_fts,
        )

        # ── Step 4: RRF Fusion ─────────────────────────────────────────────────
        t_fusion = time.perf_counter()
        fused_raw, retrieval_mode, rrf_diagnostics = self._rrf_fuse(
            semantic_raw, keyword_raw, request
        )
        fusion_latency_ms = (time.perf_counter() - t_fusion) * 1000

        # ── Step 5: Convert + filter ───────────────────────────────────────────
        chunks = [self._row_to_chunk(r) for r in fused_raw]
        total_candidates = len(chunks)

        # Track SOP presence before reranking for instrumentation + guarantee
        sop_candidates_pre = [c for c in chunks if c.chunk_type == "SOP_STEPS"]

        if request.chunk_types:
            allowed = set(request.chunk_types)
            chunks = [c for c in chunks if c.chunk_type in allowed]

        # ── Step 6: Reranking ──────────────────────────────────────────────────
        chunks = self._reranker.rerank(
            query=request.query_text,
            chunks=chunks,
            top_k=request.top_k,
        )

        # ── Step 7: Minimum SOP guarantee ─────────────────────────────────────
        # If SOP chunks reached the candidate pool but none survived reranking/top_k
        # selection, inject the highest-ranked SOP chunk. SOPs are hand-authored
        # authority sources; we must not silently discard them.
        if sop_candidates_pre and not any(c.chunk_type == "SOP_STEPS" for c in chunks):
            top_sop = sop_candidates_pre[0]
            if chunks:
                chunks[-1] = top_sop   # replace weakest non-SOP rather than growing list
            else:
                chunks.append(top_sop)
            LOGGER.info(
                "HybridRetrieval: SOP guarantee applied — promoted '%s' chunk_type=SOP_STEPS "
                "for client=%s (0 SOPs survived reranking from %d candidates)",
                top_sop.chunk_id[:8], request.client, len(sop_candidates_pre),
            )

        # ── Step 8: ISSUE_HEADER cap ───────────────────────────────────────────
        # ISSUE_HEADER chunks are subject-line-only metadata; they score high on
        # cosine similarity but carry no resolution content. Cap them so substantive
        # chunks (RESOLUTION_RCA, QUERY_BODY) dominate the top-k result set.
        _ih_seen = 0
        _capped: list[RetrievedChunk] = []
        for c in chunks:
            if c.chunk_type == "ISSUE_HEADER":
                if _ih_seen < _MAX_ISSUE_HEADERS:
                    _capped.append(c)
                    _ih_seen += 1
            else:
                _capped.append(c)
        if len(_capped) < len(chunks):
            LOGGER.info(
                "HybridRetrieval: ISSUE_HEADER cap applied — dropped %d headers "
                "(max=%d) for client=%s",
                len(chunks) - len(_capped), _MAX_ISSUE_HEADERS, request.client,
            )
        chunks = _capped

        total_latency_ms = (time.perf_counter() - t_start) * 1000
        used_fallback = any(r.get("_fallback") for r in semantic_raw)
        v2_hnsw_used = any(r.get("_v2_used") for r in semantic_raw)

        # Chunk-type distribution in final results
        chunk_type_dist: dict[str, int] = {}
        for c in chunks:
            chunk_type_dist[c.chunk_type] = chunk_type_dist.get(c.chunk_type, 0) + 1

        sop_in_final = chunk_type_dist.get("SOP_STEPS", 0)
        LOGGER.info(
            "HybridRetrieval: client=%s query_hash=%s sem=%d kw=%d fused=%d "
            "sop_candidates=%d sop_final=%d returned=%d mode=%s fallback=%s v2=%s "
            "ticket=%.1fms sop=%.1fms total=%.1fms",
            request.client, query_hash[:8],
            len(semantic_raw), len(keyword_raw), len(fused_raw),
            len(sop_candidates_pre), sop_in_final,
            len(chunks), retrieval_mode, used_fallback, v2_hnsw_used,
            v2_timing.get("ticket_latency_ms") or 0.0,
            v2_timing.get("sop_latency_ms") or 0.0,
            total_latency_ms,
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
                "ticket_retrieval_latency_ms": v2_timing.get("ticket_latency_ms"),
                "sop_retrieval_latency_ms": v2_timing.get("sop_latency_ms"),
                "semantic_parallel_wall_ms": v2_timing.get("parallel_wall_ms"),
                "merge_rerank_latency_ms": round(
                    (v2_timing.get("merge_latency_ms") or 0.0) + fusion_latency_ms, 1
                ),
                "keyword_latency_ms": round(keyword_latency_ms, 1),
                "fts_skipped_early_exit": _skip_fts,
                "fusion_latency_ms": round(fusion_latency_ms, 1),
                "threshold": request.similarity_threshold,
                "top_k": request.top_k,
                "index_version": request.index_version,
                "used_fallback": used_fallback,
                "v2_hnsw_used": v2_hnsw_used,
                "retrieval_mode": retrieval_mode,
                "semantic_candidates": len(semantic_raw),
                "keyword_candidates": len(keyword_raw),
                "fused_candidates": len(fused_raw),
                # SOP quality instrumentation
                "sop_candidates": len(sop_candidates_pre),
                "sop_in_final": sop_in_final,
                "chunk_type_distribution": chunk_type_dist,
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
    ) -> tuple[list[dict], dict]:
        """Semantic search: split HNSW v2 RPCs (ticket + SOP independently), falls back to match_all_b1_sources on RPC failure."""
        rows, v2_ok, timing = self._search_v2(request, embedding, fetch_count)
        if v2_ok:
            return rows, timing

        LOGGER.warning(
            "HybridTicketRetriever: HNSW v2 RPCs unavailable — "
            "falling back to match_all_b1_sources (apply B1_010 migration to activate v2)"
        )
        # Fallback: match_all_b1_sources (UNION ALL, threshold in SQL)
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
            return response.data or [], {}
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "HybridTicketRetriever: match_all_b1_sources failed: %s. Falling back.", exc
            )
            return self._fallback_search(request, embedding), {}

    def _search_v2(
        self,
        request: RetrievalRequest,
        embedding: list[float],
        fetch_count: int,
    ) -> tuple[list[dict], bool, dict]:
        """
        Call match_b1_ticket_chunks_v2 and match_b1_sop_chunks_v2 independently,
        merge in Python, apply similarity_threshold in Python.

        Returns (rows, True, timing) on success, ([], False, {}) on any RPC failure.
        timing keys: ticket_latency_ms, sop_latency_ms, merge_latency_ms.
        """
        # Run ticket and SOP RPCs concurrently — each is an independent Supabase round-trip.
        _ticket: dict = {"rows": [], "latency_ms": 0.0, "exc": None}
        _sop:    dict = {"rows": [], "latency_ms": 0.0, "exc": None}

        def _run_ticket() -> None:
            t0 = time.perf_counter()
            try:
                _ticket["rows"] = self._search_ticket_chunks_v2(request, embedding, fetch_count)
            except Exception as exc:  # noqa: BLE001
                _ticket["exc"] = exc
            _ticket["latency_ms"] = (time.perf_counter() - t0) * 1000

        def _run_sop() -> None:
            t0 = time.perf_counter()
            try:
                _sop["rows"] = self._search_sop_chunks_v2(request, embedding, fetch_count)
            except Exception as exc:  # noqa: BLE001
                _sop["exc"] = exc
            _sop["latency_ms"] = (time.perf_counter() - t0) * 1000

        t_parallel = time.perf_counter()
        _t_thread = threading.Thread(target=_run_ticket, daemon=True, name="v2_ticket")
        _s_thread = threading.Thread(target=_run_sop,    daemon=True, name="v2_sop")
        _t_thread.start()
        _s_thread.start()
        _t_thread.join(timeout=10.0)
        _s_thread.join(timeout=10.0)

        if _ticket["exc"] or _sop["exc"]:
            exc = _ticket["exc"] or _sop["exc"]
            LOGGER.warning(
                "HybridTicketRetriever: v2 RPC failed — %s: %s", type(exc).__name__, exc
            )
            return [], False, {}

        ticket_rows = _ticket["rows"]
        sop_rows    = _sop["rows"]
        ticket_latency_ms = _ticket["latency_ms"]
        sop_latency_ms    = _sop["latency_ms"]
        # Wall-clock time for both RPCs together (concurrent execution)
        parallel_wall_ms  = (time.perf_counter() - t_parallel) * 1000

        t_merge = time.perf_counter()
        all_rows = ticket_rows + sop_rows
        # Threshold applied here (v2 SQL does not filter by similarity_threshold)
        filtered = [
            r for r in all_rows
            if r.get("similarity", 0.0) >= request.similarity_threshold
        ]

        # Apply chunk-type boosts so RESOLUTION_RCA and QUERY_BODY rank above
        # ISSUE_HEADER chunks with the same cosine similarity.
        for row in filtered:
            boost = _CHUNK_TYPE_BOOSTS.get(row.get("chunk_type", ""), 0.0)
            if boost != 0.0:
                row["boosted_score"] = min(
                    1.0, max(0.0, row.get("boosted_score", 0.0) + boost)
                )

        # Sort by boosted_score DESC — mirrors match_all_b1_sources ORDER BY
        filtered.sort(key=lambda r: r.get("boosted_score", 0.0), reverse=True)
        result = filtered[:fetch_count]
        merge_latency_ms = (time.perf_counter() - t_merge) * 1000

        for row in result:
            row["_v2_used"] = True

        timing = {
            "ticket_latency_ms": round(ticket_latency_ms, 1),
            "sop_latency_ms": round(sop_latency_ms, 1),
            "parallel_wall_ms": round(parallel_wall_ms, 1),
            "merge_latency_ms": round(merge_latency_ms, 1),
        }
        LOGGER.debug(
            "HybridRetriever v2: client=%s ticket=%d (%.0fms) sop=%d (%.0fms) "
            "wall=%.0fms threshold_pass=%d merge=%.0fms",
            request.client,
            len(ticket_rows), ticket_latency_ms,
            len(sop_rows), sop_latency_ms,
            parallel_wall_ms,
            len(result), merge_latency_ms,
        )
        return result, True, timing

    def _search_ticket_chunks_v2(
        self,
        request: RetrievalRequest,
        embedding: list[float],
        fetch_count: int,
    ) -> list[dict]:
        """SPRINT0_FIX_HNSW: HNSW-compatible ticket retrieval (B1_010); no threshold in SQL."""
        response = self._client.rpc(
            "match_b1_ticket_chunks_v2",
            {
                "p_query_embedding": embedding,
                "p_client": request.client,
                "p_match_count": fetch_count,
                "p_index_version": request.index_version,
            }
        ).execute()
        return response.data or []

    def _search_sop_chunks_v2(
        self,
        request: RetrievalRequest,
        embedding: list[float],
        fetch_count: int,
    ) -> list[dict]:
        """SPRINT0_FIX_HNSW: HNSW-compatible SOP retrieval (B1_010); SOP boost in SQL, no threshold."""
        response = self._client.rpc(
            "match_b1_sop_chunks_v2",
            {
                "p_query_embedding": embedding,
                "p_client": request.client,
                "p_match_count": fetch_count,
                "p_index_version": request.index_version,
            }
        ).execute()
        return response.data or []

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
