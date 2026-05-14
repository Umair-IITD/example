"""
Stage 8 — Evaluation Dataset Construction.
Produces evaluation_dataset.json: structured Q→A pairs for RAG evaluation.
Prioritizes AI-tagged tickets (ground-truth set) and GOLD RCA tickets.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..config import PipelineConfig, default_config
from ..models import CanonicalTicket

logger = logging.getLogger(__name__)


def _ticket_to_eval_record(ticket: CanonicalTicket) -> Optional[dict[str, Any]]:
    """
    Build a single evaluation record from a ticket.
    Returns None if the ticket lacks enough content to form a meaningful eval pair.
    """
    query = ticket.cleaned_description or ticket.raw_description
    if not query or len(query.strip()) < 50:
        return None

    # Reference answer: prefer cleaned RCA, then subject as minimal fallback
    reference = ticket.cleaned_rca
    if not reference:
        return None  # Eval records require a ground-truth answer

    return {
        "id": f"eval_{ticket.ticket_id}",
        "ticket_id": ticket.ticket_id,
        "source_file": ticket.source_file,
        "tenant_id": ticket.tenant_id,
        "query": query.strip(),
        "reference_answer": reference.strip(),
        "subject": ticket.subject,
        "query_type": ticket.query_type,
        "issue_area": ticket.issue_area,
        "environment": ticket.environment,
        "automation_class": ticket.automation_class,
        "rca_quality": ticket.rca_quality,
        "is_ai_tagged": ticket.ai_auto_replied or ticket.ai_note_present,
        "ai_auto_replied": ticket.ai_auto_replied,
        "session_ids": ticket.session_ids,
        "metadata": {
            "priority": ticket.priority,
            "resolution_status": ticket.resolution_status,
            "agent_interactions": ticket.agent_interactions,
            "handling_time_minutes": ticket.handling_time_minutes,
            "is_recurring": ticket.is_recurring,
            "sop_status": ticket.sop_status,
        },
    }


def run(
    tickets: list[CanonicalTicket],
    cfg: Optional[PipelineConfig] = None,
) -> list[dict[str, Any]]:
    """
    Build the evaluation dataset from included tickets.

    Selection criteria (in priority order):
    1. Tickets tagged ai_auto_replied (ground-truth AI evaluation set)
    2. GOLD RCA quality tickets with substantive description
    3. SILVER RCA tickets with substantive description
    """
    if cfg is None:
        cfg = default_config()

    ai_tagged: list[dict] = []
    gold_records: list[dict] = []
    silver_records: list[dict] = []

    for ticket in tickets:
        if ticket.automation_class == "EXCLUDE":
            continue
        if ticket.description_len_cleaned < cfg.eval_min_description_chars:
            continue

        record = _ticket_to_eval_record(ticket)
        if record is None:
            continue

        if ticket.ai_auto_replied:
            ai_tagged.append(record)
        elif ticket.rca_quality == "GOLD":
            gold_records.append(record)
        elif ticket.rca_quality == "SILVER":
            silver_records.append(record)

    # Combine: AI-tagged first (most valuable), then GOLD, then SILVER
    eval_dataset = ai_tagged + gold_records + silver_records

    logger.info(
        "[S8] Eval dataset: %d records total "
        "(ai_tagged=%d, gold=%d, silver=%d)",
        len(eval_dataset), len(ai_tagged), len(gold_records), len(silver_records),
    )
    return eval_dataset


def save(eval_dataset: list[dict[str, Any]], cfg: Optional[PipelineConfig] = None) -> None:
    """Write evaluation dataset JSON to the processed output directory."""
    if cfg is None:
        cfg = default_config()

    out_path = cfg.processed_dir / "evaluation_dataset.json"
    out_path.write_text(
        json.dumps(eval_dataset, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("[S8] Written: %s (%d eval records)", out_path, len(eval_dataset))
