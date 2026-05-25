from __future__ import annotations

import logging
import math as _math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.embedder import EmbeddingClient
from app.query_preprocessor import preprocess_query
from app.uploader import VectorStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryResult:
    matches: list[dict[str, Any]]
    matches_by_source_type: dict[str, list[dict[str, Any]]]
    insufficient_context: bool
    clarification: str | None
    diagnostics: dict[str, Any]


WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(WORD_RE.findall(text.lower()))


# ---------------------------------------------------------------------------
# BM25 reranker
# ---------------------------------------------------------------------------

def _bm25_scores(
    query_text: str,
    candidates: list[dict],
    k1: float = 1.5,
    b: float = 0.75,
) -> None:
    """
    Compute BM25 rerank scores in-place on the candidate list.

    Uses in-batch IDF (document frequency computed from the retrieved candidate
    set), BM25 TF saturation (k1=1.5) and length normalization (b=0.75).
    Significantly outperforms naive Jaccard overlap for support ticket retrieval
    because it rewards rare but diagnostic terms and penalises verbosity.

    Writes 'rerank_score' and 'vector_score' to each dict in candidates.
    """
    q_tokens = _tokenize(query_text)
    if not q_tokens or not candidates:
        for row in candidates:
            row["rerank_score"] = 0.0
            row["vector_score"] = float(row.get("similarity", 0.0))
        return

    n = len(candidates)
    contents = [str(row.get("content", "")) for row in candidates]
    doc_token_sets = [_tokenize(c) for c in contents]
    doc_lengths = [len(t) for t in doc_token_sets]
    avg_dl = sum(doc_lengths) / n if n else 1.0

    # In-batch document frequency for each query term
    df: dict[str, int] = {
        term: sum(1 for dt in doc_token_sets if term in dt)
        for term in q_tokens
    }

    query_lower = query_text.lower().strip()

    for row, content, dl in zip(candidates, contents, doc_lengths):
        content_lower = content.lower()
        score = 0.0

        for term in q_tokens:
            tf = content_lower.count(term)
            if tf == 0:
                continue
            # Smoothed IDF — prevents negative values when df = n
            idf = _math.log((n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1.0)
            # BM25 TF component with length normalization
            tf_norm = tf * (k1 + 1.0) / (tf + k1 * (1.0 - b + b * dl / max(1.0, avg_dl)))
            score += idf * tf_norm

        # Exact phrase match bonus — strong relevance signal for support queries
        if len(query_lower) > 4 and query_lower in content_lower:
            score += len(q_tokens) * 0.5

        # Title match bonus — query terms in the document title signal high relevance
        metadata = row.get("metadata") or {}
        title_tokens = _tokenize(str(metadata.get("title", "")))
        title_overlap = len(q_tokens & title_tokens)
        if title_overlap:
            score += title_overlap * 0.3

        row["rerank_score"] = round(score, 6)
        row["vector_score"] = float(row.get("similarity", 0.0))


# ---------------------------------------------------------------------------
# Sorting and grouping helpers
# ---------------------------------------------------------------------------

def _normalize_source_type(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    clean = value.strip().lower()
    return clean or "unknown"


def _parse_iso_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _recency_score(item: dict[str, Any]) -> float:
    metadata = item.get("metadata") or {}
    for key in (
        "updated_at",
        "last_activity_date",
        "lastActivityDate",
        "last_edit_date",
        "lastEditDate",
        "creation_date",
        "creationDate",
    ):
        dt = _parse_iso_dt(metadata.get(key))
        if dt is not None:
            return dt.timestamp()
    return 0.0


def _sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(item.get("rerank_score", 0.0)),
        float(item.get("vector_score", item.get("similarity", 0.0))),
        _recency_score(item),
    )


def _resolve_source_thresholds(
    settings: Settings,
    match_threshold: float,
    source_thresholds: dict[str, float] | None,
) -> dict[str, float]:
    effective = {
        st.lower(): float(th)
        for st, th in settings.query_source_thresholds.items()
    }
    if source_thresholds:
        for source_type, threshold in source_thresholds.items():
            effective[_normalize_source_type(source_type)] = float(threshold)
    effective["__fallback__"] = float(match_threshold)
    return effective


def _group_by_source(
    matches: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in matches:
        metadata = row.get("metadata") or {}
        source_type = _normalize_source_type(metadata.get("source_type"))
        grouped.setdefault(source_type, []).append(row)
    for source_type in grouped:
        grouped[source_type].sort(key=_sort_key, reverse=True)
    return grouped


def _balanced_top_matches(
    grouped_matches: dict[str, list[dict[str, Any]]],
    match_count: int,
) -> list[dict[str, Any]]:
    if match_count <= 0:
        return []
    source_order = sorted(
        grouped_matches.keys(),
        key=lambda s: _sort_key(grouped_matches[s][0]) if grouped_matches[s] else (0.0, 0.0),
        reverse=True,
    )
    pointers = dict.fromkeys(source_order, 0)
    selected: list[dict[str, Any]] = []
    while len(selected) < match_count:
        progressed = False
        for source_type in source_order:
            rows = grouped_matches[source_type]
            idx = pointers[source_type]
            if idx >= len(rows):
                continue
            selected.append(rows[idx])
            pointers[source_type] = idx + 1
            progressed = True
            if len(selected) >= match_count:
                break
        if not progressed:
            break
    return selected


# ---------------------------------------------------------------------------
# Main query entry point
# ---------------------------------------------------------------------------

def run_query(
    settings: Settings,
    query_text: str,
    *,
    match_count: int = 5,
    match_threshold: float = 0.0,
    source_thresholds: dict[str, float] | None = None,
    source_types: list[str] | None = None,
    tenant: str | None = None,
    access_scope: str | None = None,
    updated_at_from: str | None = None,
    updated_at_to: str | None = None,
    strict_latest_within_top_n: bool | None = None,
) -> QueryResult:
    # ── Query preprocessing ───────────────────────────────────────────────────
    # Expand acronyms and normalise domain-specific terms before embedding.
    # The original query_text is preserved for BM25 reranking (exact phrases matter).
    preprocessed_query = preprocess_query(query_text)

    # ── Embedding ─────────────────────────────────────────────────────────────
    embeddings = EmbeddingClient(
        provider=settings.embedding_provider,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        timeout_s=settings.embedding_timeout_s,
        max_retries=settings.embedding_max_retries,
        retry_base_delay_s=settings.embedding_retry_base_delay_s,
    )
    t_embed = time.perf_counter()
    query_vectors = embeddings.embed_texts([preprocessed_query])
    embedding_latency_ms = round((time.perf_counter() - t_embed) * 1000, 1)

    if not query_vectors:
        return QueryResult(
            matches=[],
            matches_by_source_type={},
            insufficient_context=True,
            clarification="Unable to generate query embedding.",
            diagnostics={
                "fetch_limit": 0,
                "candidate_count": 0,
                "db_rows_returned": 0,
                "rows_after_similarity_filter": 0,
                "rows_after_metadata_filter": 0,
                "rows_after_source_threshold": 0,
                "returned_count": 0,
                "best_similarity": 0.0,
                "best_rerank_score": 0.0,
                "embedding_latency_ms": embedding_latency_ms,
                "db_latency_ms": 0.0,
                "total_latency_ms": embedding_latency_ms,
                "duration_ms": embedding_latency_ms,
                "retrieval_diagnostics": {},
            },
        )

    # ── Hybrid retrieval branch ───────────────────────────────────────────────
    if settings.hybrid_retrieval_enabled:
        try:
            from retrieval.hybrid_search import run_hybrid_search
            from retrieval.config import RetrievalConfig
            from retrieval.filters import build_metadata_filter_set

            hybrid_config = RetrievalConfig(
                semantic_top_k=max(match_count, match_count * settings.rerank_candidate_multiplier),
                keyword_top_k=max(match_count, match_count * settings.rerank_candidate_multiplier),
                fusion_top_k=max(match_count * 2, 10),
                rerank_enabled=settings.rerank_enabled,
                rerank_top_k=match_count,
                final_top_k=match_count,
                min_similarity=settings.confidence_min_similarity,
                min_rerank_score=settings.confidence_min_rerank,
            )
            metadata_filters = build_metadata_filter_set(
                source_types=source_types,
                tenant=tenant,
                access_scope=access_scope,
                index_version=settings.active_index_version,
                updated_at_from=updated_at_from,
                updated_at_to=updated_at_to,
            )
            hybrid_result = run_hybrid_search(
                supabase_url=settings.supabase_url,
                supabase_key=settings.supabase_key,
                table_name=settings.supabase_table,
                query_embedding=query_vectors[0],
                query_text=query_text,
                config=hybrid_config,
                metadata_filters=metadata_filters,
                match_threshold=match_threshold,
                final_top_k=match_count,
                embedding_latency_ms=embedding_latency_ms,
            )
            LOGGER.info(
                "hybrid_query sem=%d kw=%d fused=%d final=%d latency=%.0fms",
                hybrid_result.diagnostics.get("semantic_candidates", 0),
                hybrid_result.diagnostics.get("keyword_candidates", 0),
                hybrid_result.diagnostics.get("fused_candidates", 0),
                len(hybrid_result.matches),
                hybrid_result.diagnostics.get("latency_ms", {}).get("total", 0),
            )
            return QueryResult(
                matches=hybrid_result.matches,
                matches_by_source_type=hybrid_result.matches_by_source_type,
                insufficient_context=hybrid_result.insufficient_context,
                clarification=hybrid_result.clarification,
                diagnostics=hybrid_result.diagnostics,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("hybrid_retrieval failed, falling back to semantic-only: %s", exc)

    # ── Semantic retrieval (default path) ─────────────────────────────────────
    store = VectorStore(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_key,
        table_name=settings.supabase_table,
        local_fallback_max_rows=settings.local_match_fallback_max_rows,
    )

    # fetch_limit: how many candidates to request from DB before Python reranking.
    # Larger pool → better reranking precision. Distinct from match_count (final output).
    fetch_limit = max(match_count, match_count * settings.rerank_candidate_multiplier)

    t_db = time.perf_counter()
    match_result = store.match_documents(
        query_embedding=query_vectors[0],
        match_count=fetch_limit,
        match_threshold=match_threshold,
        metadata_filters={
            "source_types": source_types,
            "tenant": tenant,
            "access_scope": access_scope,
            "updated_at_from": updated_at_from,
            "updated_at_to": updated_at_to,
            "index_version": settings.active_index_version,
        },
    )
    db_latency_ms = round((time.perf_counter() - t_db) * 1000, 1)
    matches = match_result.rows

    # ── Reranking (BM25) ──────────────────────────────────────────────────────
    if settings.rerank_enabled:
        _bm25_scores(query_text, matches)
        matches.sort(key=_sort_key, reverse=True)

    # ── Source-type threshold filtering ───────────────────────────────────────
    effective_thresholds = _resolve_source_thresholds(settings, match_threshold, source_thresholds)
    threshold_filtered: list[dict[str, Any]] = []
    filtered_out_by_source_type: dict[str, int] = {}
    for row in matches:
        metadata = row.get("metadata") or {}
        source_type = _normalize_source_type(metadata.get("source_type"))
        similarity = float(row.get("similarity", 0.0))
        threshold = effective_thresholds.get(source_type, effective_thresholds["__fallback__"])
        if similarity < threshold:
            filtered_out_by_source_type[source_type] = (
                filtered_out_by_source_type.get(source_type, 0) + 1
            )
            continue
        threshold_filtered.append(row)

    # ── Group, balance, and select top-N ─────────────────────────────────────
    grouped_matches = _group_by_source(threshold_filtered)
    top = _balanced_top_matches(grouped_matches, match_count)

    strict_latest = (
        settings.strict_latest_within_top_n
        if strict_latest_within_top_n is None
        else strict_latest_within_top_n
    )
    if strict_latest and top:
        top.sort(
            key=lambda row: (
                _recency_score(row),
                float(row.get("rerank_score", 0.0)),
                float(row.get("similarity", 0.0)),
            ),
            reverse=True,
        )
    for idx, row in enumerate(top, start=1):
        row["final_rank"] = idx

    # ── Quality signals ───────────────────────────────────────────────────────
    best_similarity = float(top[0].get("similarity", 0.0)) if top else 0.0
    best_rerank = float(top[0].get("rerank_score", 0.0)) if top else 0.0
    insufficient = not top or (
        best_similarity < settings.confidence_min_similarity
        and best_rerank < settings.confidence_min_rerank
    )
    clarification = (
        "I do not have enough confident evidence. Try narrowing by source_type or date range."
        if insufficient
        else None
    )

    # ── Diagnostics (full per-stage breakdown) ────────────────────────────────
    retrieval_diag = match_result.to_diagnostics_dict()
    total_latency_ms = round(embedding_latency_ms + db_latency_ms, 1)

    diagnostics: dict[str, Any] = {
        # ── Query preprocessing ──
        "query_preprocessed": preprocessed_query if preprocessed_query != query_text else None,
        # ── Retrieval mode ──
        "retrieval_mode_used": "semantic_only",
        "reranker_used": "bm25" if settings.rerank_enabled else "none",
        # ── What was requested vs what DB actually returned ──
        "fetch_limit": fetch_limit,           # rows requested from DB (for reranking pool)
        "candidate_count": fetch_limit,       # backward-compat alias
        "db_rows_returned": match_result.db_rows_returned,
        "rows_after_similarity_filter": match_result.rows_after_similarity_filter,
        "rows_after_metadata_filter": match_result.rows_after_metadata_filter,
        # ── Source-type threshold filtering ──
        "rows_after_source_threshold": len(threshold_filtered),
        "filtered_out_by_source_type": filtered_out_by_source_type,
        # ── Final output ──
        "returned_count": len(top),
        "returned_count_by_source_type": {st: len(rows) for st, rows in grouped_matches.items()},
        # ── Quality signals ──
        "best_similarity": best_similarity,
        "best_rerank_score": best_rerank,
        "applied_source_thresholds": effective_thresholds,
        "strict_latest_within_top_n": strict_latest,
        # ── Latency breakdown ──
        "embedding_latency_ms": embedding_latency_ms,
        "db_latency_ms": db_latency_ms,
        "total_latency_ms": total_latency_ms,
        "duration_ms": total_latency_ms,      # backward-compat alias for tracer
        # ── Per-filter removal counters (from VectorStore) ──
        "retrieval_diagnostics": retrieval_diag,
    }

    # Warn when DB returned rows but all were filtered — the most common silent failure
    if match_result.db_rows_returned > 0 and match_result.rows_after_metadata_filter == 0:
        LOGGER.warning(
            "run_query: DB returned %d rows but all were dropped by metadata filters. "
            "retrieval_diagnostics=%s — check index_version mismatch (ACTIVE_INDEX_VERSION=%r "
            "vs data ingested with WRITE_INDEX_VERSION). "
            "Also verify sql/fix_match_documents_final.sql has been applied.",
            match_result.db_rows_returned,
            retrieval_diag,
            settings.active_index_version,
        )
    elif match_result.rows_after_metadata_filter > 0 and len(threshold_filtered) == 0:
        LOGGER.warning(
            "run_query: %d rows passed metadata filters but all dropped by source-type thresholds. "
            "filtered_out=%s applied_thresholds=%s — "
            "consider lowering QUERY_SOURCE_THRESHOLD_* env vars.",
            match_result.rows_after_metadata_filter,
            filtered_out_by_source_type,
            effective_thresholds,
        )

    return QueryResult(
        matches=top,
        matches_by_source_type=grouped_matches,
        insufficient_context=insufficient,
        clarification=clarification,
        diagnostics=diagnostics,
    )
