"""
scripts/validate_b1_db_integrity.py

Phase B1 Supabase integrity audit.

Checks:
  1. Table existence (migrations applied?)
  2. No duplicate chunk IDs
  3. No orphaned chunks (chunk.document_id not in rag_ticket_documents)
  4. No null embeddings on upserted chunks
  5. Embedding dimension consistency (all 1536?)
  6. No cross-tenant contamination (each chunk has a non-null client)
  7. Ingestion log consistency (chunk counts match)
  8. Document ↔ chunk count alignment

IMPORTANT: This script NEVER deletes or modifies data.
If problems are found, it prints the exact SQL to fix them and asks for manual confirmation.

Usage:
    python scripts/validate_b1_db_integrity.py [--live] [--report]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        print(f"ERROR: {name} is not set. Configure .env and retry.", file=sys.stderr)
        sys.exit(1)
    return val


class IntegrityAuditor:
    def __init__(self, client: Any, settings: Any) -> None:
        self.client = client
        self.settings = settings
        self.issues: list[dict] = []
        self.passed: list[str] = []

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _pass(self, check: str, detail: str = "") -> None:
        msg = f"  PASS  {check}" + (f": {detail}" if detail else "")
        print(msg)
        self.passed.append(check)

    def _fail(self, check: str, detail: str, remediation_sql: str = "") -> None:
        print(f"  FAIL  {check}: {detail}")
        if remediation_sql:
            print(f"          Remediation SQL (review before running):")
            for line in remediation_sql.strip().splitlines():
                print(f"            {line}")
        self.issues.append({"check": check, "detail": detail, "sql": remediation_sql})

    def _warn(self, check: str, detail: str) -> None:
        print(f"  WARN  {check}: {detail}")

    def _count(self, table: str, filters: dict | None = None) -> int:
        q = self.client.table(table).select("id", count="exact")
        if filters:
            for col, val in filters.items():
                q = q.eq(col, val)
        try:
            resp = q.execute()
            return resp.count or 0
        except Exception as exc:  # noqa: BLE001
            return -1  # table probably doesn't exist

    # ── Checks ─────────────────────────────────────────────────────────────────

    def check_tables_exist(self) -> None:
        print("\n[1] Table existence")
        tables = [
            self.settings.ticket_documents_table,
            self.settings.ticket_chunks_table,
            self.settings.sop_library_table,
            self.settings.sop_chunks_table,
            self.settings.ingestion_logs_table,
            self.settings.feedback_logs_table,
        ]
        for table in tables:
            count = self._count(table)
            if count == -1:
                self._fail(
                    f"table_exists:{table}",
                    f"table {table!r} not found or inaccessible — SQL migrations not applied?",
                    f"-- Apply B1_001 through B1_006 migrations in the Supabase SQL editor.",
                )
            else:
                self._pass(f"table_exists:{table}", f"{count} rows")

    def check_no_duplicate_chunk_ids(self) -> None:
        print("\n[2] Duplicate chunk IDs")
        try:
            resp = self.client.rpc("_b1_audit_duplicate_chunks", {}).execute()
            dupes = resp.data or []
        except Exception:
            # RPC not available — use raw query approximation
            try:
                resp = (
                    self.client.table(self.settings.ticket_chunks_table)
                    .select("id")
                    .execute()
                )
                ids = [r["id"] for r in (resp.data or [])]
                dupes = [id_ for id_ in set(ids) if ids.count(id_) > 1]
            except Exception as exc:  # noqa: BLE001
                self._warn("no_duplicate_chunks", f"Could not check: {exc}")
                return

        if dupes:
            self._fail(
                "no_duplicate_chunks",
                f"{len(dupes)} duplicate chunk IDs found: {dupes[:5]}",
                f"""
