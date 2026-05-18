"""
rag_engine/ingestion/sop_pipeline.py

SOP (Standard Operating Procedure) ingestion pipeline for Phase B1.

SOPs are the highest-quality retrieval source. When an SOP exists for a query
type, the +0.15 boosted_score in match_all_b1_sources ensures SOPs rank above
individual ticket anecdotes.

Ingestion sources:
  - Markdown files with YAML frontmatter (primary: Fumadocs/Bitbucket repo)
  - Knowledge cards (created via /train endpoint or directly)

Flow per SOP:
  1. Parse markdown → RagSopDocument (via SopDocumentBuilder)
  2. Check rag_sop_library for existing content_hash
  3a. New SOP:       upsert library record → embed sections → insert chunks
  3b. Unchanged SOP: skip (idempotent)
  3c. Updated SOP:   bump version → delete old chunks → re-embed → insert new chunks

Idempotency:
  - Chunk IDs are deterministic: uuid5(NAMESPACE_URL, f"sop_chunk:{sop_id}:{chunk_index}:{sop_version}")
  - Re-running on unchanged SOPs produces 0 DB writes

Tenant scope (enforced in rag_sop_chunks.clients):
  - clients=[]              → global SOP (retrieved by all tenants)
  - clients=['unity_bank']  → tenant-specific SOP

Version bump on update:
  When content changes, the old chunk rows are DELETED and new rows are inserted
  with the bumped sop_version. This prevents stale sections from leaking into
  retrieval alongside the new ones.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rag_engine.config.rag_settings import RagEngineSettings
from rag_engine.document_builder.sop_builder import RagSopDocument, SopDocumentBuilder
from rag_engine.embedding.base import EmbeddingProvider
from rag_engine.utils.tokens import count_tokens, truncate_to_token_limit

LOGGER = logging.getLogger(__name__)

# Hard cap per SOP section — sections should be self-contained; truncate rather than split
_MAX_SOP_CHUNK_TOKENS = 7_000


def _sop_chunk_id(sop_id: str, chunk_index: int, sop_version: int) -> str:
    """Deterministic UUID5 for a SOP chunk position. Changing sop_version invalidates old IDs."""
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"sop_chunk:{sop_id}:{chunk_index}:{sop_version}",
    ))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class SopIngestionResult:
    """Aggregated metrics for one ingestion run."""
    sops_processed:      int   = 0
    sops_skipped:        int   = 0   # unchanged content — no DB writes
    sops_inserted:       int   = 0   # new SOPs
    sops_updated:        int   = 0   # existing SOPs with changed content
    sops_failed:         int   = 0
    chunks_created:      int   = 0
    embeddings_generated: int  = 0
    duration_s:          float = 0.0

    def log_summary(self) -> None:
        LOGGER.info(
            "SOP ingestion: %d inserted, %d updated, %d skipped, %d failed | "
            "%d chunks embedded | %.1fs",
            self.sops_inserted, self.sops_updated, self.sops_skipped,
            self.sops_failed, self.embeddings_generated, self.duration_s,
        )


class SopIngestionPipeline:
    """
    Orchestrates SOP ingestion from markdown files or knowledge cards.

    All public methods return SopIngestionResult — they never raise.
    Per-SOP errors are caught and counted in result.sops_failed.

    Usage:
        pipeline = SopIngestionPipeline(settings, supabase_client, embedder)
        result = pipeline.ingest_directory(Path("data/sop/"))
        result.log_summary()
    """

    def __init__(
        self,
        settings: RagEngineSettings,
        supabase_client: Any,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._settings = settings
        self._client   = supabase_client
        self._embedder = embedding_provider
        self._builder  = SopDocumentBuilder()

    # ── Public API ─────────────────────────────────────────────────────────────

    def ingest_directory(
        self,
        sop_dir: Path,
        *,
        default_clients: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> SopIngestionResult:
        """Scan a directory for .md files and ingest each as a SOP."""
        total   = SopIngestionResult()
        t_start = time.monotonic()

        md_files = sorted(sop_dir.glob("*.md"))
        if not md_files:
            LOGGER.warning("No .md files found in %s", sop_dir)
            total.duration_s = time.monotonic() - t_start
            return total

        LOGGER.info("SOP ingestion: %d files in %s", len(md_files), sop_dir)
        for path in md_files:
            r = self.ingest_file(path, default_clients=default_clients, dry_run=dry_run)
            total.sops_processed       += r.sops_processed
            total.sops_skipped         += r.sops_skipped
            total.sops_inserted        += r.sops_inserted
            total.sops_updated         += r.sops_updated
            total.sops_failed          += r.sops_failed
            total.chunks_created       += r.chunks_created
            total.embeddings_generated += r.embeddings_generated

        total.duration_s = time.monotonic() - t_start
        total.log_summary()
        return total

    def ingest_file(
        self,
        file_path: Path,
        *,
        commit_sha: Optional[str] = None,
        default_clients: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> SopIngestionResult:
        """Parse and ingest a single markdown SOP file."""
        sop_doc = self._builder.build_from_markdown(
            file_path,
            commit_sha=commit_sha,
            default_clients=default_clients,
        )
        if sop_doc is None:
            LOGGER.warning("Could not parse SOP from %s", file_path)
            return SopIngestionResult(sops_processed=1, sops_failed=1)

        return self._process_sop(sop_doc, dry_run=dry_run)

    def ingest_knowledge_card(
        self,
        title: str,
        content: str,
        query_type: Optional[str] = None,
        clients: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> SopIngestionResult:
        """Ingest a manually authored knowledge card as a SOP."""
        sop_doc = self._builder.build_from_knowledge_card(
            title=title,
            content=content,
            query_type=query_type,
            clients=clients,
        )
        return self._process_sop(sop_doc, dry_run=dry_run)

    def list_ingested(self) -> list[dict]:
        """Return all SOPs currently in rag_sop_library (active + inactive)."""
        try:
            resp = (
                self._client.table(self._settings.sop_library_table)
                .select(
                    "sop_id, title, query_type, clients, version, "
                    "content_hash, ingested_at, is_active, source"
                )
                .order("ingested_at", desc=True)
                .execute()
            )
            return resp.data or []
        except Exception as exc:
            LOGGER.error("Failed to list SOPs: %s", exc)
            return []

    def deactivate_sop(self, sop_id: str) -> bool:
        """Soft-delete a SOP (is_active=False). Retrieval RPC ignores inactive SOPs."""
        try:
            self._client.table(self._settings.sop_library_table).update(
                {"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()}
            ).eq("sop_id", sop_id).execute()
            LOGGER.info("Deactivated SOP: %s", sop_id)
            return True
        except Exception as exc:
            LOGGER.error("Failed to deactivate SOP %s: %s", sop_id, exc)
            return False

    # ── Private: orchestration ─────────────────────────────────────────────────

    def _process_sop(self, sop_doc: RagSopDocument, *, dry_run: bool) -> SopIngestionResult:
        result  = SopIngestionResult(sops_processed=1)
        t_start = time.monotonic()

        try:
            existing = self._fetch_existing_sop(sop_doc.sop_id)

            if existing is not None:
                if existing["content_hash"] == sop_doc.content_hash:
                    LOGGER.info(
                        "SOP unchanged (hash match): '%s' — skipping",
                        sop_doc.title,
                    )
                    result.sops_skipped = 1
                    result.duration_s   = time.monotonic() - t_start
                    return result

                # Content changed: bump version for new chunk IDs
                new_version    = existing["version"] + 1
                sop_doc.version = new_version
                LOGGER.info(
                    "SOP updated: '%s' v%d → v%d",
                    sop_doc.title, existing["version"], new_version,
                )
                is_update = True
            else:
                LOGGER.info("New SOP: '%s' (%s)", sop_doc.title, sop_doc.sop_id)
                is_update = False

            # Build chunk records (with token-safety enforcement)
            chunks = self._build_chunk_records(sop_doc)
            if not chunks:
                LOGGER.warning("No chunks produced for SOP '%s' — skipping", sop_doc.title)
                result.sops_failed = 1
                result.duration_s  = time.monotonic() - t_start
                return result

            if dry_run:
                action = "update" if is_update else "insert"
                LOGGER.info(
                    "[DRY RUN] Would %s SOP '%s' with %d chunks",
                    action, sop_doc.title, len(chunks),
                )
                result.sops_inserted   = int(not is_update)
                result.sops_updated    = int(is_update)
                result.chunks_created  = len(chunks)
                result.duration_s      = time.monotonic() - t_start
                return result

            # 1. Upsert library record first (chunks FK references sop_id)
            self._upsert_library_record(sop_doc, is_update=is_update)

            # 2. Delete old chunk rows on update to avoid stale sections in retrieval
            if is_update:
                self._delete_sop_chunks(sop_doc.sop_id)

            # 3. Embed sections + upsert chunk rows
            embedded = self._embed_and_upsert_chunks(chunks)

            # 4. Mark library record as fully ingested
            self._mark_ingested(sop_doc.sop_id)

            result.chunks_created       = embedded
            result.embeddings_generated = embedded
            result.sops_inserted        = int(not is_update)
            result.sops_updated         = int(is_update)

        except Exception as exc:
            LOGGER.error(
                "SOP ingestion failed for '%s' (%s): %s",
                sop_doc.title, sop_doc.sop_id, exc, exc_info=True,
            )
            result.sops_failed = 1

        result.duration_s = time.monotonic() - t_start
        return result

    # ── Private: Supabase helpers ──────────────────────────────────────────────

    def _fetch_existing_sop(self, sop_id: str) -> Optional[dict]:
        try:
            resp = (
                self._client.table(self._settings.sop_library_table)
                .select("sop_id, version, content_hash, is_active")
                .eq("sop_id", sop_id)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            return rows[0] if rows else None
        except Exception as exc:
            LOGGER.warning("Could not fetch existing SOP record for %s: %s", sop_id, exc)
            return None

    def _upsert_library_record(self, sop_doc: RagSopDocument, *, is_update: bool) -> None:
        now    = datetime.now(timezone.utc).isoformat()
        record = {
            "sop_id":        sop_doc.sop_id,
            "title":         sop_doc.title,
            "version":       sop_doc.version,
            "query_type":    sop_doc.query_type,
            "issue_area":    sop_doc.issue_area,
            "clients":       sop_doc.clients,
            "content":       sop_doc.content,
            "content_hash":  sop_doc.content_hash,
            "source":        sop_doc.source,
            "source_path":   sop_doc.source_path,
            "commit_sha":    sop_doc.commit_sha,
            "index_version": self._settings.index_version,
            "is_active":     True,
            "updated_at":    now,
        }
        if not is_update:
            record["created_at"] = now

        self._client.table(self._settings.sop_library_table).upsert(
            record, on_conflict="sop_id"
        ).execute()

    def _delete_sop_chunks(self, sop_id: str) -> None:
        """Remove all chunk rows for a SOP before re-inserting on version bump."""
        try:
            self._client.table(self._settings.sop_chunks_table).delete().eq(
                "sop_id", sop_id
            ).execute()
            LOGGER.debug("Deleted old chunks for SOP %s", sop_id)
        except Exception as exc:
            LOGGER.warning("Failed to delete old SOP chunks for %s: %s", sop_id, exc)

    def _mark_ingested(self, sop_id: str) -> None:
        try:
            self._client.table(self._settings.sop_library_table).update({
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }).eq("sop_id", sop_id).execute()
        except Exception as exc:
            LOGGER.warning("Failed to mark SOP %s as ingested: %s", sop_id, exc)

    # ── Private: chunk building + embedding ────────────────────────────────────

    def _build_chunk_records(self, sop_doc: RagSopDocument) -> list[dict]:
        """
        Convert each SOP section into a chunk dict ready for DB upsert.

        Token safety: SOP sections should fit in one chunk.
        If a section exceeds _MAX_SOP_CHUNK_TOKENS, it is truncated with a warning.
        Splitting is not done — SOP step groups must remain coherent.
        """
        embedding_model = self._settings.embedding_model
        chunks: list[dict] = []

        for idx, section in enumerate(sop_doc.sections):
            content = section.get("content", "").strip()
            heading = section.get("heading")

            if not content:
                continue

            # Token safety enforcement
            token_count = count_tokens(content, embedding_model)
            truncated   = False
            if token_count > _MAX_SOP_CHUNK_TOKENS:
                LOGGER.warning(
                    "SOP '%s' section %d has %d tokens — truncating to %d",
                    sop_doc.title, idx, token_count, _MAX_SOP_CHUNK_TOKENS,
                )
                content     = truncate_to_token_limit(content, _MAX_SOP_CHUNK_TOKENS, embedding_model)
                truncated   = True
                token_count = _MAX_SOP_CHUNK_TOKENS

            chunk_id     = _sop_chunk_id(sop_doc.sop_id, idx, sop_doc.version)
            content_hash = _sha256(content)

            chunks.append({
                "id":            chunk_id,
                "sop_id":        sop_doc.sop_id,
                "sop_version":   sop_doc.version,
                "chunk_index":   idx,
                "chunk_heading": heading,
                "chunk_type":    "SOP_STEPS",
                "content":       content,
                "word_count":    len(content.split()),
                "content_hash":  content_hash,
                "embedding":     None,          # filled during _embed_and_upsert_chunks
                "query_type":    sop_doc.query_type,
                "issue_area":    sop_doc.issue_area,
                "clients":       sop_doc.clients,
                "index_version": self._settings.index_version,
                # Internal-only fields — stripped before upsert
                "_truncated":    truncated,
                "_token_count":  token_count,
            })

        return chunks

    def _embed_and_upsert_chunks(self, chunks: list[dict]) -> int:
        """
        Embed SOP chunks in batches using embed_batch(), upsert to rag_sop_chunks.

        Returns the count of successfully embedded + upserted chunks.
        A failed embedding batch is logged but does not stop remaining batches.
        """
        batch_size    = self._settings.embedding_batch_size
        embedded_total = 0

        # Strip internal helpers before building the upsert row
        _DB_KEYS = {k for k in chunks[0] if not k.startswith("_")} if chunks else set()

        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start : batch_start + batch_size]
            texts = [c["content"] for c in batch]

            # Embed
            try:
                embed_result = self._embedder.embed_batch(texts)
                for chunk, embedding in zip(batch, embed_result.embeddings):
                    chunk["embedding"] = embedding
            except Exception as exc:
                LOGGER.error(
                    "SOP embedding batch %d failed (%d chunks): %s",
                    batch_start // batch_size + 1, len(batch), exc,
                )
                continue  # Remaining batches still attempted

            # Upsert only successfully embedded chunks
            rows = [
                {k: v for k, v in c.items() if k in _DB_KEYS}
                for c in batch
                if c.get("embedding") is not None
            ]
            if not rows:
                continue

            try:
                self._client.table(self._settings.sop_chunks_table).upsert(rows).execute()
                embedded_total += len(rows)
                LOGGER.debug(
                    "SOP batch %d: upserted %d chunks",
                    batch_start // batch_size + 1, len(rows),
                )
            except Exception as exc:
                LOGGER.error(
                    "SOP chunk upsert failed for batch %d: %s",
                    batch_start // batch_size + 1, exc,
                )

        return embedded_total
