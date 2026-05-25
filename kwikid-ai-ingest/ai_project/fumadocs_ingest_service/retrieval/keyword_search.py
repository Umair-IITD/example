"""
retrieval/keyword_search.py
----------------------------
Full-text keyword search using PostgreSQL ts_vector / ts_rank.

Implementation strategy (three levels, most accurate to least):
  1. RPC: calls search_documents_fts() for real ts_rank scores from PostgreSQL
  2. PostgREST websearch: uses websearch_to_tsquery via .text_search() with raw query text
  3. ILIKE: last-resort substring match when FTS column is absent

The original FTS implementation passed a manually constructed tsquery string
to text_search() with type="websearch", which are incompatible formats —
websearch_to_tsquery() does not recognise the | and :* tsquery operators.
This is now fixed by passing raw query_text instead of a pre-formatted tsquery.

PostgreSQL setup required (run fts_setup.sql in Supabase SQL editor):
  ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED;
  CREATE INDEX IF NOT EXISTS documents_fts_gin_idx ON documents USING gin(fts);

For actual ts_rank scores, also run fts_rpc.sql to create search_documents_fts().
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

_TOKEN_RE = re.compile(r"[A-Za-z0-9][\w\-]*[A-Za-z0-9]|[A-Za-z0-9]+")


def _meaningful_tokens(query_text: str) -> list[str]:
    """Extract meaningful tokens from query text, deduplicated and ordered."""
    seen: set[str] = set()
    result: list[str] = []
    for t in _TOKEN_RE.findall(query_text):
        tl = t.lower()
        if tl not in seen and len(t) >= 2:
            seen.add(tl)
            result.append(t)
    return result


def _compute_ts_rank_approx(query_tokens: set[str], content: str) -> float:
    """
    BM25-inspired ts_rank approximation — used when actual PostgreSQL ts_rank
    is unavailable (PostgREST text_search path).

    Significantly more accurate than naive overlap/len(query) Jaccard:
    - Counts raw token frequency in content
    - Normalises by query token count
    - Results are roughly proportional to true ts_rank ordering

    NOT used when the search_documents_fts() RPC is available (which returns
    actual PostgreSQL ts_rank values).
    """
    content_lower = content.lower()
    score = 0.0
    for term in query_tokens:
        tf = content_lower.count(term.lower())
        if tf:
            score += min(tf, 5)  # cap at 5 to prevent verbosity dominance
    return score / max(1, len(query_tokens))


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

    Strategy:
      1. Try search_documents_fts() RPC (real ts_rank scores)
      2. Fall back to PostgREST .text_search() with raw query text + websearch type
      3. Fall back to ilike content search

    Returns:
        (candidates, latency_ms)
    """
    t_start = time.perf_counter()
    effective_top_k = top_k if top_k is not None else config.keyword_top_k
    tokens = _meaningful_tokens(query_text)

    if not tokens:
        return [], (time.perf_counter() - t_start) * 1000

    query_tokens = {t.lower() for t in tokens}

    # ── Level 1: RPC-based FTS with real ts_rank ──────────────────────────────
    candidates = _try_rpc_fts(
        supabase_client=supabase_client,
        query_text=query_text,
        query_tokens=query_tokens,
        metadata_filters=metadata_filters,
        top_k=effective_top_k,
        min_ts_rank=config.keyword_min_ts_rank,
    )
    retrieval_method = "rpc_fts"

    # ── Level 2: PostgREST text_search (websearch_to_tsquery) ─────────────────
    if candidates is None:
        candidates = _try_posgtrest_fts(
            supabase_client=supabase_client,
            table_name=table_name,
            query_text=query_text,
            query_tokens=query_tokens,
            metadata_filters=metadata_filters,
            top_k=effective_top_k,
            min_ts_rank=config.keyword_min_ts_rank,
        )
        retrieval_method = "posgtrest_fts"

    # ── Level 3: ILIKE fallback ────────────────────────────────────────────────
    if candidates is None:
        candidates = _fallback_ilike_search(
            supabase_client=supabase_client,
            table_name=table_name,
            tokens=tokens,
            query_tokens=query_tokens,
            metadata_filters=metadata_filters,
            top_k=effective_top_k,
        )
        retrieval_method = "ilike"

    latency_ms = (time.perf_counter() - t_start) * 1000
    LOGGER.debug(
        "keyword_search method=%s returned %d candidates in %.1fms",
        retrieval_method,
        len(candidates),
        latency_ms,
    )
    return candidates, latency_ms


