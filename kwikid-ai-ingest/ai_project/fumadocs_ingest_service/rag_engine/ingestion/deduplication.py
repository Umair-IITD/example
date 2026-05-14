"""
rag_engine/ingestion/deduplication.py

Hash-based deduplication for the ingestion pipeline.

Strategy:
  Before upserting chunks, we check the existing content_hash in the DB.
  If the hash is identical → skip (content unchanged, no re-embedding needed).
  If the hash differs → update (content changed, re-embed).
  If not found → insert (new chunk).

  This avoids both:
  - Wasting embedding API calls on unchanged content
  - Creating duplicate rows for the same ticket

  Deterministic UUIDs ensure upserts are always safe even without pre-checking,
  but the hash check avoids the unnecessary API call cost.
"""
from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


class DeduplicationChecker:
    """
    Queries rag_ticket_chunks for existing content hashes.
    Used by the ingestion pipeline to decide: skip | update | insert.
    """

    def __init__(
        self,
        supabase_client: Any,           # supabase.Client
        chunks_table: str = "rag_ticket_chunks",
    ) -> None:
        self._client = supabase_client
        self._chunks_table = chunks_table

    def fetch_existing_hashes(
        self,
        ticket_ids: list[str],
        index_version: str = "v1",
    ) -> dict[str, dict[int, str]]:
        """
        Fetch existing content hashes for a batch of ticket_ids.

        Returns:
            {ticket_id: {chunk_index: content_hash, ...}, ...}

        Used to determine per-chunk skip/update/insert status without
        hitting the DB once per chunk.
        """
        if not ticket_ids:
            return {}

        try:
            response = (
                self._client.table(self._chunks_table)
                .select("ticket_id, chunk_index, content_hash")
                .in_("ticket_id", ticket_ids)
                .eq("index_version", index_version)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Failed to fetch existing hashes for %d tickets: %s. "
                "Proceeding without dedup (all chunks will be upserted).",
                len(ticket_ids), exc
            )
            return {}

        result: dict[str, dict[int, str]] = {}
        for row in response.data or []:
            tid = row.get("ticket_id", "")
            idx = row.get("chunk_index")
            h = row.get("content_hash", "")
            if tid and isinstance(idx, int) and h:
                result.setdefault(tid, {})[idx] = h

        return result

    def classify_chunks(
        self,
        chunks: list,                   # list[TicketChunk]
        existing_hashes: dict[str, dict[int, str]],
    ) -> tuple[list, list, list]:
        """
        Split chunks into three groups:
          - to_insert:  no existing hash for this ticket+index
          - to_update:  existing hash differs from current
          - to_skip:    existing hash matches; content unchanged

        Returns: (to_insert, to_update, to_skip)
        """
        to_insert: list = []
        to_update: list = []
        to_skip: list = []

        for chunk in chunks:
            ticket_hashes = existing_hashes.get(chunk.ticket_id, {})
            existing_hash = ticket_hashes.get(chunk.chunk_index)

            if existing_hash is None:
                to_insert.append(chunk)
            elif existing_hash == chunk.content_hash:
                to_skip.append(chunk)
            else:
                to_update.append(chunk)

        return to_insert, to_update, to_skip
