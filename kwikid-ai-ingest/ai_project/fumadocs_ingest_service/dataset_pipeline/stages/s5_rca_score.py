"""
Stage 5 — RCA Quality Scoring.
Assigns rca_quality label to each ticket: GOLD | SILVER | TRIVIAL | NONE
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..config import PipelineConfig, default_config
from ..models import CanonicalTicket

logger = logging.getLogger(__name__)

# Patterns indicating technical specificity (upgrade SILVER → GOLD candidate)
_TECHNICAL_INDICATORS = re.compile(
    r"\b(error|exception|timeout|null|undefined|500|404|403|api|db|database|"
    r"server|deploy|config|cache|queue|token|auth|ssl|tls|firewall|dns|"
    r"memory|cpu|disk|crash|hang|loop|race|deadlock|migration|rollback)\b",
    re.IGNORECASE,
)


def _score_rca(ticket: CanonicalTicket, cfg: PipelineConfig) -> str:
    """
    Determine RCA quality tier for a single ticket.
    Priority order: GOLD > SILVER > TRIVIAL > NONE
    """
    cleaned = ticket.cleaned_rca
    if not cleaned:
        return "NONE"

    length = len(cleaned)

    if length < cfg.min_rca_chars_usable:
        return "TRIVIAL"

    if length >= cfg.min_rca_chars_gold:
        # Additional check: must have at least some technical substance
        tech_matches = len(_TECHNICAL_INDICATORS.findall(cleaned))
        if tech_matches >= 1:
            return "GOLD"
        # Long but non-technical → SILVER
        return "SILVER"

    if length >= cfg.min_rca_chars_silver:
        return "SILVER"

    return "TRIVIAL"


def run(
    tickets: list[CanonicalTicket],
    cfg: Optional[PipelineConfig] = None,
) -> list[CanonicalTicket]:
    """
    Score RCA quality for all tickets. Mutates rca_quality field in-place.
    """
    if cfg is None:
        cfg = default_config()

    counts: dict[str, int] = {"GOLD": 0, "SILVER": 0, "TRIVIAL": 0, "NONE": 0}

    for ticket in tickets:
        quality = _score_rca(ticket, cfg)
        ticket.rca_quality = quality
        counts[quality] = counts.get(quality, 0) + 1

    total = len(tickets)
    logger.info(
        "[S5] RCA scoring done: GOLD=%d (%.1f%%), SILVER=%d (%.1f%%), "
        "TRIVIAL=%d (%.1f%%), NONE=%d (%.1f%%)",
        counts["GOLD"],    100 * counts["GOLD"] / max(total, 1),
        counts["SILVER"],  100 * counts["SILVER"] / max(total, 1),
        counts["TRIVIAL"], 100 * counts["TRIVIAL"] / max(total, 1),
        counts["NONE"],    100 * counts["NONE"] / max(total, 1),
    )
    return tickets