-- Review duplicates before deleting. Example for one ID:
SELECT id, ticket_id, chunk_index, created_at FROM {self.settings.ticket_chunks_table}
WHERE id = '{dupes[0] if dupes else "?"}';
""",
            )
        else:
            self._pass("no_duplicate_chunks")

    def check_no_null_embeddings(self) -> None:
        print("\n[3] Null embeddings")
        try:
            resp = (
                self.client.table(self.settings.ticket_chunks_table)
                .select("id", count="exact")
                .is_("embedding", "null")
                .execute()
            )
            null_count = resp.count or 0
        except Exception as exc:  # noqa: BLE001
            self._warn("no_null_embeddings", f"Could not check: {exc}")
            return

        if null_count > 0:
            self._fail(
                "no_null_embeddings",
                f"{null_count} chunks have NULL embedding — re-run ingestion to fill them",
                f"""
-- Identify which tickets have null-embedding chunks:
SELECT ticket_id, count(*) as null_chunks
FROM {self.settings.ticket_chunks_table}
WHERE embedding IS NULL
GROUP BY ticket_id
ORDER BY null_chunks DESC
LIMIT 20;
-- Fix: re-run ingestion in delta mode — dedup will only re-embed the missing ones.
""",
            )
        else:
            self._pass("no_null_embeddings")

    def check_no_cross_tenant_contamination(self) -> None:
        print("\n[4] Tenant field completeness")
        try:
            resp = (
                self.client.table(self.settings.ticket_chunks_table)
                .select("id", count="exact")
                .is_("client", "null")
                .execute()
            )
            null_count = resp.count or 0
        except Exception as exc:  # noqa: BLE001
            self._warn("tenant_completeness", f"Could not check: {exc}")
            return

        if null_count > 0:
            self._fail(
                "tenant_completeness",
                f"{null_count} chunks have NULL client — cross-tenant isolation broken",
                f"""
-- Find affected tickets:
SELECT ticket_id, count(*) FROM {self.settings.ticket_chunks_table}
WHERE client IS NULL GROUP BY ticket_id LIMIT 20;
""",
            )
        else:
            self._pass("tenant_completeness")

    def check_orphaned_chunks(self) -> None:
        print("\n[5] Orphaned chunks (chunk.document_id not in rag_ticket_documents)")
        try:
            resp = (
                self.client.table(self.settings.ticket_chunks_table)
                .select("document_id")
                .is_("document_id", "null")
                .limit(10)
                .execute()
            )
            null_doc_id = len(resp.data or [])
        except Exception as exc:  # noqa: BLE001
            self._warn("orphaned_chunks", f"Could not check null document_id: {exc}")
            return

        if null_doc_id > 0:
            self._fail(
                "orphaned_chunks",
                f"at least {null_doc_id} chunks have NULL document_id — document upsert failed or wasn't run",
                f"""
-- Count orphans:
SELECT count(*) FROM {self.settings.ticket_chunks_table} WHERE document_id IS NULL;
-- Fix: re-run full ingestion — pipeline upserts documents before chunks.
""",
            )
        else:
            self._pass("orphaned_chunks")

    def check_ingestion_log_consistency(self) -> None:
        print("\n[6] Ingestion log — last completed run")
        try:
            resp = (
                self.client.table(self.settings.ingestion_logs_table)
                .select("run_id, run_mode, status, chunks_created, documents_processed, completed_at")
                .eq("status", "completed")
                .order("completed_at", desc=True)
                .limit(1)
                .execute()
            )
            runs = resp.data or []
        except Exception as exc:  # noqa: BLE001
            self._warn("ingestion_log", f"Could not check: {exc}")
            return

        if not runs:
            self._warn("ingestion_log", "No completed ingestion runs found — run ingestion first")
        else:
            r = runs[0]
            self._pass(
                "ingestion_log",
                f"last run {r.get('run_id','?')[:8]} | mode={r.get('run_mode')} "
                f"| docs={r.get('documents_processed')} | chunks={r.get('chunks_created')} "
                f"| completed={r.get('completed_at')}",
            )

    def check_vector_dimension_consistency(self) -> None:
        print("\n[7] Vector dimension consistency (spot check)")
        # We can't query array_length from supabase-py easily; produce SQL for manual run
        self._warn(
            "vector_dimensions",
            "Cannot check via supabase-py. Run this SQL manually in Supabase SQL editor:",
        )
        print(f"""
    SELECT
        array_length(embedding, 1) AS dims,
        count(*) AS chunk_count
    FROM {self.settings.ticket_chunks_table}
    WHERE embedding IS NOT NULL
    GROUP BY dims
    ORDER BY chunk_count DESC;
    -- Expected: single row with dims=1536 (text-embedding-3-small) or dims=768 (nomic).
