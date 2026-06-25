"""
freshdesk/handlers.py

Sprint 2.28.1: Freshdesk webhook event handlers.

Handlers:
  FreshdeskTicketCreatedHandler — processes ticket-created webhook events
  FreshdeskTicketUpdatedHandler — processes ticket-updated webhook events

Architecture (Golden Path):
  Webhook Receiver → Client Resolution Layer → Ticket Orchestrator → ...

Handler responsibilities:
  1. Validate payload shape
  2. Check idempotency (skip if duplicate)
  3. Resolve client via ClientResolver (Sprint 2.27.9)
  4. Route to TicketOrchestrator (ticket-created) or update state (ticket-updated)
  5. Write audit events
  6. Update conversation state
  7. Return structured result

These handlers NEVER block the HTTP request path — they are called from
FastAPI BackgroundTasks after the 200 OK has been returned.

Scope: Part 6 (ticket-created), Part 7 (ticket-updated), Part 8 (clarification state)
EXCLUDED: LLM logic, tool execution, Action Gateway calls, Unity Bank API calls.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from freshdesk.conversation_state import ConversationLifecycle, ConversationState, ConversationStateStore
from freshdesk.idempotency import WebhookIdempotencyStore
from freshdesk.metrics import (
    COUNTER_FD_CUSTOMER_REPLIES_TOTAL,
    COUNTER_FD_DUPLICATE_EVENTS_TOTAL,
    COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL,
    LATENCY_FD_PROCESSING_MS,
)
from freshdesk.freshdesk_models import (
    FreshdeskConversation,
    FreshdeskLatestComment,
    FreshdeskTicketPayload,
    FreshdeskWebhookPayload,
    FreshdeskUpdateEvent,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class HandlerResult:
    success: bool
    ticket_id: str = ""
    case_id: str | None = None
    action: str = ""
    error_code: str | None = None
    detail: dict[str, Any] | None = None
    skipped: bool = False
    skip_reason: str = ""


class FreshdeskTicketCreatedHandler:
    """
    Processes ticket-created webhook events.

    Flow:
      1. Parse FreshdeskWebhookPayload from raw dict
      2. Idempotency check — skip if already received
      3. Validate required fields (ticket_id, requester_email)
      4. Client resolution via ClientResolver
      5. Mark idempotency as RECEIVED
      6. Delegate to TicketOrchestrator.process_ticket()
      7. Update ConversationState
      8. Write audit event (TICKET_INGESTED)
      9. Mark idempotency COMPLETED / FAILED
    """

    def __init__(
        self,
        *,
        idempotency_store: WebhookIdempotencyStore,
        conversation_store: ConversationStateStore,
        ticket_orchestrator: Any = None,
        client_resolver: Any = None,
        audit_logger: Any = None,
        metrics_collector: Any = None,
    ) -> None:
        self._idempotency = idempotency_store
        self._conversations = conversation_store
        self._orchestrator = ticket_orchestrator
        self._client_resolver = client_resolver
        self._audit = audit_logger
        self._metrics = metrics_collector

    def handle(self, raw_payload: dict[str, Any]) -> HandlerResult:
        start = time.monotonic()
        self._inc(COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL)

        # 1. Parse payload
        try:
            payload = FreshdeskWebhookPayload.from_dict(raw_payload)
            ticket = payload.ticket
        except Exception as exc:
            LOGGER.warning("ticket_created.handle: parse error=%s", exc)
            return HandlerResult(success=False, error_code="PARSE_ERROR")

        ticket_id = ticket.ticket_id
        event_type = "ticket_created"
        event_ts = ticket.created_at or ""
        idem_key = self._idempotency.make_key(ticket_id, event_type, event_ts)

        # 2. Idempotency check
        if self._idempotency.check(idem_key):
            self._inc(COUNTER_FD_DUPLICATE_EVENTS_TOTAL)
            LOGGER.info("ticket_created.handle: DUPLICATE ticket_id=%s", ticket_id)
            self._audit_event("WEBHOOK_DUPLICATE", ticket_id=ticket_id, client_id="")
            return HandlerResult(
                success=True,
                ticket_id=ticket_id,
                skipped=True,
                skip_reason="DUPLICATE",
            )

        # 3. Validate required fields
        if not ticket_id or ticket_id == "0":
            return HandlerResult(success=False, ticket_id=ticket_id, error_code="MISSING_TICKET_ID")

        email = ticket.requester_email
        if not email:
            self._idempotency.mark_received(idem_key, ticket_id, event_type, event_ts)
            self._idempotency.mark_failed(idem_key, "MISSING_EMAIL")
            return HandlerResult(success=False, ticket_id=ticket_id, error_code="MISSING_EMAIL")

        # 4. Client resolution (domain only logged — no PII)
        client_id = ""
        client_name = ticket.custom_fields.cf_clients or ""
        if self._client_resolver is not None:
            try:
                tenant_ctx = self._client_resolver.resolve(email)
                client_id = tenant_ctx.client_id
                client_name = tenant_ctx.client_name
                LOGGER.info(
                    "ticket_created.handle: client_resolved client_id=%s ticket_id=%s",
                    client_id, ticket_id,
                )
            except Exception as exc:
                # UnknownClientError → escalate
                domain = email.split("@")[-1] if "@" in email else "unknown"
                LOGGER.warning(
                    "ticket_created.handle: client_resolution_failed domain=%s ticket_id=%s error=%s",
                    domain, ticket_id, type(exc).__name__,
                )
                self._idempotency.mark_received(idem_key, ticket_id, event_type, event_ts)
                self._audit_event("CLIENT_RESOLUTION_FAILED", ticket_id=ticket_id, client_id="UNKNOWN",
                                  detail={"domain": domain})
                self._idempotency.mark_failed(idem_key, "UNKNOWN_CLIENT")
                return HandlerResult(
                    success=False,
                    ticket_id=ticket_id,
                    error_code="UNKNOWN_CLIENT",
                    detail={"domain": domain},
                )

        # 5. Mark idempotency received
        self._idempotency.mark_received(idem_key, ticket_id, event_type, event_ts)

        # 6. Initialize conversation state
        self._conversations.get_or_create(ticket_id, client_id)

        # 7. Delegate to TicketOrchestrator (if wired)
        case_id: str | None = None
        if self._orchestrator is not None:
            try:
                orch_result = self._orchestrator.process_ticket(
                    ticket_id=ticket_id,
                    subject=ticket.subject,
                    description_text=ticket.description_text or ticket.description,
                    requester_email=email,
                    client=client_id or client_name,
                    metadata={
                        "freshdesk_ticket": ticket.to_dict(),
                        "cf_clients": ticket.custom_fields.cf_clients,
                        "cf_environment": ticket.custom_fields.cf_environment,
                    },
                )
                if hasattr(orch_result, "case_id"):
                    case_id = orch_result.case_id
            except Exception as exc:
                LOGGER.error(
                    "ticket_created.handle: orchestrator error ticket_id=%s error=%s",
                    ticket_id, exc,
                )
                self._idempotency.mark_failed(idem_key, f"ORCHESTRATOR_ERROR:{exc}")
                return HandlerResult(
                    success=False,
                    ticket_id=ticket_id,
                    error_code="ORCHESTRATOR_ERROR",
                )

        # 8. Update conversation state with case_id
        if case_id:
            self._conversations.update(ticket_id, case_id=case_id)
            self._idempotency.mark_completed(idem_key, case_id=case_id)
        else:
            self._idempotency.mark_completed(idem_key)

        # 9. Audit
        self._audit_event("TICKET_INGESTED", ticket_id=ticket_id, client_id=client_id,
                          detail={"case_id": case_id, "cf_clients": client_name})

        latency_ms = int((time.monotonic() - start) * 1000)
        self._record_latency(LATENCY_FD_PROCESSING_MS, latency_ms)
        LOGGER.info(
            "ticket_created.handle: SUCCESS ticket_id=%s case_id=%s latency_ms=%d",
            ticket_id, case_id, latency_ms,
        )
        return HandlerResult(
            success=True,
            ticket_id=ticket_id,
            case_id=case_id,
            action="ticket_ingested",
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _inc(self, name: str) -> None:
        if self._metrics is not None:
            try:
                self._metrics.increment(name)
            except Exception:
                pass

    def _record_latency(self, name: str, value: int) -> None:
        if self._metrics is not None:
            try:
                self._metrics.record_latency(name, value)
            except Exception:
                pass

    def _audit_event(
        self,
        event_type_name: str,
        *,
        ticket_id: str,
        client_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None:
            return
        try:
            from case_engine.models import AuditEntry, AuditEventType
            et = AuditEventType(event_type_name)
            entry = AuditEntry(
                ticket_id=ticket_id,
                client=client_id,
                action_type=et,
                action_detail=detail or {},
                outcome="SUCCESS" if "FAILED" not in event_type_name else "FAILURE",
            )
            self._audit._write(entry)
        except Exception as exc:
            LOGGER.debug("ticket_created.audit_event: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────


class FreshdeskTicketUpdatedHandler:
    """
    Processes ticket-updated webhook events.

    Event type detection (from conversation entries):
      incoming=True,  private=False → CUSTOMER_REPLY (clarification continuation)
      incoming=False, private=False → AGENT_REPLY    (skip or log)
      incoming=False, private=True  → INTERNAL_NOTE  (skip or log)

    Status change detection:
      ticket.status changed → update ConversationState lifecycle

    Clarification continuation:
      CUSTOMER_REPLY while conversation is CLARIFICATION state
      → emit CLARIFICATION_REPLY_RECEIVED event
      → update ConversationState (awaiting_customer=False, clarification_pending=False)
      → queue for further processing (NOT handled here — event emitted only)
    """

    def __init__(
        self,
        *,
        idempotency_store: WebhookIdempotencyStore,
        conversation_store: ConversationStateStore,
        audit_logger: Any = None,
        metrics_collector: Any = None,
    ) -> None:
        self._idempotency = idempotency_store
        self._conversations = conversation_store
        self._audit = audit_logger
        self._metrics = metrics_collector

    def handle(self, raw_payload: dict[str, Any]) -> HandlerResult:
        start = time.monotonic()
        self._inc(COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL)

        # 1. Parse payload
        try:
            event = FreshdeskUpdateEvent.from_dict(raw_payload)
            ticket = event.ticket
        except Exception as exc:
            LOGGER.warning("ticket_updated.handle: parse error=%s", exc)
            return HandlerResult(success=False, error_code="PARSE_ERROR")

        ticket_id = str(event.ticket_id) if event.ticket_id else ""
        if not ticket_id or ticket_id == "0":
            return HandlerResult(success=False, error_code="MISSING_TICKET_ID")

        event_type = "ticket_updated"
        event_ts = event.updated_at or ""
        idem_key = self._idempotency.make_key(ticket_id, event_type, event_ts)

        # 2. Idempotency check
        if self._idempotency.check(idem_key):
            self._inc(COUNTER_FD_DUPLICATE_EVENTS_TOTAL)
            LOGGER.info("ticket_updated.handle: DUPLICATE ticket_id=%s", ticket_id)
            return HandlerResult(
                success=True,
                ticket_id=ticket_id,
                skipped=True,
                skip_reason="DUPLICATE",
            )

        self._idempotency.mark_received(idem_key, ticket_id, event_type, event_ts)

        # 3. Detect event type — latest_comment is the authoritative source for replies
        changes = event.changes
        action = self._detect_update_action(ticket, changes, event.latest_comment)

        # 4. Load conversation state
        conv_state = self._conversations.get(ticket_id)
        client_id = conv_state.client_id if conv_state else ""

        # 5. Route by action
        result_detail: dict[str, Any] = {"action": action, "ticket_id": ticket_id}

        if action == "customer_reply":
            self._inc(COUNTER_FD_CUSTOMER_REPLIES_TOTAL)
            result_detail["event"] = "CUSTOMER_REPLY"

            # Check if we were awaiting clarification
            if conv_state and conv_state.awaiting_customer:
                self._conversations.update(
                    ticket_id,
                    awaiting_customer=False,
                    clarification_pending=False,
                    lifecycle_state=ConversationLifecycle.OPEN,
                )
                self._audit_event(
                    "CLARIFICATION_REPLY_RECEIVED",
                    ticket_id=ticket_id,
                    client_id=client_id,
                    detail={"previous_state": "CLARIFICATION"},
                )
                result_detail["clarification_resolved"] = True
            else:
                self._audit_event(
                    "CUSTOMER_REPLY_RECEIVED",
                    ticket_id=ticket_id,
                    client_id=client_id,
                )

        elif action == "agent_reply":
            self._audit_event(
                "AGENT_NOTE_RECEIVED",
                ticket_id=ticket_id,
                client_id=client_id,
                detail={"private": False},
            )

        elif action == "internal_note":
            self._audit_event(
                "AGENT_NOTE_RECEIVED",
                ticket_id=ticket_id,
                client_id=client_id,
                detail={"private": True},
            )

        elif action == "status_change":
            new_status = changes.get("status", [None, None])[1] if "status" in changes else None
            if new_status is not None:
                lifecycle = self._status_to_lifecycle(new_status)
                self._conversations.update(ticket_id, lifecycle_state=lifecycle)
                self._audit_event(
                    "TICKET_UPDATED",
                    ticket_id=ticket_id,
                    client_id=client_id,
                    detail={"new_status": new_status, "lifecycle": lifecycle.value if lifecycle else None},
                )
                result_detail["new_status"] = new_status

        elif action == "tag_update":
            self._audit_event(
                "TICKET_UPDATED",
                ticket_id=ticket_id,
                client_id=client_id,
                detail={"changes": "tags"},
            )

        else:
            # Unrecognized / no-op update
            self._audit_event(
                "TICKET_SKIPPED",
                ticket_id=ticket_id,
                client_id=client_id,
                detail={"reason": "no_actionable_change"},
            )
            self._idempotency.mark_completed(idem_key)
            return HandlerResult(
                success=True,
                ticket_id=ticket_id,
                action=action,
                skipped=True,
                skip_reason="NO_ACTIONABLE_CHANGE",
            )

        self._idempotency.mark_completed(idem_key)
        latency_ms = int((time.monotonic() - start) * 1000)
        self._record_latency(LATENCY_FD_PROCESSING_MS, latency_ms)

        LOGGER.info(
            "ticket_updated.handle: SUCCESS ticket_id=%s action=%s latency_ms=%d",
            ticket_id, action, latency_ms,
        )
        return HandlerResult(
            success=True,
            ticket_id=ticket_id,
            action=action,
            detail=result_detail,
        )

    # ── Detection helpers ──────────────────────────────────────────────────────

    def _detect_update_action(
        self,
        ticket: FreshdeskTicketPayload,
        changes: dict[str, Any],
        latest_comment: "FreshdeskLatestComment | None" = None,
    ) -> str:
        """
        Detect what kind of update event this is.

        Priority order (source: freshdesk_integration.md Section 10.1):
          1. latest_comment present → authoritative source for reply/note events.
             incoming=True,  private=False → customer_reply
             incoming=False, private=True  → internal_note
             incoming=False, private=False → agent_reply
          2. changes contains 'status' → status_change
          3. changes contains 'tags'   → tag_update
          4. Fallback: 'other'

        The Sprint 2.28.1 implementation incorrectly checked changes.conversations
        which is never present in Freshdesk update payloads. latest_comment at the
        top level of freshdesk_webhook is the correct field (Section 9.2 of audit).
        """
        # Priority 1: latest_comment is the authoritative source for reply events.
        # Freshdesk places this at the top level of freshdesk_webhook, NOT inside changes.
        if latest_comment is not None:
            if latest_comment.is_customer_reply:
                return "customer_reply"
            if latest_comment.is_internal_note:
                return "internal_note"
            return "agent_reply"

        # Priority 2 & 3: field change events (no reply/note involved)
        change_keys = set(changes.keys())
        if "status" in change_keys:
            return "status_change"
        if "tags" in change_keys:
            return "tag_update"

        return "other"

    def _status_to_lifecycle(self, status: Any) -> ConversationLifecycle:
        try:
            status_int = int(status)
        except (TypeError, ValueError):
            return ConversationLifecycle.OPEN
        mapping = {
            2: ConversationLifecycle.OPEN,
            3: ConversationLifecycle.PENDING,
            4: ConversationLifecycle.RESOLVED,
            5: ConversationLifecycle.CLOSED,
            10: ConversationLifecycle.OPEN,  # In Process → OPEN
        }
        return mapping.get(status_int, ConversationLifecycle.OPEN)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _inc(self, name: str) -> None:
        if self._metrics is not None:
            try:
                self._metrics.increment(name)
            except Exception:
                pass

    def _record_latency(self, name: str, value: int) -> None:
        if self._metrics is not None:
            try:
                self._metrics.record_latency(name, value)
            except Exception:
                pass

    def _audit_event(
        self,
        event_type_name: str,
        *,
        ticket_id: str,
        client_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None:
            return
        try:
            from case_engine.models import AuditEntry, AuditEventType
            et = AuditEventType(event_type_name)
            entry = AuditEntry(
                ticket_id=ticket_id,
                client=client_id,
                action_type=et,
                action_detail=detail or {},
                outcome="SUCCESS",
            )
            self._audit._write(entry)
        except Exception as exc:
            LOGGER.debug("ticket_updated.audit_event: %s", exc)
