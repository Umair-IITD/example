"""
rag_engine/document_builder/ticket_builder.py

Transforms a validated TicketSourceRow into a RagTicketDocument.

Design philosophy:
  - The document is structured in named sections so the chunker can split on
    section boundaries rather than arbitrary word counts.
  - Each section is independently useful for retrieval:
      ISSUE_HEADER:  metadata-rich short section — drives exact-category matching
      QUERY_BODY:    the customer's actual complaint — primary semantic match target
      RESOLUTION_RCA: resolution steps + root cause — the answer generation source
  - If a field is absent (no RCA, no description), that section is omitted
    rather than generating empty/noisy content.
  - Language is support-engineer tone, not raw data dump.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

from rag_engine.schemas.ticket_document import (
    AutomationLabel,
    RagTicketDocument,
    TicketSourceRow,
)

LOGGER = logging.getLogger(__name__)

# Automation label → human-readable description for document text
_LABEL_DESCRIPTIONS: dict[str, str] = {
    "AUTO_REPLY": "Auto-resolvable — SOP-backed, recurring, within SLA",
    "HUMAN_REVIEW": "Requires agent review before posting AI draft",
    "ESCALATION": "Escalation required — high priority or dev involvement needed",
    "UNKNOWN": "Classification pending",
}

# Environment normalizations for cleaner document text
_ENV_DISPLAY: dict[str, str] = {
    "production": "Production",
    "prod": "Production",
    "uat": "UAT",
    "staging": "Staging",
    "stage": "Staging",
}

# Priority display
_PRIORITY_DISPLAY: dict[str, str] = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "urgent": "Urgent",
}

# Generic resolution status values that carry no semantic signal for RAG retrieval.
# Including these as document content creates near-identical embeddings across thousands
# of tickets (e.g. "Within SLA" appears on ~80% of resolved tickets), collapsing the
# semantic space and degrading retrieval precision.
_BOILERPLATE_STATUSES: frozenset[str] = frozenset({
    "within sla",
    "resolved",
    "closed",
    "completed",
    "done",
    "fixed",
    "resolved - within sla",
    "closed - within sla",
})


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_whitespace(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_client_name(client_slug: str) -> str:
    """unity_bank → Unity Bank."""
    return client_slug.replace("_", " ").title()


def _format_bool_signal(label: str, value: bool) -> str:
    return f"{label}: {'Yes' if value else 'No'}"


class TicketDocumentBuilder:
    """
    Converts a TicketSourceRow into a structured RagTicketDocument.
    Stateless — thread-safe, can be shared across ingestion workers.
    """

    def build(self, row: TicketSourceRow) -> Optional[RagTicketDocument]:
        """
        Build a RagTicketDocument from a source row.
        Returns None if the row lacks the minimum content required for useful retrieval.
        """
        if not row.has_usable_description and not row.has_usable_rca:
            LOGGER.debug(
                "Skipping ticket %s: no usable description or RCA", row.ticket_id
            )
            return None

        issue_header = self._build_issue_header(row)
        query_body = self._build_query_body(row)
        resolution_rca = self._build_resolution_rca(row)
        full_document = self._assemble_full_document(issue_header, query_body, resolution_rca)
        content_hash = _sha256(full_document)

        return RagTicketDocument(
            ticket_id=row.ticket_id,
            client=row.client,
            source_file=row.source_file,
            source_type="freshdesk",
            issue_header_text=issue_header,
            query_body_text=query_body,
            resolution_rca_text=resolution_rca,
            full_document_text=full_document,
            content_hash=content_hash,
            automation_label=AutomationLabel(row.automation_label),
            escalation_flag=row.escalation_flag,
            query_type=row.query_type,
            issue_area=row.issue_area,
            environment=row.environment,
            priority=row.priority,
            status=row.status,
            has_rca=row.has_usable_rca,
            has_sop=row.has_sop,
            sop_status=row.sop_status,
            rca_quality_score=row.rca_quality_score,
            subject=row.subject,
            agent_interactions=row.agent_interactions,
            handling_time_mins=row.handling_time_mins,
            issue_recurrence=row.issue_recurrence,
            resolution_status=row.resolution_status,
            ticket_created_at=row.ticket_created_at,
            ticket_resolved_at=row.ticket_resolved_at,
        )

    def _build_issue_header(self, row: TicketSourceRow) -> str:
        """
        Short metadata-rich section. Used as the ISSUE_HEADER chunk.
        Designed for category-based matching: 'OTP not received on Unity Bank'.
        """
        lines: list[str] = ["[ISSUE SUMMARY]"]

        if row.ticket_id:
            lines.append(f"Ticket ID: {row.ticket_id}")

        if row.subject:
            lines.append(f"Subject: {row.subject}")

        category_parts: list[str] = []
        if row.query_type:
            category_parts.append(row.query_type)
        if row.issue_area and row.issue_area != row.query_type:
            category_parts.append(row.issue_area)
        if category_parts:
            lines.append(f"Category: {' > '.join(category_parts)}")

        env_display = _ENV_DISPLAY.get(
            (row.environment or "").lower(), row.environment or "Unknown"
        )
        client_display = _format_client_name(row.client)
        priority_display = _PRIORITY_DISPLAY.get(
            (row.priority or "").lower(), row.priority or "Medium"
        )
        lines.append(f"Client: {client_display} | Environment: {env_display} | Priority: {priority_display}")

        label_desc = _LABEL_DESCRIPTIONS.get(row.automation_label, row.automation_label)
        lines.append(f"Automation Classification: {label_desc}")

        knowledge_parts = [
            _format_bool_signal("SOP Available", row.has_sop),
            _format_bool_signal("Recurring Issue", row.issue_recurrence == "Recurring issue"),
        ]
        lines.append(" | ".join(knowledge_parts))

        return _clean_whitespace("\n".join(lines))

    def _build_query_body(self, row: TicketSourceRow) -> str:
        """
        Customer query section. This is the PRIMARY semantic match target.
        When a new ticket arrives, its description is compared against this chunk.
        """
        if not row.has_usable_description:
            # Use subject as fallback to still have SOME semantic content
            if row.subject:
                return _clean_whitespace(f"[CUSTOMER QUERY]\n{row.subject}")
            return ""

        return _clean_whitespace(f"[CUSTOMER QUERY]\n{row.cleaned_description}")

    def _build_resolution_rca(self, row: TicketSourceRow) -> str:
        """
        Resolution + RCA section. This is the PRIMARY answer generation source.
        The LLM draws from this section to draft its response.
        """
        if not row.has_usable_rca:
            parts = ["[TROUBLESHOOTING AND RESOLUTION]"]

            if row.sop_status == "SOP Present" and row.has_sop:
                parts.append(
                    "This issue type has a documented SOP. "
                    "The support team should guide the customer through the SOP steps."
                )
            elif row.sop_status == "Solved by SOP":
                parts.append("This issue was resolved by following the standard SOP.")

            # Only include resolution_status when it carries semantic signal.
            # Generic values like "Within SLA" / "Resolved" appear on ~80% of tickets
            # and create near-identical embeddings — omit them to avoid semantic collapse.
            status_lower = (row.resolution_status or "").strip().lower()
            if row.resolution_status and status_lower not in _BOILERPLATE_STATUSES:
                parts.append(f"Resolution Status: {row.resolution_status}")

            return _clean_whitespace("\n".join(parts)) if len(parts) > 1 else ""

        parts = ["[TROUBLESHOOTING AND RESOLUTION]"]

        # Resolution context
        context_parts: list[str] = []
        if row.resolution_status:
            context_parts.append(f"Status: {row.resolution_status}")
        if row.agent_interactions is not None:
            context_parts.append(f"Interactions to resolve: {row.agent_interactions}")
        if row.handling_time_mins is not None:
            context_parts.append(f"Handling time: {row.handling_time_mins} minutes")
        if context_parts:
            parts.append(" | ".join(context_parts))

        # Core RCA content
        parts.append(f"\n[ROOT CAUSE AND FIX]\n{row.cleaned_rca}")

        return _clean_whitespace("\n".join(parts))

    def _assemble_full_document(
        self,
        issue_header: str,
        query_body: str,
        resolution_rca: str,
    ) -> str:
        """Concatenate all non-empty sections into the full document."""
        sections = [s for s in [issue_header, query_body, resolution_rca] if s.strip()]
        return _clean_whitespace("\n\n".join(sections))
