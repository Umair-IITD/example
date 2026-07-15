"""
freshdesk/response_service.py

Sprint 2.28.1: FreshdeskResponseService — ONLY write path back to Freshdesk.

Architecture constraint (Golden Path, Section 0.2):
  No component upstream of Action Gateway may call Freshdesk write endpoints.
  All writes flow: Action Gateway → Execution Layer → Freshdesk.

  FreshdeskResponseService is the SOLE gateway for writing back to Freshdesk.
  Workflow components, handlers, and orchestrators MUST NOT call FreshdeskClient
  write methods directly — they must go through this service.

Operations:
  add_internal_note(ticket_id, body)   → dict  (private=True note)
  send_customer_reply(ticket_id, body) → dict  (public reply)

Design:
  - Never raises to caller — all exceptions are caught and logged.
  - Returns result dict on success, empty dict on failure.
  - Increments metrics on every call.
  - Writes audit entries via AuditLogger if provided.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from freshdesk.client import FreshdeskClient
from freshdesk.metrics import (
    COUNTER_FD_PRIVATE_NOTES_TOTAL,
    COUNTER_FD_PUBLIC_REPLIES_TOTAL,
    COUNTER_FD_API_ERRORS_TOTAL,
    LATENCY_FD_API_CALL_MS,
)
from freshdesk.traces import (
    TRACE_FD_07_NOTE_PREPARED,
    TRACE_FD_08_NOTE_SENT,
    TRACE_FD_09_REPLY_PREPARED,
    TRACE_FD_10_REPLY_SENT,
    emit_trace,
)

LOGGER = logging.getLogger(__name__)


class FreshdeskResponseService:
    """
    Single write gateway for all Freshdesk responses.

    Args:
        freshdesk_client: FreshdeskClient instance (async HTTP client).
        metrics_collector: Optional MetricsCollector for counters/latency.
        audit_logger:      Optional AuditLogger for audit events.
    """

    def __init__(
        self,
        freshdesk_client: FreshdeskClient,
        *,
        metrics_collector: Any = None,
        audit_logger: Any = None,
    ) -> None:
        self._client = freshdesk_client
        self._metrics = metrics_collector
        self._audit = audit_logger

    async def add_internal_note(
        self,
        ticket_id: int | str,
        body: str,
        *,
        case_id: str = "",
        client_id: str = "",
    ) -> dict[str, Any]:
        """
        Post a private internal note to a Freshdesk ticket.

        This is the ONLY approved path for posting private notes.
        Never raises — returns empty dict on failure.
        """
        # Sprint 2.49 — TRACE_FD_07_NOTE_PREPARED: outbound note dispatched.
        emit_trace(
            TRACE_FD_07_NOTE_PREPARED,
            ticket_id=str(ticket_id),
            client=client_id,
            event_type="private_note",
            status="PREPARED",
        )
        start = time.monotonic()
        try:
            result = await self._client.add_private_note(ticket_id, body)
            latency_ms = int((time.monotonic() - start) * 1000)
            self._record_counter(COUNTER_FD_PRIVATE_NOTES_TOTAL)
            self._record_latency(LATENCY_FD_API_CALL_MS, latency_ms)
            LOGGER.info(
                "freshdesk.response_service.internal_note: ticket_id=%s note_id=%s latency_ms=%d",
                ticket_id, result.get("id"), latency_ms,
            )
            self._audit_private_note(ticket_id, result.get("id"), case_id, client_id)
            # Sprint 2.49 — TRACE_FD_08_NOTE_SENT: private note POST succeeded.
            emit_trace(
                TRACE_FD_08_NOTE_SENT,
                ticket_id=str(ticket_id),
                client=client_id,
                event_type="private_note",
                status="SUCCESS",
            )
            return result
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            self._record_counter(COUNTER_FD_API_ERRORS_TOTAL)
            LOGGER.error(
                "freshdesk.response_service.internal_note: FAILED ticket_id=%s error=%s",
                ticket_id, exc,
            )
            # Sprint 2.49 — TRACE_FD_08_NOTE_SENT: with FAILURE status.
            emit_trace(
                TRACE_FD_08_NOTE_SENT,
                ticket_id=str(ticket_id),
                client=client_id,
                event_type="private_note",
                status="FAILURE",
            )
            return {}

    async def send_customer_reply(
        self,
        ticket_id: int | str,
        body: str,
        *,
        case_id: str = "",
        client_id: str = "",
    ) -> dict[str, Any]:
        """
        Send a public reply to the customer on a Freshdesk ticket.

        This is the ONLY approved path for sending public replies.
        Never raises — returns empty dict on failure.
        """
        # Sprint 2.49 — TRACE_FD_09_REPLY_PREPARED: outbound reply dispatched.
        emit_trace(
            TRACE_FD_09_REPLY_PREPARED,
            ticket_id=str(ticket_id),
            client=client_id,
            event_type="public_reply",
            status="PREPARED",
        )
        start = time.monotonic()
        try:
            result = await self._client.add_public_reply(ticket_id, body)
            latency_ms = int((time.monotonic() - start) * 1000)
            self._record_counter(COUNTER_FD_PUBLIC_REPLIES_TOTAL)
            self._record_latency(LATENCY_FD_API_CALL_MS, latency_ms)
            LOGGER.info(
                "freshdesk.response_service.customer_reply: ticket_id=%s note_id=%s latency_ms=%d",
                ticket_id, result.get("id"), latency_ms,
            )
            self._audit_public_reply(ticket_id, result.get("id"), case_id, client_id)
            # Sprint 2.49 — TRACE_FD_10_REPLY_SENT: public reply POST succeeded.
            emit_trace(
                TRACE_FD_10_REPLY_SENT,
                ticket_id=str(ticket_id),
                client=client_id,
                event_type="public_reply",
                status="SUCCESS",
            )
            return result
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            self._record_counter(COUNTER_FD_API_ERRORS_TOTAL)
            LOGGER.error(
                "freshdesk.response_service.customer_reply: FAILED ticket_id=%s error=%s",
                ticket_id, exc,
            )
            # Sprint 2.49 — TRACE_FD_10_REPLY_SENT: with FAILURE status.
            emit_trace(
                TRACE_FD_10_REPLY_SENT,
                ticket_id=str(ticket_id),
                client=client_id,
                event_type="public_reply",
                status="FAILURE",
            )
            return {}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _record_counter(self, name: str) -> None:
        if self._metrics is not None:
            try:
                self._metrics.increment(name)
            except Exception:
                pass

    def _record_latency(self, name: str, latency_ms: int) -> None:
        if self._metrics is not None:
            try:
                self._metrics.record_latency(name, latency_ms)
            except Exception:
                pass

    def _audit_private_note(
        self,
        ticket_id: Any,
        note_id: Any,
        case_id: str,
        client_id: str,
    ) -> None:
        if self._audit is None:
            return
        try:
            from case_engine.models import AuditEntry, AuditEventType
            entry = AuditEntry(
                case_id=case_id,
                ticket_id=str(ticket_id),
                client=client_id,
                action_type=AuditEventType.PRIVATE_NOTE_ADDED,
                action_detail={"note_id": str(note_id or ""), "type": "private"},
                outcome="SUCCESS",
            )
            self._audit._write(entry)
        except Exception as exc:
            LOGGER.debug("response_service.audit_private_note: %s", exc)

    def _audit_public_reply(
        self,
        ticket_id: Any,
        note_id: Any,
        case_id: str,
        client_id: str,
    ) -> None:
        if self._audit is None:
            return
        try:
            from case_engine.models import AuditEntry, AuditEventType
            entry = AuditEntry(
                case_id=case_id,
                ticket_id=str(ticket_id),
                client=client_id,
                action_type=AuditEventType.PUBLIC_REPLY_SENT,
                action_detail={"note_id": str(note_id or ""), "type": "public"},
                outcome="SUCCESS",
            )
            self._audit._write(entry)
        except Exception as exc:
            LOGGER.debug("response_service.audit_public_reply: %s", exc)
