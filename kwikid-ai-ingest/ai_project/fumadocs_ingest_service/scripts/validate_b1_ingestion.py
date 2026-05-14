"""
scripts/validate_b1_ingestion.py

Phase B1-B: Ingestion Validation Script

Validates the complete ingestion pipeline end-to-end:
  1. Pre-flight checks (environment, source data, migrations)
  2. Dry-run pipeline (offline, no credentials needed)
  3. Live ingestion execution (requires credentials + --live flag)
  4. Post-ingestion DB validation (row counts, embedding dims, metadata)
  5. Distribution analysis (tenant, automation_label, RCA tier, chunk_type)
  6. Deduplication idempotency check (re-run leaves counts unchanged)
  7. Report generation

Run modes:
    python scripts/validate_b1_ingestion.py                # offline dry-run only
    python scripts/validate_b1_ingestion.py --live         # run actual ingestion + validate DB
    python scripts/validate_b1_ingestion.py --live --report  # + write markdown report
    python scripts/validate_b1_ingestion.py --status       # check last ingestion run status from DB
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

# Force UTF-8 output on Windows so Unicode symbols render correctly
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Styling helpers ────────────────────────────────────────────────────────────
OK   = "[OK]"
FAIL = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"


def _section(title: str) -> None:
    print(f"\n{'='*62}")
    print(f"  {title}")
    print(f"{'='*62}")


def _check(label: str, passed: bool, detail: str = "") -> tuple[str, bool]:
    icon = OK if passed else FAIL
    msg  = f"  {icon}  {label}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)
    return label, passed


def _warn(label: str, detail: str = "") -> None:
    msg = f"  {WARN}  {label}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)


def _info(label: str, detail: str = "") -> None:
    msg = f"  {INFO}  {label}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)


@dataclass
class ValidationResult:
    section: str
    checks: list[tuple[str, bool]] = field(default_factory=list)
    notes:  list[str]              = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for _, ok in self.checks if ok)

    @property
    def failed(self) -> int:
        return sum(1 for _, ok in self.checks if not ok)

    @property
    def total(self) -> int:
        return len(self.checks)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Pre-flight: Environment + source data
# ══════════════════════════════════════════════════════════════════════════════

def check_preflight(results: list[ValidationResult]) -> bool:
    """Verify environment and source data readiness."""
    _section("SECTION 1 — PRE-FLIGHT CHECKS")
    r = ValidationResult(section="Pre-flight")

    # .env file
    env_path = BASE_DIR / ".env"
    has_env  = env_path.exists()
    r.checks.append(_check(".env file exists", has_env,
                           str(env_path) if has_env else "Run: cp .env.example .env and populate keys"))

    # Load env vars
    if has_env:
        from dotenv import load_dotenv
        load_dotenv(env_path)

    # Required credentials
    sb_url  = os.getenv("SUPABASE_URL", "")
    sb_key  = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    emb_key = os.getenv("OPENAI_API_KEY", "") or os.getenv("EMBEDDING_API_KEY", "")

    has_sb  = bool(sb_url and sb_key and "your-" not in sb_url.lower())
    has_emb = bool(emb_key and not emb_key.startswith("sk-your"))

    r.checks.append(_check("SUPABASE_URL + SUPABASE_KEY configured", has_sb,
                           f"URL: {'set' if sb_url else 'MISSING'}, Key: {'set' if sb_key else 'MISSING'}"))
    r.checks.append(_check("OPENAI_API_KEY / EMBEDDING_API_KEY configured", has_emb,
                           "Key: set" if has_emb else "Key: MISSING — embedding step will be skipped"))

    # Source data
    processed_dir = BASE_DIR / "data" / "processed"
    parquet_path  = processed_dir / "unified_cleaned_dataset.parquet"
    csv_path      = processed_dir / "unified_cleaned_dataset.csv"
    gold_path     = processed_dir / "gold_dataset.csv"

    has_parquet = parquet_path.exists()
    has_csv     = csv_path.exists()
    has_gold    = gold_path.exists()

    r.checks.append(_check("unified_cleaned_dataset.parquet exists", has_parquet, str(parquet_path)))
    if not has_parquet:
        r.checks.append(_check("unified_cleaned_dataset.csv exists (fallback)", has_csv, str(csv_path)))
    r.checks.append(_check("gold_dataset.csv exists", has_gold,
                           "Optional for gold mode — skip if not running gold ingestion"))

    # B1 tables (static check — actual existence verified in Section 4)
    sql_dir    = BASE_DIR / "sql" / "b1_migrations"
    migration_files = list(sql_dir.glob("B1_*.sql")) if sql_dir.exists() else []
    r.checks.append(_check(f"B1 SQL migration files present ({len(migration_files)}/6)",
                           len(migration_files) >= 6,
                           f"Found: {[f.name for f in sorted(migration_files)]}"))

    results.append(r)
    can_continue = has_parquet or has_csv
    if not can_continue:
        print(f"\n  {FAIL}  Cannot continue — no source data found.")
        print("       Run the preprocessing pipeline first:")
        print("       python dataset_pipeline/run.py")
    return can_continue


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Offline dry-run pipeline
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DryRunStats:
    total_rows: int = 0
    mapped_ok:  int = 0
    map_errors: int = 0
    validated_ok: int = 0
    validation_errors: int = 0
    escalation_skipped: int = 0
    no_content_skipped: int = 0
    docs_built: int = 0
    chunks_total: int = 0
    chunk_type_counts: dict = field(default_factory=dict)
    automation_counts: dict = field(default_factory=dict)
    client_counts: dict     = field(default_factory=dict)
    rca_quality_counts: dict = field(default_factory=dict)
    chunk_word_stats: dict  = field(default_factory=dict)
    errors: list[str]       = field(default_factory=list)
    duration_s: float       = 0.0


def run_offline_dry_run(results: list[ValidationResult]) -> DryRunStats:
    """Run the full mapper → validator → builder → chunker pipeline without any DB/API calls."""
    _section("SECTION 2 — OFFLINE DRY-RUN PIPELINE")

    stats = DryRunStats()
    r     = ValidationResult(section="Dry-run pipeline")

    try:
        import pandas as pd
        from pydantic import ValidationError

        from rag_engine.ingestion.schema_mapper import DatasetSchemaMapper
        from rag_engine.document_builder.ticket_builder import TicketDocumentBuilder
        from rag_engine.chunking.ticket_chunker import TicketChunker
        from rag_engine.schemas.ticket_document import TicketSourceRow

        processed_dir = BASE_DIR / "data" / "processed"
        parquet_path  = processed_dir / "unified_cleaned_dataset.parquet"
        csv_path      = processed_dir / "unified_cleaned_dataset.csv"

        source = parquet_path if parquet_path.exists() else csv_path
        _info(f"Loading: {source.name}")

        if str(source).endswith(".parquet"):
            df = pd.read_parquet(source)
        else:
            df = pd.read_csv(source, low_memory=False)

        stats.total_rows = len(df)
        _info(f"Rows loaded: {stats.total_rows:,}")

        mapper  = DatasetSchemaMapper()
        builder = TicketDocumentBuilder()
        chunker = TicketChunker(
            max_chunk_words=400,
            overlap_words=20,
            min_query_body_chars=80,
            index_version="v1",
        )

        chunk_type_counts    = Counter()
        automation_counts    = Counter()
        client_counts        = Counter()
        rca_quality_counts   = Counter()
        word_counts_by_type  = defaultdict(list)
        chunk_id_set         = set()
        duplicate_ids        = []
        t0 = time.monotonic()

        for _, row_data in df.iterrows():
            # NaN normalize
            raw: dict[str, Any] = {}
            for k, v in row_data.to_dict().items():
                try:
                    raw[k] = None if (isinstance(v, float) and math.isnan(v)) else v
                except (TypeError, ValueError):
                    raw[k] = v

            # Schema mapping
            try:
                mapped = mapper.map(raw)
                stats.mapped_ok += 1
            except Exception as exc:
                stats.map_errors += 1
                stats.errors.append(f"MapError row {stats.total_rows}: {exc}")
                continue

            # Pydantic validation
            try:
                source_row = TicketSourceRow.model_validate(mapped)
                stats.validated_ok += 1
            except ValidationError as exc:
                stats.validation_errors += 1
                stats.errors.append(f"ValidationError {mapped.get('ticket_id','?')}: {exc}")
                continue

            # Escalation skip
            if source_row.automation_label == "ESCALATION":
                stats.escalation_skipped += 1
                automation_counts["ESCALATION"] += 1
                client_counts[source_row.client] += 1
                continue

            automation_counts[source_row.automation_label] += 1
            client_counts[source_row.client] += 1
            rca_quality_counts[str(source_row.rca_quality_score)] += 1

            # Document build
            doc = builder.build(source_row)
            if doc is None:
                stats.no_content_skipped += 1
                continue

            stats.docs_built += 1

            # Chunking
            chunks = chunker.chunk(doc, ingestion_run_id="dry-run-validation")
            stats.chunks_total += len(chunks)

            for chunk in chunks:
                chunk_type_counts[chunk.chunk_type.value] += 1
                word_counts_by_type[chunk.chunk_type.value].append(chunk.word_count)
                # Determinism check
                if chunk.id in chunk_id_set:
                    duplicate_ids.append(chunk.id)
                chunk_id_set.add(chunk.id)

        stats.duration_s = time.monotonic() - t0
        stats.chunk_type_counts  = dict(chunk_type_counts)
        stats.automation_counts  = dict(automation_counts)
        stats.client_counts      = dict(client_counts)
        stats.rca_quality_counts = dict(rca_quality_counts)

        # Word count statistics
        for ct, wcs in word_counts_by_type.items():
            if wcs:
                stats.chunk_word_stats[ct] = {
                    "min":  min(wcs),
                    "max":  max(wcs),
                    "avg":  round(sum(wcs) / len(wcs), 1),
                    "count": len(wcs),
                }

        # Report results
        r.checks.append(_check("Schema mapper: zero mapping errors", stats.map_errors == 0,
                               f"{stats.map_errors} errors / {stats.total_rows} rows"))
        r.checks.append(_check("Pydantic validation: zero validation errors", stats.validation_errors == 0,
                               f"{stats.validation_errors} errors / {stats.mapped_ok} mapped"))
        r.checks.append(_check(f"Documents built: {stats.docs_built:,}",
                               stats.docs_built >= 1000,
                               f"({stats.escalation_skipped:,} escalation skipped, "
                               f"{stats.no_content_skipped:,} no-content skipped)"))
        r.checks.append(_check(f"Chunks produced: {stats.chunks_total:,}",
                               stats.chunks_total >= 3000,
                               f"Avg {stats.chunks_total / max(stats.docs_built, 1):.2f} chunks/doc"))
        r.checks.append(_check("Deterministic IDs: zero duplicates",
                               len(duplicate_ids) == 0,
                               f"{len(duplicate_ids)} duplicate chunk IDs detected"))
        r.checks.append(_check(f"Pipeline throughput: {stats.total_rows / max(stats.duration_s, 0.001):.0f} rows/s",
                               True))

        print(f"\n  Automation label distribution:")
        for label, cnt in sorted(stats.automation_counts.items(), key=lambda x: -x[1]):
            print(f"    {label:<30}  {cnt:>6,}")

        print(f"\n  Tenant (client) distribution:")
        for client, cnt in sorted(stats.client_counts.items(), key=lambda x: -x[1]):
            print(f"    {client:<30}  {cnt:>6,}")

        print(f"\n  Chunk type distribution:")
        for ct, cnt in sorted(stats.chunk_type_counts.items()):
            stats_row = stats.chunk_word_stats.get(ct, {})
            print(f"    {ct:<20}  {cnt:>6,}  "
                  f"[avg_words={stats_row.get('avg','?')}, "
                  f"min={stats_row.get('min','?')}, "
                  f"max={stats_row.get('max','?')}]")

        print(f"\n  RCA quality score distribution:")
        score_labels = {"0": "NONE", "10": "TRIVIAL", "40": "BRONZE", "70": "SILVER", "95": "GOLD"}
        for score, cnt in sorted(stats.rca_quality_counts.items(), key=lambda x: int(x[0])):
            label = score_labels.get(score, score)
            print(f"    Score {score:>3} ({label:<7})  {cnt:>6,}")

    except ImportError as exc:
        r.checks.append(_check("Import rag_engine modules", False, str(exc)))
        print(f"  {FAIL}  Cannot import rag_engine. Is your PYTHONPATH correct?")
    except Exception as exc:
        r.checks.append(_check("Dry-run pipeline", False, str(exc)))
        stats.errors.append(str(exc))

    results.append(r)
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Live ingestion execution
# ══════════════════════════════════════════════════════════════════════════════

def run_live_ingestion(
    results: list[ValidationResult],
    dry_stats: DryRunStats,
) -> Optional[dict]:
    """
    Execute the real ingestion pipeline (requires credentials).
    Returns the IngestionRunRecord as dict, or None if failed.
    """
    _section("SECTION 3 — LIVE INGESTION EXECUTION")
    r = ValidationResult(section="Live ingestion")

    sb_url  = os.getenv("SUPABASE_URL", "")
    sb_key  = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    emb_key = os.getenv("OPENAI_API_KEY", "") or os.getenv("EMBEDDING_API_KEY", "")

    has_creds = bool(sb_url and sb_key and emb_key)
    r.checks.append(_check("Credentials available for live run", has_creds,
                           "SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY all required"))
    if not has_creds:
        print(f"\n  {WARN}  Skipping live ingestion — credentials not configured.")
        print("       Set SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY in your .env file.")
        results.append(r)
        return None

    try:
        from supabase import create_client

        from rag_engine.config.rag_settings import get_rag_settings
        from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
        from rag_engine.ingestion.pipeline import IngestionPipeline, IngestionMode

        settings  = get_rag_settings()
        sb_client = create_client(sb_url, sb_key)
        embedder  = OpenAIEmbeddingProvider(
            api_key=emb_key,
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        )

        pipeline = IngestionPipeline(
            settings=settings,
            supabase_client=sb_client,
            embedding_provider=embedder,
        )

        _info("Starting full ingestion (mode=full)...")
        t0  = time.monotonic()
        run = pipeline.run(mode=IngestionMode.FULL, triggered_by="validate_b1_ingestion")
        elapsed_s = time.monotonic() - t0

        run_dict = {
            "run_id":             run.run_id,
            "status":             run.status,
            "total_source_rows":  run.total_source_rows,
            "documents_processed":run.documents_processed,
            "documents_skipped":  run.documents_skipped,
            "documents_failed":   run.documents_failed,
            "chunks_created":     run.chunks_created,
            "chunks_skipped":     run.chunks_skipped,
            "chunks_failed":      run.chunks_failed,
            "embeddings_generated":run.embeddings_generated,
            "embedding_api_calls":run.embedding_api_calls,
            "embedding_tokens_used":run.embedding_tokens_used,
            "duration_s":         elapsed_s,
            "automation_label_counts": run.automation_label_counts,
            "client_counts":      run.client_counts,
            "query_type_counts":  run.query_type_counts,
        }

        # Validate run result
        succeeded = run.status in ("COMPLETED", "PARTIAL")
        r.checks.append(_check(f"Ingestion run status: {run.status}", succeeded))
        r.checks.append(_check(
            f"Documents processed: {run.documents_processed:,}",
            run.documents_processed >= 1000,
            f"Expected ~{dry_stats.docs_built:,} from dry run"))
        r.checks.append(_check(
            f"Chunks created: {run.chunks_created:,}",
            run.chunks_created >= 1000,
            f"Expected ~{dry_stats.chunks_total:,} from dry run"))
        r.checks.append(_check(
            f"Embeddings generated: {run.embeddings_generated:,}",
            run.embeddings_generated >= 1000))
        r.checks.append(_check(
            f"Pipeline errors (failed docs): {run.documents_failed}",
            run.documents_failed == 0,
            "Non-zero means some tickets failed processing"))
        r.checks.append(_check(
            f"Chunk failures: {run.chunks_failed}",
            run.chunks_failed == 0,
            "Non-zero means some chunks failed embedding or upsert"))

        throughput = run.documents_processed / max(elapsed_s, 1)
        _info(f"Throughput: {throughput:.1f} docs/s | Elapsed: {elapsed_s:.1f}s")

        if run.embedding_tokens_used:
            cost_usd = (run.embedding_tokens_used / 1_000_000) * 0.02
            _info(f"Embedding cost: ~${cost_usd:.4f} USD ({run.embedding_tokens_used:,} tokens)")

        results.append(r)
        return run_dict

    except Exception as exc:
        r.checks.append(_check("Live ingestion pipeline", False, str(exc)))
        _warn(f"Ingestion failed: {exc}")
        results.append(r)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Post-ingestion DB validation
# ══════════════════════════════════════════════════════════════════════════════

def validate_db_state(results: list[ValidationResult], dry_stats: DryRunStats) -> dict:
    """Query the live DB and validate what was actually ingested."""
    _section("SECTION 4 — POST-INGESTION DB VALIDATION")
    r      = ValidationResult(section="DB validation")
    db_info: dict = {}

    sb_url = os.getenv("SUPABASE_URL", "")
    sb_key = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    if not (sb_url and sb_key):
        _warn("Supabase credentials not configured — skipping DB validation")
        results.append(r)
        return db_info

    try:
        from supabase import create_client
        sb = create_client(sb_url, sb_key)

        # ── Table existence ──────────────────────────────────────────────────
        b1_tables = [
            "rag_ticket_documents",
            "rag_ticket_chunks",
            "rag_sop_library",
            "rag_sop_chunks",
            "rag_ingestion_logs",
            "rag_ingestion_errors",
        ]
        tables_ok = True
        for tbl in b1_tables:
            try:
                sb.table(tbl).select("id", count="exact").limit(1).execute()
                r.checks.append(_check(f"Table exists: {tbl}", True))
            except Exception as exc:
                r.checks.append(_check(f"Table exists: {tbl}", False, str(exc)))
                tables_ok = False

        if not tables_ok:
            _warn("Some B1 tables missing — run SQL migrations before ingesting:")
            for i in range(1, 7):
                print(f"       B1_00{i}_*.sql")
            results.append(r)
            return db_info

        # ── Chunk row count ──────────────────────────────────────────────────
        resp = sb.table("rag_ticket_chunks").select("id", count="exact").execute()
        total_chunks = resp.count if hasattr(resp, "count") else len(resp.data or [])
        db_info["total_chunks"] = total_chunks

        r.checks.append(_check(
            f"rag_ticket_chunks row count: {total_chunks:,}",
            total_chunks >= 1000,
            f"Dry-run expected {dry_stats.chunks_total:,}"))

        # ── Embedding completeness ───────────────────────────────────────────
        null_embed_resp = (
            sb.table("rag_ticket_chunks")
            .select("id", count="exact")
            .is_("embedding", "null")
            .execute()
        )
        null_embed_count = null_embed_resp.count if hasattr(null_embed_resp, "count") else len(null_embed_resp.data or [])
        db_info["null_embedding_count"] = null_embed_count

        r.checks.append(_check(
            f"Chunks with null embedding: {null_embed_count}",
            null_embed_count == 0,
            "Non-zero means embedding step incomplete or API failures"))

        # ── Sample row validation ────────────────────────────────────────────
        sample_resp = (
            sb.table("rag_ticket_chunks")
            .select("id, ticket_id, client, chunk_type, word_count, "
                    "automation_label, has_rca, rca_quality_score, index_version")
            .limit(5)
            .execute()
        )
        sample_rows = sample_resp.data or []

        has_valid_samples = len(sample_rows) > 0
        r.checks.append(_check("Sample rows retrievable", has_valid_samples))

        if sample_rows:
            print(f"\n  Sample rows (first 5):")
            for row in sample_rows:
                print(f"    ticket_id={row.get('ticket_id','?'):>8}  "
                      f"client={row.get('client','?'):<15}  "
                      f"type={row.get('chunk_type','?'):<16}  "
                      f"words={row.get('word_count','?'):>4}  "
                      f"label={row.get('automation_label','?')}")

        # ── Metadata completeness ────────────────────────────────────────────
        null_client_resp = (
            sb.table("rag_ticket_chunks")
            .select("id", count="exact")
            .or_("client.is.null,client.eq.unknown")
            .execute()
        )
        null_client = null_client_resp.count if hasattr(null_client_resp, "count") else 0
        db_info["null_client_count"] = null_client
        r.checks.append(_check(
            f"Chunks with null/unknown client: {null_client}",
            null_client == 0,
            "Should be 0 — all chunks must have tenant context"))

        # ── Escalation contamination check ───────────────────────────────────
        escalation_resp = (
            sb.table("rag_ticket_chunks")
            .select("id", count="exact")
            .eq("automation_label", "ESCALATION")
            .execute()
        )
        escalation_chunks = escalation_resp.count if hasattr(escalation_resp, "count") else 0
        db_info["escalation_chunks"] = escalation_chunks
        r.checks.append(_check(
            f"ESCALATION chunks in rag_ticket_chunks: {escalation_chunks}",
            escalation_chunks == 0,
            "Must be 0 — ESCALATION tickets must never enter the RAG corpus"))

        # ── Distribution query ───────────────────────────────────────────────
        # Automation label distribution
        for label in ("AUTO_REPLY", "HUMAN_REVIEW"):
            label_resp = (
                sb.table("rag_ticket_chunks")
                .select("id", count="exact")
                .eq("automation_label", label)
                .execute()
            )
            cnt = label_resp.count if hasattr(label_resp, "count") else 0
            db_info[f"chunks_{label.lower()}"] = cnt
            _info(f"  {label} chunks: {cnt:,}")

        # Chunk type distribution
        print(f"\n  Chunk type counts in DB:")
        for ct in ("ISSUE_HEADER", "QUERY_BODY", "RESOLUTION_RCA", "SOP_STEPS"):
            ct_resp = (
                sb.table("rag_ticket_chunks")
                .select("id", count="exact")
                .eq("chunk_type", ct)
                .execute()
            )
            cnt = ct_resp.count if hasattr(ct_resp, "count") else 0
            db_info[f"chunks_{ct.lower()}"] = cnt
            print(f"    {ct:<20}  {cnt:>8,}")

        # ── Index version check ──────────────────────────────────────────────
        v1_resp = (
            sb.table("rag_ticket_chunks")
            .select("id", count="exact")
            .eq("index_version", "v1")
            .execute()
        )
        v1_count = v1_resp.count if hasattr(v1_resp, "count") else 0
        db_info["index_v1_count"] = v1_count
        r.checks.append(_check(
            f"Chunks with index_version='v1': {v1_count:,}",
            v1_count > 0))

        # ── Ingestion log check ──────────────────────────────────────────────
        log_resp = (
            sb.table("rag_ingestion_logs")
            .select("run_id, status, documents_processed, chunks_created, created_at")
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        log_rows = log_resp.data or []
        if log_rows:
            print(f"\n  Last {len(log_rows)} ingestion run(s):")
            for log in log_rows:
                print(f"    run_id={log.get('run_id','?')[:8]}...  "
                      f"status={log.get('status','?'):<10}  "
                      f"docs={log.get('documents_processed','?'):>6}  "
                      f"chunks={log.get('chunks_created','?'):>8}  "
                      f"at={log.get('created_at','?')[:19]}")
            last_run = log_rows[0]
            r.checks.append(_check(
                f"Last ingestion run status: {last_run.get('status','?')}",
                last_run.get("status") in ("COMPLETED", "PARTIAL")))
            db_info["last_run"] = last_run

    except Exception as exc:
        r.checks.append(_check("DB validation", False, str(exc)))
        _warn(f"DB validation error: {exc}")

    results.append(r)
    return db_info


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Deduplication idempotency check
# ══════════════════════════════════════════════════════════════════════════════

def check_deduplication_idempotency(results: list[ValidationResult]) -> None:
    """
    Re-run ingestion a second time (or simulate with dry_run=True on the pipeline).
    Verify that chunk counts do not increase and skipped count matches created count.
    """
    _section("SECTION 5 — DEDUPLICATION IDEMPOTENCY CHECK")
    r = ValidationResult(section="Deduplication idempotency")

    sb_url = os.getenv("SUPABASE_URL", "")
    sb_key = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    emb_key = os.getenv("OPENAI_API_KEY", "") or os.getenv("EMBEDDING_API_KEY", "")

    if not (sb_url and sb_key and emb_key):
        _warn("Skipping deduplication check — credentials not configured")
        results.append(r)
        return

    try:
        from supabase import create_client
        from rag_engine.ingestion.deduplication import DeduplicationChecker
        from rag_engine.config.rag_settings import get_rag_settings

        settings  = get_rag_settings()
        sb_client = create_client(sb_url, sb_key)

        # Get pre-run count
        resp_before = sb_client.table("rag_ticket_chunks").select("id", count="exact").execute()
        count_before = resp_before.count if hasattr(resp_before, "count") else 0

        _info(f"Current chunk count before idempotency test: {count_before:,}")

        # Run the pipeline again — should produce 0 new inserts, all skipped
        from rag_engine.config.rag_settings import get_rag_settings
        from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
        from rag_engine.ingestion.pipeline import IngestionPipeline, IngestionMode

        embedder  = OpenAIEmbeddingProvider(
            api_key=emb_key,
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        )
        pipeline = IngestionPipeline(
            settings=settings,
            supabase_client=sb_client,
            embedding_provider=embedder,
        )

        _info("Running ingestion a second time (idempotency check)...")
        run2 = pipeline.run(mode=IngestionMode.FULL, triggered_by="idempotency_check")

        resp_after = sb_client.table("rag_ticket_chunks").select("id", count="exact").execute()
        count_after = resp_after.count if hasattr(resp_after, "count") else 0

        counts_unchanged = count_after == count_before
        r.checks.append(_check(
            f"Chunk count unchanged after re-run: {count_before:,} → {count_after:,}",
            counts_unchanged,
            "Re-ingestion must be idempotent (UUID5 dedup)"))
        r.checks.append(_check(
            f"Chunks skipped (duplicate hashes): {run2.chunks_skipped:,}",
            run2.chunks_skipped > 0,
            "Expected: all existing chunks recognized and skipped"))
        r.checks.append(_check(
            f"New chunks created on re-run: {run2.chunks_created}",
            run2.chunks_created == 0,
            "Expected: 0 — no new chunks if data unchanged"))

    except Exception as exc:
        r.checks.append(_check("Deduplication idempotency", False, str(exc)))
        _warn(f"Idempotency check error: {exc}")

    results.append(r)


# ══════════════════════════════════════════════════════════════════════════════
# Report generation
# ══════════════════════════════════════════════════════════════════════════════

def write_report(
    results:    list[ValidationResult],
    dry_stats:  DryRunStats,
    db_info:    dict,
    live_run:   Optional[dict],
    output_path: Path,
) -> None:
    """Write the b1_ingestion_validation.md report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_checks  = sum(r.total  for r in results)
    passed_checks = sum(r.passed for r in results)
    failed_checks = sum(r.failed for r in results)
    score = int((passed_checks / max(total_checks, 1)) * 100)

    if score >= 90:
        readiness = "PRODUCTION READY"
        readiness_icon = "✅"
    elif score >= 70:
        readiness = "CONDITIONAL — address failures before production"
        readiness_icon = "⚠️"
    else:
        readiness = "NOT READY — critical failures block production"
        readiness_icon = "❌"

    lines = [
        f"# Phase B1-B Ingestion Validation Report",
        f"",
        f"**Generated:** {now}  ",
        f"**System:** KwikID AI Ingest · Think360.ai  ",
        f"**Index version:** v1  ",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Overall score | **{score}%** ({passed_checks}/{total_checks} checks passed) |",
        f"| Readiness | {readiness_icon} **{readiness}** |",
        f"| Source rows | {dry_stats.total_rows:,} |",
        f"| Validation errors | {dry_stats.validation_errors} |",
        f"| Mapping errors | {dry_stats.map_errors} |",
        f"| Escalations skipped | {dry_stats.escalation_skipped:,} |",
        f"| Documents built | {dry_stats.docs_built:,} |",
        f"| Chunks generated | {dry_stats.chunks_total:,} |",
        f"| DB chunks (live) | {db_info.get('total_chunks', 'N/A'):,} |"
        if db_info.get('total_chunks') else f"| DB chunks (live) | N/A (no live run) |",
        f"| Null embeddings | {db_info.get('null_embedding_count', 'N/A')} |",
        f"| Escalation contamination | {db_info.get('escalation_chunks', 'N/A')} |",
        f"",
        f"---",
        f"",
        f"## Section Results",
        f"",
    ]

    for res in results:
        lines.append(f"### {res.section}")
        lines.append(f"")
        lines.append(f"| Check | Status |")
        lines.append(f"|-------|--------|")
        for label, ok in res.checks:
            icon = "✅" if ok else "❌"
            lines.append(f"| {label} | {icon} |")
        if res.notes:
            lines.append(f"")
            for note in res.notes:
                lines.append(f"> {note}")
        lines.append(f"")

    # Distribution section
    lines += [
        f"---",
        f"",
        f"## Data Distribution (Dry-run)",
        f"",
        f"### Automation Label Distribution",
        f"",
        f"| Label | Count |",
        f"|-------|-------|",
    ]
    for label, cnt in sorted(dry_stats.automation_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {label} | {cnt:,} |")

    lines += [
        f"",
        f"### Tenant (Client) Distribution",
        f"",
        f"| Client | Count |",
        f"|--------|-------|",
    ]
    for client, cnt in sorted(dry_stats.client_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {client} | {cnt:,} |")

    lines += [
        f"",
        f"### Chunk Type Distribution",
        f"",
        f"| Chunk Type | Count | Avg Words | Min Words | Max Words |",
        f"|------------|-------|-----------|-----------|-----------|",
    ]
    for ct, cnt in sorted(dry_stats.chunk_type_counts.items()):
        stats_row = dry_stats.chunk_word_stats.get(ct, {})
        lines.append(
            f"| {ct} | {cnt:,} | "
            f"{stats_row.get('avg', '?')} | "
            f"{stats_row.get('min', '?')} | "
            f"{stats_row.get('max', '?')} |"
        )

    score_labels = {"0": "NONE", "10": "TRIVIAL", "40": "BRONZE", "70": "SILVER", "95": "GOLD"}
    lines += [
        f"",
        f"### RCA Quality Distribution",
        f"",
        f"| Score | Tier | Count |",
        f"|-------|------|-------|",
    ]
    for score_val, cnt in sorted(dry_stats.rca_quality_counts.items(), key=lambda x: int(x[0])):
        label = score_labels.get(score_val, score_val)
        lines.append(f"| {score_val} | {label} | {cnt:,} |")

    # Live run section
    if live_run:
        lines += [
            f"",
            f"---",
            f"",
            f"## Live Ingestion Run Details",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
        ]
        for k, v in live_run.items():
            if isinstance(v, dict):
                continue
            lines.append(f"| {k} | {v} |")

    # DB state section
    if db_info:
        lines += [
            f"",
            f"---",
            f"",
            f"## Database State (Post-ingestion)",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
        ]
        for k, v in db_info.items():
            if not isinstance(v, dict):
                lines.append(f"| {k} | {v} |")

    # Errors section
    if dry_stats.errors:
        lines += [
            f"",
            f"---",
            f"",
            f"## Errors ({len(dry_stats.errors)} total)",
            f"",
            f"```",
        ]
        for err in dry_stats.errors[:20]:
            lines.append(err)
        if len(dry_stats.errors) > 20:
            lines.append(f"... and {len(dry_stats.errors) - 20} more")
        lines.append(f"```")

    # Next steps
    lines += [
        f"",
        f"---",
        f"",
        f"## Next Steps",
        f"",
    ]
    if failed_checks > 0:
        lines.append(f"### Address Failures ({failed_checks} checks failed)")
        lines.append(f"")
        for res in results:
            for label, ok in res.checks:
                if not ok:
                    lines.append(f"- **{res.section}**: {label}")
        lines.append(f"")
    lines += [
        f"### Phase B1-C: Retrieval Sanity Testing",
        f"",
        f"Run the retrieval validation script to verify search quality:",
        f"",
        f"```bash",
        f"python scripts/validate_b1_retrieval.py --live --report",
        f"```",
        f"",
        f"### Phase B2: Chat Generation",
        f"",
        f"Once B1-C passes, proceed to Phase B2:",
        f"Connect `TicketRetriever` to the LLM generation layer for grounded response drafting.",
        f"",
        f"---",
        f"*Generated by scripts/validate_b1_ingestion.py*",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  {OK}  Report written: {output_path}")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="Phase B1-B: Ingestion Validation")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run actual ingestion (requires SUPABASE_KEY + OPENAI_API_KEY) and validate DB",
    )
    parser.add_argument(
        "--skip-idempotency",
        action="store_true",
        help="Skip the deduplication idempotency re-run (saves time and API cost)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Write validation report to data/reports/b1_ingestion_validation.md",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=BASE_DIR / "data" / "reports" / "b1_ingestion_validation.md",
        help="Output path for the markdown report",
    )
    args = parser.parse_args()

    print("=" * 62)
    print("  Phase B1-B — Ingestion Validation")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 62)

    all_results: list[ValidationResult] = []

    # Section 1: Pre-flight
    can_continue = check_preflight(all_results)
    if not can_continue:
        return 1

    # Section 2: Offline dry run (always runs)
    dry_stats = run_offline_dry_run(all_results)

    # Sections 3–5: Live checks (only if --live)
    live_run_info: Optional[dict] = None
    db_info: dict = {}

    if args.live:
        live_run_info = run_live_ingestion(all_results, dry_stats)
        db_info = validate_db_state(all_results, dry_stats)
        if not args.skip_idempotency and live_run_info is not None:
            check_deduplication_idempotency(all_results)
    else:
        _section("SECTION 3–5 — LIVE CHECKS (SKIPPED)")
        _warn("Pass --live to run actual ingestion and DB validation")
        _info("Example: python scripts/validate_b1_ingestion.py --live --report")

    # Summary
    _section("VALIDATION SUMMARY")
    total_checks  = sum(r.total  for r in all_results)
    passed_checks = sum(r.passed for r in all_results)
    failed_checks = sum(r.failed for r in all_results)
    score = int((passed_checks / max(total_checks, 1)) * 100)

    for res in all_results:
        icon = OK if res.failed == 0 else FAIL
        print(f"  {icon}  {res.section:<35}  {res.passed}/{res.total} passed")

    print(f"\n  Overall score: {score}% ({passed_checks}/{total_checks} checks)")

    if score >= 90:
        print(f"\n  {OK}  INGESTION LAYER: PRODUCTION READY")
        print(f"       Proceed to: python scripts/validate_b1_retrieval.py --live --report")
    elif score >= 70:
        print(f"\n  {WARN}  INGESTION LAYER: CONDITIONAL")
        print(f"       Address failed checks before running live retrieval validation.")
    else:
        print(f"\n  {FAIL}  INGESTION LAYER: NOT READY")
        print(f"       Resolve critical failures before proceeding.")

    # Write report
    if args.report:
        write_report(
            results=all_results,
            dry_stats=dry_stats,
            db_info=db_info,
            live_run=live_run_info,
            output_path=args.report_path,
        )

    return 0 if failed_checks == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
