"""
Stage 6 — Automation Class Labeling.
Assigns deterministic 4-class labels: AUTO_RESOLVABLE | HUMAN_REVIEW_REQUIRED |
ESCALATION_REQUIRED | EXCLUDE
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import PipelineConfig, default_config
from ..models import CanonicalTicket

logger = logging.getLogger(__name__)


def _label(ticket: CanonicalTicket, cfg: PipelineConfig) -> str:
    """
    Assign automation class using a strict priority ordering:
    EXCLUDE > ESCALATION_REQUIRED > AUTO_RESOLVABLE > HUMAN_REVIEW_REQUIRED
    """
    # Already excluded by S3
    if ticket.excluded_reason or ticket.automation_class == "EXCLUDE":
        return "EXCLUDE"

    qt = (ticket.query_type or "").strip()

    # EXCLUDE: still in blocklist (belt-and-suspenders; S3 may have missed any that
    # weren't loaded with query_type populated)
    if qt in cfg.excluded_query_types:
        return "EXCLUDE"

    # ESCALATION_REQUIRED — any single signal is sufficient
    escalation_signals = [
        qt in cfg.escalation_query_types,
        (ticket.priority or "").lower() in ("high", "urgent"),
        (ticket.resolution_status or "").lower() == "sla violated",
        (ticket.rca_status or "").lower() in ("rca pending from dev", "rca pending"),
        bool(ticket.asana_ticket_link and ticket.asana_ticket_link.strip()),
    ]
    if any(escalation_signals):
        return "ESCALATION_REQUIRED"

    # AUTO_RESOLVABLE — ALL criteria must be satisfied
    status_ok = (ticket.status or "").lower() in ("closed", "resolved")
    sla_ok = "within sla" in (ticket.resolution_status or "").lower()
    interactions_ok = (
        ticket.agent_interactions is None
        or ticket.agent_interactions <= cfg.auto_max_agent_interactions
    )
    recurrence_ok = "recurring" in (ticket.issue_recurrence or "").lower()

    if status_ok and sla_ok and interactions_ok and recurrence_ok:
        return "AUTO_RESOLVABLE"

    return "HUMAN_REVIEW_REQUIRED"


def run(
    tickets: list[CanonicalTicket],
    cfg: Optional[PipelineConfig] = None,
) -> list[CanonicalTicket]:
    """
    Label all tickets with automation class. Mutates automation_class in-place.
    """
    if cfg is None:
        cfg = default_config()

    counts: dict[str, int] = {
        "AUTO_RESOLVABLE": 0,
        "HUMAN_REVIEW_REQUIRED": 0,
        "ESCALATION_REQUIRED": 0,
        "EXCLUDE": 0,
    }

    for ticket in tickets:
        label = _label(ticket, cfg)
        ticket.automation_class = label
        counts[label] = counts.get(label, 0) + 1

    total = len(tickets)
    logger.info(
        "[S6] Automation labels: AUTO=%d (%.1f%%), HUMAN=%d (%.1f%%), "
        "ESCALATION=%d (%.1f%%), EXCLUDE=%d (%.1f%%)",
        counts["AUTO_RESOLVABLE"],      100 * counts["AUTO_RESOLVABLE"] / max(total, 1),
        counts["HUMAN_REVIEW_REQUIRED"],100 * counts["HUMAN_REVIEW_REQUIRED"] / max(total, 1),
        counts["ESCALATION_REQUIRED"],  100 * counts["ESCALATION_REQUIRED"] / max(total, 1),
        counts["EXCLUDE"],              100 * counts["EXCLUDE"] / max(total, 1),
    )
    return tickets
