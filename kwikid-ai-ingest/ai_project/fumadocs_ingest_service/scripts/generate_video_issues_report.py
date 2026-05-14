from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
DATE_SUFFIX_RE = re.compile(r"\(\d{1,2}-\d{1,2}-\d{4}\)")
MULTISPACE_RE = re.compile(r"\s+")
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _normalize_subject(subject: str) -> str:
    value = (subject or "").strip()
    if not value:
        return "Unknown issue"
    value = UUID_RE.sub("", value)
    value = DATE_SUFFIX_RE.sub("", value)
    value = re.sub(r"\s+-\s+$", "", value)
    value = MULTISPACE_RE.sub(" ", value).strip(" -.")
    return value or "Unknown issue"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:  # noqa: BLE001
        pass
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return MULTISPACE_RE.sub(" ", text).strip()


def _top_counter_lines(counter: Counter[str], *, max_items: int = 10) -> list[str]:
    lines: list[str] = []
    for label, count in counter.most_common(max_items):
        if not label:
            continue
        lines.append(f"- {label}: {count}")
    return lines or ["- None"]


def _extract_support_reply_snippets(conversations: list[dict[str, Any]], *, max_len: int = 220) -> list[str]:
    snippets: list[str] = []
    for conv in conversations:
        incoming = bool(conv.get("incoming", False))
        if incoming:
            continue
        body = _clean_text(conv.get("body_text") or conv.get("body") or "")
        body = HTML_TAG_RE.sub(" ", body)
        body = _clean_text(body)
        if not body:
            continue
        # Skip raw HTML/image-token style blobs; keep human-readable responses.
        if "indattachment.freshdesk.com" in body and len(body) < 260:
            continue
        snippet = body[:max_len].strip()
        snippets.append(snippet)
    return snippets


def _derive_issue_buckets(df: pd.DataFrame) -> dict[str, list[int]]:
    by_subject: dict[str, list[int]] = defaultdict(list)
    for _, row in df.iterrows():
        ticket_id_raw = row.get("Ticket ID")
        if pd.isna(ticket_id_raw):
            continue
        try:
            ticket_id = int(ticket_id_raw)
        except (TypeError, ValueError):
            continue
        normalized_subject = _normalize_subject(_clean_text(row.get("Subject")))
        by_subject[normalized_subject].append(ticket_id)
    return dict(by_subject)


def _collect_ticket_map(conversation_json: dict[str, Any]) -> dict[int, dict[str, Any]]:
    ticket_map: dict[int, dict[str, Any]] = {}
    for item in conversation_json.get("tickets", []):
        if not isinstance(item, dict):
            continue
        ticket_id = item.get("ticket_id")
        try:
            ticket_id_int = int(ticket_id)
        except (TypeError, ValueError):
            continue
        ticket_map[ticket_id_int] = item
    return ticket_map


def _build_recommendation(issue_name: str) -> str:
    lowered = issue_name.lower()
    if "blackout" in lowered:
        return "Ask for app version, device model, and latest upload timestamp. Share playback reset SOP and request one sample user ID for backend log trace."
    if "crop" in lowered or "cropping" in lowered:
        return "Confirm source aspect ratio and upload flow used. Guide user through recrop SOP and verify transcoding completion before retry."
    if "incomplete video" in lowered or "not reflected" in lowered:
        return "Collect upload ID and processing timestamp, then check processing queue delay. Provide wait-time expectation and retry guidance."
    if "slow" in lowered or "buffer" in lowered:
        return "Capture network type, bitrate symptoms, and affected geography. Share troubleshooting SOP and raise infra check if pattern is broad."
    return "Collect ticket context (environment, client, issue area), apply matching SOP, and escalate with ticket link plus reproduction steps if unresolved."