def _try_rpc_fts(
    *,
    supabase_client: Any,
    query_text: str,
    query_tokens: set[str],
    metadata_filters: dict[str, Any] | None,
    top_k: int,
    min_ts_rank: float,
) -> list[KeywordCandidate] | None:
    """
    Call search_documents_fts() RPC for real PostgreSQL ts_rank scores.
    Returns None if the RPC is not available (so caller can fall back).
    """
    try:
        response = supabase_client.rpc(
            "search_documents_fts",
            {"p_query_text": query_text, "p_match_count": top_k},
        ).execute()
        rows: list[dict] = response.data or []

        if metadata_filters:
            rows = apply_metadata_filters(rows, metadata_filters)

        candidates: list[KeywordCandidate] = []
        for rank, row in enumerate(rows, start=1):
            ts_rank = float(row.get("ts_rank", 0.0))
            if ts_rank < min_ts_rank:
                continue
            content = str(row.get("content", ""))
            content_lower = content.lower()
            matched = sorted(t for t in query_tokens if t in content_lower)
            candidates.append(
                KeywordCandidate(
                    doc_id=str(row.get("id", "")),
                    content=content,
                    metadata=row.get("metadata") or {},
                    ts_rank=ts_rank,
                    keyword_rank=rank,
                    matched_terms=matched,
                )
            )
        return candidates

    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("search_documents_fts RPC not available: %s", exc)
        return None


def _try_posgtrest_fts(
    *,
    supabase_client: Any,
    table_name: str,
    query_text: str,
    query_tokens: set[str],
    metadata_filters: dict[str, Any] | None,
    top_k: int,
    min_ts_rank: float,
) -> list[KeywordCandidate] | None:
    """
    PostgREST full-text search using websearch_to_tsquery.

    Passes the raw query_text (not a pre-formatted tsquery string) so that
    websearch_to_tsquery() correctly parses natural language with AND semantics.
    The 'websearch' type handles: quoted phrases, OR operator, - for exclusion.
    """
    try:
        response = (
            supabase_client.table(table_name)
            .select("id,content,metadata")
            .text_search("fts", query_text, options={"type": "websearch"})
            .limit(top_k)
            .execute()
        )
        rows: list[dict] = response.data or []

        if metadata_filters:
            rows = apply_metadata_filters(rows, metadata_filters)

        candidates: list[KeywordCandidate] = []
        for rank, row in enumerate(rows, start=1):
            content = str(row.get("content", ""))
            ts_rank_approx = _compute_ts_rank_approx(query_tokens, content)
            if ts_rank_approx < min_ts_rank:
                continue
            content_lower = content.lower()
            matched = sorted(t for t in query_tokens if t in content_lower)
            candidates.append(
                KeywordCandidate(
                    doc_id=str(row.get("id", "")),
                    content=content,
                    metadata=row.get("metadata") or {},
                    ts_rank=ts_rank_approx,
                    keyword_rank=rank,
                    matched_terms=matched,
                )
            )
        return candidates

    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(
            "keyword_search PostgREST FTS failed (is the 'fts' column set up? "
            "Run sql/fts_setup.sql in Supabase): %s",
            exc,
        )
        return None


def _fallback_ilike_search(
    *,
    supabase_client: Any,
    table_name: str,
    tokens: list[str],
    query_tokens: set[str],
    metadata_filters: dict[str, Any] | None,
    top_k: int,
) -> list[KeywordCandidate]:
    """
    Last-resort fallback: ILIKE substring match on the most distinctive token.
    Significantly less accurate than FTS — used only when FTS column is unavailable.
    """
    if not tokens:
        return []

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

        for rank, row in enumerate(rows, start=1):
            content = str(row.get("content", ""))
            ts_rank_approx = _compute_ts_rank_approx(query_tokens, content)
            content_lower = content.lower()
            matched = sorted(t for t in query_tokens if t in content_lower)
            candidates.append(
                KeywordCandidate(
                    doc_id=str(row.get("id", "")),
                    content=content,
                    metadata=row.get("metadata") or {},
                    ts_rank=ts_rank_approx,
                    keyword_rank=rank,
                    matched_terms=matched,
                )
            )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("keyword_search ilike fallback also failed: %s", exc)

    return candidates
