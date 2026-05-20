from __future__ import annotations

import math
import json
import uuid
import time
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Iterable

from supabase import Client, create_client

from app.chunker import Chunk

EPSILON = 1e-12
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoreResult:
    created: int
    updated: int
    skipped: int


@dataclass
class MatchResult:
    """
    Structured result from VectorStore.match_documents.

    Exposes per-stage row counts so callers can diagnose exactly where
    candidates are lost (SQL, similarity threshold, metadata filters, or
    source-type thresholds applied later in run_query).
    """
    rows: list[dict]
    # Stage 1: how many rows the DB RPC returned before Python filtering
    db_rows_returned: int
    # Stage 2: after Python-side similarity re-check
    rows_after_similarity_filter: int
    # Stage 3: after all metadata filters
    rows_after_metadata_filter: int
    # Per-filter removal counters
    removed_by_similarity_threshold: int
    removed_by_index_version: int
    removed_by_tenant: int
    removed_by_access_scope: int
    removed_by_source_type: int
    removed_by_date: int
    # Fallback path used (None = normal RPC path)
    used_fallback: str | None  # None | "2arg_overload" | "local"

    def to_diagnostics_dict(self) -> dict:
        return {
            "db_rows_returned": self.db_rows_returned,
            "rows_after_similarity_filter": self.rows_after_similarity_filter,
            "rows_after_metadata_filter": self.rows_after_metadata_filter,
            "removed_by_similarity_threshold": self.removed_by_similarity_threshold,
            "removed_by_index_version": self.removed_by_index_version,
            "removed_by_tenant": self.removed_by_tenant,
            "removed_by_access_scope": self.removed_by_access_scope,
            "removed_by_source_type_filter": self.removed_by_source_type,
            "removed_by_date": self.removed_by_date,
            "used_fallback": self.used_fallback,
        }