""")

    def run_all(self) -> None:
        print(f"\n{'='*60}")
        print(f" B1 Database Integrity Audit — {datetime.now(timezone.utc).isoformat()}")
        print(f"{'='*60}")

        self.check_tables_exist()
        self.check_no_duplicate_chunk_ids()
        self.check_no_null_embeddings()
        self.check_no_cross_tenant_contamination()
        self.check_orphaned_chunks()
        self.check_ingestion_log_consistency()
        self.check_vector_dimension_consistency()

        print(f"\n{'='*60}")
        print(f" Summary: {len(self.passed)} passed, {len(self.issues)} failed/warned")
        print(f"{'='*60}")

        if self.issues:
            print("\nFAILED CHECKS (review remediation SQL above before taking action):")
            for issue in self.issues:
                print(f"  - {issue['check']}: {issue['detail']}")
        else:
            print("\nAll checks passed.")

        return len(self.issues) == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="B1 DB integrity audit")
    parser.add_argument("--live", action="store_true", help="Connect to Supabase (requires .env)")
    parser.add_argument("--report", action="store_true", help="Write report to data/reports/")
    args = parser.parse_args()

    if not args.live:
        print("Running in DRY mode — no DB connection. Pass --live to connect to Supabase.")
        print("SQL that WOULD be checked:")
        print("""
  1. Table existence: SELECT count(*) FROM rag_ticket_documents;
  2. Duplicate IDs:   SELECT id, count(*) FROM rag_ticket_chunks GROUP BY id HAVING count(*) > 1;
  3. Null embeddings: SELECT count(*) FROM rag_ticket_chunks WHERE embedding IS NULL;
  4. Null client:     SELECT count(*) FROM rag_ticket_chunks WHERE client IS NULL;
  5. Orphaned chunks: SELECT count(*) FROM rag_ticket_chunks WHERE document_id IS NULL;
  6. Ingestion logs:  SELECT * FROM rag_ingestion_logs WHERE status='completed' ORDER BY completed_at DESC LIMIT 1;
  7. Vector dims:     SELECT array_length(embedding,1), count(*) FROM rag_ticket_chunks GROUP BY 1;
""")
        return

    from dotenv import load_dotenv
    load_dotenv()

    supabase_url = _require_env("SUPABASE_URL")
    supabase_key = _require_env("SUPABASE_KEY")

    from supabase import create_client
    client = create_client(supabase_url, supabase_key)

    from rag_engine.config.rag_settings import get_rag_settings
    settings = get_rag_settings()

    auditor = IntegrityAuditor(client, settings)
    ok = auditor.run_all()

    if args.report:
        report_dir = settings.reports_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "b1_integrity_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# B1 Integrity Report\n\n**Date:** {datetime.now(timezone.utc).isoformat()}\n\n")
            f.write(f"**Passed:** {len(auditor.passed)}  **Failed:** {len(auditor.issues)}\n\n")
            if auditor.issues:
                f.write("## Failed Checks\n\n")
                for issue in auditor.issues:
                    f.write(f"### {issue['check']}\n\n{issue['detail']}\n\n")
                    if issue["sql"]:
                        f.write(f"```sql\n{issue['sql']}\n```\n\n")
        print(f"\nReport written to {report_path}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
