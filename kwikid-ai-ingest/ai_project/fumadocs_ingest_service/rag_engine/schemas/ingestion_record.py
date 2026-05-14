"""
rag_engine/schemas/ingestion_record.py

Data models for ingestion run tracking (rag_ingestion_logs, rag_ingestion_errors).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class IngestionRunRecord:
    """Represents one ingestion run. Maps to rag_ingestion_logs."""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_mode: str = "full"              # full | delta | gold | sop
    source_file: Optional[str] = None
    triggered_by: str = "cli"
    index_version: str = "v1"

    # Timing
    started_at: str = field(default_factory=_now_iso)
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None

    # Metrics (accumulated during run)
    total_source_rows: int = 0
    documents_processed: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    chunks_created: int = 0
    chunks_updated: int = 0
    chunks_skipped: int = 0
    chunks_failed: int = 0
    embeddings_generated: int = 0
    embedding_api_calls: int = 0
    embedding_tokens_used: int = 0

    # Distributions (filled at end of run)
    automation_label_counts: dict = field(default_factory=dict)
    client_counts: dict = field(default_factory=dict)
    query_type_counts: dict = field(default_factory=dict)

    # Status
    status: str = "RUNNING"            # RUNNING | COMPLETED | FAILED | PARTIAL
    error_count: int = 0
    error_summary: Optional[str] = None
    warnings: list = field(default_factory=list)

    def mark_completed(self, started_ts: float, current_ts: float) -> None:
        self.status = "COMPLETED" if self.documents_failed == 0 else "PARTIAL"
        self.completed_at = _now_iso()
        self.duration_seconds = current_ts - started_ts

    def mark_failed(self, error: str, started_ts: float, current_ts: float) -> None:
        self.status = "FAILED"
        self.completed_at = _now_iso()
        self.duration_seconds = current_ts - started_ts
        self.error_summary = error
        self.error_count += 1

    def to_db_row(self) -> dict:
        return {
            "run_id": self.run_id,
            "run_mode": self.run_mode,
            "source_file": self.source_file,
            "triggered_by": self.triggered_by,
            "index_version": self.index_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "total_source_rows": self.total_source_rows,
            "documents_processed": self.documents_processed,
            "documents_skipped": self.documents_skipped,
            "documents_failed": self.documents_failed,
            "chunks_created": self.chunks_created,
            "chunks_updated": self.chunks_updated,
            "chunks_skipped": self.chunks_skipped,
            "chunks_failed": self.chunks_failed,
            "embeddings_generated": self.embeddings_generated,
            "embedding_api_calls": self.embedding_api_calls,
            "embedding_tokens_used": self.embedding_tokens_used,
            "automation_label_counts": self.automation_label_counts,
            "client_counts": self.client_counts,
            "query_type_counts": self.query_type_counts,
            "status": self.status,
            "error_count": self.error_count,
            "error_summary": self.error_summary,
            "warnings": self.warnings,
        }


@dataclass
class IngestionErrorRecord:
    """One error event during ingestion. Maps to rag_ingestion_errors."""
    run_id: str
    error_stage: str                    # document_build | chunking | embedding | upsert
    error_type: str                     # Exception class name
    error_message: str
    ticket_id: Optional[str] = None
    error_detail: dict = field(default_factory=dict)

    def to_db_row(self) -> dict:
        return {
            "run_id": self.run_id,
            "ticket_id": self.ticket_id,
            "error_stage": self.error_stage,
            "error_type": self.error_type,
            "error_message": (
                self.error_message[:1997] + "..." if len(self.error_message) > 2000
                else self.error_message
            ),
            "error_detail": self.error_detail,
        }
