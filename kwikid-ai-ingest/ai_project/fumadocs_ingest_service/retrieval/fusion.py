"""
retrieval/fusion.py

Reciprocal Rank Fusion (RRF) combining semantic and keyword search results.

RRF Formula:
    rrf_score(doc) = Σ 1 / (k + rank_i)

Where:
    k     = smoothing constant — prevents top-ranked docs from dominating
    rank_i = rank of document in result list i (1-based; 0 = not in that list → contributes 0)

Standard k=60 (Cormack et al. 2009) is the default.

Adaptive k (enabled when RETRIEVAL_ADAPTIVE_RRF_ENABLED=true):
    k is adjusted per-request based on retrieval quality signals:
      - Short queries (< 3 tokens): lower k (sharpen top-rank effect)
      - Long queries (> 10 tokens): higher k (more smoothing)
      - High overlap (semantic ∩ keyword > 50%): lower k (confident signal)
      - Low overlap (<10%): higher k (uncertain signal, smooth more)
      - Low density (few results total): higher k (avoid winner-takes-all)

    k range: [20, 100]. Default: 60.

All decisions are deterministic and exposed in diagnostics for tuning.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from retrieval.models import SemanticCandidate, KeywordCandidate, FusedCandidate
from retrieval.config import RetrievalConfig

LOGGER = logging.getLogger(__name__)

# ── Adaptive RRF configuration ────────────────────────────────────────────────

_ADAPTIVE_RRF_ENABLED = os.getenv("RETRIEVAL_ADAPTIVE_RRF_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
_RRF_K_MIN = int(os.getenv("RETRIEVAL_RRF_K_MIN", "20"))
_RRF_K_MAX = int(os.getenv("RETRIEVAL_RRF_K_MAX", "100"))


def _compute_adaptive_k(
    query_text: str,
    semantic_candidates: list[SemanticCandidate],
    keyword_candidates: list[KeywordCandidate],
    base_k: int,
) -> tuple[int, dict[str, Any]]:
    """
    Compute adaptive RRF k based on query and retrieval quality signals.

    Returns:
        (effective_k, diagnostics_dict)

    Algorithm:
      Start from base_k (typically 60).
      Adjust by additive delta based on signals:
        - Query length: short → -10 (sharpen), long → +10 (smooth)
        - Overlap ratio: high → -15, moderate → 0, low → +10
        - Result density: sparse → +10, dense → 0
      Clamp to [_RRF_K_MIN, _RRF_K_MAX].

    Rationale:
      Low k = small denominator = bigger score difference between rank 1 and rank 2.
      Use low k when we're confident (clear winner, high overlap).
      Use high k when uncertain (noisy signals, low overlap, sparse results).
    """
    import re
    tokens = re.findall(r"[a-z0-9]+", query_text.lower())
    query_len = len(tokens)

    sem_ids = {c.doc_id for c in semantic_candidates}
    kw_ids = {c.doc_id for c in keyword_candidates}
    union_size = len(sem_ids | kw_ids)
    overlap_size = len(sem_ids & kw_ids)
    overlap_ratio = overlap_size / max(1, union_size)

    delta = 0
    reasons: list[str] = []

    # Query length signal
    if query_len <= 2:
        delta -= 15
        reasons.append("very_short_query→-15")
    elif query_len <= 4:
        delta -= 10
        reasons.append("short_query→-10")
    elif query_len >= 12:
        delta += 10
        reasons.append("long_query→+10")

    # Overlap signal
    if overlap_ratio >= 0.5:
        delta -= 15
        reasons.append(f"high_overlap({overlap_ratio:.2f})→-15")
    elif overlap_ratio <= 0.1 and union_size > 5:
        delta += 10
        reasons.append(f"low_overlap({overlap_ratio:.2f})→+10")

    # Result density signal
    if union_size <= 3:
        delta += 10
        reasons.append("sparse_results→+10")

    effective_k = max(_RRF_K_MIN, min(_RRF_K_MAX, base_k + delta))
    diagnostics = {
        "adaptive_k": effective_k,
        "base_k": base_k,
        "delta": delta,
        "query_len_tokens": query_len,
        "overlap_ratio": round(overlap_ratio, 3),
        "union_size": union_size,
        "reasons": reasons,
    }
    return effective_k, diagnostics


def run_rrf_fusion(
    semantic_candidates: list[SemanticCandidate],
    keyword_candidates: list[KeywordCandidate],
    config: RetrievalConfig,
    query_text: str = "",
) -> tuple[list[FusedCandidate], float]:
    """
    Combine semantic and keyword candidates using Reciprocal Rank Fusion.

    Args:
        semantic_candidates: Ranked results from pgvector semantic search
        keyword_candidates:  Ranked results from PostgreSQL FTS keyword search
        config:              RetrievalConfig (contains rrf_k, fusion_top_k)
        query_text:          Raw query string (used for adaptive k computation)

    Returns:
        (fused_candidates sorted by rrf_score descending, latency_ms)
    """
    t_start = time.perf_counter()

    # ── Determine effective k ─────────────────────────────────────────────────
    adaptive_diagnostics: dict[str, Any] = {}
    if _ADAPTIVE_RRF_ENABLED and query_text:
        k, adaptive_diagnostics = _compute_adaptive_k(
            query_text, semantic_candidates, keyword_candidates, config.rrf_k
        )
    else:
        k = config.rrf_k

    # ── Build lookup maps ─────────────────────────────────────────────────────
    semantic_map: dict[str, SemanticCandidate] = {c.doc_id: c for c in semantic_candidates}
    keyword_map: dict[str, KeywordCandidate] = {c.doc_id: c for c in keyword_candidates}
    all_doc_ids: set[str] = set(semantic_map.keys()) | set(keyword_map.keys())

    # ── Compute RRF scores ────────────────────────────────────────────────────
    fused: list[FusedCandidate] = []
    for doc_id in all_doc_ids:
        sem = semantic_map.get(doc_id)
        kw = keyword_map.get(doc_id)

        sem_rank = sem.semantic_rank if sem else 0
        kw_rank = kw.keyword_rank if kw else 0

        # RRF: a document not in a result list contributes 0 (not infinity)
        rrf_from_semantic = (1.0 / (k + sem_rank)) if sem_rank > 0 else 0.0
        rrf_from_keyword = (1.0 / (k + kw_rank)) if kw_rank > 0 else 0.0
        rrf_score = rrf_from_semantic + rrf_from_keyword

        # Prefer semantic candidate as base (has embedding-quality content)
        base = sem or kw
        assert base is not None

        fused.append(
            FusedCandidate(
                doc_id=doc_id,
                content=base.content,
                metadata=base.metadata,
                semantic_rank=sem_rank,
                keyword_rank=kw_rank,
                semantic_score=sem.similarity if sem else 0.0,
                keyword_score=kw.ts_rank if kw else 0.0,
                rrf_score=rrf_score,
            )
        )

    # Sort descending by RRF score
    fused.sort(key=lambda c: c.rrf_score, reverse=True)

    # Apply fusion_top_k cap
    fused = fused[: config.fusion_top_k]

    latency_ms = (time.perf_counter() - t_start) * 1000

    LOGGER.debug(
        "rrf_fusion: %d sem + %d kw → %d fused (k=%d%s) in %.1fms",
        len(semantic_candidates),
        len(keyword_candidates),
        len(fused),
        k,
        f" adaptive" if _ADAPTIVE_RRF_ENABLED else "",
        latency_ms,
    )

    # Embed adaptive diagnostics on the FusedCandidates (pass-through via metadata)
    if adaptive_diagnostics:
        for c in fused:
            if not isinstance(c.metadata, dict):
                c.metadata = {}
            c.metadata.setdefault("_rrf_diagnostics", adaptive_diagnostics)

    return fused, latency_ms


def fused_to_row(candidate: FusedCandidate) -> dict[str, Any]:
    """
    Convert a FusedCandidate to the backward-compatible row dict used by
    existing consumers (app/query.py, API responses).
    """
    return {
        "id": candidate.doc_id,
        "content": candidate.content,
        "metadata": candidate.metadata,
        # Backward-compat: existing consumers read "similarity"
        "similarity": candidate.semantic_score,
        "vector_score": candidate.semantic_score,
        "rerank_score": candidate.rerank_score,
        # New hybrid fields
        "rrf_score": candidate.rrf_score,
        "semantic_rank": candidate.semantic_rank,
        "keyword_rank": candidate.keyword_rank,
        "final_rank": candidate.final_rank,
    }
