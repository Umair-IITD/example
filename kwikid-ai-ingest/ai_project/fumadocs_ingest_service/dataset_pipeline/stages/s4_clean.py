"""
Stage 4 — Text Cleaning.
Applies HTML stripping and signature removal to Description and RCA fields.
Updates cleaning audit fields on each ticket (mutates in place — returns same list).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..config import PipelineConfig, default_config
from ..models import CanonicalTicket
from ..preprocessing.html_cleaner import strip_html
from ..preprocessing.signature_cleaner import strip_signatures

logger = logging.getLogger(__name__)

_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_TRIVIAL_WHITESPACE_RE = re.compile(r"^\s*$")


def _clean_rca(rca_raw: Optional[str], trivial_phrases: frozenset) -> Optional[str]:
    """Normalize RCA text; return None if trivial."""
    if not rca_raw:
        return None
    # Strip HTML from RCA too (some RCA fields have HTML)
    cleaned, _ = strip_html(rca_raw)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return None
    # Check against trivial phrase list (case-insensitive)
    lower = cleaned.lower()
    for phrase in trivial_phrases:
        if phrase in lower and len(cleaned) < 60:
            return None
    return cleaned if len(cleaned) >= 3 else None


def run(
    tickets: list[CanonicalTicket],
    cfg: Optional[PipelineConfig] = None,
) -> list[CanonicalTicket]:
    """
    Clean Description and RCA fields on all tickets.
    Mutates tickets in-place; returns the same list for pipeline chaining.
    """
    if cfg is None:
        cfg = default_config()

    html_stripped_count = 0
    sig_stripped_count = 0
    short_after_clean = 0
    rca_cleaned_count = 0

    for ticket in tickets:
        # ── Description cleaning ─────────────────────────────────────────────
        raw_desc = ticket.raw_description
        if raw_desc:
            # Step 1: HTML → plain text
            after_html, was_html = strip_html(raw_desc)
            if was_html:
                html_stripped_count += 1
                ticket.html_stripped = True

            # Step 2: signature removal
            after_sig, was_sig = strip_signatures(after_html)
            if was_sig:
                sig_stripped_count += 1
                ticket.signature_stripped = True

            ticket.cleaned_description = after_sig if after_sig.strip() else None
            ticket.description_len_cleaned = len(after_sig.strip()) if after_sig.strip() else 0

            if ticket.description_len_cleaned < cfg.min_description_chars:
                short_after_clean += 1
                # Mark for potential exclusion but don't remove here — S3 already ran;
                # downstream stages check description_len_cleaned as needed.
                if not ticket.excluded_reason and ticket.description_len_cleaned == 0:
                    ticket.excluded_reason = "description_empty_after_clean"
        else:
            ticket.cleaned_description = None
            ticket.description_len_cleaned = 0

        # ── RCA cleaning ─────────────────────────────────────────────────────
        if ticket.rca:
            cleaned_rca = _clean_rca(ticket.rca, cfg.trivial_rca_phrases)
            ticket.cleaned_rca = cleaned_rca
            if cleaned_rca:
                rca_cleaned_count += 1

    total = len(tickets)
    logger.info(
        "[S4] Cleaning done on %d tickets: "
        "HTML stripped=%d, sig stripped=%d, short after clean=%d, RCA cleaned=%d",
        total, html_stripped_count, sig_stripped_count, short_after_clean, rca_cleaned_count,
    )
    return tickets
