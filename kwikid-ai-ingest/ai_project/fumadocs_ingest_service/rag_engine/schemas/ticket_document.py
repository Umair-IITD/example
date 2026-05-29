"""
rag_engine/schemas/ticket_document.py

Pydantic models for the RAG ticket document layer.

TicketSourceRow   — validated representation of one row from the preprocessed dataset
RagTicketDocument — fully constructed document ready for chunking and embedding
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AutomationLabel(str, Enum):
    AUTO_REPLY = "AUTO_REPLY"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    ESCALATION = "ESCALATION"
    UNKNOWN = "UNKNOWN"


class ChunkType(str, Enum):
    # ── Ticket chunk types ─────────────────────────────────────────────────────
    ISSUE_HEADER   = "ISSUE_HEADER"    # ticket metadata (category, client, priority)
    QUERY_BODY     = "QUERY_BODY"      # customer complaint — primary semantic match target
    RESOLUTION_RCA = "RESOLUTION_RCA" # resolution steps + root cause — answer source

    # ── SOP chunk types ────────────────────────────────────────────────────────
    SOP_STEPS = "SOP_STEPS"           # standard operating procedure section

    # ── Knowledge chunk types ──────────────────────────────────────────────────
    VERIFIED_REPLY  = "VERIFIED_REPLY"   # human-reviewed Q+A
    TROUBLESHOOTING = "TROUBLESHOOTING"  # troubleshooting procedure
    HOW_TO          = "HOW_TO"           # step-by-step guide
    CONCEPT         = "CONCEPT"          # explanatory prose
    CODE_SAMPLE     = "CODE_SAMPLE"      # fenced code block
    COMMAND         = "COMMAND"          # shell/CLI command sequence
    FAQ_ANSWER      = "FAQ_ANSWER"       # general FAQ response
    POLICY          = "POLICY"           # policy / compliance text
    RCA             = "RCA"              # root cause analysis article


class TicketSourceRow(BaseModel):
    """
    Validated representation of one row from unified_cleaned_dataset.parquet.
    Field names map to the column names output by the preprocessing pipeline.
    Optional fields handle the schema differences between RBL_RCA and CSV sources.
    """
    # Required identity fields
    ticket_id: str = Field(..., description="Freshdesk ticket ID")
    # client is mapped from tenant_id by DatasetSchemaMapper; default guards against None
    client: str = Field(default="unknown", description="Normalized tenant slug (unity_bank, rbl_bank, etc.)")
    source_file: str = Field(default="unknown", description="Source file identifier")

    # Ticket classification
    subject: Optional[str] = None
    query_type: Optional[str] = None
    issue_area: Optional[str] = None
    environment: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None

    # Cleaned content (from preprocessing)
    cleaned_description: Optional[str] = None    # HTML-stripped, signature-removed
    cleaned_rca: Optional[str] = None

    # Preprocessing labels
    automation_label: str = "UNKNOWN"
    rca_quality_score: int = Field(default=0, ge=0, le=100)
    has_rca: bool = False
    has_sop: bool = False
    sop_status: Optional[str] = None
    escalation_flag: bool = False
    issue_recurrence: Optional[str] = None
    resolution_status: Optional[str] = None

    # Ticket metrics
    agent_interactions: Optional[int] = None
    handling_time_mins: Optional[int] = None

    # Timestamps — accept ISO strings from pandas (Pydantic auto-coerces to datetime)
    ticket_created_at: Optional[datetime] = None
    ticket_resolved_at: Optional[datetime] = None

    @field_validator("ticket_created_at", "ticket_resolved_at", mode="before")
    @classmethod
    def parse_optional_datetime(cls, v: object) -> Optional[str]:
        """Accept None, NaT strings, and ISO timestamp strings."""
        if v is None:
            return None
        s = str(v).strip()
        return None if s.lower() in ("", "nan", "none", "nat") else s

    @field_validator("automation_label", mode="before")
    @classmethod
    def normalize_automation_label(cls, v: object) -> str:
        if isinstance(v, str):
            upper = v.strip().upper().replace(" ", "_")
            # Map legacy labels from preprocessing pipeline
            mapping = {
                "AUTO_RESOLVABLE": "AUTO_REPLY",
                "AUTO_REPLY": "AUTO_REPLY",
                "HUMAN_REVIEW": "HUMAN_REVIEW",
                "HUMAN_REVIEW_REQUIRED": "HUMAN_REVIEW",
                "ESCALATION": "ESCALATION",
                "ESCALATION_REQUIRED": "ESCALATION",
            }
            return mapping.get(upper, "HUMAN_REVIEW")
        return "HUMAN_REVIEW"

    @field_validator("client", mode="before")
    @classmethod
    def normalize_client(cls, v: object) -> str:
        if isinstance(v, str) and v.strip():
            return v.strip().lower().replace(" ", "_").replace("-", "_")
        return "unknown"

    @field_validator("ticket_id", mode="before")
    @classmethod
    def coerce_ticket_id(cls, v: object) -> str:
        return str(v).strip()

    @property
    def has_usable_description(self) -> bool:
        return bool(self.cleaned_description and len(self.cleaned_description) >= 80)

    @property
    def has_usable_rca(self) -> bool:
        return bool(self.cleaned_rca and len(self.cleaned_rca) >= 30)


class RagTicketDocument(BaseModel):
    """
    Fully constructed RAG document ready for chunking.
    Built by TicketDocumentBuilder from a TicketSourceRow.
    """
    # Identity
    ticket_id: str
    client: str
    source_file: str
    source_type: str = "freshdesk"

    # Structured document text sections (built by document_builder)
    issue_header_text: str          # Short metadata-rich section
    query_body_text: str            # Customer complaint text
    resolution_rca_text: str        # Resolution + RCA text (may be empty)

    # Full assembled document (all sections concatenated, used for single-chunk fallback)
    full_document_text: str

    # Content hash for deduplication
    content_hash: str

    # Classification metadata
    automation_label: AutomationLabel
    escalation_flag: bool
    query_type: Optional[str] = None
    issue_area: Optional[str] = None
    environment: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None

    # Knowledge signals
    has_rca: bool
    has_sop: bool
    sop_status: Optional[str] = None
    rca_quality_score: int = Field(default=0, ge=0, le=100)

    # Ticket metrics
    subject: Optional[str] = None
    agent_interactions: Optional[int] = None
    handling_time_mins: Optional[int] = None
    issue_recurrence: Optional[str] = None
    resolution_status: Optional[str] = None

    # Timestamps
    ticket_created_at: Optional[datetime] = None
    ticket_resolved_at: Optional[datetime] = None

    def extra_metadata_dict(self) -> dict:
        """Returns JSONB-compatible dict for the extra_metadata column."""
        return {
            k: v for k, v in {
                "issue_recurrence": self.issue_recurrence,
                "resolution_status": self.resolution_status,
                "sop_status": self.sop_status,
                "handling_time_mins": self.handling_time_mins,
                "source_file": self.source_file,
            }.items() if v is not None
        }
