#!/usr/bin/env python3
"""
scripts/backfill_section_columns.py

Backfill issue_header_text / query_body_text / resolution_rca_text in
rag_ticket_documents by parsing the section markers already embedded in
document_text by TicketDocumentBuilder.

Safety guarantees:
  - Skips rows where all three section columns are already populated.
  - Only writes to columns that are currently NULL.  Populated columns are
    never touched, even when the parsed text differs.
  - Fully idempotent: safe to re-run any number of times.
  - No schema changes; no destructive migrations.

Backfill logic:
  Each document_text was assembled by TicketDocumentBuilder as:
      {issue_header_text}\n\n{query_body_text}\n\n{resolution_rca_text}
  The three section texts themselves begin with their marker line, e.g.:
      "[ISSUE SUMMARY]\nTicket ID: 12345\n..."
  This script splits document_text on those markers and writes each
  extracted section back into its dedicated column.

Usage:
  python scripts/backfill_section_columns.py --client unity_bank
  python scripts/backfill_section_columns.py --all-clients
  python scripts/backfill_section_columns.py --all-clients --dry-run

Environment:
  SUPABASE_URL, SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY)
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("backfill_sections")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(_ROOT, ".env"), override=False)

# ── Constants ─────────────────────────────────────────────────────────────────

_PAGE_SIZE        = 500   # smaller pages — document_text is a large field
_PROGRESS_EVERY   = 100   # log progress line every N rows processed

# Matches [ISSUE SUMMARY], [CUSTOMER QUERY], [TROUBLESHOOTING AND RESOLUTION]
# with tolerance for internal/surrounding whitespace and case variation.
_MARKER_RE = re.compile(
    r'\[\s*(ISSUE\s+SUMMARY|CUSTOMER\s+QUERY|TROUBLESHOOTING\s+AND\s+RESOLUTION)\s*\]',
    re.IGNORECASE,
)

# Normalized marker name → DB column name
_MARKER_TO_COL: dict[str, str] = {
    "ISSUE SUMMARY":                  "issue_header_text",
    "CUSTOMER QUERY":                 "query_body_text",
    "TROUBLESHOOTING AND RESOLUTION": "resolution_rca_text",
}

_ALL_SECTION_COLS = ("issue_header_text", "query_body_text", "resolution_rca_text")


# ── Section parser ────────────────────────────────────────────────────────────

def _normalize_marker(raw: str) -> str:
    """Collapse internal whitespace and uppercase for dict lookup."""
    return re.sub(r'\s+', ' ', raw).upper()


def _parse_sections(document_text: str) -> dict[str, str]:
    """
    Split document_text on embedded section markers and return a dict mapping
    each DB column name to its extracted text (including the marker line).

    The extracted text for each section matches exactly what TicketDocumentBuilder
    originally wrote into issue_header_text / query_body_text / resolution_rca_text:
      - Starts with the [MARKER] line
      - Has trailing whitespace stripped
      - Has runs of 3+ blank lines collapsed to 2

    Returns empty string for any marker not found in document_text.
    Markers that appear multiple times: only the first occurrence is used.
    """
    result: dict[str, str] = {col: "" for col in _ALL_SECTION_COLS}
    if not document_text:
        return result

    matches = list(_MARKER_RE.finditer(document_text))
    if not matches:
        return result

    seen: set[str] = set()
    for i, m in enumerate(matches):
        name = _normalize_marker(m.group(1))
        col = _MARKER_TO_COL.get(name)
        if col is None or col in seen:
            continue
        seen.add(col)

        # Section text: from the start of this marker to the start of the next
        # (or to the end of the document).
        section_start = m.start()
        section_end   = matches[i + 1].start() if i + 1 < len(matches) else len(document_text)
        raw_text      = document_text[section_start:section_end]

        # Normalize: collapse 3+ blank lines → 2, strip leading/trailing whitespace.
        cleaned = re.sub(r'\n{3,}', '\n\n', raw_text).strip()
        result[col] = cleaned

    return result


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class BackfillStats:
    total_scanned:    int = 0
    already_populated: int = 0  # all three cols non-empty → skipped entirely
    updated:          int = 0   # rows where at least one col was written
    partial_parse:    int = 0   # some but not all markers found
    no_markers:       int = 0   # document_text had zero recognized markers
    update_errors:    int = 0
    errors: list[str] = field(default_factory=list)


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _fetch_docs_paginated(supabase: Any, *, client: str | None) -> list[dict]:
    """
    Fetch all rag_ticket_documents rows that need backfilling.
    Only selects the columns required for parsing and updating.
    """
    select_cols = (
        "id,ticket_id,document_text,"
        "issue_header_text,query_body_text,resolution_rca_text"
    )
    all_rows: list[dict] = []
    offset   = 0
    page_num = 0

    while True:
        page_num += 1
        q = supabase.table("rag_ticket_documents").select(select_cols)
        if client:
            q = q.eq("client", client)
        q = q.order("id").range(offset, offset + _PAGE_SIZE - 1)
        batch = q.execute().data or []
        all_rows.extend(batch)
        LOGGER.debug(
            "fetch page=%d offset=%d fetched=%d cumulative=%d",
            page_num, offset, len(batch), len(all_rows),
        )
        if len(batch) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    if page_num > 1:
        LOGGER.info("Paginated fetch: %d rows in %d pages", len(all_rows), page_num)
    return all_rows


def _update_row(supabase: Any, row_id: str, payload: dict[str, str]) -> None:
    supabase.table("rag_ticket_documents").update(payload).eq("id", row_id).execute()


# ── Core backfill logic ───────────────────────────────────────────────────────

def run_backfill(
    supabase: Any,
    *,
    client: str | None,
    dry_run: bool,
) -> BackfillStats:
    stats = BackfillStats()
    scope = f"client={client}" if client else "all clients"
    LOGGER.info("=== Section column backfill | scope=%s dry_run=%s ===", scope, dry_run)

    rows = _fetch_docs_paginated(supabase, client=client)
    stats.total_scanned = len(rows)
    LOGGER.info("Fetched %d rows to evaluate", len(rows))

    for i, row in enumerate(rows, start=1):
        # --- Skip rows that are already fully populated ---
        if all((row.get(col) or "").strip() for col in _ALL_SECTION_COLS):
            stats.already_populated += 1
            continue

        doc_text = (row.get("document_text") or "").strip()
        sections = _parse_sections(doc_text)

        # Determine which columns need writing:
        #   - Current DB value is NULL/empty   AND
        #   - We successfully parsed a non-empty value
        update_payload: dict[str, str] = {}
        for col in _ALL_SECTION_COLS:
            existing = (row.get(col) or "").strip()
            parsed   = sections[col]
            if not existing and parsed:
                update_payload[col] = parsed

        # Classify parse quality for stats
        found_markers = sum(1 for v in sections.values() if v)
        if found_markers == 0:
            stats.no_markers += 1
            LOGGER.debug(
                "ticket_id=%s — no markers found in document_text (len=%d)",
                row.get("ticket_id"), len(doc_text),
            )
        elif found_markers < len(_ALL_SECTION_COLS):
            stats.partial_parse += 1
            LOGGER.debug(
                "ticket_id=%s — partial parse: found=%d/%d cols=%s",
                row.get("ticket_id"),
                found_markers,
                len(_ALL_SECTION_COLS),
                [col for col in _ALL_SECTION_COLS if sections[col]],
            )

        if not update_payload:
            # Either no_markers or all parseable sections already written in a prior run
            continue

        if dry_run:
            LOGGER.info(
                "[DRY-RUN] Would update ticket_id=%-8s cols=%s",
                row.get("ticket_id"),
                list(update_payload.keys()),
            )
            stats.updated += 1
        else:
            try:
                _update_row(supabase, row["id"], update_payload)
                stats.updated += 1
            except Exception as exc:
                stats.update_errors += 1
                msg = f"ticket_id={row.get('ticket_id')} id={row.get('id')}: {exc}"
                stats.errors.append(msg)
                LOGGER.error("Update failed — %s", msg)

        if i % _PROGRESS_EVERY == 0:
            LOGGER.info(
                "Progress: scanned=%d updated=%d partial=%d no_markers=%d errors=%d",
                i, stats.updated, stats.partial_parse, stats.no_markers, stats.update_errors,
            )

    return stats


# ── Output ────────────────────────────────────────────────────────────────────

def _print_summary(stats: BackfillStats, *, dry_run: bool) -> None:
    tag = "[DRY-RUN] " if dry_run else ""
    verb = "Would update" if dry_run else "Updated"
    w = 62

    print(f"\n{'='*w}")
    print(f"  {tag}SECTION COLUMN BACKFILL — SUMMARY")
    print(f"{'='*w}")
    print(f"  Total scanned:       {stats.total_scanned}")
    print(f"  Already populated:   {stats.already_populated}  (skipped — all 3 cols present)")
    print(f"  {verb:20s} {stats.updated}")
    print(f"  Partial parse:       {stats.partial_parse}  (not all 3 markers in document_text)")
    print(f"  No markers:          {stats.no_markers}  (document_text has no [SECTION] markers)")
    print(f"  Update errors:       {stats.update_errors}")
    status = "OK" if stats.update_errors == 0 else "DEGRADED"
    print(f"\n  Status: {status}")
    print(f"{'='*w}")
    if stats.errors:
        print(f"\nFirst {min(len(stats.errors), 10)} error(s):")
        for e in stats.errors[:10]:
            print(f"  {e}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill issue_header_text / query_body_text / resolution_rca_text "
            "in rag_ticket_documents from embedded document_text markers."
        ),
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--client", metavar="SLUG",
        help="Backfill only this tenant (e.g. unity_bank)",
    )
    scope.add_argument(
        "--all-clients", action="store_true",
        help="Backfill all tenants (default when no scope flag is given)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written without making any DB changes",
    )
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL", os.getenv("SUPABASE_SERVICE_URL", "")).strip()
    key = (os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        LOGGER.error("SUPABASE_URL and SUPABASE_KEY environment variables required")
        return 1

    try:
        from supabase import create_client  # noqa: PLC0415
    except ImportError:
        LOGGER.error("supabase-py not installed: pip install supabase")
        return 1

    sb = create_client(url, key)
    client_arg = args.client if args.client else None  # None → all clients

    stats = run_backfill(sb, client=client_arg, dry_run=args.dry_run)
    _print_summary(stats, dry_run=args.dry_run)
    return 1 if stats.update_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
