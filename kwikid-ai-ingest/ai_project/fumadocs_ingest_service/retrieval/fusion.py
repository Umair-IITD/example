"""
retrieval/fusion.py
--------------------
Reciprocal Rank Fusion (RRF) combining semantic and keyword search results.

RRF Formula:
    rrf_score(doc) = Σ 1 / (k + rank_i)

Where:
    k   = smoothing constant (default 60, prevents top-ranked docs dominating)
    rank_i = rank of document in result list i (1-based)

Key properties:
    - Robust to different score scales across retrieval methods
    - Documents not in a result set are treated as rank = Inf (score contribution = 0)
    - Preserves full transparency: semantic_rank, keyword_rank, rrf_score all retained
    - No external dependencies
"""
from __future__ import annotations

import time
import logging
from typing import Any

from retrieval.models import SemanticCandidate, KeywordCandidate, FusedCandidate
from retrieval.config import RetrievalConfig

LOGGER = logging.getLogger(__name__)


def run_rrf_fusion(
    semantic_candidates: list[SemanticCandidate],
    keyword_candidates: list[KeywordCandidate],
    config: RetrievalConfig,
) -> tuple[list[FusedCandidate], float]:
    """
    Combine semantic and keyword candidates using Reciprocal Rank Fusion.

    Returns:
        (fused_candidates sorted by rrf_score descending, latency_ms)
    """
    t_start = time.perf_counter()
    k = config.rrf_k

    # Index semantic results: doc_id → candidate
    semantic_map: dict[str, SemanticCandidate] = {c.doc_id: c for c in semantic_candidates}
    keyword_map: dict[str, KeywordCandidate] = {c.doc_id: c for c in keyword_candidates}

    # Union of all doc_ids
    all_doc_ids: set[str] = set(semantic_map.keys()) | set(keyword_map.keys())

    fused: list[FusedCandidate] = []
    for doc_id in all_doc_ids:
        sem = semantic_map.get(doc_id)
        kw = keyword_map.get(doc_id)

        # RRF contributions — 0 if not present in that result set
        sem_rank = sem.semantic_rank if sem else 0
        kw_rank = kw.keyword_rank if kw else 0

        rrf_from_semantic = (1.0 / (k + sem_rank)) if sem_rank > 0 else 0.0
        rrf_from_keyword = (1.0 / (k + kw_rank)) if kw_rank > 0 else 0.0
        rrf_score = rrf_from_semantic + rrf_from_keyword

        # Use the candidate that has the richest data (prefer semantic for content)
        base = sem or kw
        assert base is not None  # guaranteed since doc_id came from union

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

    # Sort by RRF score descending
    fused.sort(key=lambda c: c.rrf_score, reverse=True)

    # Apply fusion top_k cap
    fused = fused[: config.fusion_top_k]

    latency_ms = (time.perf_counter() - t_start) * 1000
    LOGGER.debug(
        "rrf_fusion: %d semantic + %d keyword → %d fused in %.1fms (k=%d)",
        len(semantic_candidates),
        len(keyword_candidates),
        len(fused),
        latency_ms,
        k,
    )
    return fused, latency_ms


def fused_to_row(candidate: FusedCandidate) -> dict[str, Any]:
    """
    Convert a FusedCandidate to the row dict format used by existing query.py
    and returned by API endpoints. Preserves full backward compatibility.
    """
    return {
        "id": candidate.doc_id,
        "content": candidate.content,
        "metadata": candidate.metadata,
        # Expose both score fields for compatibility with existing consumers
        "similarity": candidate.semantic_score,
        "vector_score": candidate.semantic_score,
        "rerank_score": candidate.rerank_score,
        # New hybrid-specific fields
        "rrf_score": candidate.rrf_score,
        "semantic_rank": candidate.semantic_rank,
        "keyword_rank": candidate.keyword_rank,
        "final_rank": candidate.final_rank,
    }
