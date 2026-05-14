"""
Canonical ticket model for the KwikID dataset engineering pipeline.

Every source file (CSV, XLS, XLSX) normalizes into this single model
before any downstream stage touches the data.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class CanonicalTicket(BaseModel):
    """Normalized representation of a single Freshdesk support ticket."""

    # ── Identity ──────────────────────────────────────────────────────────────
    ticket_id: int
    source_file: str                    # filename of origin export
    source_format: str                  # csv | xlsx | xls

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    last_updated_at: Optional[datetime] = None

    # ── Routing & Classification ───────────────────────────────────────────────
    status: Optional[str] = None
    priority: Optional[str] = None
    source_channel: Optional[str] = None   # Email / Portal / Phone / Chat
    ticket_type: Optional[str] = None      # Question / Problem / Incident
    agent: Optional[str] = None
    group: Optional[str] = None

    # ── Core text content ────────────────────────────────────────────────────
    subject: Optional[str] = None
    raw_description: Optional[str] = None      # Original field value (may contain HTML)
    cleaned_description: Optional[str] = None  # After HTML + signature strip
    rca: Optional[str] = None                  # Raw Root Cause Analysis text
    cleaned_rca: Optional[str] = None          # Cleaned RCA (whitespace + trivial filter)

    # ── Operational Classification ────────────────────────────────────────────
    query_type: Optional[str] = None
    issue_area: Optional[str] = None
    environment: Optional[str] = None
    sop_status: Optional[str] = None
    resolution_classification: Optional[str] = None
    issue_recurrence: Optional[str] = None
    impact: Optional[str] = None
    rca_status: Optional[str] = None

    # ── Metrics ───────────────────────────────────────────────────────────────
    agent_interactions: Optional[int] = None
    customer_interactions: Optional[int] = None
    handling_time_minutes: Optional[int] = None
    first_response_hrs: Optional[float] = None
    resolution_hrs: Optional[float] = None
    resolution_status: Optional[str] = None    # Within SLA / SLA Violated
    first_response_status: Optional[str] = None

    # ── Tenant isolation ──────────────────────────────────────────────────────
    client_name: Optional[str] = None          # Raw value from Clients column
    tenant_id: Optional[str] = None            # Normalized slug (e.g. "unity_bank")

    # ── Session data ──────────────────────────────────────────────────────────
    session_ids: List[str] = Field(default_factory=list)

    # ── References & Links ────────────────────────────────────────────────────
    stackoverflow_link: Optional[str] = None
    asana_ticket_link: Optional[str] = None
    bajaj_azure_ticket_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    # ── AI pipeline signals (derived from tags) ───────────────────────────────
    ai_auto_replied: bool = False
    ai_note_present: bool = False
    rag_context_used: bool = False

    # ── Pipeline-assigned labels (set by stages) ──────────────────────────────
    automation_class: Optional[str] = None  # AUTO_RESOLVABLE | HUMAN_REVIEW_REQUIRED | ESCALATION_REQUIRED | EXCLUDE
    rca_quality: Optional[str] = None       # GOLD | SILVER | TRIVIAL | NONE

    # ── Cleaning audit fields ─────────────────────────────────────────────────
    has_description: bool = False
    description_len_raw: int = 0
    description_len_cleaned: int = 0
    html_stripped: bool = False
    signature_stripped: bool = False
    excluded_reason: Optional[str] = None

    @model_validator(mode="after")
    def derive_flags(self) -> "CanonicalTicket":
        self.has_description = bool(
            self.raw_description and len(self.raw_description.strip()) > 10
        )
        self.description_len_raw = len(self.raw_description or "")
        return self

    @property
    def effective_text(self) -> str:
        """Best available text for embedding — prefer cleaned description."""
        parts = []
        if self.subject:
            parts.append(f"Subject: {self.subject}")
        if self.cleaned_description:
            parts.append(self.cleaned_description)
        elif self.raw_description:
            parts.append(self.raw_description)
        if self.cleaned_rca:
            parts.append(f"RCA: {self.cleaned_rca}")
        return "\n\n".join(parts)

    @property
    def is_recurring(self) -> bool:
        return "recurring" in (self.issue_recurrence or "").lower()

    @property
    def asana_ticket_present(self) -> bool:
        return bool(self.asana_ticket_link and self.asana_ticket_link.strip())
