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
    response_confidence: float | None = None
    observation_note: str | None = None


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
        _response_confidence: float | None = None
        _obs_note: str | None = None
        _agent_result: dict | None = None  # populated when orchestrator is wired
        _agent_status: str = ""            # populated from orchestrator result if available
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
                        # Sprint 2.63.1: preserve the reasoning-layer confidence score
                        # alongside the draft so ReplySafetyGate can evaluate it at the
                        # route layer — previously this was silently dropped here,
                        # leaving send_customer_reply() with no confidence to gate on.
                        _response_confidence = _rd.get("confidence")
                    # Blueprint §14: extract observation note for OBSGEN → FDNOTE path.
                    # Source 1: intelligence layer (Wave 4A — LLM-generated, richest)
                    _intel = (_agent_result.get("metadata") or {}).get("intelligence_result") or {}
                    _intel_obs = (_intel.get("observation") or {}).get("note") or ""
                    if _intel_obs:
                        _obs_note = _intel_obs
                    # Source 2: workflow investigation step (structural fallback)
                    # step_results is always a list (WorkflowExecutionResult.step_results:
                    # list[dict]); guard handles None and the legacy dict shape defensively.
                    if not _obs_note:
                        _wf = _agent_result.get("workflow_result") or {}
                        _sr = _wf.get("step_results")
                        for _step_val in (_sr.values() if isinstance(_sr, dict) else (_sr or [])):
                            _step_obs = ((_step_val or {}).get("result") or {}).get("observation_note") or ""
                            if _step_obs:
                                _obs_note = _step_obs
                                break
                    # Sprint 2.5.8: Blueprint §20A — extract engineering_result for
                    # async Asana URL update + escalation reply in background task.
                    # When a live Asana task was created (external_id populated),
                    # suppress response_draft so the background task can send
                    # a custom escalation reply that includes the Asana URL.
                    _eng_result = _agent_result.get("engineering_result")
                    if (
                        _eng_result
                        and _eng_result.get("success")
                        and (_eng_result.get("ticket") or {}).get("external_id")
                    ):
                        # Real Asana task created — suppress generic draft, let
                        # background task send the proper escalation reply with URL.
                        _response_draft = None
                LOGGER.warning(
                    "RETURN_HANDLER_DRAFT ticket_id=%s has_draft=%s agent_status=%s has_obs=%s",
                    ticket_id, _response_draft is not None, _agent_status, _obs_note is not None,
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
        # Sprint 2.5.8: propagate engineering_result so the async background
        # task can update cf_asana_ticket_link + send the escalation reply.
        _detail: dict[str, Any] = {}
        if _agent_result and isinstance(_agent_result, dict):
            _er = _agent_result.get("engineering_result")
            if _er:
                _detail["engineering_result"] = _er

        return HandlerResult(
            success=True,
            ticket_id=ticket_id,
            case_id=case_id,
            action="ticket_ingested",
            response_draft=_response_draft,
            response_confidence=_response_confidence,
            observation_note=_obs_note,
            detail=_detail or None,
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
        nlp_router: Any = None,
        case_service: Any = None,
    ) -> None:
        self._idempotency = idempotency_store
        self._conversations = conversation_store
        self._orchestrator = ticket_orchestrator
        self._audit = audit_logger
        self._metrics = metrics_collector
        self._nlp_router = nlp_router
        self._case_service = case_service

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
        _resume_response_confidence: float | None = None

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

                # Wave 7 Prep — Blueprint Layer 6: explicit NLU slot extraction.
                # Run customer reply through NLPRouter to extract URN/session_id,
                # then call CaseService.receive_message() to fill slots.
                # If more slots still needed: _resume_response_draft = next_question.
                # If all slots filled: fall through to orchestrator for investigation.
                _nlp_slot_question: str | None = None
                if (
                    self._nlp_router is not None
                    and self._case_service is not None
                    and event.latest_comment is not None
                ):
                    _nlp_msg = event.latest_comment.body_text or event.latest_comment.body or ""
                    if not _nlp_msg.strip():
                        LOGGER.warning(
                            "ticket_updated.handle: EMPTY_COMMENT_BODY ticket_id=%s action=%s "
                            "— latest_comment present but body_text and body are both empty; "
                            "skipping NLP slot resume",
                            ticket_id, action,
                        )
                    if _nlp_msg.strip():
                        _nlp_slot_question = self._nlp_slot_resume(
                            ticket_id=ticket_id,
                            comment_text=_nlp_msg,
                            case_id=conv_state.case_id if conv_state else None,
                        )
                        if _nlp_slot_question is not None:
                            _resume_response_draft = _nlp_slot_question
                            # Deterministic, template-driven clarification prompt (not
                            # LLM free text) — treated as fixed-confidence for the gate.
                            _resume_response_confidence = 1.0
                            LOGGER.info(
                                "NLP_SLOT_RESUME: still_needs_clarification ticket_id=%s",
                                ticket_id,
                            )
                            # Re-arm clarification state so the next customer reply routes
                            # through the awaiting_customer path again (Bug 3 fix).
                            self._conversations.update(
                                ticket_id,
                                awaiting_customer=True,
                                clarification_pending=True,
                                lifecycle_state=ConversationLifecycle.CLARIFICATION,
                            )

                # Only run investigation when all slots are filled (_nlp_slot_question is None).
                # If _nlp_slot_question is not None, the customer still needs to provide
                # information (or the case was just escalated); skip the orchestrator in both cases.
                if _nlp_slot_question is None and self._orchestrator is not None and event.latest_comment is not None:
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
                                _resume_response_confidence = _resume_rd.get("confidence")
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
                # Resume the pipeline for customer replies on open/pending conversations
                # that are NOT in clarification mode (multi-turn workflow continuation).
                _active_lifecycle = (
                    ConversationLifecycle.OPEN,
                    ConversationLifecycle.PENDING,
                )
                if (
                    self._orchestrator is not None
                    and conv_state is not None
                    and conv_state.lifecycle_state in _active_lifecycle
                    and event.latest_comment is not None
                ):
                    _msg = event.latest_comment.body_text or event.latest_comment.body or ""
                    if _msg.strip():
                        LOGGER.info("ENTER_HANDLER_CONTINUE ticket_id=%s", ticket_id)
                        emit_trace(
                            TRACE_FD_05_PIPELINE_STARTED,
                            ticket_id=ticket_id,
                            tenant=client_id,
                            event_type="customer_reply_continue",
                            status="STARTED",
                        )
                        try:
                            _cont_result = self._orchestrator.resume_ticket(
                                ticket_id, _msg,
                                case_id=conv_state.case_id if conv_state else None,
                            )
                            emit_trace(
                                TRACE_FD_06_PIPELINE_COMPLETED,
                                ticket_id=ticket_id,
                                tenant=client_id,
                                event_type="customer_reply_continue",
                                status=(
                                    "SUCCESS"
                                    if getattr(_cont_result, "error_code", None) is None
                                    else "FAILURE"
                                ),
                            )
                            LOGGER.info(
                                "RETURN_HANDLER_CONTINUE ticket_id=%s error_code=%s",
                                ticket_id, _cont_result.error_code,
                            )
                            _cont_agent = getattr(_cont_result, "agent_result", None) or {}
                            if isinstance(_cont_agent, dict):
                                _cont_rd = _cont_agent.get("response_draft") or {}
                                if isinstance(_cont_rd, dict):
                                    _resume_response_draft = (
                                        _cont_rd.get("body_html")
                                        or _cont_rd.get("body_text")
                                        or None
                                    )
                                    _resume_response_confidence = _cont_rd.get("confidence")
                        except Exception as _cont_exc:
                            LOGGER.warning(
                                "ticket_updated.handle: continue_ticket failed ticket_id=%s error=%s",
                                ticket_id, _cont_exc,
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
            response_confidence=_resume_response_confidence,
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

    def _nlp_slot_resume(
        self,
        ticket_id: str,
        comment_text: str,
        case_id: str | None,
    ) -> str | None:
        """
        Wave 7 Prep — Blueprint Layer 6 clarification resume.

        Runs the customer reply through NLPRouter to extract URN/session_id, then
        calls CaseService.receive_message() to fill the extracted slots.

        Returns:
            str  — next clarification question (more slots still needed)
            None — all required slots filled; investigation can proceed via orchestrator
                   (also None on any error — fail-safe, falls through to orchestrator)

        Never raises. PII discipline: comment_text is never logged.
        """
        if not case_id:
            LOGGER.info("NLP_SLOT_RESUME: no case_id ticket_id=%s — skipping", ticket_id)
            return None

        try:
            nlp_signal = self._nlp_router.route(comment_text)
        except Exception as exc:
            LOGGER.error(
                "NLP_SLOT_RESUME: nlp_router.route failed ticket_id=%s error=%s",
                ticket_id, exc,
            )
            return None

        LOGGER.info(
            "NLP_SLOT_RESUME: intent=%s needs_clarification=%s missing_slots=%s ticket_id=%s",
            nlp_signal.intent,
            nlp_signal.needs_clarification,
            [k for k, v in nlp_signal.entities.items() if not v],
            ticket_id,
        )

        try:
            case = self._case_service.get_case(case_id)
        except Exception as exc:
            LOGGER.error(
                "NLP_SLOT_RESUME: get_case failed case_id=%s error=%s", case_id, exc
            )
            return None

        if case is None:
            LOGGER.info("NLP_SLOT_RESUME: case not found case_id=%s — skipping", case_id)
            return None

        # Fill each extracted entity slot via CaseService.receive_message().
        # Slots filled in entity order; last call reveals whether more are needed.
        last_result = None
        try:
            for slot_name, slot_value in nlp_signal.entities.items():
                if slot_value:
                    last_result = self._case_service.receive_message(
                        case,
                        comment_text,
                        slot_name=slot_name,
                        slot_value_str=slot_value,
                    )
            # If no entities extracted, attempt implicit extraction from message text.
            if last_result is None:
                last_result = self._case_service.receive_message(case, comment_text)
        except Exception as exc:
            LOGGER.error(
                "NLP_SLOT_RESUME: receive_message failed case_id=%s error=%s",
                case_id, exc,
            )
            return None

        if last_result is None:
            return None

        # Max-attempts exceeded: case is ESCALATED — return a human-transfer message so the
        # handler sends it as a customer reply and skips the investigation orchestrator.
        if getattr(last_result, "escalated", False):
            LOGGER.info(
                "NLP_SLOT_RESUME: max_attempts_exceeded escalating_to_human case_id=%s ticket_id=%s",
                case_id, ticket_id,
            )
            return (
                "We were unable to collect the required information after multiple attempts. "
                "A human agent will review your request and assist you shortly."
            )

        next_q = getattr(last_result, "next_question", None)
        if next_q and isinstance(next_q, dict):
            prompt_text = next_q.get("prompt_text") or str(next_q)
            LOGGER.info(
                "NLP_SLOT_RESUME: next_question returned case_id=%s ticket_id=%s slot=%s",
                case_id, ticket_id, next_q.get("slot_name"),
            )
            return prompt_text

        LOGGER.info(
            "NLP_SLOT_RESUME: all_slots_filled case_id=%s ticket_id=%s — proceeding to investigation",
            case_id, ticket_id,
        )
        return None

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