def _build_markdown(
    *,
    excel_path: Path,
    json_path: Path,
    df: pd.DataFrame,
    conversation_payload: dict[str, Any],
) -> str:
    total_tickets = len(df.index)
    unique_ticket_ids = df["Ticket ID"].dropna().astype(int).nunique() if "Ticket ID" in df.columns else 0

    status_counter = Counter(_clean_text(v) for v in df.get("Status", pd.Series(dtype="object")) if _clean_text(v))
    resolution_counter = Counter(
        _clean_text(v) for v in df.get("Resolution Classification", pd.Series(dtype="object")) if _clean_text(v)
    )
    issue_area_counter = Counter(_clean_text(v) for v in df.get("Issue Area", pd.Series(dtype="object")) if _clean_text(v))
    impact_counter = Counter(_clean_text(v) for v in df.get("Impact", pd.Series(dtype="object")) if _clean_text(v))
    portal_counter = Counter(_clean_text(v) for v in df.get("Portal", pd.Series(dtype="object")) if _clean_text(v))

    issue_buckets = _derive_issue_buckets(df)
    top_issue_buckets = sorted(issue_buckets.items(), key=lambda kv: len(kv[1]), reverse=True)[:15]

    ticket_map = _collect_ticket_map(conversation_payload)
    support_snippet_counter: Counter[str] = Counter()
    for item in ticket_map.values():
        conversations = item.get("conversations", [])
        if not isinstance(conversations, list):
            continue
        support_snippet_counter.update(_extract_support_reply_snippets(conversations))

    lines: list[str] = []
    lines.append("# Video Related Issues Consolidated Report")
    lines.append("")
    lines.append("## Purpose")
    lines.append(
        "This document consolidates video-related support issues, team responses, and conversation evidence for ingest into the AI knowledge pipeline."
    )
    lines.append("")
    lines.append("## Data Sources")
    lines.append(f"- Excel source: `{excel_path}`")
    lines.append(f"- Freshdesk conversation export: `{json_path}`")
    lines.append(f"- Report generated at (UTC): `{datetime.now(UTC).isoformat()}`")
    lines.append("")
    lines.append("## Dataset Overview")
    lines.append(f"- Total rows in Excel: {total_tickets}")
    lines.append(f"- Unique ticket IDs: {unique_ticket_ids}")
    lines.append(f"- Freshdesk tickets exported with conversation context: {len(ticket_map)}")
    lines.append(f"- Freshdesk export failures: {len(conversation_payload.get('failures', []))}")
    lines.append("")
    lines.append("## Ticket Distribution")
    lines.append("### Status")
    lines.extend(_top_counter_lines(status_counter, max_items=10))
    lines.append("")
    lines.append("### Resolution Classification")
    lines.extend(_top_counter_lines(resolution_counter, max_items=12))
    lines.append("")
    lines.append("### Issue Area")
    lines.extend(_top_counter_lines(issue_area_counter, max_items=12))
    lines.append("")
    lines.append("### Impact")
    lines.extend(_top_counter_lines(impact_counter, max_items=10))
    lines.append("")
    lines.append("### Portal")
    lines.extend(_top_counter_lines(portal_counter, max_items=10))
    lines.append("")
    lines.append("## Top Recurring Video Issue Themes")
    for theme, tickets in top_issue_buckets:
        sample = ", ".join(str(t) for t in tickets[:8])
        lines.append(f"- {theme} -> {len(tickets)} tickets (sample IDs: {sample})")
    lines.append("")
    lines.append("## Support Response Patterns (from conversation threads)")
    for snippet, count in support_snippet_counter.most_common(20):
        lines.append(f"- ({count}) {snippet}")
    if not support_snippet_counter:
        lines.append("- No outgoing support conversation snippets were detected.")
    lines.append("")
    lines.append("## AI Response Playbook (Issue -> Suggested Response Policy)")
    for theme, tickets in top_issue_buckets[:12]:
        recommendation = _build_recommendation(theme)
        lines.append(f"- {theme} ({len(tickets)} tickets): {recommendation}")
    lines.append("")
    lines.append("## Evidence Mapping (Issue -> StackOverflow / Ticket Links)")
    for _, row in df.head(80).iterrows():
        ticket_id = row.get("Ticket ID")
        subject = _normalize_subject(_clean_text(row.get("Subject")))
        so_link = _clean_text(row.get("StackOverflow Link"))
        if pd.isna(ticket_id):
            continue
        try:
            tid = int(ticket_id)
        except (TypeError, ValueError):
            continue
        ticket_link = ""
        ticket_payload = ticket_map.get(tid, {})
        if ticket_payload:
            ticket_obj = ticket_payload.get("ticket", {})
            if isinstance(ticket_obj, dict):
                url = ticket_obj.get("url")
                if isinstance(url, str):
                    ticket_link = url
        joined_links = ", ".join(part for part in [so_link, ticket_link] if part and part.lower() != "nan")
        lines.append(f"- Ticket {tid}: {subject}" + (f" | Links: {joined_links}" if joined_links else ""))
    lines.append("")
    lines.append("## Ingest Notes")
    lines.append("- This markdown is structured for direct ingestion as reference knowledge.")
    lines.append("- Freshdesk JSON export should be retained for traceability and future re-processing.")
    lines.append("- If this report is re-generated, ingest the latest version and archive previous snapshots.")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate consolidated video-issues markdown report from Excel + Freshdesk export.")
    parser.add_argument(
        "--excel",
        default="../../../../video related queries excel sheet.xlsx",
        help="Input Excel path.",
    )
    parser.add_argument(
        "--json",
        default="./data/exports/video_related_freshdesk_conversations.json",
        help="Input Freshdesk conversations JSON path.",
    )
    parser.add_argument(
        "--out",
        default="./data/reports/video_related_issues_consolidated.md",
        help="Output markdown path.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    excel_path = Path(args.excel).expanduser().resolve()
    json_path = Path(args.json).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(excel_path)
    conversation_payload = json.loads(json_path.read_text(encoding="utf-8"))
    report = _build_markdown(
        excel_path=excel_path,
        json_path=json_path,
        df=df,
        conversation_payload=conversation_payload,
    )
    out_path.write_text(report, encoding="utf-8")
    print(f"Report written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

