"""
rag_engine/ingestion/pipeline.py

Main ingestion orchestrator for Phase B1.

Reads from preprocessed parquet/CSV → builds documents → chunks → dedup → embed → upsert.

Supports three run modes:
  full:   Ingest all 3,635 AI-usable tickets from unified_cleaned_dataset.parquet
  delta:  Ingest only tickets newer than last COMPLETED run (or explicit ticket_id list)
  gold:   Ingest only Gold-tier Q→A pairs from gold_dataset.csv (for eval/training)

Resume behavior:
  The pipeline uses streaming embed+upsert: each embedding batch is upserted to the DB
  immediately. If the run crashes mid-embedding, re-running will skip already-upserted
  chunks via the deduplication checker (content hash match → to_skip).

Document persistence:
  rag_ticket_documents is upserted before embedding (idempotent, on_conflict="id").
  Deterministic document UUIDs (UUID5) ensure idempotent re-runs.
  Chunks carry document_id so the parent-child FK is always populated.

Error policy:
  - Per-document errors are caught, logged, and counted (do NOT stop the run)
  - Embedding failures are isolated per batch (do NOT stop the run)
  - Circuit breaker in BatchEmbeddingProcessor stops repeated API hammering
  - If documents_failed / total_processed > 50%, the run is marked PARTIAL
  - Only a complete configuration failure (no source file, bad credentials) marks FAILED
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from pydantic import ValidationError

from rag_engine.config.rag_settings import RagEngineSettings
from rag_engine.chunking.ticket_chunker import TicketChunker
from rag_engine.document_builder.ticket_builder import TicketDocumentBuilder
from rag_engine.embedding.base import EmbeddingProvider
from rag_engine.embedding.batch_processor import BatchEmbeddingProcessor, BatchEmbeddingMetrics
from rag_engine.ingestion.deduplication import DeduplicationChecker
from rag_engine.ingestion.delta_tracker import DeltaTracker
from rag_engine.ingestion.schema_mapper import DatasetSchemaMapper
from rag_engine.observability.ingestion_logger import IngestionLogger
from rag_engine.schemas.chunk_schema import TicketChunk
from rag_engine.schemas.ingestion_record import IngestionErrorRecord, IngestionRunRecord
from rag_engine.schemas.ticket_document import RagTicketDocument, TicketSourceRow

LOGGER = logging.getLogger(__name__)


def _deterministic_document_id(ticket_id: str, index_version: str = "v1") -> str:
    """
    Version-scoped UUID5 document ID.

    Used ONLY for tickets not yet present in rag_ticket_documents.
    For tickets that already have a canonical document row, the DB-fetched
    id is used directly — see _fetch_existing_document_ids.

    Including index_version in the seed ensures different ingestion versions
    produce distinct document rows, consistent with the chunk ID scheme
    (freshdesk:{ticket_id}:{chunk_index}:{index_version}).
    """
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"freshdesk:doc:{ticket_id}:{index_version}",
    ))


class IngestionMode:
    FULL  = "full"
    DELTA = "delta"
    GOLD  = "gold"


class IngestionPipeline:
    """
    Orchestrates the full ingestion flow:
    Load → Validate → Build → Chunk → Upsert Docs → Dedup → Embed → Upsert Chunks → Log

    The embedding and chunk-upsert steps are streamed together:
    each batch is upserted immediately after embedding (resume support).
    """

    def __init__(
        self,
        settings: RagEngineSettings,
        supabase_client: Any,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._settings = settings
        self._client   = supabase_client
        self._embedding_provider = embedding_provider

        self._doc_builder   = TicketDocumentBuilder()
        self._schema_mapper = DatasetSchemaMapper()
        self._chunker = TicketChunker(
            max_chunk_words=settings.ticket_max_chunk_words,
            overlap_words=settings.ticket_overlap_words,
            min_query_body_chars=settings.min_query_body_chars,
            index_version=settings.index_version,
            chunk_target_tokens=settings.chunk_target_tokens,
            chunk_overlap_tokens=settings.chunk_overlap_tokens,
            max_input_tokens=settings.embedding_max_input_tokens,
            embedding_model=settings.embedding_model,
        )
        self._batch_embedder = BatchEmbeddingProcessor(
            provider=embedding_provider,
            batch_size=settings.embedding_batch_size,
            circuit_breaker_threshold=settings.embedding_circuit_breaker_threshold,
            circuit_breaker_cooldown_s=settings.embedding_circuit_breaker_cooldown_s,
            max_input_tokens=settings.embedding_max_input_tokens,
            embedding_model=settings.embedding_model,
        )
        self._deduplicator = DeduplicationChecker(
            supabase_client=supabase_client,
            chunks_table=settings.ticket_chunks_table,
        )
        self._delta_tracker = DeltaTracker(
            supabase_client=supabase_client,
            ingestion_logs_table=settings.ingestion_logs_table,
        )
        self._ingest_logger = IngestionLogger(
            reports_dir=settings.reports_dir,
            log_to_file=settings.log_to_file,
        )

    def run(
        self,
        *,
        mode: str = IngestionMode.FULL,
        source_path: Optional[Path] = None,
        since: Optional[datetime] = None,
        ticket_ids: Optional[list[str]] = None,
        dry_run: bool = False,
        triggered_by: str = "cli",
    ) -> IngestionRunRecord:
        """
        Execute an ingestion run.

        Args:
            mode:         'full' | 'delta' | 'gold'
            source_path:  Override default parquet path (useful for CI/testing)
            since:        For delta mode: only ingest tickets newer than this timestamp
            ticket_ids:   For delta mode: only ingest these specific tickets
            dry_run:      Build and chunk but skip embedding and upsert
            triggered_by: 'cli' | 'webhook' | 'scheduler'

        Returns:
            IngestionRunRecord with final metrics and status.
        """
        run = IngestionRunRecord(
            run_id=str(uuid.uuid4()),
            run_mode=mode,
            triggered_by=triggered_by,
            index_version=self._settings.index_version,
        )
        t_start = time.monotonic()
        LOGGER.info(
            "Ingestion run %s started | mode=%s | dry_run=%s | index_version=%s",
            run.run_id[:8], mode, dry_run, self._settings.index_version,
        )

        # ── Step 1: Write run start to DB ─────────────────────────────────────
        if not dry_run:
            self._write_run_record(run)

        try:
            # ── Step 2: Load source data ──────────────────────────────────────
            df = self._load_source(mode=mode, source_path=source_path)
            run.source_file = str(source_path) if source_path else "default"

            # ── Step 2a: Source-level ticket deduplication ────────────────────
            # The parquet source may contain duplicate ticket_id rows. Identical
            # ticket_ids produce identical deterministic chunk UUIDs via UUID5.
            # When two identical UUIDs land in the same PostgreSQL upsert batch,
            # Postgres raises "ON CONFLICT DO UPDATE command cannot affect row a
            # second time", rolling back the entire 50-row batch atomically and
            # causing collateral failures for innocent chunks in the same batch.
            # Dedup here so all downstream stages see only unique ticket_ids.
            _pre_dedup_count = len(df)
            df = df.drop_duplicates(subset=["ticket_id"], keep="first")
            _deduped_count = len(df)
            _dupes_removed = _pre_dedup_count - _deduped_count
            if _dupes_removed:
                LOGGER.info(
                    "Source deduplication removed %d duplicate ticket rows (%d → %d)",
                    _dupes_removed, _pre_dedup_count, _deduped_count,
                )
            run.total_source_rows = _deduped_count

            # ── Step 3: Apply delta filter ────────────────────────────────────
            if mode == IngestionMode.DELTA:
                effective_since = since or self._delta_tracker.get_last_completed_run_time(
                    index_version=self._settings.index_version
                )
                df = self._delta_tracker.filter_delta(
                    df, since=effective_since, ticket_ids=ticket_ids
                )
                LOGGER.info("Delta filter: %d rows to process", len(df))

            if df.empty:
                LOGGER.info("No rows to ingest after filtering. Run complete.")
                run.mark_completed(t_start, time.monotonic())
                return run

            # ── Step 3a: Pre-fetch existing document IDs ─────────────────────
            # Documents are canonical and shared across index versions.
            # For tickets already in rag_ticket_documents, the stored ID is the
            # authoritative FK target — a new version-specific UUID would conflict
            # with the UNIQUE(ticket_id, source_file) constraint.
            source_ticket_ids = [
                str(r.get("ticket_id", "")) for _, r in df.iterrows()
                if r.get("ticket_id")
            ]
            existing_doc_ids = self._fetch_existing_document_ids(source_ticket_ids)
            LOGGER.info(
                "Document pre-fetch: %d existing document rows found (of %d source tickets)",
                len(existing_doc_ids), len(source_ticket_ids),
            )

            # ── Step 4: Process each ticket (build + chunk) ───────────────────
            new_docs:   list[tuple[RagTicketDocument, str]] = []  # new tickets only
            all_chunks: list[TicketChunk] = []
            consecutive_errors = 0

            for _, row_data in df.iterrows():
                if consecutive_errors >= self._settings.max_batch_errors:
                    msg = (
                        f"Aborting run: {consecutive_errors} consecutive errors exceeded "
                        f"max_batch_errors={self._settings.max_batch_errors}"
                    )
                    LOGGER.error(msg)
                    run.mark_failed(msg, t_start, time.monotonic())
                    if not dry_run:
                        self._write_run_record(run)
                    return run

                try:
                    doc, doc_id, chunks = self._process_row(row_data, run, existing_doc_ids)
                    if doc is not None and doc.ticket_id not in existing_doc_ids:
                        new_docs.append((doc, doc_id))
                    all_chunks.extend(chunks)
                    consecutive_errors = 0
                    run.documents_processed += 1
                except Exception as exc:  # noqa: BLE001
                    consecutive_errors += 1
                    run.documents_failed += 1
                    ticket_id = str(row_data.get("ticket_id", "unknown"))
                    LOGGER.warning("Failed to process ticket %s: %s", ticket_id, exc)
                    error_rec = IngestionErrorRecord(
                        run_id=run.run_id,
                        ticket_id=ticket_id,
                        error_stage="document_build",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    if not dry_run:
                        self._write_error_record(error_rec)

            LOGGER.info(
                "Document phase: %d processed, %d skipped, %d failed | %d chunks",
                run.documents_processed, run.documents_skipped,
                run.documents_failed, len(all_chunks),
            )

            if not all_chunks:
                LOGGER.warning("No chunks produced. Check filtering and document builder.")
                run.mark_completed(t_start, time.monotonic())
                return run

            # ── Step 5: Upsert new documents only (rag_ticket_documents) ──────
            # Existing document rows are reused via existing_doc_ids — they are
            # the canonical source of truth and must not be mutated by this run.
            # Only tickets with no existing row receive a new document insert.
            docs_reused = len(existing_doc_ids)
            if not dry_run:
                if new_docs:
                    doc_rows = [
                        self._doc_to_db_row(doc, doc_id, run)
                        for doc, doc_id in new_docs
                    ]
                    doc_upserted, doc_failed = self._upsert_documents(doc_rows, run)
                    LOGGER.info(
                        "Document upsert: %d new inserted, %d existing reused, %d failed",
                        doc_upserted, docs_reused, doc_failed,
                    )
                else:
                    LOGGER.info(
                        "Document upsert: 0 new (all %d existing rows reused)",
                        docs_reused,
                    )
            else:
                LOGGER.info(
                    "[DRY RUN] Would insert %d new document rows; %d existing reused.",
                    len(new_docs), docs_reused,
                )

            # ── Step 6: Deduplication ─────────────────────────────────────────
            all_ticket_ids = list({c.ticket_id for c in all_chunks})
            existing_hashes = self._deduplicator.fetch_existing_hashes(
                all_ticket_ids, index_version=self._settings.index_version
            )
            to_insert, to_update, to_skip = self._deduplicator.classify_chunks(
                all_chunks, existing_hashes
            )
            run.chunks_skipped = len(to_skip)
            run.chunks_updated  = len(to_update)
            LOGGER.info(
                "Dedup: %d to insert, %d to update, %d unchanged (skipped)",
                len(to_insert), len(to_update), len(to_skip),
            )

            chunks_to_embed = to_insert + to_update
            if not chunks_to_embed:
                LOGGER.info("All chunks up-to-date; nothing to embed.")
                run.mark_completed(t_start, time.monotonic())
                return run

            # ── Step 7+8: Streaming embed + upsert chunks ─────────────────────
            # Each batch is upserted immediately after embedding.
            # Resume-on-crash: re-running will dedup-skip already-upserted chunks.
            if not dry_run:
                self._embed_and_upsert_streaming(chunks_to_embed, run)
            else:
                LOGGER.info(
                    "[DRY RUN] Would embed and upsert %d chunks.", len(chunks_to_embed)
                )

            # ── Step 9: Distribution metrics ──────────────────────────────────
            run.automation_label_counts = dict(Counter(
                c.automation_label.value for c in all_chunks
            ))
            run.client_counts = dict(Counter(c.client for c in all_chunks))
            run.query_type_counts = dict(Counter(
                c.query_type for c in all_chunks if c.query_type
            ))

            run.mark_completed(t_start, time.monotonic())

        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "Ingestion run %s failed with unhandled error: %s",
                run.run_id[:8], exc,
            )
            run.mark_failed(str(exc)[:500], t_start, time.monotonic())

        finally:
            if not dry_run:
                self._write_run_record(run)
            self._ingest_logger.log_run_summary(run)

        return run

    # ── Private: document persistence ─────────────────────────────────────────

    def _doc_to_db_row(
        self,
        doc: RagTicketDocument,
        document_id: str,
        run: IngestionRunRecord,
    ) -> dict:
        """Serialize a RagTicketDocument to the rag_ticket_documents row shape."""
        return {
            "id":                 document_id,
            "ticket_id":          doc.ticket_id,
            "source_file":        doc.source_file,
            "source_type":        doc.source_type,
            "client":             doc.client,
            "query_type":         doc.query_type,
            "issue_area":         doc.issue_area,
            "environment":        doc.environment,
            "priority":           doc.priority,
            "status":             doc.status,
            "automation_label":   doc.automation_label.value,
            "escalation_flag":    doc.escalation_flag,
            "has_rca":            doc.has_rca,
            "has_sop":            doc.has_sop,
            "sop_status":         doc.sop_status,
            "rca_quality_score":  doc.rca_quality_score,
            "subject":            doc.subject,
            "document_text":      doc.full_document_text,
            "issue_header_text":  doc.issue_header_text,
            "query_body_text":    doc.query_body_text,
            "resolution_rca_text": doc.resolution_rca_text,
            "content_hash":       doc.content_hash,
            "agent_interactions": doc.agent_interactions,
            "handling_time_mins": doc.handling_time_mins,
            "issue_recurrence":   doc.issue_recurrence,
            "resolution_status":  doc.resolution_status,
            "ticket_created_at":  (
                doc.ticket_created_at.isoformat() if doc.ticket_created_at else None
            ),
            "ticket_resolved_at": (
                doc.ticket_resolved_at.isoformat() if doc.ticket_resolved_at else None
            ),
            "ingestion_run_id":   run.run_id,
            "index_version":      self._settings.index_version,
        }

    def _upsert_documents(
        self,
        doc_rows: list[dict],
        run: IngestionRunRecord,
    ) -> tuple[int, int]:
        """Upsert document rows to rag_ticket_documents. Returns (upserted, failed)."""
        total_upserted = 0
        total_failed   = 0
        batch_size = self._settings.upsert_batch_size

        for i in range(0, len(doc_rows), batch_size):
            batch = doc_rows[i : i + batch_size]
            try:
                self._client.table(self._settings.ticket_documents_table).upsert(
                    batch, on_conflict="id"
                ).execute()
                total_upserted += len(batch)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(
                    "Document upsert batch %d failed (%d rows): %s",
                    i // batch_size + 1, len(batch), exc,
                )
                total_failed += len(batch)

        return total_upserted, total_failed

    def _fetch_existing_document_ids(
        self,
        ticket_ids: list[str],
    ) -> dict[str, str]:
        """
        Bulk-fetch existing document IDs from rag_ticket_documents by ticket_id.

        Returns {ticket_id: document_id} for all tickets that already have a
        canonical document row. These IDs are used as the FK target in v2+ chunks,
        preserving the UNIQUE(ticket_id, source_file) constraint and FK integrity
        without touching any existing document rows.

        Batches IN() queries at 500 ticket_ids each to respect PostgREST limits.
        On fetch failure, logs a warning and returns partial results — callers fall
        back to generating new version-agnostic IDs for the missing tickets.
        """
        if not ticket_ids:
            return {}

        result: dict[str, str] = {}
        _BATCH = 500  # safe upper bound for PostgREST IN() clause

        for i in range(0, len(ticket_ids), _BATCH):
            batch = ticket_ids[i : i + _BATCH]
            try:
                resp = (
                    self._client.table(self._settings.ticket_documents_table)
                    .select("id, ticket_id")
                    .in_("ticket_id", batch)
                    .execute()
                )
                for row in resp.data or []:
                    tid = row.get("ticket_id")
                    did = row.get("id")
                    if tid and did:
                        result[tid] = did
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "Failed to fetch existing document IDs (batch %d/%d): %s. "
                    "Affected tickets will attempt new document inserts.",
                    i // _BATCH + 1,
                    -(-len(ticket_ids) // _BATCH),
                    exc,
                )

        return result

    # ── Private: streaming embed + upsert ─────────────────────────────────────

    def _embed_and_upsert_streaming(
        self,
        chunks: list[TicketChunk],
        run: IngestionRunRecord,
    ) -> None:
        """
        Embed chunks in batches; upsert each batch immediately after embedding.
        Updates run metrics in-place.
        """
        upsert_failed_total = 0

        def _on_batch_embedded(batch: list[TicketChunk]) -> None:
            nonlocal upsert_failed_total
            embedded = [c for c in batch if c.embedding is not None]
            if not embedded:
                return
            upserted, failed = self._upsert_chunks(embedded, run)
            run.chunks_created += upserted
            upsert_failed_total += failed
            if failed:
                run.chunks_failed += failed

        try:
            embed_metrics = self._batch_embedder.embed_chunks(
                chunks,
                on_batch_embedded=_on_batch_embedded,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "Embedding phase encountered an unhandled error: %s. "
                "Chunks embedded before the error are already upserted. "
                "Re-running will resume from where this crashed.",
                exc,
            )
            run.documents_failed += 1
            return

        run.embeddings_generated  = embed_metrics.chunks_embedded
        run.chunks_failed        += embed_metrics.chunks_failed
        run.embedding_api_calls   = embed_metrics.api_calls
        run.embedding_tokens_used = embed_metrics.total_tokens

        if embed_metrics.chunks_invalid:
            LOGGER.warning(
                "%d chunks skipped by pre-embed validation (empty, oversized, or repetitive).",
                embed_metrics.chunks_invalid,
            )

        missed = [c for c in chunks if c.embedding is None]
        if missed:
            LOGGER.warning(
                "%d chunks have no embedding (failed batches or validation). "
                "Re-running will retry them via natural dedup miss.",
                len(missed),
            )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_source(self, mode: str, source_path: Optional[Path]) -> pd.DataFrame:
        if source_path:
            path = source_path
        elif mode == IngestionMode.GOLD:
            path = self._settings.processed_data_dir / "gold_dataset.csv"
        else:
            parquet_path = self._settings.processed_data_dir / "unified_cleaned_dataset.parquet"
            csv_path     = self._settings.processed_data_dir / "unified_cleaned_dataset.csv"
            path = parquet_path if parquet_path.exists() else csv_path

        if not path.exists():
            raise FileNotFoundError(
                f"Source file not found: {path}. "
                "Run the preprocessing pipeline first (dataset_pipeline/run.py)."
            )

        LOGGER.info("Loading source: %s", path)
        if str(path).endswith(".parquet"):
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, low_memory=False)

        LOGGER.info("Loaded %d rows from %s", len(df), path.name)
        return df

    def _process_row(
        self,
        row_data: Any,
        run: IngestionRunRecord,
        existing_doc_ids: dict[str, str],
    ) -> tuple[Optional[RagTicketDocument], str, list[TicketChunk]]:
        """
        Single DataFrame row → TicketSourceRow → RagTicketDocument → list[TicketChunk].

        Returns (doc, document_id, chunks).
        doc is None when the row is skipped (ESCALATION or no usable content).
        document_id is the existing DB row's id if the ticket is already in
        rag_ticket_documents, or a freshly generated version-agnostic UUID5 otherwise.
        """
        import math

        # Normalize row to dict
        if hasattr(row_data, "to_dict"):
            raw = row_data.to_dict()
        else:
            raw = dict(row_data)

        # Normalize pandas NaN → None
        normalized: dict[str, Any] = {}
        for k, v in raw.items():
            try:
                normalized[k] = None if (isinstance(v, float) and math.isnan(v)) else v
            except (TypeError, ValueError):
                normalized[k] = v

        mapped = self._schema_mapper.map(normalized)

        try:
            source_row = TicketSourceRow.model_validate(mapped)
        except ValidationError as exc:
            raise ValueError(f"Schema validation failed: {exc}") from exc

        raw_ticket_id  = str(normalized.get("ticket_id", "unknown"))
        placeholder_id = (
            existing_doc_ids.get(raw_ticket_id)
            or _deterministic_document_id(raw_ticket_id)
        )

        if source_row.automation_label == "ESCALATION":
            run.documents_skipped += 1
            return None, placeholder_id, []

        doc = self._doc_builder.build(source_row)
        if doc is None:
            run.documents_skipped += 1
            return None, placeholder_id, []

        document_id = (
            existing_doc_ids.get(doc.ticket_id)
            or _deterministic_document_id(doc.ticket_id)
        )
        chunks = self._chunker.chunk(
            doc,
            document_id=document_id,
            ingestion_run_id=run.run_id,
        )
        return doc, document_id, chunks

    def _upsert_chunks(
        self,
        chunks: list[TicketChunk],
        run: IngestionRunRecord,
    ) -> tuple[int, int]:
        """Upsert chunks in batches; return (upserted_count, failed_count)."""
        total_upserted = 0
        total_failed   = 0
        batch_size = self._settings.upsert_batch_size

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            rows  = [c.to_db_row() for c in batch]
            try:
                self._client.table(self._settings.ticket_chunks_table).upsert(rows).execute()
                total_upserted += len(batch)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(
                    "Upsert batch %d failed (%d chunks): %s",
                    i // batch_size + 1, len(batch), exc,
                )
                total_failed += len(batch)
                for chunk in batch:
                    error_rec = IngestionErrorRecord(
                        run_id=run.run_id,
                        ticket_id=chunk.ticket_id,
                        error_stage="upsert",
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:500],
                        error_detail={"chunk_index": chunk.chunk_index},
                    )
                    self._write_error_record(error_rec)

        return total_upserted, total_failed

    def _write_run_record(self, run: IngestionRunRecord) -> None:
        try:
            self._client.table(self._settings.ingestion_logs_table).upsert(
                run.to_db_row(), on_conflict="run_id"
            ).execute()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to write ingestion run record: %s", exc)

    def _write_error_record(self, error: IngestionErrorRecord) -> None:
        try:
            self._client.table(self._settings.ingestion_errors_table).insert(
                error.to_db_row()
            ).execute()
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Failed to write error record: %s", exc)
