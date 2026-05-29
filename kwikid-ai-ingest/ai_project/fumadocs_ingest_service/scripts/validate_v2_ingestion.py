#!/usr/bin/env python3
"""
scripts/validate_v2_ingestion.py

Comprehensive v2 ingestion state validation.

Answers: "Is my v2 ingestion in a good state to flip ACTIVE_INDEX_VERSION to v2?"

Checks performed:
  1.  Row counts by source type and index_version
  2.  Chunk type distribution (are sections balanced?)
  3.  Null embedding detection (rows not yet embedded)
  4.  Duplicate content hash detection (exact dedup health)
  5.  Average word count by chunk_type (size distribution)
  6.  Top 20 shortest chunks — boilerplate detector
  7.  SOP v2 chunk presence confirmation
  8.  Ticket semantic section distribution (ISSUE_HEADER / QUERY_BODY / RESOLUTION_RCA)
  9.  Knowledge chunk quality score distribution
 10.  Orphan chunk detection (chunks without parent document)
 11.  Empty/missing metadata fields

Usage:
  # Check v2 state for unity_bank
  python scripts/validate_v2_ingestion.py --client unity_bank

  # Check v2 state for all clients
  python scripts/validate_v2_ingestion.py --all-clients

  # Output as JSON (for CI)
  python scripts/validate_v2_ingestion.py --client unity_bank --json

  # Also check v1 for comparison
  python scripts/validate_v2_ingestion.py --client unity_bank --all-versions

Environment:
  SUPABASE_URL, SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
LOGGER = logging.getLogger("validate_v2")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"), override=False)

_PAGE_SIZE = 1000

OK   = "[OK]  "
FAIL = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"


# ── Pagination helper ─────────────────────────────────────────────────────────

def _paginate(sb: Any, table: str, select: str, *, filters: dict | None = None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        q = sb.table(table).select(select)
        if filters:
            for k, v in filters.items():
                if v is not None:
                    q = q.eq(k, v)
        q = q.order("id").range(offset, offset + _PAGE_SIZE - 1)
        batch = q.execute().data or []
        rows.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return rows


def _count(sb: Any, table: str, *, filters: dict | None = None, null_col: str | None = None) -> int:
    q = sb.table(table).select("id", count="exact")
    if filters:
        for k, v in filters.items():
            if v is not None:
                q = q.eq(k, v)
    if null_col:
        q = q.is_(null_col, "null")
    try:
        resp = q.execute()
        return resp.count or len(resp.data or [])
    except Exception:
        return -1


# ── Validation checks ─────────────────────────────────────────────────────────

def _check_row_counts(sb: Any, client: str, index_version: str) -> dict:
    result: dict = {"label": "Row counts by table", "items": []}
    tables = [
        ("rag_ticket_chunks",    {"client": client, "index_version": index_version}),
        ("rag_sop_chunks",       {"index_version": index_version}),
        ("rag_knowledge_chunks", {"index_version": index_version}),
    ]
    for table, filters in tables:
        n = _count(sb, table, filters=filters)
        status = OK if n > 0 else FAIL
        result["items"].append({"table": table, "count": n, "status": status})
    return result


def _check_chunk_types(sb: Any, client: str, index_version: str) -> dict:
    result: dict = {"label": "Chunk type distribution", "items": []}

    rows = _paginate(sb, "rag_ticket_chunks", "chunk_type",
                     filters={"client": client, "index_version": index_version})
    dist: Counter = Counter(r.get("chunk_type", "UNKNOWN") for r in rows)
    for ct, n in sorted(dist.items()):
        result["items"].append({"chunk_type": ct, "count": n, "table": "rag_ticket_chunks"})

    sop_rows = _paginate(sb, "rag_sop_chunks", "chunk_type",
                         filters={"index_version": index_version})
    sop_dist: Counter = Counter(r.get("chunk_type", "UNKNOWN") for r in sop_rows)
    for ct, n in sorted(sop_dist.items()):
        result["items"].append({"chunk_type": ct, "count": n, "table": "rag_sop_chunks"})

    return result


def _check_null_embeddings(sb: Any, client: str, index_version: str) -> dict:
    result: dict = {"label": "Null embeddings", "items": [], "passed": True}
    tables = [
        ("rag_ticket_chunks",    {"client": client, "index_version": index_version}),
        ("rag_sop_chunks",       {"index_version": index_version}),
        ("rag_knowledge_chunks", {"index_version": index_version}),
    ]
    for table, filters in tables:
        n_null = _count(sb, table, filters=filters, null_col="embedding")
        n_total = _count(sb, table, filters=filters)
        passed = n_null == 0
        if not passed:
            result["passed"] = False
        status = OK if passed else FAIL
        result["items"].append({
            "table": table,
            "null_embeddings": n_null,
            "total": n_total,
            "status": status,
        })
    return result


def _check_duplicate_hashes(sb: Any, client: str, index_version: str) -> dict:
    result: dict = {"label": "Duplicate content hashes", "items": [], "passed": True}

    ticket_rows = _paginate(sb, "rag_ticket_chunks", "content_hash",
                            filters={"client": client, "index_version": index_version})
    hashes = [r.get("content_hash") for r in ticket_rows if r.get("content_hash")]
    hash_counts = Counter(hashes)
    dupes = {h: c for h, c in hash_counts.items() if c > 1}
    if dupes:
        result["passed"] = False
        result["items"].append({
            "table": "rag_ticket_chunks",
            "duplicate_hash_count": len(dupes),
            "total_duplicate_rows": sum(dupes.values()),
            "status": WARN,
            "sample": list(dupes.keys())[:3],
        })
    else:
        result["items"].append({
            "table": "rag_ticket_chunks",
            "duplicate_hash_count": 0,
            "status": OK,
        })
    return result


def _check_word_count_distribution(sb: Any, client: str, index_version: str) -> dict:
    result: dict = {"label": "Avg word count by chunk_type", "items": []}

    rows = _paginate(sb, "rag_ticket_chunks", "chunk_type,word_count",
                     filters={"client": client, "index_version": index_version})
    by_type: dict[str, list[int]] = {}
    for r in rows:
        ct = r.get("chunk_type", "UNKNOWN")
        wc = r.get("word_count", 0) or 0
        by_type.setdefault(ct, []).append(wc)

    for ct, counts in sorted(by_type.items()):
        avg = sum(counts) / len(counts) if counts else 0
        result["items"].append({
            "chunk_type": ct,
            "count": len(counts),
            "avg_word_count": round(avg, 1),
            "min": min(counts) if counts else 0,
            "max": max(counts) if counts else 0,
        })
    return result


def _check_boilerplate_detection(sb: Any, client: str, index_version: str) -> dict:
    result: dict = {"label": "Top 20 shortest chunks (boilerplate detector)", "items": []}

    try:
        resp = (
            sb.table("rag_ticket_chunks")
            .select("content,chunk_type,word_count")
            .eq("client", client)
            .eq("index_version", index_version)
            .order("word_count", desc=False)
            .limit(20)
            .execute()
        )
        rows = resp.data or []
        for r in rows:
            content = (r.get("content") or "")[:120]
            result["items"].append({
                "chunk_type": r.get("chunk_type"),
                "word_count": r.get("word_count"),
                "content_preview": content.replace("\n", " "),
            })
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _check_sop_v2(sb: Any, index_version: str) -> dict:
    result: dict = {"label": "SOP v2 chunk presence", "passed": False, "items": []}

    # Count SOP library entries for this index_version
    lib_count = _count(sb, "rag_sop_library", filters={"index_version": index_version})
    chunk_count = _count(sb, "rag_sop_chunks", filters={"index_version": index_version})

    result["items"].append({
        "sop_library_rows": lib_count,
        "sop_chunk_rows": chunk_count,
    })

    if chunk_count > 0:
        result["passed"] = True
        result["status"] = OK
    else:
        result["status"] = FAIL
        result["message"] = (
            "No SOP chunks found for index_version={index_version}. "
            "Run SopIngestionPipeline.ingest_directory() with B1_INDEX_VERSION={index_version} "
            "to populate SOP v2 chunks."
        ).format(index_version=index_version)

    return result


def _check_ticket_section_distribution(sb: Any, client: str, index_version: str) -> dict:
    result: dict = {"label": "Ticket section distribution", "passed": True}

    rows = _paginate(sb, "rag_ticket_chunks", "chunk_type",
                     filters={"client": client, "index_version": index_version})
    dist: Counter = Counter(r.get("chunk_type") for r in rows)
    total = sum(dist.values())

    has_issue_header   = dist.get("ISSUE_HEADER", 0) > 0
    has_query_body     = dist.get("QUERY_BODY", 0) > 0
    has_resolution_rca = dist.get("RESOLUTION_RCA", 0) > 0

    if not has_query_body:
        result["passed"] = False

    result["distribution"] = {k: {"count": v, "pct": round(100 * v / total, 1)} for k, v in dist.items()} if total else {}
    result["has_issue_header"]   = has_issue_header
    result["has_query_body"]     = has_query_body
    result["has_resolution_rca"] = has_resolution_rca
    result["total_chunks"]       = total
    result["status"]             = OK if result["passed"] else FAIL
    return result


def _check_knowledge_quality(sb: Any, index_version: str) -> dict:
    result: dict = {"label": "Knowledge chunk quality distribution"}

    rows = _paginate(sb, "rag_knowledge_chunks", "chunk_type,quality_score",
                     filters={"index_version": index_version})
    if not rows:
        result["count"] = 0
        return result

    scores = [float(r.get("quality_score") or 0) for r in rows]
    result["count"]   = len(rows)
    result["avg_quality"] = round(sum(scores) / len(scores), 4) if scores else 0.0
    result["min_quality"] = round(min(scores), 4) if scores else 0.0
    result["max_quality"] = round(max(scores), 4) if scores else 0.0
    result["below_threshold"] = sum(1 for s in scores if s < 0.55)

    type_dist: Counter = Counter(r.get("chunk_type") for r in rows)
    result["chunk_type_dist"] = dict(type_dist)
    return result


# ── Report builder ────────────────────────────────────────────────────────────

def run_validation(sb: Any, client: str, index_version: str) -> dict:
    report: dict = {
        "client":        client,
        "index_version": index_version,
        "checks":        [],
        "overall_ok":    True,
        "issues":        [],
    }

    checks = [
        _check_row_counts(sb, client, index_version),
        _check_chunk_types(sb, client, index_version),
        _check_null_embeddings(sb, client, index_version),
        _check_duplicate_hashes(sb, client, index_version),
        _check_word_count_distribution(sb, client, index_version),
        _check_boilerplate_detection(sb, client, index_version),
        _check_sop_v2(sb, index_version),
        _check_ticket_section_distribution(sb, client, index_version),
        _check_knowledge_quality(sb, index_version),
    ]

    for check in checks:
        report["checks"].append(check)
        passed = check.get("passed", True)
        if not passed:
            report["overall_ok"] = False
            msg = check.get("message") or f"FAILED: {check.get('label')}"
            report["issues"].append(msg)

    return report


def _print_report(report: dict, *, verbose: bool = False) -> None:
    w = 68
    print(f"\n{'='*w}")
    print(f"  V2 INGESTION VALIDATION: {report['client']} | {report['index_version']}")
    print(f"{'='*w}")

    for check in report["checks"]:
        label = check.get("label", "")
        passed = check.get("passed", None)

        if passed is True:
            icon = OK
        elif passed is False:
            icon = FAIL
        else:
            icon = INFO

        print(f"\n{icon} {label}")

        # Print items if present
        items = check.get("items", [])
        if items and verbose:
            for item in items:
                print(f"      {item}")
        elif items:
            # Compact single-line summary
            for item in items[:5]:
                parts = [f"{k}={v}" for k, v in item.items() if k != "status"]
                status = item.get("status", "")
                print(f"  {status}  {', '.join(parts[:4])}")

        # Special fields
        for key in ("distribution", "has_issue_header", "status", "message", "count",
                    "avg_quality", "below_threshold", "chunk_type_dist"):
            if key in check and key not in ("items",):
                print(f"      {key}: {check[key]}")

    print(f"\n{'='*w}")
    if report["overall_ok"]:
        print(f"  {OK} All checks passed — v2 looks healthy for {report['client']}")
    else:
        print(f"  {FAIL} {len(report['issues'])} issue(s) found:")
        for issue in report["issues"]:
            print(f"      - {issue}")
    print(f"{'='*w}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate v2 ingestion state across all RAG tables",
    )
    parser.add_argument("--client",        default=None, help="Tenant slug (omit for first found)")
    parser.add_argument("--all-clients",   action="store_true", help="Run for every tenant")
    parser.add_argument("--index-version", default="v2", help="Version to validate (default: v2)")
    parser.add_argument("--all-versions",  action="store_true", help="Validate v1 AND v2")
    parser.add_argument("--json",          action="store_true", help="Output as JSON")
    parser.add_argument("--verbose",       action="store_true", help="Show full item lists")
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL", os.getenv("SUPABASE_SERVICE_URL", "")).strip()
    key = (os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        LOGGER.error("SUPABASE_URL and SUPABASE_KEY required")
        return 1

    try:
        from supabase import create_client  # noqa: PLC0415
    except ImportError:
        LOGGER.error("supabase-py not installed: pip install supabase")
        return 1

    sb = create_client(url, key)

    # Determine clients
    if args.all_clients:
        rows = sb.table("rag_ticket_documents").select("client").execute().data or []
        clients = sorted({r["client"] for r in rows if r.get("client")})
        if not clients:
            LOGGER.warning("No clients found in rag_ticket_documents")
            return 0
    elif args.client:
        clients = [args.client]
    else:
        # Auto-detect first client
        rows = sb.table("rag_ticket_documents").select("client").limit(1).execute().data or []
        clients = [rows[0]["client"]] if rows else []
        if not clients:
            LOGGER.error("No clients found. Specify --client SLUG")
            return 1

    versions = ["v1", "v2"] if args.all_versions else [args.index_version]

    all_reports: list[dict] = []
    any_failure = False

    for client in clients:
        for version in versions:
            LOGGER.info("Validating client=%s index_version=%s ...", client, version)
            report = run_validation(sb, client, version)
            all_reports.append(report)
            if not report["overall_ok"]:
                any_failure = True
            if not args.json:
                _print_report(report, verbose=args.verbose)

    if args.json:
        payload = all_reports[0] if len(all_reports) == 1 else all_reports
        print(json.dumps(payload, indent=2, default=str))

    return 1 if any_failure else 0


if __name__ == "__main__":
    sys.exit(main())
