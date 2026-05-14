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
            # Don't block ingestion on pre-check read timeouts; deterministic IDs still make upserts safe.
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
        Try a single bulk upsert first for throughput.
        If the network/database times out, fall back to per-record upserts with retries.
        This keeps Freshdesk ticket-ID ingests working even on intermittent Supabase latency.
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
        # Safe path: page through rows and delete matching IDs one-by-one.
        # This avoids PostgREST 400 errors from complex JSON filters or large in_(...) payloads.
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

    def match_documents(
        self,
        *,
        query_embedding: list[float],
        match_count: int,
        match_threshold: float = 0.0,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[dict]:
        # First try DB RPC for server-side vector search.
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
            thresholded = self._apply_similarity_threshold(response.data or [], match_threshold)
            return self._apply_metadata_filters(thresholded, metadata_filters)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "PGRST203" in msg or "Multiple Choices" in msg:
                response = (
                    self._client.rpc(
                        "match_documents",
                        {"query_embedding": query_embedding, "match_count": match_count},
                    )
                    .execute()
                )
                thresholded = self._apply_similarity_threshold(response.data or [], match_threshold)
                return self._apply_metadata_filters(thresholded, metadata_filters)
            if 'relation "public.kb_chunks" does not exist' in msg or 'relation "kb_chunks" does not exist' in msg:
                # Fallback path: compute cosine similarity in application layer
                # when DB RPC still points at old table definitions.
                return self._match_documents_locally(
                    query_embedding=query_embedding,
                    match_count=match_count,
                    match_threshold=match_threshold,
                    metadata_filters=metadata_filters,
                )
            raise

    def _match_documents_locally(
        self,
        *,
        query_embedding: list[float],
        match_count: int,
        match_threshold: float,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[dict]:
        page_size = 1000
        offset = 0
        rows: list[dict] = []
        while True:
            response = (
                self._client.table(self._table_name)
                .select("id,content,metadata,embedding")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            data = response.data or []
            rows.extend(data)
            if len(rows) >= self._local_fallback_max_rows:
                rows = rows[: self._local_fallback_max_rows]
                break
            if len(data) < page_size:
                break
            offset += page_size

        query_norm = self._l2_norm(query_embedding)
        if query_norm <= EPSILON:
            return []

        scored: list[dict] = []
        for row in rows:
            embedding = self._normalize_embedding(row.get("embedding"))
            if not embedding:
                continue
            similarity = self._cosine_similarity(query_embedding, embedding, query_norm)
            if similarity < match_threshold:
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
        filtered = self._apply_metadata_filters(scored, metadata_filters)
        return filtered[:match_count]

    def healthcheck(self) -> bool:
        response = self._client.table(self._table_name).select("id").limit(1).execute()
        return response.data is not None

    @staticmethod
    def _l2_norm(vector: list[float]) -> float:
        return math.sqrt(sum(float(v) * float(v) for v in vector))

    @classmethod
    def _cosine_similarity(cls, left: list[float], right: list[float], left_norm: float | None = None) -> float:
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
    def _apply_metadata_filters(rows: list[dict], metadata_filters: dict[str, object] | None) -> list[dict]:
        if not metadata_filters:
            return rows

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
            if VectorStore._matches_metadata_filters(
                metadata=metadata,
                source_type_set=source_type_set,
                wanted_tenant=wanted_tenant,
                wanted_access_scope=wanted_access_scope,
                wanted_index_version=wanted_index_version,
                dt_from=dt_from,
                dt_to=dt_to,
            ):
                filtered.append(row)
        return filtered

    @staticmethod
    def _apply_similarity_threshold(rows: list[dict], threshold: float) -> list[dict]:
        if threshold <= 0.0:
            return rows
        filtered: list[dict] = []
        for row in rows:
            similarity = float(row.get("similarity", 0.0))
            if similarity >= threshold:
                filtered.append(row)
        return filtered

    @staticmethod
    def _parse_dt(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

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

