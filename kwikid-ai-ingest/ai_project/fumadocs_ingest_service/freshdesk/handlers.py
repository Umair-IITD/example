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
import traceback as _traceback
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
from freshdesk.traces import (
    TRACE_FD_02_PAYLOAD_NORMALIZED,
    TRACE_FD_03_TENANT_RESOLVED,
    TRACE_FD_04_CASE_CREATED,
    TRACE_FD_05_PIPELINE_STARTED,
    TRACE_FD_06_PIPELINE_COMPLETED,
    emit_trace,
)
from case_engine.trace import make_trace_id, trace_log

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
    response_draft: str | None = None


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
        # TRACE_ENTER_HANDLER — always emits at WARNING; proves handler was called
        LOGGER.warning(
            "TRACE_ENTER_HANDLER handler=ticket_created raw_payload_keys=%s",
            sorted(raw_payload.keys()),
        )
        start = time.monotonic()
        self._inc(COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL)

        # 1. Parse payload
        try:
            payload = FreshdeskWebhookPayload.from_dict(raw_payload)
            ticket = payload.ticket
        except Exception as exc:
            LOGGER.warning(
                "TRACE_PARSE_EXCEPTION handler=ticket_created error=%s traceback=%s",
                exc, _traceback.format_exc(),
            )
            return HandlerResult(success=False, error_code="PARSE_ERROR")

        ticket_id = ticket.ticket_id
        event_type = "ticket_created"
        event_ts = ticket.created_at or ""
        idem_key = self._idempotency.make_key(ticket_id, event_type, event_ts)

        # Sprint 2.49 — TRACE_FD_02_PAYLOAD_NORMALIZED: parsed model available.
        emit_trace(
            TRACE_FD_02_PAYLOAD_NORMALIZED,
            ticket_id=ticket_id,
            client=ticket.custom_fields.cf_clients or "",
            event_type=event_type,
            status="NORMALIZED",
        )

        # TRACE_PAYLOAD_02_MODEL — proves parsing extracted the correct fields
        # Email masked to domain only (PII protection).
        _email_domain = ticket.requester_email.split("@")[-1] if "@" in ticket.requester_email else "(empty)"
        LOGGER.warning(
            "TRACE_PAYLOAD_02_MODEL ticket_id=%s subject=%r email_domain=%s cf_clients=%r",
            ticket_id,
            (ticket.subject or "")[:60],
            _email_domain,
            ticket.custom_fields.cf_clients,
        )

        # Sprint 2.30.1 — TRACE_START: first trace point in Golden Path
        # TRACE_STEP_A: make_trace_id()
        LOGGER.warning("TRACE_STEP_A_ENTER ticket_id=%s calling_make_trace_id", ticket_id)
        try:
            trace_id = make_trace_id(ticket_id)
            LOGGER.warning("TRACE_STEP_A_EXIT ticket_id=%s trace_id=%s", ticket_id, trace_id)
        except Exception:
            LOGGER.warning(
                "TRACE_STEP_A_EXCEPTION ticket_id=%s traceback=%s",
                ticket_id, _traceback.format_exc(),
            )
            trace_id = f"FD-{ticket_id}-ERR"
            LOGGER.warning("TRACE_STEP_A_FALLBACK ticket_id=%s trace_id=%s", ticket_id, trace_id)

        # TRACE_STEP_B: trace_log("TRACE_START")
        LOGGER.warning(
            "TRACE_STEP_B_ENTER ticket_id=%s calling_trace_log orchestrator_wired=%s resolver_wired=%s",
            ticket_id,
            self._orchestrator is not None,
            self._client_resolver is not None,
        )
        try:
            trace_log("TRACE_START", trace_id,
                      ticket_id=ticket_id,
                      event_type=event_type,
                      orchestrator_wired=self._orchestrator is not None,
                      client_resolver_wired=self._client_resolver is not None)
            LOGGER.warning("TRACE_STEP_B_EXIT ticket_id=%s trace_log_completed", ticket_id)
        except Exception:
            LOGGER.warning(
                "TRACE_STEP_B_EXCEPTION ticket_id=%s traceback=%s",
                ticket_id, _traceback.format_exc(),
            )

        # 2. Idempotency check
        LOGGER.warning(
            "TRACE_HANDLER_BRANCH_01 ticket_id=%s entering_idempotency_check idem_key=%r",
            ticket_id, idem_key,
        )
        _idem_result = self._idempotency.check(idem_key)
        LOGGER.warning(
            "TRACE_HANDLER_BRANCH_02 ticket_id=%s idempotency_result=%s",
            ticket_id, _idem_result,
        )
        if _idem_result:
            LOGGER.warning(
                "TRACE_HANDLER_BRANCH_02A ticket_id=%s DUPLICATE returning_early",
                ticket_id,
            )
            self._inc(COUNTER_FD_DUPLICATE_EVENTS_TOTAL)
            LOGGER.info("ticket_created.handle: DUPLICATE ticket_id=%s", ticket_id)
            self._audit_event("WEBHOOK_DUPLICATE", ticket_id=ticket_id, client_id="")
            return HandlerResult(
                success=True,
                ticket_id=ticket_id,
                skipped=True,
                skip_reason="DUPLICATE",
            )
        LOGGER.warning(
            "TRACE_HANDLER_BRANCH_02B ticket_id=%s not_duplicate continuing",
            ticket_id,
        )

        # 3. Validate required fields
        LOGGER.warning(
            "TRACE_HANDLER_BRANCH_03 ticket_id=%r email_domain=%s validating_fields",
            ticket_id,
            ticket.requester_email.split("@")[-1] if "@" in ticket.requester_email else "(empty)",
        )
        if not ticket_id or ticket_id == "0":
            LOGGER.warning(
                "TRACE_HANDLER_BRANCH_03A ticket_id=%r MISSING_TICKET_ID returning_early",
                ticket_id,
            )
            return HandlerResult(success=False, ticket_id=ticket_id, error_code="MISSING_TICKET_ID")
        LOGGER.warning(
            "TRACE_HANDLER_BRANCH_03B ticket_id=%s ticket_id_valid",
            ticket_id,
        )

        email = ticket.requester_email
        if not email:
            LOGGER.warning(
                "TRACE_HANDLER_BRANCH_04A ticket_id=%s MISSING_EMAIL returning_early",
                ticket_id,
            )
            self._idempotency.mark_received(idem_key, ticket_id, event_type, event_ts)
            self._idempotency.mark_failed(idem_key, "MISSING_EMAIL")
            return HandlerResult(success=False, ticket_id=ticket_id, error_code="MISSING_EMAIL")
        LOGGER.warning(
            "TRACE_HANDLER_BRANCH_04B ticket_id=%s email_domain=%s email_present",
            ticket_id,
            email.split("@")[-1] if "@" in email else "(no_at)",
        )

        # 4. Client resolution (domain only logged — no PII)
        client_id = ""
        client_name = ticket.custom_fields.cf_clients or ""
        LOGGER.warning(
            "TRACE_HANDLER_BRANCH_05 ticket_id=%s resolver_present=%s cf_clients=%r",
            ticket_id, self._client_resolver is not None, ticket.custom_fields.cf_clients,
        )
        if self._client_resolver is not None:
            LOGGER.warning(
                "TRACE_HANDLER_BRANCH_05B ticket_id=%s resolver_wired calling_resolve email_domain=%s",
                ticket_id,
                email.split("@")[-1] if "@" in email else "(no_at)",
            )
            try:
                tenant_ctx = self._client_resolver.resolve(email)
                client_id = tenant_ctx.client_id
                client_name = tenant_ctx.client_name
                # Sprint 2.49 — TRACE_FD_03_TENANT_RESOLVED: client resolved successfully.
                emit_trace(
                    TRACE_FD_03_TENANT_RESOLVED,
                    ticket_id=ticket_id,
                    tenant=client_id,
                    client=client_name,
                    event_type=event_type,
                    status="RESOLVED",
                )
                LOGGER.warning(
                    "TRACE_HANDLER_BRANCH_06A ticket_id=%s resolve_ok client_id=%r",
                    ticket_id, client_id,
                )
                LOGGER.info(
                    "ticket_created.handle: client_resolved client_id=%s ticket_id=%s",
                    client_id, ticket_id,
                )
                # Sprint 2.30.1 — TRACE_CLIENT_RESOLVED
                trace_log("TRACE_CLIENT_RESOLVED", trace_id,
                          ticket_id=ticket_id,
                          client_id=client_id,
                          client_name=client_name)
            except Exception as exc:
                # UnknownClientError → escalate
                domain = email.split("@")[-1] if "@" in email else "unknown"
                LOGGER.warning(
                    "TRACE_HANDLER_BRANCH_06B ticket_id=%s resolve_raised exc_type=%s domain=%s UNKNOWN_CLIENT_returning",
                    ticket_id, type(exc).__name__, domain,
                )
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
        else:
            LOGGER.warning(
                "TRACE_HANDLER_BRANCH_05A ticket_id=%s resolver_absent skipping_resolution",
                ticket_id,
            )

        # TRACE_PAYLOAD_03_HANDLER — post-validation, post-client-resolution checkpoint
        LOGGER.warning(
            "TRACE_PAYLOAD_03_HANDLER ticket_id=%s subject=%r client_id=%r client_name=%r email_domain=%s",
            ticket_id,
            (ticket.subject or "")[:60],
            client_id,
            client_name,
            email.split("@")[-1] if "@" in email else "(empty)",
        )

        # 5. Mark idempotency received
        self._idempotency.mark_received(idem_key, ticket_id, event_type, event_ts)

        # 6. Initialize conversation state
        self._conversations.get_or_create(ticket_id, client_id)

        # 7. Delegate to TicketOrchestrator (if wired)
        #    Sprint 2.30.1 fix: build TicketContext — process_ticket() requires a
        #    TicketContext object, NOT flat keyword arguments (interface correction).
        case_id: str | None = None
        _response_draft: str | None = None
        _agent_status: str = ""  # populated from orchestrator result if available
        if self._orchestrator is not None:
            try:
                from case_engine.ticket_orchestration.models import TicketContext  # noqa: PLC0415
                ticket_context = TicketContext(
                    ticket_id=ticket_id,
                    client=client_id or client_name,
                    subject=ticket.subject or "",
                    description=ticket.description_text or ticket.description or "",
                    requester_email=email,
                    metadata={
                        "freshdesk_ticket": ticket.to_dict(),
                        "cf_clients": ticket.custom_fields.cf_clients,
                        "cf_environment": ticket.custom_fields.cf_environment,
                        "trace_id": trace_id,
                    },
                )
                # TRACE_PAYLOAD_04_CONTEXT — proves TicketContext is correctly populated
                LOGGER.warning(
                    "TRACE_PAYLOAD_04_CONTEXT ticket_id=%s subject=%r client=%r description_len=%d email_domain=%s",
                    ticket_context.ticket_id,
                    (ticket_context.subject or "")[:60],
                    ticket_context.client,
                    len(ticket_context.description or ""),
                    ticket_context.requester_email.split("@")[-1] if "@" in (ticket_context.requester_email or "") else "(empty)",
                )
                # Sprint 2.49 — TRACE_FD_05_PIPELINE_STARTED: pipeline dispatch.
                emit_trace(
                    TRACE_FD_05_PIPELINE_STARTED,
                    ticket_id=ticket_id,
                    tenant=client_id,
                    client=client_name,
                    event_type=event_type,
                    status="STARTED",
                )
                orch_result = self._orchestrator.process_ticket(ticket_context)
                if hasattr(orch_result, "case_id"):
                    case_id = orch_result.case_id
                # Sprint 2.49 — TRACE_FD_06_PIPELINE_COMPLETED: pipeline returned.
                emit_trace(
                    TRACE_FD_06_PIPELINE_COMPLETED,
                    ticket_id=ticket_id,
                    tenant=client_id,
                    client=client_name,
                    event_type=event_type,
                    status="SUCCESS" if getattr(orch_result, "success", False) else "FAILURE",
                )
                # Sprint 2.49 — TRACE_FD_04_CASE_CREATED: case_id assigned by orchestrator.
                if case_id:
                    emit_trace(
                        TRACE_FD_04_CASE_CREATED,
                        ticket_id=ticket_id,
                        tenant=client_id,
                        client=client_name,
                        event_type=event_type,
                        status=case_id,
                    )
                LOGGER.info(
                    "ticket_created.handle: orchestrator_complete ticket_id=%s case_id=%s success=%s",
                    ticket_id, case_id, getattr(orch_result, "success", "?"),
                )
                _agent_result = getattr(orch_result, "agent_result", None) or {}
                if isinstance(_agent_result, dict):
                    _agent_status = _agent_result.get("agent_status", "")
                    _rd = _agent_result.get("response_draft") or {}
                    if isinstance(_rd, dict):
                        _response_draft = _rd.get("body_html") or _rd.get("body_text") or None
                LOGGER.warning(
                    "RETURN_HANDLER_DRAFT ticket_id=%s has_draft=%s agent_status=%s",
                    ticket_id, _response_draft is not None, _agent_status,
                )
            except Exception as exc:
                LOGGER.warning(
                    "TRACE_ORCHESTRATOR_EXCEPTION ticket_id=%s error=%s traceback=%s",
                    ticket_id, exc, _traceback.format_exc(),
                )
                self._idempotency.mark_failed(idem_key, f"ORCHESTRATOR_ERROR:{exc}")
                return HandlerResult(
                    success=False,
                    ticket_id=ticket_id,
                    error_code="ORCHESTRATOR_ERROR",
                )

        # 8. Update conversation state with case_id and clarification flags.
        #    BLOCKER 1 FIX: when the agent returned AWAITING_CLARIFICATION, set
        #    awaiting_customer=True so that the ticket-updated handler correctly
        #    routes the customer's reply to resume_ticket() in Pass 2.
        if case_id:
            if _agent_status == "AWAITING_CLARIFICATION":
                self._conversations.update(
                    ticket_id,
                    case_id=case_id,
                    awaiting_customer=True,
                    clarification_pending=True,
                    lifecycle_state=ConversationLifecycle.CLARIFICATION,
                )
                LOGGER.info(
                    "ticket_created.handle: awaiting_clarification ticket_id=%s case_id=%s — "
                    "conv_state.awaiting_customer=True written",
                    ticket_id, case_id,
                )
            else:
                self._conversations.update(ticket_id, case_id=case_id)
            self._idempotency.mark_completed(idem_key, case_id=case_id)
        else:
            self._idempotency.mark_completed(idem_key)

        # 9. Audit
        self._audit_event("TICKET_INGESTED", ticket_id=ticket_id, client_id=client_id,
                          case_id=case_id,
                          detail={"case_id": case_id, "cf_clients": client_name})

        latency_ms = int((time.monotonic() - start) * 1000)
        self._record_latency(LATENCY_FD_PROCESSING_MS, latency_ms)
        LOGGER.info(
            "ticket_created.handle: SUCCESS ticket_id=%s case_id=%s latency_ms=%d",
            ticket_id, case_id, latency_ms,
        )
        # Sprint 2.30.1 — TRACE_COMPLETE
        trace_log("TRACE_COMPLETE", trace_id,
                  ticket_id=ticket_id,
                  case_id=case_id or "",
                  latency_ms=latency_ms)
        return HandlerResult(
            success=True,
            ticket_id=ticket_id,
            case_id=case_id,
            action="ticket_ingested",
            response_draft=_response_draft,
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
        case_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None:
            return
        try:
            from case_engine.models import AuditEntry, AuditEventType
            et = AuditEventType(event_type_name)
            entry = AuditEntry(
                case_id=case_id,
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
        ticket_orchestrator: Any = None,
        audit_logger: Any = None,
        metrics_collector: Any = None,
    ) -> None:
        self._idempotency = idempotency_store
        self._conversations = conversation_store
        self._orchestrator = ticket_orchestrator
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

        # Sprint 2.49 — TRACE_FD_02_PAYLOAD_NORMALIZED: parsed update event ok.
        emit_trace(
            TRACE_FD_02_PAYLOAD_NORMALIZED,
            ticket_id=ticket_id,
            client=ticket.custom_fields.cf_clients or "",
            event_type=event_type,
            status="NORMALIZED",
        )

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
        _resume_response_draft: str | None = None  # populated if Pass 2 produces a reply

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
                    case_id=conv_state.case_id if conv_state else None,
                    detail={"previous_state": "CLARIFICATION"},
                )
                result_detail["clarification_resolved"] = True
                if self._orchestrator is not None and event.latest_comment is not None:
                    _msg = event.latest_comment.body_text or event.latest_comment.body or ""
                    LOGGER.info("ENTER_HANDLER_RESUME ticket_id=%s", ticket_id)
                    # Sprint 2.49 — TRACE_FD_05_PIPELINE_STARTED: resume dispatch.
                    emit_trace(
                        TRACE_FD_05_PIPELINE_STARTED,
                        ticket_id=ticket_id,
                        tenant=client_id,
                        event_type="customer_reply_resume",
                        status="STARTED",
                    )
                    try:
                        _resume_result = self._orchestrator.resume_ticket(ticket_id, _msg)
                        # Sprint 2.49 — TRACE_FD_06_PIPELINE_COMPLETED: resume returned.
                        emit_trace(
                            TRACE_FD_06_PIPELINE_COMPLETED,
                            ticket_id=ticket_id,
                            tenant=client_id,
                            event_type="customer_reply_resume",
                            status=(
                                "SUCCESS"
                                if getattr(_resume_result, "error_code", None) is None
                                else "FAILURE"
                            ),
                        )
                        LOGGER.info(
                            "RETURN_HANDLER_RESUME ticket_id=%s error_code=%s",
                            ticket_id, _resume_result.error_code,
                        )
                        # BLOCKER 3 FIX: extract response_draft from Pass 2 agent result
                        # so the route layer can call send_customer_reply() after this returns.
                        _resume_agent = getattr(_resume_result, "agent_result", None) or {}
                        if isinstance(_resume_agent, dict):
                            _resume_rd = _resume_agent.get("response_draft") or {}
                            if isinstance(_resume_rd, dict):
                                _resume_response_draft = (
                                    _resume_rd.get("body_html")
                                    or _resume_rd.get("body_text")
                                    or None
                                )
                        LOGGER.info(
                            "HANDLER_RESUME_DRAFT ticket_id=%s has_draft=%s",
                            ticket_id, _resume_response_draft is not None,
                        )
                    except Exception as _resume_exc:
                        LOGGER.warning(
                            "ticket_updated.handle: resume_ticket failed ticket_id=%s error=%s",
                            ticket_id, _resume_exc,
                        )
            else:
                self._audit_event(
                    "CUSTOMER_REPLY_RECEIVED",
                    ticket_id=ticket_id,
                    client_id=client_id,
                    case_id=conv_state.case_id if conv_state else None,
                )

        elif action == "agent_reply":
            self._audit_event(
                "AGENT_NOTE_RECEIVED",
                ticket_id=ticket_id,
                client_id=client_id,
                case_id=conv_state.case_id if conv_state else None,
                detail={"private": False},
            )

        elif action == "internal_note":
            self._audit_event(
                "AGENT_NOTE_RECEIVED",
                ticket_id=ticket_id,
                client_id=client_id,
                case_id=conv_state.case_id if conv_state else None,
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
                    case_id=conv_state.case_id if conv_state else None,
                    detail={"new_status": new_status, "lifecycle": lifecycle.value if lifecycle else None},
                )
                result_detail["new_status"] = new_status

        elif action == "tag_update":
            self._audit_event(
                "TICKET_UPDATED",
                ticket_id=ticket_id,
                client_id=client_id,
                case_id=conv_state.case_id if conv_state else None,
                detail={"changes": "tags"},
            )

        else:
            # Unrecognized / no-op update
            self._audit_event(
                "TICKET_SKIPPED",
                ticket_id=ticket_id,
                client_id=client_id,
                case_id=conv_state.case_id if conv_state else None,
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
            "ticket_updated.handle: SUCCESS ticket_id=%s action=%s latency_ms=%d has_draft=%s",
            ticket_id, action, latency_ms, _resume_response_draft is not None,
        )
        return HandlerResult(
            success=True,
            ticket_id=ticket_id,
            action=action,
            detail=result_detail,
            response_draft=_resume_response_draft,
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
        case_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None:
            return
        try:
            from case_engine.models import AuditEntry, AuditEventType
            et = AuditEventType(event_type_name)
            entry = AuditEntry(
                case_id=case_id,
                ticket_id=ticket_id,
                client=client_id,
                action_type=et,
                action_detail=detail or {},
                outcome="SUCCESS",
            )
            self._audit._write(entry)
        except Exception as exc:
            LOGGER.debug("ticket_updated.audit_event: %s", exc)
