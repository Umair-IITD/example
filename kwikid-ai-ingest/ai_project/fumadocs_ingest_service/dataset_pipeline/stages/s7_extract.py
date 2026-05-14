"""
Stage 7 — Knowledge Extraction.
Produces two JSON artifacts:
  - query_taxonomy.json: category/subcategory frequency map with SOP and automation signals
  - knowledge_card_candidates.json: tickets ideal for Teach-the-AI knowledge card generation
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from ..config import PipelineConfig, default_config
from ..models import CanonicalTicket

logger = logging.getLogger(__name__)


def _build_query_taxonomy(tickets: list[CanonicalTicket]) -> dict[str, Any]:
    """
    Aggregate ticket counts by (query_type, issue_area) with SOP and automation signals.
    """
    taxonomy: dict[str, dict] = defaultdict(lambda: {
        "total": 0,
        "auto_resolvable": 0,
        "escalation": 0,
        "human_review": 0,
        "exclude": 0,
        "sop_present": 0,
        "no_sop_available": 0,
        "has_rca": 0,
        "issue_areas": defaultdict(int),
        "environments": defaultdict(int),
        "tenants": defaultdict(int),
    })

    for t in tickets:
        qt = t.query_type or "Unknown"
        entry = taxonomy[qt]
        entry["total"] += 1

        ac = (t.automation_class or "").upper()
        if ac == "AUTO_RESOLVABLE":
            entry["auto_resolvable"] += 1
        elif ac == "ESCALATION_REQUIRED":
            entry["escalation"] += 1
        elif ac == "HUMAN_REVIEW_REQUIRED":
            entry["human_review"] += 1
        elif ac == "EXCLUDE":
            entry["exclude"] += 1

        sop = (t.sop_status or "").lower()
        if "present" in sop:
            entry["sop_present"] += 1
        elif "no sop available" in sop:
            entry["no_sop_available"] += 1

        if t.cleaned_rca:
            entry["has_rca"] += 1

        if t.issue_area:
            entry["issue_areas"][t.issue_area] += 1
        if t.environment:
            entry["environments"][t.environment] += 1
        if t.tenant_id:
            entry["tenants"][t.tenant_id] += 1

    # Convert defaultdicts to plain dicts and sort issue_areas
    result: dict[str, Any] = {}
    for qt, entry in sorted(taxonomy.items(), key=lambda x: -x[1]["total"]):
        result[qt] = {
            "total": entry["total"],
            "automation": {
                "auto_resolvable": entry["auto_resolvable"],
                "human_review": entry["human_review"],
                "escalation": entry["escalation"],
                "exclude": entry["exclude"],
            },
            "sop": {
                "sop_present": entry["sop_present"],
                "no_sop_available": entry["no_sop_available"],
            },
            "has_rca": entry["has_rca"],
            "issue_areas": dict(sorted(entry["issue_areas"].items(), key=lambda x: -x[1])),
            "environments": dict(entry["environments"]),
            "tenants": dict(entry["tenants"]),
        }
    return result


def _build_knowledge_card_candidates(
    tickets: list[CanonicalTicket],
    cfg: PipelineConfig,
) -> list[dict[str, Any]]:
    """
    Select tickets that are ideal knowledge card sources:
    - AUTO_RESOLVABLE + GOLD/SILVER RCA + has substantive description
    - OR tickets with 'No SOP Available' that have rich Description (knowledge gaps)
    """
    candidates: list[dict[str, Any]] = []

    for t in tickets:
        is_gold_candidate = (
            t.automation_class == "AUTO_RESOLVABLE"
            and t.rca_quality in ("GOLD", "SILVER")
            and t.description_len_cleaned >= cfg.min_description_chars
        )
        is_gap_candidate = (
            "no sop available" in (t.sop_status or "").lower()
            and t.description_len_cleaned >= cfg.eval_min_description_chars
            and t.automation_class not in ("EXCLUDE", "ESCALATION_REQUIRED")
        )

        if not (is_gold_candidate or is_gap_candidate):
            continue

        candidates.append({
            "ticket_id": t.ticket_id,
            "source_file": t.source_file,
            "tenant_id": t.tenant_id,
            "subject": t.subject,
            "query_type": t.query_type,
            "issue_area": t.issue_area,
            "sop_status": t.sop_status,
            "automation_class": t.automation_class,
            "rca_quality": t.rca_quality,
            "description_preview": (t.cleaned_description or "")[:300],
            "rca": t.cleaned_rca,
            "is_recurring": t.is_recurring,
            "asana_present": t.asana_ticket_present,
            "session_ids": t.session_ids,
            "environment": t.environment,
            "candidate_type": "gap" if is_gap_candidate else "gold",
        })

    # Sort: gaps first (highest training value), then gold candidates by RCA quality
    candidates.sort(key=lambda x: (0 if x["candidate_type"] == "gap" else 1, x["ticket_id"]))
    return candidates


def run(
    tickets: list[CanonicalTicket],
    cfg: Optional[PipelineConfig] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Build query taxonomy and knowledge card candidates.

    Returns:
        (query_taxonomy_dict, knowledge_card_candidates_list)
    """
    if cfg is None:
        cfg = default_config()

    taxonomy = _build_query_taxonomy(tickets)
    kc_candidates = _build_knowledge_card_candidates(tickets, cfg)

    logger.info(
        "[S7] Query taxonomy: %d unique query types; Knowledge card candidates: %d",
        len(taxonomy), len(kc_candidates),
    )
    return taxonomy, kc_candidates


def save(
    taxonomy: dict[str, Any],
    kc_candidates: list[dict[str, Any]],
    cfg: Optional[PipelineConfig] = None,
) -> None:
    """Write both JSON artifacts to the processed output directory."""
    if cfg is None:
        cfg = default_config()

    tax_path = cfg.processed_dir / "query_taxonomy.json"
    kc_path = cfg.processed_dir / "knowledge_card_candidates.json"

    tax_path.write_text(json.dumps(taxonomy, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("[S7] Written: %s (%d query types)", tax_path, len(taxonomy))

    kc_path.write_text(json.dumps(kc_candidates, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("[S7] Written: %s (%d candidates)", kc_path, len(kc_candidates))
