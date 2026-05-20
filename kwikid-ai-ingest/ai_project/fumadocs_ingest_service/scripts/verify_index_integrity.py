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
from typing import Any

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


def _safe_count(resp: Any) -> int:
    """Extract row count from Supabase response (handles both .count and .data)."""
    if hasattr(resp, "count") and resp.count is not None:
        return int(resp.count)
    return len(resp.data or [])


def _check_table(
    supabase: Any,
    table: str,
    config: dict,
    *,
    client: str | None,
    index_version: str | None,
) -> dict:
    """Run all checks for one chunk table. Returns a structured result dict."""
    result: dict[str, Any] = {
        "table": table,
        "ok": True,
        "issues": [],
        "checks": {},
    }

    def _base_q(extra_selects: str = "id"):
        q = supabase.table(table).select(extra_selects, count="exact")
        if index_version:
            q = q.eq("index_version", index_version)
        if client and config["per_client"]:
            q = q.eq("client", client)
        return q

    # ── 1. Row count by index_version ──────────────────────────────────────
    try:
        if index_version:
            resp = _base_q().execute()
            result["checks"]["row_count"] = {"index_version": index_version, "count": _safe_count(resp)}
        else:
            # Show distribution across all index versions
            dist: dict[str, int] = {}
            for ver in ("v1", "v2"):
                q = supabase.table(table).select("id", count="exact").eq("index_version", ver)
                if client and config["per_client"]:
                    q = q.eq("client", client)
                r = q.execute()
                dist[ver] = _safe_count(r)
            result["checks"]["row_count_by_version"] = dist
    except Exception as exc:
        result["checks"]["row_count_error"] = str(exc)

    # Determine total count for context
    total_q = supabase.table(table).select("id", count="exact")
    if client and config["per_client"]:
        total_q = total_q.eq("client", client)
    if index_version:
        total_q = total_q.eq("index_version", index_version)
    try:
        total_count = _safe_count(total_q.execute())
        result["checks"]["total_rows_in_scope"] = total_count
    except Exception:
        total_count = 0

    if total_count == 0:
        result["issues"].append(f"No rows found in scope (client={client}, index_version={index_version})")
        result["ok"] = False
        return result

    # ── 2. Null embeddings ──────────────────────────────────────────────────
    try:
        null_q = supabase.table(table).select("id", count="exact").is_("embedding", "null")
        if index_version:
            null_q = null_q.eq("index_version", index_version)
        if client and config["per_client"]:
            null_q = null_q.eq("client", client)
        null_count = _safe_count(null_q.execute())
        result["checks"]["null_embeddings"] = null_count
        if null_count > 0:
            pct = round(100.0 * null_count / max(1, total_count), 1)
            issue = f"{null_count} rows ({pct}%) have null embedding"
            result["issues"].append(issue)
            result["ok"] = False
    except Exception as exc:
        result["checks"]["null_embedding_error"] = str(exc)

    # ── 3. Orphan chunks ────────────────────────────────────────────────────
    parent_table = config["parent_table"]
    chunk_fk = config["chunk_fk"]
    parent_pk = config["parent_pk"]

    try:
        # Fetch unique FK values from chunk table (limit for performance)
        fk_q = supabase.table(table).select(chunk_fk)
        if index_version:
            fk_q = fk_q.eq("index_version", index_version)
        if client and config["per_client"]:
            fk_q = fk_q.eq("client", client)
        fk_resp = fk_q.limit(5000).execute()
        chunk_fk_values = {str(r[chunk_fk]) for r in (fk_resp.data or []) if r.get(chunk_fk)}

        parent_resp = supabase.table(parent_table).select(parent_pk).execute()
        parent_ids = {str(r[parent_pk]) for r in (parent_resp.data or []) if r.get(parent_pk)}

        orphans = chunk_fk_values - parent_ids
        result["checks"]["orphan_chunks"] = {
            "count": len(orphans),
            "sample": list(orphans)[:5],
        }
        if orphans:
            issue = f"{len(orphans)} orphan FK values (parent rows deleted from {parent_table})"
            result["issues"].append(issue)
            result["ok"] = False
    except Exception as exc:
        result["checks"]["orphan_check_error"] = str(exc)

    # ── 4. Missing content_hash ─────────────────────────────────────────────
    try:
        hash_q = supabase.table(table).select("id", count="exact").is_("content_hash", "null")
        if index_version:
            hash_q = hash_q.eq("index_version", index_version)
        if client and config["per_client"]:
            hash_q = hash_q.eq("client", client)
        missing_hash = _safe_count(hash_q.execute())
        result["checks"]["missing_content_hash"] = missing_hash
        if missing_hash > 0:
            result["issues"].append(f"{missing_hash} rows missing content_hash")
    except Exception as exc:
        result["checks"]["content_hash_error"] = str(exc)

    # ── 5. Quality score distribution (knowledge chunks only) ───────────────
    if config["has_quality_score"]:
        try:
            all_q = supabase.table(table).select("quality_score")
            if index_version:
                all_q = all_q.eq("index_version", index_version)
            qs_resp = all_q.execute()
            scores = [float(r["quality_score"]) for r in (qs_resp.data or []) if r.get("quality_score") is not None]
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

    # ── 6. Null FTS column (B1_007) ─────────────────────────────────────────
    if config["has_fts"]:
        try:
            fts_q = supabase.table(table).select("id", count="exact").is_("fts", "null")
            if index_version:
                fts_q = fts_q.eq("index_version", index_version)
            if client and config["per_client"]:
                fts_q = fts_q.eq("client", client)
            null_fts = _safe_count(fts_q.execute())
            result["checks"]["null_fts"] = null_fts
            # null fts is expected if B1_007 not yet applied — warn but don't fail
            if null_fts > 0:
                pct = round(100.0 * null_fts / max(1, total_count), 1)
                result["checks"]["fts_note"] = (
                    f"{null_fts} ({pct}%) rows have null fts — apply B1_007_fts_setup.sql if not done"
                )
        except Exception as exc:
            result["checks"]["fts_error"] = str(exc)

    # ── 7. Client distribution (ticket only) ───────────────────────────────
    if config["per_client"] and not client:
        try:
            dist_resp = supabase.table(table).select("client").execute()
            client_dist: dict[str, int] = {}
            for row in (dist_resp.data or []):
                c = row.get("client", "unknown")
                client_dist[c] = client_dist.get(c, 0) + 1
            result["checks"]["client_distribution"] = dict(
                sorted(client_dist.items(), key=lambda kv: kv[1], reverse=True)
            )
        except Exception as exc:
            result["checks"]["client_dist_error"] = str(exc)

    return result


def _run_checks(
    supabase: Any,
    *,
    tables: list[str],
    client: str | None,
    index_version: str | None,
) -> dict:
    """Run all checks across selected tables and return full report."""
    report: dict[str, Any] = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "client_filter": client,
        "index_version_filter": index_version,
        "tables": {},
        "summary": {"total_issues": 0, "tables_with_issues": 0, "overall_ok": True},
    }

    for table in tables:
        cfg = _TABLE_CONFIG[table]
        LOGGER.info("Checking %s ...", table)
        t0 = time.perf_counter()
        result = _check_table(supabase, table, cfg, client=client, index_version=index_version)
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
    return p.parse_args()


def main() -> int:
    args = _parse_args()

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
        "Starting integrity check: tables=%s client=%s index_version=%s",
        tables, args.client, args.index_version,
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
        LOGGER.info(
            "Integrity check PASSED — all %d tables clean",
            len(tables),
        )
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
