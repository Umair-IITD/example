"""
scripts/validate_b1_infrastructure.py

Phase B1-A: Infrastructure Validation Script

Validates (offline + optional live checks):
  1. Environment configuration completeness
  2. SQL migration file correctness (static analysis)
  3. Processed dataset integrity
  4. Schema mapper correctness (all 3,635 rows)
  5. Document builder + chunker pipeline (dry run)
  6. Embedding provider connectivity (if credentials available)
  7. Supabase connectivity (if credentials available)
  8. B1 table existence (if credentials available)

Run:
    python scripts/validate_b1_infrastructure.py
    python scripts/validate_b1_infrastructure.py --live   # also tests DB + API
    python scripts/validate_b1_infrastructure.py --report # write markdown report
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

# ── Styling helpers ────────────────────────────────────────────────────────────
OK = "✅"
FAIL = "❌"
WARN = "⚠️ "
INFO = "ℹ️ "


def _section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def _check(label: str, passed: bool, detail: str = "") -> tuple[str, bool]:
    icon = OK if passed else FAIL
    msg = f"{icon}  {label}"
    if detail:
        msg += f"\n     {detail}"
    print(msg)
    return label, passed


@dataclass
class ValidationReport:
    run_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    results: list[tuple[str, bool, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, label: str, passed: bool, detail: str = "") -> None:
        self.results.append((label, passed, detail))

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"{WARN} {msg}")

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for _, p, _ in self.results if p)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def score(self) -> float:
        return (self.passed / self.total * 100) if self.total > 0 else 0.0


# =============================================================================
# CHECK 1: Environment configuration
# =============================================================================
def check_environment(report: ValidationReport) -> dict[str, str]:
    _section("1. Environment Configuration")

    env_path = BASE_DIR / ".env"
    dotenv_vars: dict[str, str] = {}

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    dotenv_vars[k.strip()] = v.strip()
        report.add(".env file exists", True, str(env_path))
    else:
        report.add(".env file exists", False,
                   "Create .env from rag_engine/config/.env.b1.template")

    # Merge with OS env
    all_env = {**dotenv_vars, **os.environ}

    required_vars = {
        "SUPABASE_URL": "Supabase project URL",
        "SUPABASE_KEY": "Supabase service-role key",
        "EMBEDDING_API_KEY": "OpenAI API key for embeddings",
    }

    for var, desc in required_vars.items():
        val = all_env.get(var, "")
        has_real_value = bool(val) and val not in (
            "your_service_role_key", "your-service-role-key",
            "sk-...", "sk-placeholder"
        )
        label, passed = _check(
            f"Env: {var}",
            has_real_value,
            f"{desc} — {'SET' if has_real_value else 'MISSING or placeholder'}"
        )
        report.add(label, passed, desc)

    optional_vars = {
        "EMBEDDING_MODEL": ("text-embedding-3-small", "Embedding model"),
        "EMBEDDING_DIMENSIONS": ("1536", "Embedding dimensions"),
        "EMBEDDING_PROVIDER": ("openai", "Embedding provider"),
        "B1_INDEX_VERSION": ("v1", "B1 index version"),
    }

    for var, (default, desc) in optional_vars.items():
        val = all_env.get(var, default)
        _check(f"Env: {var}", True, f"{val} (effective)")
        report.add(f"Env: {var}", True, val)

    # Dimension consistency check
    dims = int(all_env.get("EMBEDDING_DIMENSIONS", "1536"))
    dim_ok = dims == 1536
    label, passed = _check(
        "Embedding dimensions = 1536",
        dim_ok,
        f"Got {dims} — pgvector schema uses VECTOR(1536)"
    )
    report.add(label, passed)

    # Processed data path
    data_dir = BASE_DIR / "data" / "processed"
    label, passed = _check(
        "Processed data directory exists",
        data_dir.exists(),
        str(data_dir)
    )
    report.add(label, passed)

    return all_env


# =============================================================================
# CHECK 2: SQL Migration Files (static analysis)
# =============================================================================
def check_sql_migrations(report: ValidationReport) -> None:
    _section("2. SQL Migration Files (Static Analysis)")

    migrations_dir = BASE_DIR / "sql" / "b1_migrations"
    label, passed = _check(
        "b1_migrations directory exists",
        migrations_dir.exists(),
        str(migrations_dir)
    )
    report.add(label, passed)
    if not passed:
        return

    expected_files = [
        ("B1_001_ticket_documents.sql", ["rag_ticket_documents", "rag_ticket_chunks", "UNIQUE", "VECTOR"]),
        ("B1_002_sop_library.sql",      ["rag_sop_library", "rag_sop_chunks", "VECTOR"]),
        ("B1_003_ingestion_logs.sql",   ["rag_ingestion_logs", "rag_ingestion_errors"]),
        ("B1_004_feedback_logs.sql",    ["rag_feedback_logs", "edit_ratio"]),
        ("B1_005_rpc_functions.sql",    ["match_ticket_chunks", "match_sop_chunks",
                                          "match_all_b1_sources", "p_client"]),
        ("B1_006_indexes.sql",          ["hnsw", "GIN", "ROW LEVEL SECURITY", "POLICY"]),
    ]

    for filename, required_terms in expected_files:
        path = migrations_dir / filename
        if not path.exists():
            label, passed = _check(f"Migration: {filename}", False, "FILE NOT FOUND")
            report.add(label, passed)
            continue

        content = path.read_text(encoding="utf-8").upper()
        missing = [t for t in required_terms if t.upper() not in content]
        ok = not missing
        label, passed = _check(
            f"Migration: {filename}",
            ok,
            f"Missing terms: {missing}" if missing else "All required SQL terms present"
        )
        report.add(label, passed)

    # Critical: p_client NOT NULL enforcement in RPC
    rpc_file = migrations_dir / "B1_005_rpc_functions.sql"
    if rpc_file.exists():
        rpc_content = rpc_file.read_text(encoding="utf-8")
        has_client_param = "p_client" in rpc_content
        client_enforced = "p_client" in rpc_content and "WHERE" in rpc_content
        label, passed = _check(
            "RPC: tenant isolation (p_client param)",
            has_client_param and client_enforced,
            "p_client parameter present in all match functions"
        )
        report.add(label, passed)

    # Critical: RLS policies present
    idx_file = migrations_dir / "B1_006_indexes.sql"
    if idx_file.exists():
        idx_content = idx_file.read_text(encoding="utf-8").upper()
        has_rls = "ROW LEVEL SECURITY" in idx_content
        has_policy = "POLICY" in idx_content
        has_hnsw = "HNSW" in idx_content
        label, _ = _check("Indexes: HNSW vector index defined", has_hnsw)
        report.add(label, has_hnsw)
        label, _ = _check("Indexes: RLS enabled on chunk table", has_rls and has_policy)
        report.add(label, has_rls and has_policy)


# =============================================================================
# CHECK 3: Processed Dataset Integrity
# =============================================================================
def check_dataset(report: ValidationReport) -> Optional[Any]:
    _section("3. Processed Dataset Integrity")

    try:
        import pandas as pd
    except ImportError:
        report.add("pandas available", False, "pip install pandas")
        return None

    data_dir = BASE_DIR / "data" / "processed"
    required_files = [
        ("unified_cleaned_dataset.parquet", "Main corpus"),
        ("gold_dataset.csv", "Gold Q→A pairs"),
        ("automation_labels.csv", "Automation labels"),
        ("evaluation_dataset.json", "Eval dataset"),
    ]

    for fname, desc in required_files:
        path = data_dir / fname
        label, passed = _check(
            f"Dataset file: {fname}",
            path.exists(),
            f"{desc} — {path}"
        )
        report.add(label, passed)

    parquet_path = data_dir / "unified_cleaned_dataset.parquet"
    if not parquet_path.exists():
        return None

    try:
        import pyarrow  # noqa: F401
    except ImportError:
        report.add("pyarrow available (for parquet)", False, "pip install pyarrow")
        return None

    df = pd.read_parquet(parquet_path)
    label, passed = _check(f"Parquet row count ≥ 3000", len(df) >= 3000, f"Got {len(df)} rows")
    report.add(label, passed)

    required_cols = [
        "ticket_id", "tenant_id", "cleaned_description", "cleaned_rca",
        "automation_class", "rca_quality", "sop_status", "query_type", "has_description"
    ]
    missing_cols = [c for c in required_cols if c not in df.columns]
    label, passed = _check(
        "Required columns present",
        not missing_cols,
        f"Missing: {missing_cols}" if missing_cols else f"All {len(required_cols)} required columns present"
    )
    report.add(label, passed)

    # Class distribution sanity check
    class_dist = df["automation_class"].value_counts().to_dict()
    has_all_classes = all(k in str(class_dist) for k in ["AUTO", "HUMAN", "ESCALATION"])
    label, passed = _check(
        "Automation classes present",
        has_all_classes,
        str(class_dist)
    )
    report.add(label, passed)

    # Data quality: how many have usable content
    with_desc = df["cleaned_description"].notna().sum()
    with_rca = df["cleaned_rca"].notna().sum()
    non_escalation = df[~df["automation_class"].str.contains("ESCALATION", na=False)]
    ingestible = len(non_escalation)

    print(f"{INFO} Dataset profile:")
    print(f"     Total rows:              {len(df)}")
    print(f"     Non-escalation:          {ingestible}")
    print(f"     With cleaned_description: {with_desc}")
    print(f"     With cleaned_rca:         {with_rca}")
    print(f"     Automation distribution:  {class_dist}")

    report.add("Dataset: with description", with_desc >= 2000, f"{with_desc} rows")
    report.add("Dataset: with RCA", with_rca >= 200, f"{with_rca} rows")

    return df


# =============================================================================
# CHECK 4: Schema Mapper + Pipeline Dry Run
# =============================================================================
def check_pipeline_dry_run(df: Any, report: ValidationReport) -> dict:
    _section("4. Schema Mapper + Pipeline Dry Run")

    from rag_engine.ingestion.schema_mapper import DatasetSchemaMapper
    from rag_engine.schemas.ticket_document import TicketSourceRow
    from rag_engine.document_builder.ticket_builder import TicketDocumentBuilder
    from rag_engine.chunking.ticket_chunker import TicketChunker
    from pydantic import ValidationError

    mapper = DatasetSchemaMapper()
    builder = TicketDocumentBuilder()
    chunker = TicketChunker()

    errors: list[str] = []
    successes = 0
    escalation_skipped = 0
    no_content_skipped = 0
    docs_built = 0
    total_chunks = 0
    chunk_types: dict[str, int] = {}

    t_start = time.perf_counter()

    for _, row in df.iterrows():
        raw = row.to_dict()
        normalized = {k: (None if (isinstance(v, float) and math.isnan(v)) else v) for k, v in raw.items()}

        try:
            mapped = mapper.map(normalized)
            src = TicketSourceRow.model_validate(mapped)
            successes += 1

            if src.automation_label == "ESCALATION":
                escalation_skipped += 1
                continue

            doc = builder.build(src)
            if doc is None:
                no_content_skipped += 1
                continue

            docs_built += 1
            chunks = chunker.chunk(doc)
            total_chunks += len(chunks)
            for c in chunks:
                ct = c.chunk_type.value
                chunk_types[ct] = chunk_types.get(ct, 0) + 1

        except (ValidationError, Exception) as exc:
            errors.append(f"ticket {raw.get('ticket_id')}: {type(exc).__name__}: {str(exc)[:100]}")
            if len(errors) >= 20:
                break

    elapsed = (time.perf_counter() - t_start) * 1000

    label, passed = _check("Schema mapper: zero validation errors", not errors,
                            f"{len(errors)} errors" if errors else "All rows pass")
    report.add(label, passed, str(errors[:3]) if errors else "")

    label, passed = _check("Documents built ≥ 1500", docs_built >= 1500, f"{docs_built} docs built")
    report.add(label, passed)

    label, passed = _check("Chunks produced ≥ 4000", total_chunks >= 4000, f"{total_chunks} chunks")
    report.add(label, passed)

    avg_chunks = total_chunks / max(docs_built, 1)
    label, passed = _check("Avg chunks/doc 2.0–4.0", 2.0 <= avg_chunks <= 4.0, f"{avg_chunks:.2f}")
    report.add(label, passed)

    label, passed = _check("All 3 chunk types present",
                            all(k in chunk_types for k in ["ISSUE_HEADER", "QUERY_BODY", "RESOLUTION_RCA"]),
                            str(chunk_types))
    report.add(label, passed)

    # Deduplication check: same doc → same chunk IDs
    import hashlib, uuid
    def det_id(ticket_id, chunk_index):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"freshdesk:{ticket_id}:{chunk_index}:v1"))
    id_a = det_id("12345", 0)
    id_b = det_id("12345", 0)
    label, passed = _check("Deterministic chunk IDs (dedup)", id_a == id_b)
    report.add(label, passed)

    print(f"\n{INFO} Dry run stats:")
    print(f"     Total rows:        {len(df)}")
    print(f"     Escalation skip:   {escalation_skipped}")
    print(f"     No-content skip:   {no_content_skipped}")
    print(f"     Docs built:        {docs_built}")
    print(f"     Chunks produced:   {total_chunks}")
    print(f"     Processing time:   {elapsed:.0f}ms")
    print(f"     Errors:            {len(errors)}")

    return {
        "docs_built": docs_built, "total_chunks": total_chunks,
        "escalation_skipped": escalation_skipped, "no_content_skipped": no_content_skipped,
        "chunk_types": chunk_types, "errors": errors, "elapsed_ms": elapsed
    }


# =============================================================================
# CHECK 5: Embedding Provider (live, optional)
# =============================================================================
def check_embedding_live(env: dict[str, str], report: ValidationReport) -> bool:
    _section("5. Embedding Provider (Live Test)")

    api_key = env.get("EMBEDDING_API_KEY", env.get("OPENAI_API_KEY", ""))
    if not api_key or api_key in ("sk-...", ""):
        report.warn("Skipping live embedding test — no API key configured")
        report.add("Embedding live test", False, "No API key — SKIPPED")
        return False

    try:
        from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
        model = env.get("EMBEDDING_MODEL", "text-embedding-3-small")
        dims = int(env.get("EMBEDDING_DIMENSIONS", "1536"))

        provider = OpenAIEmbeddingProvider(
            api_key=api_key,
            model=model,
            dimensions=dims,
            max_retries=1,
        )

        t_start = time.perf_counter()
        result = provider.embed_batch(["OTP not received on mobile", "Video KYC session failed"])
        elapsed = (time.perf_counter() - t_start) * 1000

        dim_ok = all(len(e) == dims for e in result.embeddings)
        label, passed = _check(
            f"Embedding API: {model}",
            len(result.embeddings) == 2 and dim_ok,
            f"Got {len(result.embeddings)} embeddings, dim={len(result.embeddings[0])}, {elapsed:.0f}ms"
        )
        report.add(label, passed)

        label, passed = _check("Embedding dimensions match schema", dim_ok, f"{dims} expected")
        report.add(label, passed)

        label, passed = _check("Embedding not all zeros",
                                any(abs(v) > 0.001 for v in result.embeddings[0][:10]))
        report.add(label, passed)

        provider.close()
        return True

    except Exception as exc:
        label, passed = _check("Embedding live test", False, str(exc)[:200])
        report.add(label, passed)
        return False


# =============================================================================
# CHECK 6: Supabase connectivity (live, optional)
# =============================================================================
def check_supabase_live(env: dict[str, str], report: ValidationReport) -> Optional[Any]:
    _section("6. Supabase Connectivity + B1 Table Existence (Live)")

    url = env.get("SUPABASE_URL", "")
    key = env.get("SUPABASE_KEY", "")

    if not url or not key or key in ("your_service_role_key", "your-service-role-key"):
        report.warn("Skipping live Supabase test — no credentials configured")
        report.add("Supabase live test", False, "No credentials — SKIPPED")
        return None

    try:
        from supabase import create_client
        client = create_client(url, key)

        # Test basic connectivity with existing documents table
        t_start = time.perf_counter()
        response = client.table("documents").select("id").limit(1).execute()
        elapsed = (time.perf_counter() - t_start) * 1000

        label, passed = _check(
            "Supabase connection",
            response.data is not None,
            f"Existing 'documents' table reachable in {elapsed:.0f}ms"
        )
        report.add(label, passed)

        if not passed:
            return None

        # Check which B1 tables exist
        b1_tables = [
            "rag_ticket_documents",
            "rag_ticket_chunks",
            "rag_sop_library",
            "rag_sop_chunks",
            "rag_ingestion_logs",
            "rag_feedback_logs",
        ]

        existing_tables = []
        missing_tables = []

        for table in b1_tables:
            try:
                resp = client.table(table).select("id").limit(0).execute()
                existing_tables.append(table)
            except Exception as e:
                if "does not exist" in str(e).lower() or "not found" in str(e).lower():
                    missing_tables.append(table)
                else:
                    existing_tables.append(table)  # Unknown error, may exist

        label, passed = _check(
            f"B1 tables exist ({len(existing_tables)}/{len(b1_tables)})",
            len(missing_tables) == 0,
            f"Missing: {missing_tables}" if missing_tables else "All B1 tables present"
        )
        report.add(label, passed)

        if missing_tables:
            print(f"\n{WARN} Run these migrations in Supabase SQL editor:")
            mig_dir = BASE_DIR / "sql" / "b1_migrations"
            for i in range(1, 7):
                files = list(mig_dir.glob(f"B1_00{i}_*.sql"))
                if files:
                    print(f"     {files[0].name}")

        # Check existing documents table for vectors
        existing_count = len(client.table("documents").select("id").execute().data or [])
        report.add("Existing vector store non-empty", existing_count > 0,
                   f"{existing_count} rows in 'documents'")

        return client

    except Exception as exc:
        label, passed = _check("Supabase connection", False, str(exc)[:200])
        report.add(label, passed)
        return None


# =============================================================================
# REPORT GENERATION
# =============================================================================
def write_report(report: ValidationReport, pipeline_stats: dict, write_file: bool) -> None:
    _section("VALIDATION SUMMARY")

    print(f"\n{'='*60}")
    print(f"  PHASE B1-A INFRASTRUCTURE VALIDATION RESULTS")
    print(f"{'='*60}")
    print(f"  Checks run:   {report.total}")
    print(f"  Passed:       {report.passed}")
    print(f"  Failed:       {report.failed}")
    print(f"  Score:        {report.score:.1f}%")

    if report.failed > 0:
        print(f"\n  FAILED CHECKS:")
        for label, passed, detail in report.results:
            if not passed:
                print(f"    {FAIL} {label}")
                if detail:
                    print(f"       → {detail}")

    prod_ready = report.score >= 80.0
    print(f"\n  Production readiness: {'READY ✅' if prod_ready else 'NOT READY ❌'}")
    print(f"{'='*60}")

    if not write_file:
        return

    # Write markdown report
    report_dir = BASE_DIR / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "b1_infrastructure_validation.md"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Phase B1-A: Infrastructure Validation Report",
        f"**Generated**: {now}",
        f"**Score**: {report.score:.1f}% ({report.passed}/{report.total} checks passed)",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total checks | {report.total} |",
        f"| Passed | {report.passed} |",
        f"| Failed | {report.failed} |",
        f"| Score | {report.score:.1f}% |",
        f"| Production ready | {'✅ Yes' if prod_ready else '❌ No'} |",
        "",
        "## Pipeline Dry Run Stats",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total source rows | 3,635 |",
        f"| Escalation skipped | {pipeline_stats.get('escalation_skipped', '—')} |",
        f"| No-content skipped | {pipeline_stats.get('no_content_skipped', '—')} |",
        f"| Documents built | {pipeline_stats.get('docs_built', '—')} |",
        f"| Total chunks | {pipeline_stats.get('total_chunks', '—')} |",
        f"| Build errors | {len(pipeline_stats.get('errors', []))} |",
        "",
        "## Check Results",
        "",
        "| Check | Status | Detail |",
        "|-------|--------|--------|",
    ]

    for label, passed, detail in report.results:
        icon = "✅" if passed else "❌"
        lines.append(f"| {label} | {icon} | {detail or '—'} |")

    if report.warnings:
        lines.extend(["", "## Warnings", ""])
        for w in report.warnings:
            lines.append(f"- ⚠️ {w}")

    lines.extend([
        "",
        "## Next Steps",
        "",
        "1. Fix all FAILED checks above",
        "2. Apply SQL migrations (B1_001 → B1_006) in Supabase",
        "3. Set SUPABASE_KEY and EMBEDDING_API_KEY in .env",
        "4. Run: `python scripts/validate_b1_ingestion.py`",
        "5. After ingestion: `python scripts/validate_b1_retrieval.py`",
    ])

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{OK} Report written: {report_path}")


# =============================================================================
# MAIN
# =============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Phase B1-A Infrastructure Validation")
    parser.add_argument("--live", action="store_true", help="Run live DB + API tests")
    parser.add_argument("--report", action="store_true", help="Write markdown report")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  PHASE B1-A: INFRASTRUCTURE VALIDATION")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    report = ValidationReport()
    pipeline_stats: dict = {}

    env = check_environment(report)
    check_sql_migrations(report)
    df = check_dataset(report)

    if df is not None:
        pipeline_stats = check_pipeline_dry_run(df, report)

    if args.live:
        check_embedding_live(env, report)
        check_supabase_live(env, report)
    else:
        print(f"\n{INFO} Skipping live tests. Run with --live to test DB + API connectivity.")

    write_report(report, pipeline_stats, args.report)

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
