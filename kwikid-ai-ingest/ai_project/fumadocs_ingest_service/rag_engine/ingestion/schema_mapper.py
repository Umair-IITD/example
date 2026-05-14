"""
rag_engine/ingestion/schema_mapper.py

DatasetSchemaMapper — bridges the gap between the actual preprocessed
dataset column names and the TicketSourceRow Pydantic model.

This layer exists because:
  1. The preprocessing pipeline has its own naming conventions
     (tenant_id, automation_class, rca_quality, created_at, etc.)
  2. The TicketSourceRow schema is designed for the vector store
     (client, automation_label, rca_quality_score int, ticket_created_at, etc.)
  3. Several fields must be DERIVED (has_rca, has_sop, escalation_flag)
     rather than read directly from columns

Keeping this as a separate module means:
  - The pipeline can swap to a different source format without changing schemas
  - Column renames in future preprocessing runs only require updating this mapper
  - All derived-field logic is in one testable place

VERIFIED against unified_cleaned_dataset.parquet (3,635 rows, 51 columns, 2026-05-13)
"""
from __future__ import annotations

import math
from typing import Any, Optional


# =============================================================================
# rca_quality string → integer score mapping
# Values confirmed from actual dataset: NONE, TRIVIAL, SILVER, GOLD
# =============================================================================
_RCA_QUALITY_SCORE_MAP: dict[str, int] = {
    "NONE": 0,
    "TRIVIAL": 10,
    "BRONZE": 40,
    "SILVER": 70,
    "GOLD": 95,
}

# SOP statuses that indicate an SOP exists for this issue
_SOP_PRESENT_VALUES: frozenset[str] = frozenset({
    "SOP Present",
    "SOP Created (New)",
    "Old SOP Modified",
    "Solved by SOP",
})

# automation_class → automation_label normalization
_AUTOMATION_CLASS_MAP: dict[str, str] = {
    "AUTO_RESOLVABLE": "AUTO_REPLY",
    "AUTO_REPLY": "AUTO_REPLY",
    "HUMAN_REVIEW_REQUIRED": "HUMAN_REVIEW",
    "HUMAN_REVIEW": "HUMAN_REVIEW",
    "ESCALATION_REQUIRED": "ESCALATION",
    "ESCALATION": "ESCALATION",
}


def _safe_str(value: Any) -> Optional[str]:
    """Return string or None if null/NaN/empty."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    s = str(value).strip()
    return s if s and s.lower() not in ("nan", "none", "nat") else None


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isnan(value):
        return default
    s = str(value).lower()
    return s in ("true", "1", "yes")


class DatasetSchemaMapper:
    """
    Maps a raw parquet/CSV row dict (from unified_cleaned_dataset)
    to a dict that TicketSourceRow.model_validate() can accept.

    Handles:
      - Column renames (tenant_id → client, automation_class → automation_label, etc.)
      - Type coercions (rca_quality str → rca_quality_score int)
      - Derived fields (has_rca, has_sop, escalation_flag)
      - NaN/None normalization (pandas NaN is float('nan'), not None)
    """

    def map(self, row: dict[str, Any]) -> dict[str, Any]:
        """
        Transform a dataset row dict into a TicketSourceRow-compatible dict.
        Returns None if the row should be skipped entirely.
        """
        # ── Identity ───────────────────────────────────────────────────────────
        ticket_id = _safe_str(row.get("ticket_id")) or ""
        source_file = _safe_str(row.get("source_file")) or "unknown"

        # ── Tenant isolation (CRITICAL) ────────────────────────────────────────
        # Actual column is `tenant_id` (normalized slug already: unity_bank, rbl_bank, etc.)
        client = _safe_str(row.get("tenant_id")) or _safe_str(row.get("client")) or "unknown"

        # ── Automation classification ──────────────────────────────────────────
        raw_class = _safe_str(row.get("automation_class")) or _safe_str(row.get("automation_label")) or ""
        automation_label = _AUTOMATION_CLASS_MAP.get(raw_class.upper(), "HUMAN_REVIEW")

        # Escalation flag: true if automation_class is ESCALATION_REQUIRED
        escalation_flag = automation_label == "ESCALATION"

        # ── RCA quality: string → integer score ───────────────────────────────
        rca_quality_raw = _safe_str(row.get("rca_quality")) or "NONE"
        rca_quality_score = _RCA_QUALITY_SCORE_MAP.get(rca_quality_raw.upper(), 0)

        # ── Derived knowledge signals ──────────────────────────────────────────
        # has_rca: true if rca_quality is better than TRIVIAL AND cleaned_rca is not null
        has_rca = (
            rca_quality_score >= _RCA_QUALITY_SCORE_MAP["SILVER"]
            and _safe_str(row.get("cleaned_rca")) is not None
        )

        # has_sop: true if sop_status indicates an SOP exists
        sop_status = _safe_str(row.get("sop_status"))
        has_sop = sop_status in _SOP_PRESENT_VALUES if sop_status else False

        # ── Timestamp renames ─────────────────────────────────────────────────
        # Dataset: created_at, resolved_at (or closed_at)
        # Schema: ticket_created_at, ticket_resolved_at
        ticket_created_at = _safe_str(row.get("created_at")) or _safe_str(row.get("ticket_created_at"))
        ticket_resolved_at = (
            _safe_str(row.get("resolved_at"))
            or _safe_str(row.get("closed_at"))
            or _safe_str(row.get("ticket_resolved_at"))
        )

        # ── Metric rename ─────────────────────────────────────────────────────
        # Dataset: handling_time_minutes | Schema: handling_time_mins
        handling_time_mins_raw = row.get("handling_time_minutes") or row.get("handling_time_mins")
        handling_time_mins: Optional[int] = None
        if handling_time_mins_raw is not None:
            try:
                f = float(handling_time_mins_raw)
                if not math.isnan(f):
                    handling_time_mins = int(f)
            except (TypeError, ValueError):
                pass

        # ── Direct pass-through fields ────────────────────────────────────────
        return {
            "ticket_id": str(ticket_id),
            "client": client,
            "source_file": source_file,
            "subject": _safe_str(row.get("subject")),
            "query_type": _safe_str(row.get("query_type")),
            "issue_area": _safe_str(row.get("issue_area")),
            "environment": _safe_str(row.get("environment")),
            "priority": _safe_str(row.get("priority")),
            "status": _safe_str(row.get("status")),
            "cleaned_description": _safe_str(row.get("cleaned_description")),
            "cleaned_rca": _safe_str(row.get("cleaned_rca")),
            "automation_label": automation_label,
            "rca_quality_score": rca_quality_score,
            "has_rca": has_rca,
            "has_sop": has_sop,
            "sop_status": sop_status,
            "escalation_flag": escalation_flag,
            "issue_recurrence": _safe_str(row.get("issue_recurrence")),
            "resolution_status": _safe_str(row.get("resolution_status")),
            "agent_interactions": _safe_int(row.get("agent_interactions"), 0),
            "handling_time_mins": handling_time_mins,
            "ticket_created_at": ticket_created_at,
            "ticket_resolved_at": ticket_resolved_at,
        }
