"""
rag_engine/observability/ingestion_logger.py

Structured JSON logging for ingestion runs.
Every run produces:
  - Console output (JSON log lines)
  - File output: data/reports/ingestion_YYYYMMDD_HHMMSS_<run_id[:8]>.json
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rag_engine.schemas.ingestion_record import IngestionRunRecord

LOGGER = logging.getLogger(__name__)


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


class IngestionLogger:
    """
    Writes structured run summaries to console (JSON) and optionally to file.
    """

    def __init__(
        self,
        reports_dir: Path,
        log_to_file: bool = True,
    ) -> None:
        self._reports_dir = reports_dir
        self._log_to_file = log_to_file

    def log_run_summary(self, run: IngestionRunRecord) -> None:
        """Write a complete run summary after the run completes."""
        summary = self._build_summary(run)
        self._log_to_console(summary)
        if self._log_to_file:
            self._write_to_file(run, summary)

    def log_progress(
        self,
        *,
        run_id: str,
        docs_done: int,
        docs_total: int,
        chunks_so_far: int,
        message: str = "",
    ) -> None:
        """Emit a progress line during the run (every N documents)."""
        pct = (docs_done / max(docs_total, 1)) * 100
        line = {
            "event": "ingestion_progress",
            "run_id": run_id[:8],
            "docs_done": docs_done,
            "docs_total": docs_total,
            "pct": round(pct, 1),
            "chunks_so_far": chunks_so_far,
            "message": message,
            "ts": _utc_now_str(),
        }
        LOGGER.info(json.dumps(line))

    def _build_summary(self, run: IngestionRunRecord) -> dict:
        return {
            "event": "ingestion_run_complete",
            "run_id": run.run_id,
            "run_mode": run.run_mode,
            "status": run.status,
            "source_file": run.source_file,
            "index_version": run.index_version,

            "timing": {
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "duration_seconds": round(run.duration_seconds or 0, 1),
            },
            "documents": {
                "total_source_rows": run.total_source_rows,
                "processed": run.documents_processed,
                "skipped": run.documents_skipped,
                "failed": run.documents_failed,
            },
            "chunks": {
                "created": run.chunks_created,
                "updated": run.chunks_updated,
                "skipped": run.chunks_skipped,
                "failed": run.chunks_failed,
            },
            "embeddings": {
                "generated": run.embeddings_generated,
                "api_calls": run.embedding_api_calls,
                "tokens_used": run.embedding_tokens_used,
            },
            "distributions": {
                "automation_labels": run.automation_label_counts,
                "clients": run.client_counts,
                "top_query_types": dict(
                    sorted(run.query_type_counts.items(), key=lambda x: x[1], reverse=True)[:10]
                ),
            },
            "errors": {
                "count": run.error_count,
                "summary": run.error_summary,
                "warnings": run.warnings,
            },
        }

    def _log_to_console(self, summary: dict) -> None:
        status = summary.get("status", "UNKNOWN")
        level = logging.INFO if status in ("COMPLETED", "PARTIAL") else logging.ERROR
        LOGGER.log(level, json.dumps(summary, indent=None, default=str))

    def _write_to_file(self, run: IngestionRunRecord, summary: dict) -> None:
        try:
            self._reports_dir.mkdir(parents=True, exist_ok=True)
            filename = f"ingestion_{_utc_now_str()}_{run.run_id[:8]}.json"
            path = self._reports_dir / filename
            path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
            LOGGER.info("Ingestion report written to: %s", path)
        except OSError as exc:
            LOGGER.warning("Failed to write ingestion report file: %s", exc)