class VectorStore:
    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        table_name: str = "documents",
        local_fallback_max_rows: int = 5000,
    ) -> None:
        self._table_name = table_name
        self._local_fallback_max_rows = local_fallback_max_rows
        self._client: Client = create_client(supabase_url, supabase_key)

    # ── Ingestion helpers ──────────────────────────────────────────────────────

    def fetch_existing_hashes(self, source_type: str, source_id: str) -> dict[int, str]:
        try:
            response = (
                self._client.table(self._table_name)
                .select("metadata")
                .eq("metadata->>source_type", source_type)
                .eq("metadata->>source_id", source_id)
                .execute()
            )
        except Exception:
            # Don't block ingestion on pre-check read timeouts; deterministic IDs make upserts safe.
            return {}
        existing: dict[int, str] = {}
        for row in response.data or []:
            metadata = row.get("metadata") or {}
            idx = metadata.get("chunk_index")
            content_hash = metadata.get("content_hash")
            if isinstance(idx, int) and isinstance(content_hash, str):
                existing[idx] = content_hash
        return existing

    def upsert_chunks(
        self,
        *,
        repo: str,
        commit_sha: str,
        index_version: str,
        chunk_vectors: Iterable[tuple[Chunk, list[float]]],
    ) -> StoreResult:
        created = 0
        updated = 0
        skipped = 0
        existing_by_source: dict[str, dict[int, str]] = {}
        payloads: list[dict[str, object]] = []
        for chunk, vector in chunk_vectors:
            source_key = f"{chunk.source_type}:{chunk.source_id}"
            source_existing = existing_by_source.get(source_key)
            if source_existing is None:
                source_existing = self.fetch_existing_hashes(chunk.source_type, chunk.source_id)
                existing_by_source[source_key] = source_existing
            existing = source_existing.get(chunk.chunk_index)
            if existing == chunk.content_hash:
                skipped += 1
                continue
            if existing is None:
                created += 1
            else:
                updated += 1

            deterministic_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{repo}:{chunk.source_type}:{chunk.source_id}:{chunk.chunk_index}",
                )
            )
            payloads.append(
                {
                    "id": deterministic_id,
                    "content": chunk.content,
                    "embedding": vector,
                    "metadata": {
                        "source": chunk.source_type,
                        "source_type": chunk.source_type,
                        "repo": repo,
                        "commit_sha": commit_sha,
                        "title": chunk.title,
                        "heading": chunk.heading,
                        "tags": chunk.tags,
                        "source_id": chunk.source_id,
                        "chunk_index": chunk.chunk_index,
                        "word_count": chunk.word_count,
                        "content_hash": chunk.content_hash,
                        "index_version": index_version,
                        **chunk.metadata,
                    },
                }
            )
        if payloads:
            self._upsert_payloads_with_fallback(payloads)
        return StoreResult(created=created, updated=updated, skipped=skipped)

    def _upsert_payloads_with_fallback(self, payloads: list[dict[str, object]]) -> None:
        """
        Bulk upsert with per-row retry fallback.
        Keeps Freshdesk ticket-ID ingests working even on intermittent Supabase latency.
        """
        try:
            self._client.table(self._table_name).upsert(payloads).execute()
            return
        except Exception as bulk_exc:  # noqa: BLE001
            LOGGER.warning(
                "Bulk upsert failed for %s payloads, retrying one-by-one: %s",
                len(payloads),
                bulk_exc,
            )

        row_errors: list[str] = []
        for row in payloads:
            row_id = str(row.get("id") or "")
            last_exc: Exception | None = None
            for attempt in range(1, 4):
                try:
                    self._client.table(self._table_name).upsert([row]).execute()
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt < 3:
                        time.sleep(min(8.0, 1.5 * attempt))
            if last_exc is not None:
                row_errors.append(f"id={row_id}: {last_exc}")

        if row_errors:
            sample = "; ".join(row_errors[:5])
            extra = "" if len(row_errors) <= 5 else f" (+{len(row_errors) - 5} more)"
            raise RuntimeError(f"Upsert failed for {len(row_errors)} row(s): {sample}{extra}")

    def delete_repo_records(self, repo: str) -> None:
        page_size = 1000
        offset = 0
        while True:
            response = (
                self._client.table(self._table_name)
                .select("id,metadata")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                break

            for row in rows:
                metadata = row.get("metadata") or {}
                if metadata.get("repo") == repo:
                    self._client.table(self._table_name).delete().eq("id", row["id"]).execute()

            if len(rows) < page_size:
                break
            offset += page_size

    # ── Retrieval ──────────────────────────────────────────────────────────────

    def match_documents(
        self,
        *,
        query_embedding: list[float],
        match_count: int,
        match_threshold: float = 0.0,
        metadata_filters: dict[str, object] | None = None,
    ) -> MatchResult:
        """
        Execute server-side vector similarity search and return a MatchResult with
        full per-stage diagnostic counters.

        Filter order:
          1. SQL RPC:  cosine similarity >= match_threshold  (server-side, fast)
          2. Python:   _apply_similarity_threshold_v2        (no-op when threshold <= 0)
          3. Python:   _filter_with_diagnostics              (index_version, tenant, etc.)

        On PGRST203 (overload conflict): falls back to 2-arg call (no threshold arg).
        On kb_chunks missing: falls back to local cosine similarity scan.

        Run sql/fix_match_documents_final.sql in Supabase to eliminate both fallbacks.
        """
        used_fallback: str | None = None

        try:
            response = (
                self._client.rpc(
                    "match_documents",
                    {
                        "query_embedding": query_embedding,
                        "match_count": match_count,
                        "match_threshold": match_threshold,
                    },
                )
                .execute()
            )
            raw_rows = response.data or []

        except Exception as exc:  # noqa: BLE001
            msg = str(exc)

            if "PGRST203" in msg or "Multiple Choices" in msg:
                LOGGER.warning(
                    "match_documents: PGRST203 overload conflict — using 2-arg fallback. "
                    "Permanently fix: run sql/fix_match_documents_final.sql in Supabase."
                )
                used_fallback = "2arg_overload"
                response = (
                    self._client.rpc(
                        "match_documents",
                        {"query_embedding": query_embedding, "match_count": match_count},
                    )
                    .execute()
                )
                raw_rows = response.data or []

            elif (
                'relation "public.kb_chunks" does not exist' in msg
                or 'relation "kb_chunks" does not exist' in msg
            ):
                LOGGER.warning(
                    "match_documents: kb_chunks table missing — local cosine fallback. "
                    "Permanently fix: run sql/fix_match_documents_final.sql in Supabase."
                )
                return self._match_documents_locally_v2(
                    query_embedding=query_embedding,
                    match_count=match_count,
                    match_threshold=match_threshold,
                    metadata_filters=metadata_filters,
                )
            else:
                raise

        db_rows_returned = len(raw_rows)

        # Python-side similarity re-check (no-op when threshold <= 0)
        after_sim, removed_by_sim = self._apply_similarity_threshold_v2(raw_rows, match_threshold)

        # Metadata filters with per-filter counters
        filtered, counters = self._filter_with_diagnostics(after_sim, metadata_filters)

        if counters["removed_by_index_version"] > 0:
            LOGGER.warning(
                "match_documents: %d/%d rows dropped by index_version filter "
                "(wanted=%r). Documents must be ingested with matching "
                "WRITE_INDEX_VERSION env var. Set ACTIVE_INDEX_VERSION='' to disable "
                "this filter, or re-ingest with the correct version.",
                counters["removed_by_index_version"],
                db_rows_returned,
                metadata_filters.get("index_version") if metadata_filters else None,
            )

        return MatchResult(
            rows=filtered,
            db_rows_returned=db_rows_returned,
            rows_after_similarity_filter=len(after_sim),
            rows_after_metadata_filter=len(filtered),
            removed_by_similarity_threshold=removed_by_sim,
            removed_by_index_version=counters["removed_by_index_version"],
            removed_by_tenant=counters["removed_by_tenant"],
            removed_by_access_scope=counters["removed_by_access_scope"],
            removed_by_source_type=counters["removed_by_source_type"],
            removed_by_date=counters["removed_by_date"],
            used_fallback=used_fallback,
        )

    def healthcheck(self) -> bool:
        response = self._client.table(self._table_name).select("id").limit(1).execute()
        return response.data is not None

    # ── Private: similarity filtering ─────────────────────────────────────────

    @staticmethod
    def _apply_similarity_threshold_v2(
        rows: list[dict], threshold: float
    ) -> tuple[list[dict], int]:
        """Return (filtered_rows, removed_count). No-op when threshold <= 0."""
        if threshold <= 0.0:
            return rows, 0
        filtered = [r for r in rows if float(r.get("similarity", 0.0)) >= threshold]
        return filtered, len(rows) - len(filtered)

    @staticmethod
    def _apply_similarity_threshold(rows: list[dict], threshold: float) -> list[dict]:
        """Backward-compat wrapper."""
        result, _ = VectorStore._apply_similarity_threshold_v2(rows, threshold)
        return result

    # ── Private: metadata filtering ───────────────────────────────────────────

    @staticmethod
    def _filter_with_diagnostics(
        rows: list[dict],
        metadata_filters: dict[str, object] | None,
    ) -> tuple[list[dict], dict[str, int]]:
        """
        Apply metadata filters and return (filtered_rows, per_filter_counters).

        Filters are applied in priority order; the first failing filter short-circuits
        the remaining checks for that row (stops at first rejection).
        """
        counters: dict[str, int] = {
            "removed_by_index_version": 0,
            "removed_by_tenant": 0,
            "removed_by_access_scope": 0,
            "removed_by_source_type": 0,
            "removed_by_date": 0,
        }

        if not metadata_filters:
            return rows, counters

        wanted_source_types = metadata_filters.get("source_types")
        wanted_tenant = metadata_filters.get("tenant")
        wanted_access_scope = metadata_filters.get("access_scope")
        wanted_index_version = metadata_filters.get("index_version")
        updated_from = metadata_filters.get("updated_at_from")
        updated_to = metadata_filters.get("updated_at_to")

        dt_from = VectorStore._parse_dt(updated_from)
        dt_to = VectorStore._parse_dt(updated_to)
        source_type_set = set(wanted_source_types) if isinstance(wanted_source_types, list) else None

        filtered: list[dict] = []
        for row in rows:
            metadata = row.get("metadata") or {}

            if source_type_set is not None and metadata.get("source_type") not in source_type_set:
                counters["removed_by_source_type"] += 1
                continue

            if wanted_tenant is not None and metadata.get("tenant") != wanted_tenant:
                counters["removed_by_tenant"] += 1
                continue

            if wanted_access_scope is not None and metadata.get("access_scope") != wanted_access_scope:
                counters["removed_by_access_scope"] += 1
                continue

            if wanted_index_version is not None and metadata.get("index_version") != wanted_index_version:
                counters["removed_by_index_version"] += 1
                continue

            if dt_from or dt_to:
                row_dt = VectorStore._parse_dt(metadata.get("updated_at"))
                if row_dt is None or (dt_from and row_dt < dt_from) or (dt_to and row_dt > dt_to):
                    counters["removed_by_date"] += 1
                    continue

            filtered.append(row)

        return filtered, counters

    @staticmethod
    def _apply_metadata_filters(
        rows: list[dict], metadata_filters: dict[str, object] | None
    ) -> list[dict]:
        """Backward-compat wrapper."""
        result, _ = VectorStore._filter_with_diagnostics(rows, metadata_filters)
        return result

    # ── Private: local cosine similarity fallback ──────────────────────────────

    def _match_documents_locally_v2(
        self,
        *,
        query_embedding: list[float],
        match_count: int,
        match_threshold: float,
        metadata_filters: dict[str, object] | None = None,
    ) -> MatchResult:
        """
        Application-layer cosine similarity scan — last-resort fallback when the
        Supabase RPC is unavailable. Reads up to local_fallback_max_rows from the
        documents table and scores them in Python.
        """
        page_size = 1000
        offset = 0
        all_rows: list[dict] = []
        while True:
            response = (
                self._client.table(self._table_name)
                .select("id,content,metadata,embedding")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            data = response.data or []
            all_rows.extend(data)
            if len(all_rows) >= self._local_fallback_max_rows:
                all_rows = all_rows[: self._local_fallback_max_rows]
                break
            if len(data) < page_size:
                break
            offset += page_size

        db_rows_returned = len(all_rows)
        query_norm = self._l2_norm(query_embedding)
        if query_norm <= EPSILON:
            return MatchResult(
                rows=[],
                db_rows_returned=db_rows_returned,
                rows_after_similarity_filter=0,
                rows_after_metadata_filter=0,
                removed_by_similarity_threshold=db_rows_returned,
                removed_by_index_version=0,
                removed_by_tenant=0,
                removed_by_access_scope=0,
                removed_by_source_type=0,
                removed_by_date=0,
                used_fallback="local",
            )

        scored: list[dict] = []
        below_threshold = 0
        for row in all_rows:
            embedding = self._normalize_embedding(row.get("embedding"))
            if not embedding:
                continue
            similarity = self._cosine_similarity(query_embedding, embedding, query_norm)
            if similarity < match_threshold:
                below_threshold += 1
                continue
            scored.append(
                {
                    "id": row.get("id"),
                    "content": row.get("content"),
                    "metadata": row.get("metadata"),
                    "similarity": similarity,
                }
            )
        scored.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)

        filtered, counters = self._filter_with_diagnostics(scored, metadata_filters)

        return MatchResult(
            rows=filtered[:match_count],
            db_rows_returned=db_rows_returned,
            rows_after_similarity_filter=len(scored),
            rows_after_metadata_filter=len(filtered),
            removed_by_similarity_threshold=below_threshold,
            removed_by_index_version=counters["removed_by_index_version"],
            removed_by_tenant=counters["removed_by_tenant"],
            removed_by_access_scope=counters["removed_by_access_scope"],
            removed_by_source_type=counters["removed_by_source_type"],
            removed_by_date=counters["removed_by_date"],
            used_fallback="local",
        )

    def _match_documents_locally(
        self,
        *,
        query_embedding: list[float],
        match_count: int,
        match_threshold: float,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[dict]:
        """Backward-compat wrapper around _match_documents_locally_v2."""
        return self._match_documents_locally_v2(
            query_embedding=query_embedding,
            match_count=match_count,
            match_threshold=match_threshold,
            metadata_filters=metadata_filters,
        ).rows

    # ── Private: linear algebra ───────────────────────────────────────────────

    @staticmethod
    def _l2_norm(vector: list[float]) -> float:
        return math.sqrt(sum(float(v) * float(v) for v in vector))

    @classmethod
    def _cosine_similarity(
        cls, left: list[float], right: list[float], left_norm: float | None = None
    ) -> float:
        if len(left) != len(right):
            return 0.0
        norm_left = left_norm if left_norm is not None else cls._l2_norm(left)
        norm_right = cls._l2_norm(right)
        if norm_left <= EPSILON or norm_right <= EPSILON:
            return 0.0
        dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
        return dot / (norm_left * norm_right)

    @staticmethod
    def _normalize_embedding(raw: object) -> list[float]:
        if isinstance(raw, list):
            return [float(value) for value in raw]
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return [float(value) for value in parsed]
        return []

    @staticmethod
    def _parse_dt(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    # ── Backward-compat: static method kept for any external callers ──────────

    @staticmethod
    def _matches_metadata_filters(
        *,
        metadata: dict,
        source_type_set: set[str] | None,
        wanted_tenant: object,
        wanted_access_scope: object,
        wanted_index_version: object,
        dt_from: datetime | None,
        dt_to: datetime | None,
    ) -> bool:
        passes_exact_filters = all(
            (
                source_type_set is None or metadata.get("source_type") in source_type_set,
                wanted_tenant is None or metadata.get("tenant") == wanted_tenant,
                wanted_access_scope is None or metadata.get("access_scope") == wanted_access_scope,
                wanted_index_version is None or metadata.get("index_version") == wanted_index_version,
            )
        )
        if not passes_exact_filters:
            return False
        if not (dt_from or dt_to):
            return True
        row_dt = VectorStore._parse_dt(metadata.get("updated_at"))
        if row_dt is None:
            return False
        return (dt_from is None or row_dt >= dt_from) and (dt_to is None or row_dt <= dt_to)
