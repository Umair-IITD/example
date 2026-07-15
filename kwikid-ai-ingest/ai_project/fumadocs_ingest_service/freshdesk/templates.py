"""
freshdesk/templates.py

Sprint 2.48: SOT-compliant HTML template builders for Freshdesk notes and replies.

Source of truth
---------------
Freshdesk_discovery/notes_and_replies.md:
    Section 3 — Private notes: content and formatting guidelines
    Section 4 — Draft approval pattern (⚠️ AI DRAFT — PENDING HUMAN REVIEW)
    Section 5.4 — Resolution message structure
    Section 5.5 — Clarification request structure
    Section 5.6 — Escalation acknowledgment structure
    Section 10  — HTML body formatting requirements

Responsibilities
----------------
Every builder returns a body string that is safe to send to
POST /api/v2/tickets/{id}/notes  or
POST /api/v2/tickets/{id}/reply

Design rules
------------
- All builders escape user-supplied values (subject, description, name) with
  html.escape() to prevent XSS if the template is later rendered in an admin UI.
- URLs written into anchors are restricted to http(s); anything else becomes
  empty text (mirrors app/freshdesk_webhook.py _safe_url()).
- Bodies always wrap the visible content in <p> tags — Freshdesk renders plain
  text without formatting (SOT notes_and_replies.md Section 10).
- No emojis in output unless the caller opts in — SOT permits emojis but the
  agent style is conservative and consistent.
- These are PURE functions: no I/O, no config, no state, no logging.

Dependency direction
--------------------
    templates.py → stdlib only
    templates.py → NO imports from case_engine/, api/, freshdesk client, etc.
"""
from __future__ import annotations

import html
from typing import Any

SIGNATURE = "KwikID Support Team"
DRAFT_HEADER = "AI DRAFT — PENDING HUMAN REVIEW"

_CONFIDENCE_LABELS: dict[str, str] = {
    "high":   "HIGH",
    "medium": "MEDIUM",
    "low":    "LOW",
}


# ── Escaping / small utilities ─────────────────────────────────────────────────

def _esc(value: Any) -> str:
    """HTML-escape a value; None → empty string."""
    return html.escape(str(value or ""), quote=True)


def _safe_url(value: Any) -> str:
    """
    Return an HTML-escaped URL safe to place inside an href, or "" if not
    http/https.
    """
    url = str(value or "").strip()
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return _esc(url)


def _paragraph(text: str) -> str:
    """
    Escape and wrap the text in <p>. Preserves paragraph breaks (double newline)
    and line breaks (single newline).
    """
    escaped = _esc(text)
    escaped = escaped.replace("\n\n", "</p><p>").replace("\n", "<br>")
    return f"<p>{escaped}</p>"


def _confidence_label(confidence: str | None) -> str:
    if confidence is None:
        return "UNKNOWN"
    return _CONFIDENCE_LABELS.get(str(confidence).strip().lower(), "UNKNOWN")


# ── Public reply builders (notes_and_replies.md Section 5) ────────────────────

def build_resolution_reply(
    customer_name: str,
    resolution_summary: str,
    *,
    acknowledgment: str | None = None,
    signature: str = SIGNATURE,
) -> str:
    """
    notes_and_replies.md 5.4 — public reply body for a resolved ticket.

    Structure:
        <p>Hi {name},</p>
        <p>{acknowledgment}</p>            (optional)
        <p>{resolution_summary}</p>
        <p>If you experience any further issues, please reply to this email.</p>
        <p>Regards,<br>{signature}</p>
    """
    greeting = f"<p>Hi {_esc(customer_name or 'there')},</p>"
    ack_p    = _paragraph(acknowledgment) if acknowledgment else ""
    body_p   = _paragraph(resolution_summary or "")
    footer   = (
        "<p>If you experience any further issues, please don't hesitate to "
        "reply to this email.</p>"
        f"<p>Regards,<br>{_esc(signature)}</p>"
    )
    return greeting + ack_p + body_p + footer


def build_clarification_reply(
    customer_name: str,
    questions: list[str],
    *,
    preamble: str | None = None,
    signature: str = SIGNATURE,
) -> str:
    """
    notes_and_replies.md 5.5 — public reply body asking for missing information.

    Structure:
        <p>Hi {name},</p>
        <p>{preamble}</p>                  (default: thank-you + investigating)
        <ul><li>{q1}</li>…<li>{qN}</li></ul>
        <p>Regards,<br>{signature}</p>
    """
    greeting = f"<p>Hi {_esc(customer_name or 'there')},</p>"
    intro    = _paragraph(
        preamble
        or "Thank you for reaching out. To investigate this issue, "
           "we need a bit more information:"
    )
    if not questions:
        # Should not happen in production; keep the reply safe.
        list_html = ""
    else:
        items = "".join(f"<li>{_esc(q)}</li>" for q in questions if q and q.strip())
        list_html = f"<ul>{items}</ul>"
    footer = f"<p>Regards,<br>{_esc(signature)}</p>"
    return greeting + intro + list_html + footer


def build_escalation_reply(
    customer_name: str,
    *,
    engineering_details: str | None = None,
    signature: str = SIGNATURE,
) -> str:
    """
    notes_and_replies.md 5.6 — public reply body acknowledging escalation.
    """
    greeting = f"<p>Hi {_esc(customer_name or 'there')},</p>"
    intro = _paragraph(
        "Thank you for reporting this issue. We have reviewed the details and "
        "have escalated this to our engineering team for further investigation."
    )
    detail_p = _paragraph(engineering_details) if engineering_details else ""
    reassurance = _paragraph(
        "We will keep you updated on the progress and will notify you as soon "
        "as a resolution is available."
    )
    footer = f"<p>Regards,<br>{_esc(signature)}</p>"
    return greeting + intro + detail_p + reassurance + footer


