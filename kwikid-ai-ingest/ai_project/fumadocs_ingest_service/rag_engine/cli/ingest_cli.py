"""
rag_engine/cli/ingest_cli.py

Ingestion CLI for Phase B1.

Usage:
    # Full ingest from default parquet
    python -m rag_engine.cli.ingest_cli --mode full

    # Full ingest from custom path
    python -m rag_engine.cli.ingest_cli --mode full --source /path/to/unified_cleaned_dataset.parquet

    # Delta ingest (only new tickets since last run)
    python -m rag_engine.cli.ingest_cli --mode delta

    # Delta ingest since specific date
    python -m rag_engine.cli.ingest_cli --mode delta --since 2026-05-01

    # Ingest only Gold-tier Q→A pairs
    python -m rag_engine.cli.ingest_cli --mode gold

    # Dry run (build + chunk, skip embedding + upsert)
    python -m rag_engine.cli.ingest_cli --mode full --dry-run

    # Ingest specific ticket IDs (for manual re-ingest)
    python -m rag_engine.cli.ingest_cli --mode delta --tickets 12345,67890

    # Check status of last N runs
    python -m rag_engine.cli.ingest_cli --status
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _build_embedding_provider(settings):
    """Build the appropriate embedding provider from existing app settings."""
    from app.config import get_settings as get_app_settings
    app_settings = get_app_settings()

    from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
    return OpenAIEmbeddingProvider(
        api_key=app_settings.embedding_api_key,
        model=app_settings.embedding_model,
        base_url=app_settings.embedding_base_url,
        dimensions=app_settings.embedding_dimensions,
        max_retries=settings.embedding_max_retries,
        retry_base_delay_s=settings.embedding_retry_base_delay_s,
        retry_max_delay_s=settings.embedding_retry_max_delay_s,
        # Separate timeouts — the key resilience addition
        connect_timeout_s=settings.embedding_connect_timeout_s,
        read_timeout_s=settings.embedding_read_timeout_s,
        write_timeout_s=settings.embedding_write_timeout_s,
        pool_timeout_s=settings.embedding_pool_timeout_s,
    )


def cmd_ingest(args: argparse.Namespace) -> int:
    """Execute an ingestion run."""
    from rag_engine.config.rag_settings import get_rag_settings
    from rag_engine.database.supabase_client import build_supabase_client_from_settings
    from rag_engine.ingestion.pipeline import IngestionPipeline, IngestionMode

    settings = get_rag_settings()
    _setup_logging(settings.log_level)

    LOGGER = logging.getLogger("ingest_cli")
    LOGGER.info("Phase B1 Ingestion CLI starting...")
    LOGGER.info("Mode: %s | Dry run: %s", args.mode, args.dry_run)

    # Validate mode
    valid_modes = {IngestionMode.FULL, IngestionMode.DELTA, IngestionMode.GOLD}
    if args.mode not in valid_modes:
        LOGGER.error("Invalid mode '%s'. Must be one of: %s", args.mode, valid_modes)
        return 1

    # Build clients
    try:
        supabase_client    = build_supabase_client_from_settings()
        embedding_provider = _build_embedding_provider(settings)
    except Exception as exc:
        LOGGER.error("Failed to initialize clients: %s", exc)
        LOGGER.error("Check your .env file has SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY")
        return 1

    # Parse arguments
    source_path = Path(args.source) if args.source else None

    since: datetime | None = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            LOGGER.error("Invalid --since date format. Use YYYY-MM-DD or ISO format.")
            return 1

    ticket_ids: list[str] | None = None
    if args.tickets:
        ticket_ids = [t.strip() for t in args.tickets.split(",") if t.strip()]
        LOGGER.info("Explicit ticket IDs: %s", ticket_ids)

    # Run pipeline — use context manager to ensure HTTP client is properly closed
    pipeline = IngestionPipeline(
        settings=settings,
        supabase_client=supabase_client,
        embedding_provider=embedding_provider,
    )

    try:
        run = pipeline.run(
            mode=args.mode,
            source_path=source_path,
            since=since,
            ticket_ids=ticket_ids,
            dry_run=args.dry_run,
            triggered_by="cli",
        )
    finally:
        # Always close the HTTP client to release connections
        if hasattr(embedding_provider, "close"):
            embedding_provider.close()

    # Print final summary
    print("\n" + "=" * 60)
    print(f"INGESTION RUN COMPLETE")
    print("=" * 60)
    print(f"Run ID:      {run.run_id}")
    print(f"Status:      {run.status}")
    print(f"Mode:        {run.run_mode}")
    print(f"Duration:    {run.duration_seconds:.1f}s")
    print(f"")
    print(f"Documents:")
    print(f"  Source rows:    {run.total_source_rows}")
    print(f"  Processed:      {run.documents_processed}")
    print(f"  Skipped:        {run.documents_skipped}")
    print(f"  Failed:         {run.documents_failed}")
    print(f"")
    print(f"Chunks:")
    print(f"  Created:        {run.chunks_created}")
    print(f"  Skipped (dup):  {run.chunks_skipped}")
    print(f"  Failed:         {run.chunks_failed}")
    print(f"")
    print(f"Embeddings:")
    print(f"  Generated:      {run.embeddings_generated}")
    print(f"  API calls:      {run.embedding_api_calls}")
    print(f"  Tokens used:    {run.embedding_tokens_used}")
    print(f"")
    if run.automation_label_counts:
        print(f"Automation labels: {json.dumps(run.automation_label_counts)}")
    if run.client_counts:
        print(f"Client distribution: {json.dumps(run.client_counts)}")
    if run.error_count:
        print(f"\nERRORS: {run.error_count}")
        if run.error_summary:
            print(f"  {run.error_summary}")
    print("=" * 60)

    return 0 if run.status in ("COMPLETED", "PARTIAL") else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show status of recent ingestion runs."""
    from rag_engine.config.rag_settings import get_rag_settings
    from rag_engine.database.supabase_client import build_supabase_client_from_settings

    _setup_logging("WARNING")
    settings = get_rag_settings()

    try:
        client = build_supabase_client_from_settings()
        response = (
            client.table(settings.ingestion_logs_table)
            .select(
                "run_id, run_mode, status, documents_processed, chunks_created, "
                "embeddings_generated, duration_seconds, started_at, error_count"
            )
            .order("started_at", desc=True)
            .limit(args.limit)
            .execute()
        )
        rows = response.data or []
    except Exception as exc:
        print(f"Failed to fetch run history: {exc}")
        return 1

    if not rows:
        print("No ingestion runs found. Run 'python -m rag_engine.cli.ingest_cli --mode full' first.")
        return 0

    print(f"\n{'Run ID':<10} {'Mode':<8} {'Status':<10} {'Docs':<6} {'Chunks':<8} {'Embeds':<8} {'Duration':<10} {'Started At'}")
    print("-" * 90)
    for row in rows:
        run_id_short = str(row.get("run_id", ""))[:8]
        started = str(row.get("started_at", ""))[:16]
        dur = f"{float(row.get('duration_seconds') or 0):.1f}s"
        errors = row.get("error_count", 0)
        status = row.get("status", "?")
        if errors:
            status = f"{status}({errors}err)"
        print(
            f"{run_id_short:<10} "
            f"{str(row.get('run_mode','')):<8} "
            f"{status:<10} "
            f"{row.get('documents_processed',0):<6} "
            f"{row.get('chunks_created',0):<8} "
            f"{row.get('embeddings_generated',0):<8} "
            f"{dur:<10} "
            f"{started}"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase B1 — KwikID RAG Ingestion CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--mode",
        choices=["full", "delta", "gold"],
        default="full",
        help="Ingestion mode: full=all tickets, delta=new only, gold=gold Q→A pairs"
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Override source file path (default: data/processed/unified_cleaned_dataset.parquet)"
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="For delta mode: ingest tickets created after this date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--tickets",
        type=str,
        default=None,
        help="Comma-separated ticket IDs for explicit re-ingestion"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Build and chunk documents but skip embedding and upsert"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        default=False,
        help="Show recent ingestion run history instead of running"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of recent runs to show with --status (default: 10)"
    )

    args = parser.parse_args()

    if args.status:
        sys.exit(cmd_status(args))
    else:
        sys.exit(cmd_ingest(args))


if __name__ == "__main__":
    main()
