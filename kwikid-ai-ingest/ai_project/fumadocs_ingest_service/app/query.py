from __future__ import annotations

import logging
import re
import time
from typing import Any
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import Settings
from app.embedder import EmbeddingClient
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


def _rerank_score(query_text: str, candidate_text: str) -> float:
    q = _tokenize(query_text)
    c = _tokenize(candidate_text)
    if not q or not c:
        return 0.0
    overlap = len(q & c)
    return overlap / max(1, len(q))


def _normalize_source_type(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    clean = value.strip().lower()
    return clean or "unknown"


def _sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(item.get("rerank_score", 0.0)),
        float(item.get("vector_score", item.get("similarity", 0.0))),
        _recency_score(item),
    )


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
    candidates = (
        metadata.get("updated_at"),
        metadata.get("last_activity_date"),
        metadata.get("lastActivityDate"),
        metadata.get("last_edit_date"),
        metadata.get("lastEditDate"),
        metadata.get("creation_date"),
        metadata.get("creationDate"),
    )
    for value in candidates:
        dt = _parse_iso_dt(value)
        if dt is not None:
            return dt.timestamp()
    return 0.0


def _resolve_source_thresholds(
    settings: Settings,
    match_threshold: float,
    source_thresholds: dict[str, float] | None,
) -> dict[str, float]:
    effective = {source_type.lower(): float(threshold) for source_type, threshold in settings.query_source_thresholds.items()}
    if source_thresholds:
        for source_type, threshold in source_thresholds.items():
            effective[_normalize_source_type(source_type)] = float(threshold)
    effective["__fallback__"] = float(match_threshold)
    return effective


def _group_by_source(matches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in matches:
        metadata = row.get("metadata") or {}
        source_type = _normalize_source_type(metadata.get("source_type"))
        grouped.setdefault(source_type, []).append(row)
    for source_type in grouped:
        grouped[source_type].sort(key=_sort_key, reverse=True)
    return grouped


def _balanced_top_matches(grouped_matches: dict[str, list[dict[str, Any]]], match_count: int) -> list[dict[str, Any]]:
    if match_count <= 0:
        return []
    source_order = sorted(
        grouped_matches.keys(),
        key=lambda source: _sort_key(grouped_matches[source][0]) if grouped_matches[source] else (0.0, 0.0),
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
    embeddings = EmbeddingClient(
        provider=settings.embedding_provider,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        timeout_s=settings.embedding_timeout_s,
        max_retries=settings.embedding_max_retries,
        retry_base_delay_s=settings.embedding_retry_base_delay_s,
    )
    t_embed_start = time.perf_counter()
    query_vectors = embeddings.embed_texts([query_text])
    embedding_latency_ms = (time.perf_counter() - t_embed_start) * 1000

    if not query_vectors:
        return QueryResult(
            matches=[],
            matches_by_source_type={},
            insufficient_context=True,
            clarification="Unable to generate query embedding.",
            diagnostics={"candidate_count": 0, "returned_count": 0, "best_similarity": 0.0, "best_rerank_score": 0.0},
        )

    # ── Hybrid retrieval branch ─────────────────────────────────────────────
    # Enabled via HYBRID_RETRIEVAL_ENABLED=true in .env
    # When disabled (default), falls through to the existing VectorStore path.
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
            # Hybrid retrieval failed — fall through to existing retrieval path
            LOGGER.warning("hybrid_retrieval failed, falling back to semantic-only: %s", exc)
    # ── End hybrid branch ───────────────────────────────────────────────────

    store = VectorStore(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_key,
        table_name=settings.supabase_table,
        local_fallback_max_rows=settings.local_match_fallback_max_rows,
    )
    candidate_count = max(match_count, match_count * settings.rerank_candidate_multiplier)
    matches = store.match_documents(
        query_embedding=query_vectors[0],
        match_count=candidate_count,
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
    if settings.rerank_enabled:
        for row in matches:
            row["vector_score"] = row.get("similarity", 0.0)
            row["rerank_score"] = _rerank_score(query_text, str(row.get("content") or ""))
        matches.sort(
            key=_sort_key,
            reverse=True,
        )

    effective_thresholds = _resolve_source_thresholds(settings, match_threshold, source_thresholds)
    threshold_filtered: list[dict[str, Any]] = []
    filtered_out_by_source_type: dict[str, int] = {}
    for row in matches:
        metadata = row.get("metadata") or {}
        source_type = _normalize_source_type(metadata.get("source_type"))
        similarity = float(row.get("similarity", 0.0))
        threshold = effective_thresholds.get(source_type, effective_thresholds["__fallback__"])
        if similarity < threshold:
            filtered_out_by_source_type[source_type] = filtered_out_by_source_type.get(source_type, 0) + 1
            continue
        threshold_filtered.append(row)

    grouped_matches = _group_by_source(threshold_filtered)
    top = _balanced_top_matches(grouped_matches, match_count)
    strict_latest = settings.strict_latest_within_top_n if strict_latest_within_top_n is None else strict_latest_within_top_n
    if strict_latest and top:
        top.sort(
            key=lambda row: (_recency_score(row), float(row.get("rerank_score", 0.0)), float(row.get("similarity", 0.0))),
            reverse=True,
        )
    for idx, row in enumerate(top, start=1):
        row["final_rank"] = idx

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
    diagnostics = {
        "candidate_count": candidate_count,
        "returned_count": len(top),
        "returned_count_by_source_type": {source_type: len(rows) for source_type, rows in grouped_matches.items()},
        "filtered_out_by_source_type": filtered_out_by_source_type,
        "applied_source_thresholds": effective_thresholds,
        "best_similarity": best_similarity,
        "best_rerank_score": best_rerank,
        "strict_latest_within_top_n": strict_latest,
    }
    return QueryResult(
        matches=top,
        matches_by_source_type=grouped_matches,
        insufficient_context=insufficient,
        clarification=clarification,
        diagnostics=diagnostics,
    )
