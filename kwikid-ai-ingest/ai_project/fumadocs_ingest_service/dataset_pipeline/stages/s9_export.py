"""
Stage 9 — Export.
Writes all 8 output artifacts and the preprocessing quality report.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ..config import PipelineConfig, default_config
from ..models import CanonicalTicket

logger = logging.getLogger(__name__)


def _tickets_to_records(tickets: list[CanonicalTicket]) -> list[dict]:
    """Serialize CanonicalTicket list to plain dicts for DataFrame/JSON export."""
    records = []
    for t in tickets:
        d = t.model_dump()
        # Convert datetime to ISO strings for CSV/parquet compat
        for field in ("created_at", "resolved_at", "closed_at", "last_updated_at"):
            val = d.get(field)
            if val is not None and isinstance(val, datetime):
                d[field] = val.isoformat()
        # Lists → pipe-separated strings for CSV
        d["session_ids"] = "|".join(t.session_ids)
        d["tags"] = "|".join(t.tags)
        records.append(d)
    return records


def _build_preprocessing_report(
    all_tickets: list[CanonicalTicket],
    included: list[CanonicalTicket],
    excluded: list[CanonicalTicket],
) -> str:
    """Build a markdown preprocessing quality report."""
    total = len(all_tickets)
    n_incl = len(included)
    n_excl = len(excluded)

    # Automation class counts
    ac_counts: dict[str, int] = {}
    for t in all_tickets:
        ac = t.automation_class or "UNLABELED"
        ac_counts[ac] = ac_counts.get(ac, 0) + 1

    # RCA quality counts
    rca_counts: dict[str, int] = {}
    for t in included:
        rq = t.rca_quality or "NONE"
        rca_counts[rq] = rca_counts.get(rq, 0) + 1

    # Cleaning stats
    html_stripped = sum(1 for t in included if t.html_stripped)
    sig_stripped = sum(1 for t in included if t.signature_stripped)
    with_desc = sum(1 for t in included if t.description_len_cleaned > 0)
    with_rca = sum(1 for t in included if t.cleaned_rca)

    # Exclusion reasons
    excl_reasons: dict[str, int] = {}
    for t in excluded:
        r = t.excluded_reason or "unknown"
        # Bucket by prefix for readability
        prefix = r.split(":")[0]
        excl_reasons[prefix] = excl_reasons.get(prefix, 0) + 1

    # Source breakdown
    source_counts: dict[str, int] = {}
    for t in all_tickets:
        source_counts[t.source_format] = source_counts.get(t.source_format, 0) + 1

    # Tenant breakdown
    tenant_counts: dict[str, int] = {}
    for t in included:
        tid = t.tenant_id or "unknown"
        tenant_counts[tid] = tenant_counts.get(tid, 0) + 1

    def pct(n: int, d: int) -> str:
        return f"{100 * n / max(d, 1):.1f}%"

    lines = [
        "# Preprocessing Quality Report",
        f"> Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## 1. Volume Summary",
        "",
        f"| Metric | Count | % of Total |",
        f"|---|---|---|",
        f"| Raw rows loaded | {total} | 100% |",
        f"| Included (customer-facing) | {n_incl} | {pct(n_incl, total)} |",
        f"| Excluded (non-support / ops) | {n_excl} | {pct(n_excl, total)} |",
        "",
        "### Source file breakdown",
        "",
        "| Source | Rows |",
        "|---|---|",
    ]
    for src, cnt in sorted(source_counts.items()):
        lines.append(f"| {src} | {cnt} |")

    lines += [
        "",
        "## 2. Exclusion Breakdown",
        "",
        "| Reason | Count |",
        "|---|---|",
    ]
    for reason, cnt in sorted(excl_reasons.items(), key=lambda x: -x[1]):
        lines.append(f"| {reason} | {cnt} |")

    lines += [
        "",
        "## 3. Automation Class Distribution",
        "",
        "| Class | Count | % |",
        "|---|---|---|",
    ]
    for cls in ("AUTO_RESOLVABLE", "HUMAN_REVIEW_REQUIRED", "ESCALATION_REQUIRED", "EXCLUDE"):
        cnt = ac_counts.get(cls, 0)
        lines.append(f"| {cls} | {cnt} | {pct(cnt, total)} |")

    lines += [
        "",
        "## 4. RCA Quality (Included Tickets Only)",
        "",
        "| Tier | Count | % |",
        "|---|---|---|",
    ]
    for tier in ("GOLD", "SILVER", "TRIVIAL", "NONE"):
        cnt = rca_counts.get(tier, 0)
        lines.append(f"| {tier} | {cnt} | {pct(cnt, n_incl)} |")

    lines += [
        "",
        "## 5. Text Cleaning Stats (Included Tickets)",
        "",
        f"| Stat | Count | % |",
        f"|---|---|---|",
        f"| HTML stripped | {html_stripped} | {pct(html_stripped, n_incl)} |",
        f"| Signatures stripped | {sig_stripped} | {pct(sig_stripped, n_incl)} |",
        f"| Has cleaned description | {with_desc} | {pct(with_desc, n_incl)} |",
        f"| Has cleaned RCA | {with_rca} | {pct(with_rca, n_incl)} |",
        "",
        "## 6. Tenant Distribution (Included Tickets)",
        "",
        "| Tenant | Count | % |",
        "|---|---|---|",
    ]
    for tenant, cnt in sorted(tenant_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {tenant} | {cnt} | {pct(cnt, n_incl)} |")

    lines += ["", "---", "_No source data files were modified during this pipeline run._"]
    return "\n".join(lines)


def run(
    all_tickets: list[CanonicalTicket],
    included: list[CanonicalTicket],
    excluded: list[CanonicalTicket],
    eval_dataset: list[dict[str, Any]],
    taxonomy: dict[str, Any],
    kc_candidates: list[dict[str, Any]],
    cfg: Optional[PipelineConfig] = None,
) -> None:
    """
    Write all 8 output artifacts:
      1. unified_cleaned_dataset.parquet
      2. unified_cleaned_dataset.csv
      3. gold_dataset.csv
      4. evaluation_dataset.json
      5. knowledge_card_candidates.json
      6. query_taxonomy.json
      7. automation_labels.csv
      8. preprocessing_report.md  (in reports/)
    """
    if cfg is None:
        cfg = default_config()

    cfg.ensure_dirs()

    # ── Unified dataset (included only) ─────────────────────────────────────
    records = _tickets_to_records(included)
    df_all = pd.DataFrame(records)

    parquet_path = cfg.processed_dir / "unified_cleaned_dataset.parquet"
    df_all.to_parquet(parquet_path, index=False, engine="pyarrow")
    logger.info("[S9] Written: %s (%d rows)", parquet_path, len(df_all))

    csv_path = cfg.processed_dir / "unified_cleaned_dataset.csv"
    df_all.to_csv(csv_path, index=False, encoding="utf-8")
    logger.info("[S9] Written: %s", csv_path)

    # ── Gold dataset (GOLD RCA + sufficient description) ─────────────────────
    gold_tickets = [
        t for t in included
        if t.rca_quality == "GOLD"
        and t.description_len_cleaned >= cfg.min_description_chars
    ]
    gold_records = _tickets_to_records(gold_tickets)
    df_gold = pd.DataFrame(gold_records) if gold_records else pd.DataFrame()
    gold_path = cfg.processed_dir / "gold_dataset.csv"
    df_gold.to_csv(gold_path, index=False, encoding="utf-8")
    logger.info("[S9] Written: %s (%d gold tickets)", gold_path, len(gold_tickets))

    # ── Automation labels (all tickets including excluded) ────────────────────
    label_records = []
    for t in all_tickets:
        label_records.append({
            "ticket_id": t.ticket_id,
            "source_file": t.source_file,
            "tenant_id": t.tenant_id,
            "automation_class": t.automation_class,
            "rca_quality": t.rca_quality,
            "excluded_reason": t.excluded_reason,
            "query_type": t.query_type,
            "priority": t.priority,
            "resolution_status": t.resolution_status,
            "agent_interactions": t.agent_interactions,
            "is_recurring": t.is_recurring,
            "asana_present": t.asana_ticket_present,
        })
    df_labels = pd.DataFrame(label_records)
    labels_path = cfg.processed_dir / "automation_labels.csv"
    df_labels.to_csv(labels_path, index=False, encoding="utf-8")
    logger.info("[S9] Written: %s (%d rows)", labels_path, len(df_labels))

    # ── Evaluation dataset ───────────────────────────────────────────────────
    eval_path = cfg.processed_dir / "evaluation_dataset.json"
    eval_path.write_text(
        json.dumps(eval_dataset, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("[S9] Written: %s (%d records)", eval_path, len(eval_dataset))

    # ── Query taxonomy ────────────────────────────────────────────────────────
    tax_path = cfg.processed_dir / "query_taxonomy.json"
    tax_path.write_text(json.dumps(taxonomy, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("[S9] Written: %s (%d query types)", tax_path, len(taxonomy))

    # ── Knowledge card candidates ─────────────────────────────────────────────
    kc_path = cfg.processed_dir / "knowledge_card_candidates.json"
    kc_path.write_text(
        json.dumps(kc_candidates, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("[S9] Written: %s (%d candidates)", kc_path, len(kc_candidates))

    # ── Preprocessing report ──────────────────────────────────────────────────
    report_md = _build_preprocessing_report(all_tickets, included, excluded)
    report_path = cfg.reports_dir / "preprocessing_report.md"
    report_path.write_text(report_md, encoding="utf-8")
    logger.info("[S9] Written: %s", report_path)

    logger.info("[S9] All 8 artifacts written to %s", cfg.processed_dir)
