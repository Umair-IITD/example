"""
freshdesk/traces.py

Sprint 2.49 — Deterministic runtime traces for Postman-verifiable end-to-end
Freshdesk flow certification.

Purpose
-------
Ten canonical trace tags (`TRACE_FD_01_*` … `TRACE_FD_10_*`) are emitted at
specific execution boundaries so an operator watching a log stream can verify
each step of the Freshdesk boundary without attaching a debugger.

Every trace line has the exact shape:

    TRACE_FD_NN_<STAGE> ticket_id=<id> tenant=<t> client=<c> \\
        event_type=<e> status=<s>

Design contract
---------------
- **Deterministic.** The tag and the field order are fixed. Grep-friendly.
- **PII-free by construction.** Only IDs, enum values, and status strings pass
  through. Email addresses, phone numbers, ticket bodies, session IDs, and
  API keys must never reach the trace layer. Any value containing a PII
  marker (`@`, `password`, `authorization`, `cf_session_ids`) is redacted.
- **WARNING level.** The traces are Ops/QA verification artefacts, not INFO
  chatter; they must survive default log filters.
- **Zero coupling.** `traces.py` imports stdlib only. Any module in the
  Freshdesk boundary can safely import it.
- **Never raises.** Exceptions inside `emit_trace` are swallowed and logged as
  a WARNING with tag `freshdesk.traces.emit_error`.

Trace inventory (Sprint 2.49)
-----------------------------
    TRACE_FD_01_WEBHOOK_RECEIVED   — inbound webhook accepted (post-parse)
    TRACE_FD_02_PAYLOAD_NORMALIZED — FreshdeskWebhookPayload.from_dict() ok
    TRACE_FD_03_TENANT_RESOLVED    — ClientResolver.resolve() ok
    TRACE_FD_04_CASE_CREATED       — case_id assigned by orchestrator
    TRACE_FD_05_PIPELINE_STARTED   — TicketOrchestrator.process_ticket() entry
    TRACE_FD_06_PIPELINE_COMPLETED — TicketOrchestrator.process_ticket() exit
    TRACE_FD_07_NOTE_PREPARED      — FreshdeskResponseService.add_internal_note enter
    TRACE_FD_08_NOTE_SENT          — FreshdeskResponseService.add_internal_note success
    TRACE_FD_09_REPLY_PREPARED     — FreshdeskResponseService.send_customer_reply enter
    TRACE_FD_10_REPLY_SENT         — FreshdeskResponseService.send_customer_reply success
"""
from __future__ import annotations

import logging
from typing import Final

LOGGER = logging.getLogger("freshdesk.traces")


# ── Canonical trace tags (never rename these) ─────────────────────────────────

TRACE_FD_01_WEBHOOK_RECEIVED:   Final[str] = "TRACE_FD_01_WEBHOOK_RECEIVED"
TRACE_FD_02_PAYLOAD_NORMALIZED: Final[str] = "TRACE_FD_02_PAYLOAD_NORMALIZED"
TRACE_FD_03_TENANT_RESOLVED:    Final[str] = "TRACE_FD_03_TENANT_RESOLVED"
TRACE_FD_04_CASE_CREATED:       Final[str] = "TRACE_FD_04_CASE_CREATED"
TRACE_FD_05_PIPELINE_STARTED:   Final[str] = "TRACE_FD_05_PIPELINE_STARTED"
TRACE_FD_06_PIPELINE_COMPLETED: Final[str] = "TRACE_FD_06_PIPELINE_COMPLETED"
TRACE_FD_07_NOTE_PREPARED:      Final[str] = "TRACE_FD_07_NOTE_PREPARED"
TRACE_FD_08_NOTE_SENT:          Final[str] = "TRACE_FD_08_NOTE_SENT"
TRACE_FD_09_REPLY_PREPARED:     Final[str] = "TRACE_FD_09_REPLY_PREPARED"
TRACE_FD_10_REPLY_SENT:         Final[str] = "TRACE_FD_10_REPLY_SENT"

ALL_TRACE_TAGS: Final[frozenset[str]] = frozenset({
    TRACE_FD_01_WEBHOOK_RECEIVED,
    TRACE_FD_02_PAYLOAD_NORMALIZED,
    TRACE_FD_03_TENANT_RESOLVED,
    TRACE_FD_04_CASE_CREATED,
    TRACE_FD_05_PIPELINE_STARTED,
    TRACE_FD_06_PIPELINE_COMPLETED,
    TRACE_FD_07_NOTE_PREPARED,
    TRACE_FD_08_NOTE_SENT,
    TRACE_FD_09_REPLY_PREPARED,
    TRACE_FD_10_REPLY_SENT,
})

# Substrings that indicate a value carries PII and must be redacted.
_PII_MARKERS: Final[tuple[str, ...]] = (
    "@",                # email
    "password",
    "authorization",
    "bearer ",
    "cf_session_ids",   # session UUIDs — SOT api_reference §7.4
    "api_key",
)

_REDACTED: Final[str] = "REDACTED"
_MISSING:  Final[str] = "-"


# ── Public API ────────────────────────────────────────────────────────────────

def emit_trace(
    tag: str,
    *,
    ticket_id:  str = "",
    tenant:     str = "",
    client:     str = "",
    event_type: str = "",
    status:     str = "",
) -> None:
    """
    Emit one of the 10 canonical Sprint 2.49 traces at WARNING level.

    Any value found to contain a PII marker is replaced with the literal
    string "REDACTED" before the trace is emitted. Empty values render as
    "-" so the field layout is stable for grep-based verification.

    Never raises. Never returns anything.
    """
    try:
        if tag not in ALL_TRACE_TAGS:
            LOGGER.warning(
                "freshdesk.traces.unknown_tag tag=%s ticket_id=%s",
                tag, ticket_id or _MISSING,
            )
            return

        fields = {
            "ticket_id":  _sanitize(ticket_id),
            "tenant":     _sanitize(tenant),
            "client":     _sanitize(client),
            "event_type": _sanitize(event_type),
            "status":     _sanitize(status),
        }

        LOGGER.warning(
            "%s ticket_id=%s tenant=%s client=%s event_type=%s status=%s",
            tag,
            fields["ticket_id"],
            fields["tenant"],
            fields["client"],
            fields["event_type"],
            fields["status"],
        )
    except Exception as exc:      # never propagate — telemetry must not break the pipeline
        LOGGER.warning("freshdesk.traces.emit_error tag=%s error=%s", tag, exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize(value: object) -> str:
    """
    Normalize a field value for tracing.

    Returns:
      - "-" if the value is empty / None
      - "REDACTED" if any PII marker is present
      - the string form of the value otherwise
    """
    if value is None:
        return _MISSING
    s = str(value).strip()
    if not s:
        return _MISSING
    low = s.lower()
    for marker in _PII_MARKERS:
        if marker in low:
            return _REDACTED
    # Extra guardrail: reject obviously long free-text (>128 chars) which is
    # almost never an ID / status but is often a subject line or body slice.
    if len(s) > 128:
        return _REDACTED
    return s