# ── Private note builders (notes_and_replies.md Sections 3 & 4) ───────────────

def build_diagnostic_note(
    *,
    ticket_id: str,
    client: str,
    query_type: str = "",
    issue_area: str = "",
    portal: str = "",
    issue_recurrence: str = "",
    sop_status: str = "",
    sop_link: str = "",
    confidence: str = "",
    resolution: str = "",
    rca: str = "",
    handling_time_minutes: int | None = None,
) -> str:
    """
    notes_and_replies.md 3.4 — private diagnostic note.

    Rendered as an <hr>-delimited card with structured sections.
    Any empty section is omitted to keep the note readable.
    """
    header = (
        f"<p><strong>KwikID AI Support Agent</strong></p>"
        f"<p>Ticket: <code>{_esc(ticket_id)}</code> | "
        f"Client: <code>{_esc(client)}</code></p>"
    )

    classification: list[str] = []
    if query_type:
        classification.append(f"Query Type: {_esc(query_type)}")
    if issue_area:
        classification.append(f"Issue Area: {_esc(issue_area)}")
    if portal:
        classification.append(f"Portal: {_esc(portal)}")
    if issue_recurrence:
        classification.append(f"Issue Recurrence: {_esc(issue_recurrence)}")

    class_block = ""
    if classification:
        class_block = (
            "<p><strong>Classification:</strong><br>"
            + "<br>".join(classification)
            + "</p>"
        )

    sop_lines: list[str] = []
    if sop_status:
        sop_lines.append(f"Status: {_esc(sop_status)}")
    safe_link = _safe_url(sop_link)
    if safe_link:
        sop_lines.append(f'Link: <a href="{safe_link}">{safe_link}</a>')
    conf_label = _confidence_label(confidence)
    if confidence:
        sop_lines.append(f"Confidence: {_esc(conf_label)}")
    sop_block = ""
    if sop_lines:
        sop_block = (
            "<p><strong>SOP Match:</strong><br>"
            + "<br>".join(sop_lines)
            + "</p>"
        )

    resolution_block = ""
    if resolution:
        resolution_block = f"<p><strong>Resolution:</strong><br>{_esc(resolution)}</p>"

    rca_block = ""
    if rca:
        rca_block = f"<p><strong>RCA:</strong><br>{_esc(rca)}</p>"

    time_block = ""
    if isinstance(handling_time_minutes, int) and handling_time_minutes >= 0:
        time_block = f"<p><strong>Handling Time:</strong> {handling_time_minutes} minutes</p>"

    return header + class_block + sop_block + resolution_block + rca_block + time_block


def build_draft_reply_note(
    draft_reply_html: str,
    *,
    reason_not_auto_sent: str,
    confidence: str = "",
) -> str:
    """
    notes_and_replies.md 4 — draft-approval pattern.

    The AI could not send autonomously; the reply is stored as a private note
    tagged AI DRAFT — PENDING HUMAN REVIEW, with the reason and confidence
    surfaced for the reviewing agent.
    """
    conf_label = _confidence_label(confidence)
    header = f"<p><strong>&#9888; {_esc(DRAFT_HEADER)}</strong></p>"
    reason_p = _paragraph(f"Reason not auto-sent: {reason_not_auto_sent}")
    conf_p   = ""
    if confidence:
        conf_p = _paragraph(f"Confidence: {conf_label}")
    hr = "<hr>"
    draft_block = draft_reply_html or "<p>(empty draft)</p>"
    footer = _paragraph(
        "Review this draft and either send as-is or edit before sending to the customer."
    )
    return header + reason_p + conf_p + hr + draft_block + hr + footer


def build_unknown_tenant_note(domain: str) -> str:
    """
    workflow_discovery.md Section 9 — the AI stops the pipeline when the
    tenant cannot be resolved. This note surfaces the ticket to a human agent.
    """
    return (
        "<p><strong>AI Support System — Action Required</strong></p>"
        "<p>This ticket could not be assigned to an automation workflow because "
        f"the requester domain <code>{_esc(domain or 'unknown')}</code> is not "
        "registered in the tenant registry.</p>"
        "<p><strong>Human review required.</strong> No automated actions have "
        "been taken on this ticket.</p>"
    )


def build_escalation_note(
    *,
    ticket_id: str,
    root_cause: str,
    evidence_summary: str = "",
    reproduction_steps: str = "",
    priority: str = "",
    asana_url: str = "",
) -> str:
    """
    observations.md Section 2.3 — private note that mirrors the Asana escalation.

    Serves as the internal audit record for engineering handoff.
    """
    header = (
        "<p><strong>KwikID AI Support Agent — Engineering Escalation</strong></p>"
        f"<p>Ticket: <code>{_esc(ticket_id)}</code> | "
        f"Priority: {_esc(priority or 'unspecified')}</p>"
    )
    rca_block = f"<p><strong>Root Cause Hypothesis:</strong><br>{_esc(root_cause)}</p>"
    ev_block = ""
    if evidence_summary:
        ev_block = f"<p><strong>Evidence:</strong><br>{_esc(evidence_summary)}</p>"
    repro_block = ""
    if reproduction_steps:
        repro_block = (
            "<p><strong>Reproduction Steps:</strong><br>"
            f"{_esc(reproduction_steps)}</p>"
        )
    asana_safe = _safe_url(asana_url)
    asana_block = ""
    if asana_safe:
        asana_block = (
            f'<p><strong>Asana Task:</strong> <a href="{asana_safe}">{asana_safe}</a></p>'
        )
    return header + rca_block + ev_block + repro_block + asana_block
