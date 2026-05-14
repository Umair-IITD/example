"""
Stage 2 — Schema Normalization.
Maps raw DataFrames to canonical CanonicalTicket instances.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from ..config import PipelineConfig, default_config
from ..loaders.schema import apply_column_map
from ..models import CanonicalTicket

logger = logging.getLogger(__name__)

# Session ID extraction: alphanumeric codes that look like Freshdesk session UUIDs or numeric IDs
_SESSION_ID_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9]{8,20})\b",
    re.IGNORECASE,
)

# AI-related tag signals
_AI_AUTO_REPLY_TAG = "ai_auto_replied"
_AI_NOTE_TAG = "ai_note"
_RAG_CONTEXT_TAG = "rag_context"


def _safe_str(val: Any) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("nan", "none", "nat") else None


def _safe_int(val: Any) -> Optional[int]:
    s = _safe_str(val)
    if s is None:
        return None
    # Remove non-numeric suffixes like " mins"
    s = re.sub(r"[^\d]", "", s.split(".")[0])
    try:
        return int(s) if s else None
    except (ValueError, OverflowError):
        return None


def _safe_float(val: Any) -> Optional[float]:
    s = _safe_str(val)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_datetime(val: Any) -> Optional[datetime]:
    s = _safe_str(val)
    if s is None:
        return None
    # pandas Timestamp
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime() if not pd.isna(val) else None
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d %b %Y, %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            pass
    try:
        return pd.to_datetime(s, dayfirst=True, errors="coerce").to_pydatetime()
    except Exception:
        return None


def _parse_tags(raw_tags: Any) -> list[str]:
    """Parse comma-separated Freshdesk tags string into a list."""
    s = _safe_str(raw_tags)
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def _parse_session_ids(raw: Any) -> list[str]:
    """Extract session IDs from a free-text field."""
    s = _safe_str(raw)
    if not s:
        return []
    # If the column is a structured list like "ID1, ID2"
    found = _SESSION_ID_RE.findall(s)
    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for sid in found:
        if sid not in seen:
            seen.add(sid)
            result.append(sid)
    return result


def _normalize_tenant(client_name: Optional[str], slug_map: dict[str, str]) -> Optional[str]:
    if not client_name:
        return None
    key = client_name.lower().strip()
    # Exact match first
    if key in slug_map:
        return slug_map[key]
    # Partial match
    for fragment, slug in slug_map.items():
        if fragment in key:
            return slug
    # Return lowercased slug of the raw value
    return re.sub(r"\s+", "_", key)


def _row_to_ticket(
    row: pd.Series,
    col_map: dict[str, str],
    source_key: str,
    source_file: str,
    tenant_slug_map: dict[str, str],
) -> Optional[CanonicalTicket]:
    """Convert one raw row into a CanonicalTicket, returning None if ticket_id is missing."""
    mapped: dict[str, Any] = {}
    for raw_col, canonical in col_map.items():
        if raw_col in row.index:
            mapped[canonical] = row[raw_col]

    ticket_id_raw = mapped.get("ticket_id")
    tid = _safe_int(ticket_id_raw)
    if tid is None:
        return None

    tags = _parse_tags(mapped.get("tags_raw"))
    session_ids = _parse_session_ids(mapped.get("session_ids_raw"))
    client_name = _safe_str(mapped.get("client_name"))
    tenant_id = _normalize_tenant(client_name, tenant_slug_map)

    return CanonicalTicket(
        ticket_id=tid,
        source_file=source_file,
        source_format=source_key,
        # Timestamps
        created_at=_parse_datetime(mapped.get("created_at")),
        resolved_at=_parse_datetime(mapped.get("resolved_at")),
        closed_at=_parse_datetime(mapped.get("closed_at")),
        last_updated_at=_parse_datetime(mapped.get("last_updated_at")),
        # Routing
        status=_safe_str(mapped.get("status")),
        priority=_safe_str(mapped.get("priority")),
        source_channel=_safe_str(mapped.get("source_channel")),
        ticket_type=_safe_str(mapped.get("ticket_type")),
        agent=_safe_str(mapped.get("agent")),
        group=_safe_str(mapped.get("group")),
        # Text
        subject=_safe_str(mapped.get("subject")),
        raw_description=_safe_str(mapped.get("raw_description")),
        rca=_safe_str(mapped.get("rca")),
        # Operational
        query_type=_safe_str(mapped.get("query_type")),
        issue_area=_safe_str(mapped.get("issue_area")),
        environment=_safe_str(mapped.get("environment")),
        sop_status=_safe_str(mapped.get("sop_status")),
        resolution_classification=_safe_str(mapped.get("resolution_classification")),
        issue_recurrence=_safe_str(mapped.get("issue_recurrence")),
        impact=_safe_str(mapped.get("impact")),
        rca_status=_safe_str(mapped.get("rca_status")),
        # Metrics
        agent_interactions=_safe_int(mapped.get("agent_interactions")),
        customer_interactions=_safe_int(mapped.get("customer_interactions")),
        handling_time_minutes=_safe_int(mapped.get("handling_time_minutes")),
        first_response_hrs=_safe_float(mapped.get("first_response_hrs")),
        resolution_hrs=_safe_float(mapped.get("resolution_hrs")),
        resolution_status=_safe_str(mapped.get("resolution_status")),
        first_response_status=_safe_str(mapped.get("first_response_status")),
        # Tenant
        client_name=client_name,
        tenant_id=tenant_id,
        # Session
        session_ids=session_ids,
        # References
        stackoverflow_link=_safe_str(mapped.get("stackoverflow_link")),
        asana_ticket_link=_safe_str(mapped.get("asana_ticket_link")),
        bajaj_azure_ticket_id=_safe_str(mapped.get("bajaj_azure_ticket_id")),
        tags=tags,
        # AI signals derived from tags
        ai_auto_replied=_AI_AUTO_REPLY_TAG in tags,
        ai_note_present=_AI_NOTE_TAG in tags,
        rag_context_used=_RAG_CONTEXT_TAG in tags,
    )


def run(
    frames: dict[str, pd.DataFrame],
    cfg: Optional[PipelineConfig] = None,
) -> list[CanonicalTicket]:
    """
    Normalize all source DataFrames into CanonicalTicket instances.

    Deduplication strategy:
    - Within each file: deduplicate by ticket_id (keep first occurrence).
    - Cross-file (XLS vs CSV): XLS is dropped in favour of CSV (same tickets, no Description in either).
    - Cross-file (RBL files): zero overlap — both are kept.
    """
    if cfg is None:
        cfg = default_config()

    tickets: list[CanonicalTicket] = []
    seen_ids_csv_xls: set[int] = set()   # XLS/CSV share the same ticket space
    seen_ids_rbl1: set[int] = set()
    seen_ids_rbl: set[int] = set()

    for source_key, df in frames.items():
        col_map = apply_column_map(list(df.columns), source_key)
        source_file = df["_source_file"].iloc[0] if "_source_file" in df.columns else source_key
        logger.info(
            "[S2] Normalizing source '%s': %d rows, %d mapped columns",
            source_key, len(df), len(col_map),
        )

        parse_errors = 0
        duplicates = 0
        added = 0

        for _, row in df.iterrows():
            try:
                ticket = _row_to_ticket(
                    row, col_map, source_key, source_file, cfg.tenant_slug_map
                )
            except Exception as exc:
                logger.debug("[S2] Parse error in '%s' row: %s", source_key, exc)
                parse_errors += 1
                continue

            if ticket is None:
                parse_errors += 1
                continue

            tid = ticket.ticket_id

            # Deduplication
            if source_key in ("csv", "xls"):
                if tid in seen_ids_csv_xls:
                    duplicates += 1
                    continue
                seen_ids_csv_xls.add(tid)
            elif source_key == "rbl_rca1":
                if tid in seen_ids_rbl1:
                    duplicates += 1
                    continue
                seen_ids_rbl1.add(tid)
            elif source_key == "rbl_rca":
                if tid in seen_ids_rbl:
                    duplicates += 1
                    continue
                seen_ids_rbl.add(tid)

            tickets.append(ticket)
            added += 1

        logger.info(
            "[S2] '%s': %d added, %d duplicates skipped, %d parse errors",
            source_key, added, duplicates, parse_errors,
        )

    logger.info("[S2] Total canonical tickets: %d", len(tickets))
    return tickets
