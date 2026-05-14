"""
retrieval/keyword_search.py
----------------------------
Full-text keyword search using PostgreSQL ts_vector / ts_rank.

This is ADDITIVE — it runs alongside pgvector semantic search.
It specifically addresses the weaknesses of pure semantic retrieval:
  - Exact error code matches ("ERR-4021")
  - Acronym matches ("VKYC", "PAN", "OCR")
  - Technical ID matches (ticket IDs, API codes)
  - Partial lexical overlap

Implementation:
  Uses PostgreSQL's built-in full-text search via PostgREST text filter.
  Falls back gracefully if the FTS column / index is unavailable.

PostgreSQL setup required (run in Supabase SQL editor):
  -- Add generated tsvector column
  ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;

  -- Create GIN index for fast FTS
  CREATE INDEX IF NOT EXISTS documents_fts_idx ON documents USING gin(fts);
"""
from __future__ import annotations

import re
import time
import logging
from typing import Any

from retrieval.models import KeywordCandidate
from retrieval.config import RetrievalConfig
from retrieval.filters import apply_metadata_filters

LOGGER = logging.getLogger(__name__)

# Pattern to extract meaningful technical tokens (preserves acronyms, IDs, codes)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][\w\-]*[A-Za-z0-9]|[A-Za-z0-9]+")


def _build_tsquery(query_text: str) -> str:
    """
    Build a PostgreSQL tsquery from the user query.

    Strategy:
      1. Extract all tokens (preserving technical terms like VKYC, ERR-4021)
      2. Combine with | (OR) for broad matching
      3. Use :* prefix matching to catch partial terms

    Example:
      "PAN mismatch during VKYC" → "PAN:* | mismatch:* | VKYC:*"
    """
    tokens = _TOKEN_RE.findall(query_text)
    if not tokens:
        return ""
    # Deduplicate, preserve order
    seen: set[str] = set()
    unique_tokens: list[str] = []
    for t in tokens:
        tl = t.lower()
        if tl not in seen and len(t) >= 2:
            seen.add(tl)
            unique_tokens.append(t)
    if not unique_tokens:
        return ""
    return " | ".join(f"{t}:*" for t in unique_tokens)


def run_keyword_search(
    *,
    supabase_client: Any,
    table_name: str,
    query_text: str,
    config: RetrievalConfig,
    metadata_filters: dict[str, Any] | None = None,
    top_k: int | None = None,
) -> tuple[list[KeywordCandidate], float]:
    """
    Execute PostgreSQL full-text search on the documents table.

    Uses the 'fts' generated column (tsvector) if available.
    Falls back to ilike content search if FTS is not set up.

    Returns:
        (candidates, latency_ms)
    """
    t_start = time.perf_counter()
    effective_top_k = top_k if top_k is not None else config.keyword_top_k

    candidates: list[KeywordCandidate] = []
    tsquery = _build_tsquery(query_text)

    if not tsquery:
        latency_ms = (time.perf_counter() - t_start) * 1000
        return candidates, latency_ms

    try:
        # Attempt full-text search using the generated 'fts' column
        # PostgREST supports textSearch via .text_search()
        response = (
            supabase_client.table(table_name)
            .select("id,content,metadata")
            .text_search("fts", tsquery, options={"type": "websearch"})
            .limit(effective_top_k)
            .execute()
        )
        rows: list[dict] = response.data or []

        # Apply metadata filters
        if metadata_filters:
            rows = apply_metadata_filters(rows, metadata_filters)

        # Score using simple term frequency as proxy for ts_rank
        # (PostgREST does not expose ts_rank directly without a custom RPC)
        query_tokens = {t.lower() for t in _TOKEN_RE.findall(query_text)}
        for rank, row in enumerate(rows, start=1):
            content = str(row.get("content", "")).lower()
            content_tokens = set(re.findall(r"\b\w+\b", content))
            overlap_count = len(query_tokens & content_tokens)
            ts_rank_approx = overlap_count / max(1, len(query_tokens))

            if ts_rank_approx < config.keyword_min_ts_rank:
                continue

            candidates.append(
                KeywordCandidate(
                    doc_id=str(row.get("id", "")),
                    content=str(row.get("content", "")),
                    metadata=row.get("metadata") or {},
                    ts_rank=ts_rank_approx,
                    keyword_rank=rank,
                    matched_terms=sorted(query_tokens & content_tokens),
                )
            )

    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(
            "keyword_search FTS failed (is the 'fts' column set up?): %s — "
            "falling back to ilike search",
            exc,
        )
        candidates = _fallback_ilike_search(
            supabase_client=supabase_client,
            table_name=table_name,
            query_text=query_text,
            metadata_filters=metadata_filters,
            top_k=effective_top_k,
            config=config,
        )

    latency_ms = (time.perf_counter() - t_start) * 1000
    LOGGER.debug(
        "keyword_search returned %d candidates in %.1fms (query: %r)",
        len(candidates),
        latency_ms,
        tsquery[:80],
    )
    return candidates, latency_ms


def _fallback_ilike_search(
    *,
    supabase_client: Any,
    table_name: str,
    query_text: str,
    metadata_filters: dict[str, Any] | None,
    top_k: int,
    config: RetrievalConfig,
) -> list[KeywordCandidate]:
    """
    Graceful fallback when the FTS 'fts' column is not available.
    Uses PostgreSQL ILIKE for simple case-insensitive substring matching.
    Less powerful but always works without schema changes.
    """
    tokens = _TOKEN_RE.findall(query_text)
    if not tokens:
        return []

    # Use the most distinctive token (longest) for the ilike search
    primary_token = max(tokens, key=len)
    candidates: list[KeywordCandidate] = []

    try:
        response = (
            supabase_client.table(table_name)
            .select("id,content,metadata")
            .ilike("content", f"%{primary_token}%")
            .limit(top_k)
            .execute()
        )
        rows: list[dict] = response.data or []

        if metadata_filters:
            rows = apply_metadata_filters(rows, metadata_filters)

        query_tokens = {t.lower() for t in tokens}
        for rank, row in enumerate(rows, start=1):
            content = str(row.get("content", "")).lower()
            content_tokens = set(re.findall(r"\b\w+\b", content))
            overlap_count = len(query_tokens & content_tokens)
            ts_rank_approx = overlap_count / max(1, len(query_tokens))

            candidates.append(
                KeywordCandidate(
                    doc_id=str(row.get("id", "")),
                    content=str(row.get("content", "")),
                    metadata=row.get("metadata") or {},
                    ts_rank=ts_rank_approx,
                    keyword_rank=rank,
                    matched_terms=sorted(query_tokens & content_tokens),
                )
            )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("keyword_search ilike fallback also failed: %s", exc)

    return candidates
