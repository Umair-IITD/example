"""
retrieval/semantic_search.py
-----------------------------
Isolated semantic vector search using pgvector via the existing VectorStore.

This module wraps the existing match_documents RPC — it does NOT rewrite it.
It provides:
  - Consistent SemanticCandidate output objects
  - Per-call timing instrumentation
  - Configurable top_k and threshold
  - Clean error surface for the hybrid pipeline to handle
"""
from __future__ import annotations

import time
import logging
from typing import Any

from retrieval.models import SemanticCandidate
from retrieval.config import RetrievalConfig
from retrieval.filters import apply_metadata_filters

LOGGER = logging.getLogger(__name__)


def run_semantic_search(
    *,
    supabase_client: Any,             # supabase.Client — typed loosely to avoid hard import
    table_name: str,
    query_embedding: list[float],
    config: RetrievalConfig,
    metadata_filters: dict[str, Any] | None = None,
    match_threshold: float | None = None,
    top_k: int | None = None,
) -> tuple[list[SemanticCandidate], float]:
    """
    Execute semantic vector search via the existing match_documents RPC.

    Returns:
        (candidates, latency_ms)

    The existing VectorStore.match_documents is NOT called here directly —
    we call the Supabase RPC ourselves so this module stays independently
    testable with a mock client.
    """
    t_start = time.perf_counter()
    effective_top_k = top_k if top_k is not None else config.semantic_top_k
    effective_threshold = match_threshold if match_threshold is not None else config.semantic_threshold

    candidates: list[SemanticCandidate] = []

    try:
        response = (
            supabase_client.rpc(
                "match_documents",
                {
                    "query_embedding": query_embedding,
                    "match_count": effective_top_k,
                    "match_threshold": effective_threshold,
                },
            )
            .execute()
        )
        rows: list[dict] = response.data or []

        # Apply metadata filters (same logic as existing VectorStore)
        if metadata_filters:
            rows = apply_metadata_filters(rows, metadata_filters)

        for rank, row in enumerate(rows, start=1):
            candidates.append(
                SemanticCandidate(
                    doc_id=str(row.get("id", "")),
                    content=str(row.get("content", "")),
                    metadata=row.get("metadata") or {},
                    similarity=float(row.get("similarity", 0.0)),
                    semantic_rank=rank,
                )
            )

    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("semantic_search failed: %s", exc)

    latency_ms = (time.perf_counter() - t_start) * 1000
    LOGGER.debug(
        "semantic_search returned %d candidates in %.1fms",
        len(candidates),
        latency_ms,
    )
    return candidates, latency_ms
