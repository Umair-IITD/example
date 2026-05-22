#!/usr/bin/env python3
"""
scripts/verify_index_integrity.py

Read-only integrity check for the B1/B3 RAG chunk tables in Supabase.

Checks performed per table:
  1. Row counts by index_version
  2. Null embeddings (rows that were written but not embedded)
  3. Orphan chunks (FK references to deleted parent documents)
  4. Missing content_hash values
  5. Quality score distribution (knowledge chunks only)
  6. Null FTS tsvector column (when B1_007 migration is applied)
  7. Client distribution (ticket chunks only)

PAGINATION:
  Supabase's PostgREST REST API returns HTTP 206 Partial Content when results
  exceed the server's max-rows limit (default: 1000). All queries that fetch
  actual row data (not just counts) use _fetch_all_paginated() to ensure
  complete results regardless of table size.

  Count-only queries (using count="exact") are NOT affected by pagination —
  Supabase returns the total count in resp.count even for large tables.

Usage:
  python scripts/verify_index_integrity.py
  python scripts/verify_index_integrity.py --client unity_bank
  python scripts/verify_index_integrity.py --index-version v2
  python scripts/verify_index_integrity.py --output report.json
  python scripts/verify_index_integrity.py --table ticket
  python scripts/verify_index_integrity.py --fail-on-issues

Exit codes:
  0 — all checks passed (or --fail-on-issues not set)
  1 — issues detected (when --fail-on-issues is set)
  2 — connection / configuration error

Environment:
  SUPABASE_URL  — required
  SUPABASE_KEY  — required (service role key)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("verify_index_integrity")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()


# ── Table configuration ───────────────────────────────────────────────────────

_TABLE_CONFIG = {
    "rag_ticket_chunks": {
        "short": "ticket",
        "parent_table": "rag_ticket_documents",
        "chunk_fk": "document_id",
        "parent_pk": "id",
        "per_client": True,      # uses scalar `client` column
        "has_quality_score": False,
        "has_fts": True,
    },
    "rag_sop_chunks": {
        "short": "sop",
        "parent_table": "rag_sop_library",
        "chunk_fk": "sop_id",
        "parent_pk": "sop_id",
        "per_client": False,     # uses `clients[]` array — global
        "has_quality_score": False,
        "has_fts": True,
    },
    "rag_knowledge_chunks": {
        "short": "knowledge",
        "parent_table": "rag_knowledge_articles",
        "chunk_fk": "article_id",
        "parent_pk": "article_id",
        "per_client": False,
        "has_quality_score": True,
        "has_fts": True,
    },
}

# ── Supabase PostgREST page size ──────────────────────────────────────────────
# Default PostgREST max-rows is 1000. All paginated fetches use this as the
# page size. If your Supabase project uses a lower limit, reduce this value.
_PAGE_SIZE = 1000


# ── Pagination helper ─────────────────────────────────────────────────────────

def _fetch_all_paginated(
    supabase: Any,
    table: str,
    select_cols: str,
    *,
    eq_filters: Optional[dict[str, Any]] = None,
    page_size: int = _PAGE_SIZE,
    description: str = "",
) -> list[dict]:
    """
    Fetch ALL rows from a Supabase table using Range-based pagination.

    Supabase's PostgREST returns HTTP 206 Partial Content and silently
    truncates results at the server's max-rows limit (default: 1000).
    Callers MUST paginate to get the complete result set.

    This function rebuilds a fresh query builder on each page iteration
    because the supabase-py builder is stateful — calling .range() on
    an existing builder mutates it.

    Args:
        supabase:    Supabase client instance
        table:       Table name (e.g. "rag_ticket_chunks")
        select_cols: Columns to select ("col1,col2" or "col1" or "*")
        eq_filters:  Optional {column: value} equality filters.
                     None values are skipped.
        page_size:   Rows per page (max 1000 for standard PostgREST)
        description: Label for log messages (defaults to table name)

    Returns:
        Complete list of all matching rows across all pages.

    Raises:
        Exception: Re-raises Supabase client errors. Never returns partial
                   data silently — callers must handle or propagate errors.
    """
    label = description or table
    all_rows: list[dict] = []
    offset = 0
    page_num = 0

    while True:
        page_num += 1

        # Rebuild fresh query builder each iteration — builder is stateful,
        # calling .range() on the same instance does not reset to a new offset.
        q = supabase.table(table).select(select_cols)
        if eq_filters:
            for col, val in eq_filters.items():
                if val is not None:
                    q = q.eq(col, val)
        q = q.range(offset, offset + page_size - 1)

        resp = q.execute()
        batch = resp.data or []
        fetched = len(batch)
        all_rows.extend(batch)

        LOGGER.debug(
            "_fetch_all_paginated [%s] page=%d offset=%d fetched=%d total=%d",
            label, page_num, offset, fetched, len(all_rows),
        )

        # Fewer rows than page_size means we have reached the last page.
        if fetched < page_size:
            break

        offset += page_size

    if page_num > 1:
        LOGGER.info(
            "Paginated fetch [%s]: %d rows retrieved in %d pages (page_size=%d)",
            label, len(all_rows), page_num, page_size,
        )

    return all_rows


# ── Count helper ──────────────────────────────────────────────────────────────

def _safe_count(resp: Any) -> int:
    """Extract row count from a Supabase count="exact" response."""
    if hasattr(resp, "count") and resp.count is not None:
        return int(resp.count)
    return len(resp.data or [])


# ── Per-table check ───────────────────────────────────────────────────────────

def _check_table(
    supabase: Any,
    table: str,
    config: dict,
    *,
    client: Optional[str],
    index_version: Optional[str],
) -> dict:
    """
    Run all integrity checks for one chunk table.

    All checks that fetch row data use _fetch_all_paginated() to ensure
    correctness even when tables exceed 1000 rows. Count-only checks use
    count="exact" which is unaffected by pagination.

    Returns a structured result dict.
    """
    result: dict[str, Any] = {
        "table": table,
        "ok": True,
        "issues": [],
        "checks": {},
    }

    # ── Shared count query builder ─────────────────────────────────────────
    def _count_q(extra_is_null: Optional[str] = None):
        """Build a count="exact" query with standard filters."""
        q = supabase.table(table).select("id", count="exact")
        if index_version:
            q = q.eq("index_version", index_version)
        if client and config["per_client"]:
            q = q.eq("client", client)
        if extra_is_null:
            q = q.is_(extra_is_null, "null")
        return q

    # ── 1. Row count by index_version ──────────────────────────────────────
    # Uses count="exact" — not affected by pagination.
    try:
        if index_version:
            resp = _count_q().execute()
            result["checks"]["row_count"] = {
                "index_version": index_version,
                "count": _safe_count(resp),
            }
        else:
            dist: dict[str, int] = {}
            for ver in ("v1", "v2"):
                q = supabase.table(table).select("id", count="exact").eq("index_version", ver)
                if client and config["per_client"]:
                    q = q.eq("client", client)
                dist[ver] = _safe_count(q.execute())
            result["checks"]["row_count_by_version"] = dist
    except Exception as exc:
        result["checks"]["row_count_error"] = str(exc)

    # ── Determine in-scope total row count ─────────────────────────────────
    try:
        total_count = _safe_count(_count_q().execute())
        result["checks"]["total_rows_in_scope"] = total_count
    except Exception:
        total_count = 0

    if total_count == 0:
        result["issues"].append(
            f"No rows found in scope (client={client}, index_version={index_version})"
        )
        result["ok"] = False
        return result

    # ── 2. Null embeddings ─────────────────────────────────────────────────
    # Uses count="exact" — not affected by pagination.
    try:
        null_count = _safe_count(_count_q("embedding").execute())
        result["checks"]["null_embeddings"] = null_count
        if null_count > 0:
            pct = round(100.0 * null_count / max(1, total_count), 1)
            issue = f"{null_count} rows ({pct}%) have null embedding"
            result["issues"].append(issue)
            result["ok"] = False
    except Exception as exc:
        result["checks"]["null_embedding_error"] = str(exc)

    # ── 3. Orphan chunk detection ──────────────────────────────────────────
    # CRITICAL: Both the child FK fetch AND the parent PK fetch must be fully
    # paginated. A partial read of either set produces false orphan detections.
    # The original bug was an HTTP 206 truncation of both result sets.
    parent_table = config["parent_table"]
    chunk_fk = config["chunk_fk"]
    parent_pk = config["parent_pk"]

    try:
        # Build eq_filters for child chunk fetch
        child_filters: dict[str, Any] = {}
        if index_version:
            child_filters["index_version"] = index_version
        if client and config["per_client"]:
            child_filters["client"] = client

        # Fetch ALL chunk FK values — paginated to handle large tables
        fk_rows = _fetch_all_paginated(
            supabase, table, chunk_fk,
            eq_filters=child_filters or None,
            description=f"{table}.{chunk_fk} (child FKs)",
        )
        chunk_fk_values = {str(r[chunk_fk]) for r in fk_rows if r.get(chunk_fk)}

        # Fetch ALL parent primary keys — paginated to handle large tables.
        # No client filter here: parent tables are not scoped per-tenant.
        parent_rows = _fetch_all_paginated(
            supabase, parent_table, parent_pk,
            description=f"{parent_table}.{parent_pk} (parent PKs)",
        )
        parent_ids = {str(r[parent_pk]) for r in parent_rows if r.get(parent_pk)}

        orphans = chunk_fk_values - parent_ids

        result["checks"]["orphan_chunks"] = {
            "count": len(orphans),
            "chunk_fk_total": len(chunk_fk_values),
            "parent_pk_total": len(parent_ids),
            "sample": sorted(list(orphans))[:5],  # sorted for deterministic output
        }
        LOGGER.info(
            "  [%s] orphan check: %d chunk FKs vs %d parent PKs → %d orphan(s)",
            table, len(chunk_fk_values), len(parent_ids), len(orphans),
        )

        if orphans:
            issue = (
                f"{len(orphans)} orphan FK value(s) in {chunk_fk} "
                f"not found in {parent_table}.{parent_pk}"
            )
            result["issues"].append(issue)
            result["ok"] = False

    except Exception as exc:
        result["checks"]["orphan_check_error"] = str(exc)
        LOGGER.warning("  [%s] orphan check failed: %s", table, exc)

    # ── 4. Missing content_hash ────────────────────────────────────────────
    # Uses count="exact" — not affected by pagination.
    try:
        missing_hash = _safe_count(_count_q("content_hash").execute())
        result["checks"]["missing_content_hash"] = missing_hash
        if missing_hash > 0:
            result["issues"].append(f"{missing_hash} rows missing content_hash")
    except Exception as exc:
        result["checks"]["content_hash_error"] = str(exc)

    # ── 5. Quality score distribution (knowledge chunks only) ──────────────
    # Fetches actual values — must be paginated for complete distribution.
    if config["has_quality_score"]:
        try:
            qs_filters: dict[str, Any] = {}
            if index_version:
                qs_filters["index_version"] = index_version
            if client and config["per_client"]:
                qs_filters["client"] = client

            qs_rows = _fetch_all_paginated(
                supabase, table, "quality_score",
                eq_filters=qs_filters or None,
                description=f"{table}.quality_score",
            )
            scores = [
                float(r["quality_score"])
                for r in qs_rows
                if r.get("quality_score") is not None
            ]
            if scores:
                result["checks"]["quality_score"] = {
                    "count": len(scores),
                    "min": round(min(scores), 3),
                    "max": round(max(scores), 3),
                    "avg": round(sum(scores) / len(scores), 3),
                    "below_0.40": sum(1 for s in scores if s < 0.40),
                    "below_0.55": sum(1 for s in scores if s < 0.55),
                }
        except Exception as exc:
            result["checks"]["quality_score_error"] = str(exc)

    # ── 6. Null FTS column (post-B1_007) ──────────────────────────────────
    # Uses count="exact" — not affected by pagination. Warns, does not fail.
    if config["has_fts"]:
        try:
            null_fts = _safe_count(_count_q("fts").execute())
            result["checks"]["null_fts"] = null_fts
            if null_fts > 0:
                pct = round(100.0 * null_fts / max(1, total_count), 1)
                result["checks"]["fts_note"] = (
                    f"{null_fts} ({pct}%) rows have null fts — "
                    "apply sql/b1_migrations/B1_007_fts_setup.sql if not yet done"
                )
        except Exception as exc:
            result["checks"]["fts_error"] = str(exc)

    # ── 7. Client distribution (ticket chunks, all-tenants mode only) ──────
    # Fetches actual values — must be paginated for complete distribution.
    if config["per_client"] and not client:
        try:
            dist_rows = _fetch_all_paginated(
                supabase, table, "client",
                eq_filters={"index_version": index_version} if index_version else None,
                description=f"{table}.client (distribution)",
            )
            client_dist: dict[str, int] = {}
            for row in dist_rows:
                c = row.get("client") or "unknown"
                client_dist[c] = client_dist.get(c, 0) + 1
            result["checks"]["client_distribution"] = dict(
                sorted(client_dist.items(), key=lambda kv: kv[1], reverse=True)
            )
        except Exception as exc:
            result["checks"]["client_dist_error"] = str(exc)

    return result


# ── Run all table checks ──────────────────────────────────────────────────────

def _run_checks(
    supabase: Any,
    *,
    tables: list[str],
    client: Optional[str],
    index_version: Optional[str],
) -> dict:
    """Run all checks across selected tables and return full report."""
    report: dict[str, Any] = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client_filter": client,
        "index_version_filter": index_version,
        "pagination_page_size": _PAGE_SIZE,
        "tables": {},
        "summary": {"total_issues": 0, "tables_with_issues": 0, "overall_ok": True},
    }

    for table in tables:
        cfg = _TABLE_CONFIG[table]
        LOGGER.info("Checking %s ...", table)
        t0 = time.perf_counter()
        result = _check_table(
            supabase, table, cfg,
            client=client, index_version=index_version,
        )
        result["duration_s"] = round(time.perf_counter() - t0, 2)

        report["tables"][table] = result
        if result["issues"]:
            report["summary"]["tables_with_issues"] += 1
            report["summary"]["total_issues"] += len(result["issues"])
            report["summary"]["overall_ok"] = False
            for issue in result["issues"]:
                LOGGER.warning("  [%s] ISSUE: %s", table, issue)
        else:
            LOGGER.info("  [%s] OK (%.2fs)", table, result["duration_s"])

    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only integrity check for B1/B3 RAG chunk tables",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--client", metavar="SLUG",
        help="Scope to a specific tenant slug (ticket chunks only). Omit for all tenants.",
    )
    p.add_argument(
        "--index-version", metavar="VERSION",
        help="Scope to a specific index version (v1, v2). Omit for all versions.",
    )
    p.add_argument(
        "--table",
        nargs="+",
        choices=["ticket", "sop", "knowledge", "all"],
        default=["all"],
        help="Which tables to check (default: all)",
    )
    p.add_argument(
        "--output", metavar="PATH",
        help="Write JSON report to this file path. Also always printed to stdout.",
    )
    p.add_argument(
        "--fail-on-issues", action="store_true",
        help="Exit with code 1 if any issues are found (useful in CI).",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG-level logging (pagination details, per-page counts).",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_KEY", "").strip()

    if not supabase_url or not supabase_key:
        LOGGER.error("SUPABASE_URL and SUPABASE_KEY must be set in environment")
        return 2

    try:
        from supabase import create_client  # noqa: PLC0415
    except ImportError:
        LOGGER.error("supabase-py not installed. Run: pip install supabase")
        return 2

    supabase = create_client(supabase_url, supabase_key)

    selected = set(args.table)
    if "all" in selected:
        tables = list(_TABLE_CONFIG.keys())
    else:
        short_to_full = {cfg["short"]: tbl for tbl, cfg in _TABLE_CONFIG.items()}
        tables = [short_to_full[s] for s in selected if s in short_to_full]

    LOGGER.info(
        "Starting integrity check: tables=%s client=%s index_version=%s page_size=%d",
        tables, args.client, args.index_version, _PAGE_SIZE,
    )

    report = _run_checks(
        supabase,
        tables=tables,
        client=args.client,
        index_version=args.index_version,
    )

    report_json = json.dumps(report, indent=2)
    print(report_json)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report_json)
            LOGGER.info("Report written to %s", args.output)
        except Exception as exc:
            LOGGER.warning("Could not write report to %s: %s", args.output, exc)

    summary = report["summary"]
    if summary["overall_ok"]:
        LOGGER.info("Integrity check PASSED — all %d tables clean", len(tables))
    else:
        LOGGER.error(
            "Integrity check FAILED — %d issue(s) across %d table(s)",
            summary["total_issues"],
            summary["tables_with_issues"],
        )

    if args.fail_on_issues and not summary["overall_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
