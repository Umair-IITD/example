"""
Stage 3 — Exclusion Filtering.
Removes tickets that are not customer-facing support queries.
All exclusions are auditable: excluded_reason is set on each dropped ticket.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import PipelineConfig, default_config
from ..models import CanonicalTicket

logger = logging.getLogger(__name__)


def _is_excluded(ticket: CanonicalTicket, cfg: PipelineConfig) -> Optional[str]:
    """
    Return a non-None exclusion reason string if this ticket should be excluded,
    or None if the ticket passes the filter.
    """
    # 1. Query type blocklist
    qt = (ticket.query_type or "").strip()
    if qt in cfg.excluded_query_types:
        return f"excluded_query_type:{qt}"

    # 2. No usable text content at all (no subject, no description)
    if not ticket.subject and not ticket.raw_description:
        return "no_text_content"

    # 3. XLS source: no Description column — keep for metadata only (don't fully exclude,
    #    but mark as metadata-only so downstream stages can skip description-required steps)
    # We do NOT exclude XLS tickets here; Stage 3 only applies hard exclusion rules.

    return None


def run(
    tickets: list[CanonicalTicket],
    cfg: Optional[PipelineConfig] = None,
) -> tuple[list[CanonicalTicket], list[CanonicalTicket]]:
    """
    Split tickets into (included, excluded) lists.
    Excluded tickets have their excluded_reason field set for auditability.

    Returns:
        (included_tickets, excluded_tickets)
    """
    if cfg is None:
        cfg = default_config()

    included: list[CanonicalTicket] = []
    excluded: list[CanonicalTicket] = []

    reason_counts: dict[str, int] = {}

    for ticket in tickets:
        reason = _is_excluded(ticket, cfg)
        if reason:
            ticket.excluded_reason = reason
            ticket.automation_class = "EXCLUDE"
            excluded.append(ticket)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        else:
            included.append(ticket)

    total = len(tickets)
    n_excl = len(excluded)
    logger.info(
        "[S3] Filtered: %d included, %d excluded (%.1f%%) out of %d total",
        len(included), n_excl, 100 * n_excl / max(total, 1), total,
    )
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])[:10]:
        logger.info("[S3]   %-50s %d", reason, count)

    return included, excluded
