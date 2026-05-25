"""
retrieval/hybrid_search.py
---------------------------
Main hybrid retrieval orchestrator.

Pipeline:
    1. Semantic search (pgvector cosine similarity)  ─┐
    2. Keyword search (PostgreSQL full-text search)   ─┤→ RRF Fusion → Rerank → HybridResult
    3. Reciprocal Rank Fusion                          ─┘

Design principles:
    - Each stage is independently testable
    - Every stage is timed and traced
    - Graceful degradation: keyword failures fall back silently
    - Output format is backward-compatible with existing run_query() consumers
    - Does NOT touch business logic (prompts, escalation, thresholds in chat.py)

Integration:
    This is called from app/query.py when hybrid mode is enabled.
    The existing run_query() signature is preserved exactly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from retrieval.config import RetrievalConfig
from retrieval.filters import build_metadata_filter_set
from retrieval.fusion import fused_to_row, run_rrf_fusion
from retrieval.keyword_search import run_keyword_search
from retrieval.models import FusedCandidate, HybridResult, RetrievalTrace
from retrieval.reranker import get_reranker
from retrieval.semantic_search import run_semantic_search

LOGGER = logging.getLogger(__name__)


def _compute_query_hash(query_text: str) -> str:
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()[:16]


def _normalize_source_type(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    return value.strip().lower() or "unknown"


def _group_by_source(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        source_type = _normalize_source_type((row.get("metadata") or {}).get("source_type"))
        grouped.setdefault(source_type, []).append(row)
    return grouped


def _save_trace(trace: RetrievalTrace, trace_dir: str = "./traces") -> None:
    """Persist trace to /traces directory as JSON. Non-fatal on failure."""
    import os

    try:
        os.makedirs(trace_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{trace.query_hash}_hybrid.json"
        filepath = os.path.join(trace_dir, filename)

        trace_dict = {
            "query": trace.query,
            "query_hash": trace.query_hash,
            "semantic_candidates_count": trace.semantic_candidates_count,
            "keyword_candidates_count": trace.keyword_candidates_count,
            "fused_candidates_count": trace.fused_candidates_count,
            "final_count": trace.final_count,
            "latencies_ms": {
                "embedding": trace.embedding_latency_ms,
                "semantic": trace.semantic_latency_ms,
                "keyword": trace.keyword_latency_ms,
                "fusion": trace.fusion_latency_ms,
                "rerank": trace.rerank_latency_ms,
                "total": trace.total_latency_ms,
            },
            "config_snapshot": trace.config_snapshot,
            "metadata_filters": trace.metadata_filters,
            "thresholds_applied": trace.thresholds_applied,
            "semantic_candidates": trace.semantic_candidates[:10],   # cap for file size
            "keyword_candidates": trace.keyword_candidates[:10],
            "fused_candidates": trace.fused_candidates[:10],
            "final_candidates": trace.final_candidates,
            "errors": trace.errors,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(trace_dict, f, indent=2, default=str)

        LOGGER.debug("Hybrid retrieval trace saved: %s", filepath)

    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Failed to save retrieval trace: %s", exc)


def run_hybrid_search(
    *,
    supabase_url: str,
    supabase_key: str,
    table_name: str,
    query_embedding: list[float],
    query_text: str,
    config: RetrievalConfig,
    metadata_filters: dict[str, Any] | None = None,
    match_threshold: float = 0.0,
    final_top_k: int | None = None,
    embedding_latency_ms: float = 0.0,
    trace_dir: str = "./traces",
) -> HybridResult:
    """
    Execute the full hybrid retrieval pipeline.

    Args:
        supabase_url/key: Supabase connection details
        table_name: Target documents table
        query_embedding: Pre-computed embedding vector
        query_text: Raw query string (for keyword search and reranking)
        config: RetrievalConfig with all tunable parameters
        metadata_filters: Filter dict from build_metadata_filter_set()
        match_threshold: Minimum similarity threshold (passed to semantic search)
        final_top_k: Override for number of final results
        embedding_latency_ms: Pre-measured embedding latency to include in trace
        trace_dir: Where to save trace files

    Returns:
        HybridResult with backward-compatible matches list
    """
    t_pipeline_start = time.perf_counter()
    query_hash = _compute_query_hash(query_text)
    effective_top_k = final_top_k if final_top_k is not None else config.final_top_k

    # Initialize trace
    trace = RetrievalTrace(
        query=query_text,
        query_hash=query_hash,
        embedding_latency_ms=embedding_latency_ms,
        config_snapshot=config.to_dict(),
        metadata_filters=metadata_filters or {},
    )

    # Lazy import supabase — keeps this module importable in test environments
    # where supabase-py is not installed.
    try:
        from supabase import create_client  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "supabase-py is required for hybrid_search. "
            "Install it with: pip install supabase"
        ) from exc

    # Create Supabase client
    client = create_client(supabase_url, supabase_key)

    # ── Stage 1: Semantic Search ─────────────────────────────────────────
    semantic_candidates, sem_latency = run_semantic_search(
        supabase_client=client,
        table_name=table_name,
        query_embedding=query_embedding,
        config=config,
        metadata_filters=metadata_filters,
        match_threshold=match_threshold,
        top_k=config.semantic_top_k,
    )
    trace.semantic_latency_ms = sem_latency
    trace.semantic_candidates_count = len(semantic_candidates)
    trace.semantic_candidates = [
        {"id": c.doc_id, "similarity": c.similarity, "rank": c.semantic_rank,
         "source_type": c.metadata.get("source_type", "?")}
        for c in semantic_candidates
    ]

    # ── Stage 2: Keyword Search ──────────────────────────────────────────
    keyword_candidates, kw_latency = [], 0.0
    if config.keyword_enabled:
        keyword_candidates, kw_latency = run_keyword_search(
            supabase_client=client,
            table_name=table_name,
            query_text=query_text,
            config=config,
            metadata_filters=metadata_filters,
            top_k=config.keyword_top_k,
        )
    trace.keyword_latency_ms = kw_latency
    trace.keyword_candidates_count = len(keyword_candidates)
    trace.keyword_candidates = [
        {"id": c.doc_id, "ts_rank": c.ts_rank, "rank": c.keyword_rank,
         "matched_terms": c.matched_terms[:5]}
        for c in keyword_candidates
    ]

    # ── Stage 3: RRF Fusion ──────────────────────────────────────────────
    fused_candidates, fusion_latency = run_rrf_fusion(
        semantic_candidates=semantic_candidates,
        keyword_candidates=keyword_candidates,
        config=config,
        query_text=query_text,
    )
    trace.fusion_latency_ms = fusion_latency
    trace.fused_candidates_count = len(fused_candidates)
    trace.fused_candidates = [
        {"id": c.doc_id, "rrf_score": c.rrf_score,
         "semantic_rank": c.semantic_rank, "keyword_rank": c.keyword_rank}
        for c in fused_candidates
    ]

    # ── Stage 4: Reranking ───────────────────────────────────────────────
    reranked: list[FusedCandidate] = fused_candidates
    rerank_latency = 0.0
    if config.rerank_enabled and fused_candidates:
        reranker = get_reranker(config)
        reranked, rerank_latency = reranker.rerank(
            query=query_text,
            candidates=fused_candidates,
            top_k=effective_top_k,
        )
    else:
        # Assign final ranks without reranking
        for idx, c in enumerate(reranked[:effective_top_k], start=1):
            c.final_rank = idx
        reranked = reranked[:effective_top_k]

    trace.rerank_latency_ms = rerank_latency

    # ── Stage 5: Convert to backward-compatible row format ───────────────
    top_rows = [fused_to_row(c) for c in reranked]
    trace.final_count = len(top_rows)
    trace.final_candidates = [
        {"id": r["id"], "rrf_score": r["rrf_score"],
         "rerank_score": r["rerank_score"], "final_rank": r["final_rank"]}
        for r in top_rows
    ]

    # ── Total latency ────────────────────────────────────────────────────
    trace.total_latency_ms = (time.perf_counter() - t_pipeline_start) * 1000

    # ── Save trace if enabled ────────────────────────────────────────────
    if config.trace_enabled:
        _save_trace(trace, trace_dir=trace_dir)

    # ── Build diagnostics (backward-compatible with existing run_query) ──
    grouped = _group_by_source(top_rows)
    best_similarity = float(top_rows[0].get("similarity", 0.0)) if top_rows else 0.0
    best_rerank = float(top_rows[0].get("rerank_score", 0.0)) if top_rows else 0.0
    insufficient = not top_rows or (
        best_similarity < config.min_similarity
        and best_rerank < config.min_rerank_score
    )

    # Determine which retrieval modes actually contributed results
    has_semantic = len(semantic_candidates) > 0
    has_keyword = len(keyword_candidates) > 0
    if has_semantic and has_keyword:
        retrieval_mode_used = "hybrid"
    elif has_semantic:
        retrieval_mode_used = "semantic_only"
    elif has_keyword:
        retrieval_mode_used = "keyword_only"
    else:
        retrieval_mode_used = "none"

    diagnostics = {
        # Existing fields (backward-compat)
        "candidate_count": config.semantic_top_k,
        "returned_count": len(top_rows),
        "returned_count_by_source_type": {st: len(rows) for st, rows in grouped.items()},
        "best_similarity": best_similarity,
        "best_rerank_score": best_rerank,
        # Hybrid retrieval fields
        "hybrid_mode": True,
        "retrieval_mode_used": retrieval_mode_used,
        "semantic_candidates": len(semantic_candidates),
        "keyword_candidates": len(keyword_candidates),
        "fused_candidates": len(fused_candidates),
        "reranker_used": "bm25",
        "reranked_candidate_count": len(reranked) if config.rerank_enabled else 0,
        "latency_ms": {
            "embedding": embedding_latency_ms,
            "semantic": sem_latency,
            "keyword": kw_latency,
            "fusion": fusion_latency,
            "rerank": rerank_latency,
            "total": trace.total_latency_ms,
        },
    }

    return HybridResult(
        matches=top_rows,
        matches_by_source_type=grouped,
        insufficient_context=insufficient,
        clarification=(
            "I do not have enough confident evidence. Try narrowing by source_type or date range."
            if insufficient else None
        ),
        diagnostics=diagnostics,
        trace=trace,
    )
