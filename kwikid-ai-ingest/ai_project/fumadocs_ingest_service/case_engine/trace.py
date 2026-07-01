"""
case_engine/trace.py — Sprint 2.30.1

End-to-end trace_id for Golden Path execution.

Trace ID format: FD-{ticket_id}-{YYYYMMDD}
Example:         FD-197416-20260625

Usage:
    from case_engine.trace import make_trace_id, trace_log

    trace_id = make_trace_id("197416")
    trace_log("TRACE_START", trace_id, ticket_id="197416", event_type="ticket_created")

Log format (grep-friendly):
    TRACE_START trace_id=FD-197416-20260625 ticket_id=197416 event_type=ticket_created

A single grep of the trace_id reveals the complete execution path:
    grep "FD-197416-20260625" app.log

Trace event sequence (Golden Path):
    TRACE_START          — Webhook Receiver accepted the event
    TRACE_CLIENT_RESOLVED — Client Resolution Layer resolved the tenant
    TRACE_ORCHESTRATOR   — TicketOrchestrator opened the case
    TRACE_RUNTIME        — SupportAgentRuntime.run_case() invoked
    TRACE_WORKFLOW       — WorkflowEngine selected and started a playbook
    TRACE_KNOWLEDGE      — KnowledgeOrchestrator search completed
    TRACE_REASONING      — InvestigationReasoningEngine produced a decision
    TRACE_ACTION_GATEWAY — Action Gateway received a proposal
    TRACE_EXECUTION      — Execution Layer executed an approved action
    TRACE_WRITEBACK      — FreshdeskResponseService posted a reply/note
    TRACE_COMPLETE       — Golden Path completed for this ticket
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

_TRACE_LOGGER = logging.getLogger("trace.golden_path")


def make_trace_id(ticket_id: str) -> str:
    """
    Build a deterministic trace_id for this ticket + processing day.

    The same call on the same calendar day (UTC) always returns the same
    string — suitable for grepping across distributed log lines.
    """
    date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    safe_id = str(ticket_id).strip().replace(" ", "_") or "UNKNOWN"
    return f"FD-{safe_id}-{date_str}"


def trace_log(event: str, trace_id: str, **kwargs: Any) -> None:
    """
    Emit one structured trace log line to the 'trace.golden_path' logger.

    Output format (space-separated key=value pairs):
        {event} trace_id={trace_id} key1=val1 key2=val2

    Example:
        TRACE_START trace_id=FD-197416-20260625 ticket_id=197416 event_type=ticket_created
    """
    parts: list[str] = [event, f"trace_id={trace_id}"]
    for k, v in kwargs.items():
        # Coerce values to strings, truncating long values to keep logs grep-friendly
        sv = str(v)
        if len(sv) > 120:
            sv = sv[:117] + "..."
        parts.append(f"{k}={sv}")
    _TRACE_LOGGER.info(" ".join(parts))
