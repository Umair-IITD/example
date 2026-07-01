"""
api/routes/webhooks/freshdesk.py

Sprint 2.28.1: Freshdesk webhook receiver routes.
Sprint 2.28.2: Defect fixes — replay protection, pre-persistence, unknown tenant note,
               async background tasks.

Routes:
  POST /webhooks/freshdesk/ticket-created
  POST /webhooks/freshdesk/ticket-updated

Design (NON-NEGOTIABLE):
  - Freshdesk has a 10-second webhook timeout. These routes MUST return 200 OK
    within that window. Processing is enqueued as a FastAPI BackgroundTask.
  - JSON is parsed BEFORE HMAC verification so the event_timestamp can be
    extracted and passed to the verifier for replay protection.
    (Signature is over the raw body bytes; parse order does not affect it.)
  - Signature verification is performed SYNCHRONOUSLY before returning 200 OK.
    If verification fails and enforce=True, return 401 immediately.
  - Payload size limit: 1 MB (reject oversized payloads before parsing).
  - Pre-persistence (WAL): idempotency_store.ensure_receipt() is called BEFORE
    returning 200 OK, writing the event receipt to Supabase. If the process
    crashes before the background task runs, the receipt is preserved in the DB.
  - Background task: async, processes the event after 200 is returned.
    On UNKNOWN_CLIENT: posts an internal Freshdesk note via response_service.

Security:
  - HMAC-SHA256 via FreshdeskWebhookVerifier (X-Webhook-Token / X-Freshdesk-Signature)
  - Replay protection: event_timestamp older than 5 minutes → reject (NOW ACTIVE)
  - Clock skew: future timestamp > 60s → reject
  - Payload size gate: 1 MB hard limit

Part 6 — Idempotency note (verified from live payload audit):
  Freshdesk does NOT provide a unique webhook event_id or webhook_id in the
  payload. The stable identifier is composed from ticket_id + event_type +
  event_timestamp (created_at for ticket-created, updated_at for ticket-updated).
  This matches Section 9.1 and 9.2 of SPRINT_2_28_FINAL_FRESHDESK_INTEGRATION_AUDIT.
  The existing key format "{ticket_id}:{event_type}:{event_timestamp}" is correct.
"""
from __future__ import annotations

import json
import logging
import os
import traceback as _traceback
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from freshdesk.handlers import FreshdeskTicketCreatedHandler, FreshdeskTicketUpdatedHandler
from freshdesk.idempotency import WebhookIdempotencyStore
from freshdesk.conversation_state import ConversationStateStore
from freshdesk.verifier import FreshdeskWebhookVerifier, VerificationResult
from freshdesk.metrics import (
    COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL,
    COUNTER_FD_WEBHOOKS_REJECTED_TOTAL,
)

LOGGER = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 1 * 1024 * 1024  # 1 MB

router = APIRouter(prefix="/webhooks/freshdesk", tags=["freshdesk-webhooks"])


# ── Route: ticket-created ─────────────────────────────────────────────────────

@router.post(
    "/ticket-created",
    status_code=200,
    summary="Freshdesk ticket-created webhook receiver",
)
async def freshdesk_ticket_created(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """
    Receive a Freshdesk ticket-created webhook event.

    Processing order:
      1. Payload size gate (1 MB)
      2. JSON parse (required to extract event_timestamp for replay protection)
      3. HMAC verification WITH event_timestamp for replay protection
      4. Pre-persist receipt to Supabase (WAL — before returning 200)
      5. Enqueue background task
      6. Return 200 OK immediately
    """
    
    body = await request.body()

    # 1. Payload size gate
    if len(body) > _MAX_PAYLOAD_BYTES:
        LOGGER.warning("freshdesk.ticket_created: payload too large bytes=%d", len(body))
        _inc(request, COUNTER_FD_WEBHOOKS_REJECTED_TOTAL)
        return Response(
            content='{"detail":"payload_too_large"}',
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            media_type="application/json",
        )

    # 2. JSON parse FIRST — needed to extract event_timestamp for replay check.
    #    Signature is computed over raw body bytes, so parse order is irrelevant.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        LOGGER.warning("freshdesk.ticket_created: invalid JSON")
        _inc(request, COUNTER_FD_WEBHOOKS_REJECTED_TOTAL)
        return Response(
            content='{"detail":"invalid_json"}',
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="application/json",
        )

    # ── STAGE 2 DIAGNOSTIC: log complete raw payload ──────────────────────────
    # Remains until payload shape is confirmed in production logs.
    LOGGER.debug(
        "freshdesk.ticket_created: STAGE2_RAW_PAYLOAD payload=%s",
        json.dumps(payload, indent=2),
    )
    # ─────────────────────────────────────────────────────────────────────────

    # TRACE_ENTER_ROUTE — always emits at WARNING; proves route was reached and JSON parsed
    LOGGER.warning(
        "TRACE_ENTER_ROUTE event=ticket_created payload_format=%s outer_keys=%s",
        "format_a" if "freshdesk_webhook" in payload
        else "format_b" if isinstance(payload.get("ticket"), dict)
        else "unknown",
        sorted(payload.keys()),
    )

    # 3. HMAC verification WITH event_timestamp (replay protection now active)
    inner = payload.get("freshdesk_webhook", payload)

    # ── PAYLOAD_FORENSICS: temporary structured diagnostic (Sprint 2.29.2) ───
    # Logs specific field paths to reveal Dispatch'r template key names.
    # Remove once payload structure is confirmed and stable.
    LOGGER.debug(
        "freshdesk.ticket_created: PAYLOAD_FORENSICS "
        "outer_keys=%s inner_keys=%s "
        "id=%r ticket_id=%r requester_email=%r "
        "requester_nested=%r created_at=%r ticket_created_at=%r "
        "cf_keys=%s",
        sorted(payload.keys()),
        sorted(inner.keys()) if isinstance(inner, dict) else [],
        inner.get("id"),
        inner.get("ticket_id"),
        inner.get("requester_email"),
        (inner.get("requester") or {}).get("email") if isinstance(inner.get("requester"), dict) else None,
        inner.get("created_at"),
        inner.get("ticket_created_at"),
        sorted((inner.get("ticket_custom_fields") or inner.get("custom_fields") or inner.get("ticket_cf") or {}).keys()),
    )
    # ─────────────────────────────────────────────────────────────────────────

    # Extract timestamp: check "created_at", "ticket_created_at", and Format B nested ticket.created_at
    event_ts_str = (
        inner.get("created_at")
        or inner.get("ticket_created_at")
        or (inner.get("ticket") or {}).get("created_at")
        or ""
    )
    event_timestamp = _parse_iso_timestamp(event_ts_str)

    verifier = _get_verifier(request)
    if verifier is not None:
        headers = dict(request.headers)
        result = verifier.verify(headers, body, event_timestamp=event_timestamp)
        if not result.valid:
            LOGGER.warning(
                "freshdesk.ticket_created: SIGNATURE_FAILURE reason=%s", result.reason
            )
            _inc(request, COUNTER_FD_WEBHOOKS_REJECTED_TOTAL)
            _audit_signature_failure(request, "ticket_created", result.reason)
            return Response(
                content='{"detail":"signature_invalid"}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
            )

    # 4. Pre-persist receipt (WAL) — durably written to Supabase before 200 is returned.
    #    If the process crashes before the background task processes this event,
    #    the RECEIVED record in Supabase proves the event was received.
    #
    # Ticket ID extraction — three formats supported:
    #   A) freshdesk_webhook flat:  inner.ticket_id  (int)
    #   B) Dispatch'r nested:       inner is full payload → inner["ticket"]["id"]
    #   C) Generic canonical:       inner.id
    _raw_tid = inner.get("id")
    if _raw_tid is None:
        _raw_tid = inner.get("ticket_id")
    if _raw_tid is None:
        _ticket_nested = inner.get("ticket")
        if isinstance(_ticket_nested, dict):
            _raw_tid = _ticket_nested.get("id") or _ticket_nested.get("ticket_id")
    ticket_id = str(_raw_tid) if _raw_tid else ""

    # TRACE_PAYLOAD_01_ROUTE — deterministic proof that the raw payload reached the route
    _subject_hint = (
        inner.get("subject") or inner.get("ticket_subject")
        or (inner.get("ticket") or {}).get("subject") or ""
    )[:60]
    _email_hint = (
        inner.get("requester_email") or inner.get("ticket_contact_email")
        or (inner.get("requester") or {}).get("email") or ""
    )
    _email_domain = _email_hint.split("@")[-1] if "@" in _email_hint else ""
    _cf_client_hint = (
        (inner.get("ticket_custom_fields") or inner.get("custom_fields") or {}).get("cf_clients")
        or inner.get("ticket_cf_clients")
        or ""
    )
    LOGGER.warning(
        "TRACE_PAYLOAD_01_ROUTE ticket_id=%s subject=%r email_domain=%s cf_clients=%r "
        "outer_keys=%s inner_keys=%s",
        ticket_id,
        _subject_hint,
        _email_domain,
        _cf_client_hint,
        sorted(payload.keys()),
        sorted(inner.keys()) if isinstance(inner, dict) else [],
    )

    idem_key = WebhookIdempotencyStore.make_key(ticket_id, "ticket_created", event_ts_str)
    _pre_persist(request, idem_key, ticket_id, "ticket_created", event_ts_str)

    # 5. Enqueue async background task — return 200 immediately
    _inc(request, COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL)
    background_tasks.add_task(_process_ticket_created, request, payload)
    return Response(
        content='{"status":"accepted","event":"ticket_created"}',
        status_code=200,
        media_type="application/json",
    )


# ── Route: ticket-updated ─────────────────────────────────────────────────────

@router.post(
    "/ticket-updated",
    status_code=200,
    summary="Freshdesk ticket-updated webhook receiver",
)
async def freshdesk_ticket_updated(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """
    Receive a Freshdesk ticket-updated webhook event.

    Same ordering as ticket-created: parse → verify (with timestamp) → persist → 200.
    """
    body = await request.body()

    if len(body) > _MAX_PAYLOAD_BYTES:
        LOGGER.warning("freshdesk.ticket_updated: payload too large bytes=%d", len(body))
        _inc(request, COUNTER_FD_WEBHOOKS_REJECTED_TOTAL)
        return Response(
            content='{"detail":"payload_too_large"}',
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            media_type="application/json",
        )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        LOGGER.warning("freshdesk.ticket_updated: invalid JSON")
        _inc(request, COUNTER_FD_WEBHOOKS_REJECTED_TOTAL)
        return Response(
            content='{"detail":"invalid_json"}',
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="application/json",
        )
    
    # ── STAGE 2 DIAGNOSTIC: log complete raw payload ──────────────────────────
    # Remains until payload shape is confirmed in production logs.
    LOGGER.debug(
        "freshdesk.ticket_updated: STAGE2_RAW_PAYLOAD payload=%s",
        json.dumps(payload, indent=2),
    )
    # ─────────────────────────────────────────────────────────────────────────

    inner = payload.get("freshdesk_webhook", payload)

    # ── PAYLOAD_FORENSICS: temporary structured diagnostic (Sprint 2.29.2) ───
    LOGGER.debug(
        "freshdesk.ticket_updated: PAYLOAD_FORENSICS "
        "outer_keys=%s inner_keys=%s "
        "id=%r ticket_id=%r updated_at=%r ticket_updated_at=%r",
        sorted(payload.keys()),
        sorted(inner.keys()) if isinstance(inner, dict) else [],
        inner.get("id"),
        inner.get("ticket_id"),
        inner.get("updated_at"),
        inner.get("ticket_updated_at"),
    )
    # ─────────────────────────────────────────────────────────────────────────

    # Extract timestamp: check "updated_at" (canonical) then "ticket_updated_at"
    event_ts_str = inner.get("updated_at") or inner.get("ticket_updated_at") or ""
    event_timestamp = _parse_iso_timestamp(event_ts_str)

    verifier = _get_verifier(request)
    if verifier is not None:
        headers = dict(request.headers)
        result = verifier.verify(headers, body, event_timestamp=event_timestamp)
        if not result.valid:
            LOGGER.warning(
                "freshdesk.ticket_updated: SIGNATURE_FAILURE reason=%s", result.reason
            )
            _inc(request, COUNTER_FD_WEBHOOKS_REJECTED_TOTAL)
            _audit_signature_failure(request, "ticket_updated", result.reason)
            return Response(
                content='{"detail":"signature_invalid"}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
            )

    # Extract ticket_id — same three-format logic as ticket-created
    _raw_tid = inner.get("id")
    if _raw_tid is None:
        _raw_tid = inner.get("ticket_id")
    if _raw_tid is None:
        _ticket_nested = inner.get("ticket")
        if isinstance(_ticket_nested, dict):
            _raw_tid = _ticket_nested.get("id") or _ticket_nested.get("ticket_id")
    ticket_id = str(_raw_tid) if _raw_tid else ""
    idem_key = WebhookIdempotencyStore.make_key(ticket_id, "ticket_updated", event_ts_str)
    _pre_persist(request, idem_key, ticket_id, "ticket_updated", event_ts_str)

    _inc(request, COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL)
    background_tasks.add_task(_process_ticket_updated, request, payload)
    return Response(
        content='{"status":"accepted","event":"ticket_updated"}',
        status_code=200,
        media_type="application/json",
    )


# ── Background task implementations (async) ──────────────────────────────────

async def _process_ticket_created(request: Request, payload: dict[str, Any]) -> None:
    """
    Async background task: process a ticket-created webhook event.

    On UNKNOWN_CLIENT: posts an internal Freshdesk note so the ticket is not
    silently dropped. Human agents can then review and act. Automation is
    never executed for unknown tenants.
    """
    # TRACE_ENTER_BACKGROUND_TASK — always emits at WARNING; proves background task started
    LOGGER.warning(
        "TRACE_ENTER_BACKGROUND_TASK event=ticket_created payload_keys=%s",
        sorted(payload.keys()),
    )
    handler = _get_created_handler(request)
    if handler is None:
        LOGGER.warning("freshdesk.bg.ticket_created: no handler available (offline mode)")
        return
    try:
        result = handler.handle(payload)

        if result.success:
            LOGGER.info(
                "freshdesk.bg.ticket_created: done ticket_id=%s case_id=%s skipped=%s",
                result.ticket_id, result.case_id, result.skipped,
            )
            if not result.skipped and result.response_draft:
                _resp_svc = _get_response_service(request)
                if _resp_svc is not None:
                    await _resp_svc.send_customer_reply(
                        result.ticket_id,
                        result.response_draft,
                        case_id=result.case_id or "",
                    )
                    LOGGER.warning(
                        "RETURN_CUSTOMER_REPLY_SENT ticket_id=%s case_id=%s",
                        result.ticket_id, result.case_id,
                    )
                else:
                    LOGGER.warning(
                        "RETURN_NO_RESPONSE_SERVICE ticket_id=%s",
                        result.ticket_id,
                    )
        else:
            LOGGER.warning(
                "freshdesk.bg.ticket_created: FAILED ticket_id=%s error_code=%s",
                result.ticket_id, result.error_code,
            )

            # Part 5: Unknown tenant safe handling.
            # Post an internal Freshdesk note so the ticket is visible to agents.
            # Never execute automation for unknown tenants.
            if result.error_code == "UNKNOWN_CLIENT" and result.ticket_id:
                domain = (result.detail or {}).get("domain", "unknown")
                response_svc = _get_response_service(request)
                if response_svc is not None:
                    note_body = (
                        "<p><strong>AI Support System — Action Required</strong><br>"
                        f"This ticket could not be assigned to an automation workflow "
                        f"because the requester domain <code>{domain}</code> is not "
                        "registered in the tenant registry.<br><br>"
                        "<strong>Human review required.</strong> No automated actions "
                        "have been taken on this ticket.</p>"
                    )
                    await response_svc.add_internal_note(result.ticket_id, note_body)
                    LOGGER.info(
                        "freshdesk.bg.ticket_created: unknown_tenant_note_posted "
                        "ticket_id=%s domain=%s",
                        result.ticket_id, domain,
                    )
                else:
                    LOGGER.warning(
                        "freshdesk.bg.ticket_created: unknown_tenant NO response_service "
                        "ticket_id=%s domain=%s — note not posted",
                        result.ticket_id, domain,
                    )

    except Exception as exc:
        LOGGER.warning(
            "TRACE_BG_EXCEPTION event=ticket_created error=%s traceback=%s",
            exc, _traceback.format_exc(),
        )


async def _process_ticket_updated(request: Request, payload: dict[str, Any]) -> None:
    """
    Async background task: process a ticket-updated webhook event.

    Handles: customer reply, agent reply, internal note, status change, tag update.
    Customer reply while in CLARIFICATION state resumes the workflow.

    Sprint 2.28.3 — Clarification Loop:
      When action="customer_reply", if a rag_processor is available on app.state,
      it is called to generate a follow-up response and post it back to Freshdesk.
      The rag_processor must be a callable(ticket_id, query_text, tenant) → None.
    """
    handler = _get_updated_handler(request)
    if handler is None:
        LOGGER.warning("freshdesk.bg.ticket_updated: no handler available (offline mode)")
        return
    try:
        result = handler.handle(payload)
        LOGGER.info(
            "freshdesk.bg.ticket_updated: done ticket_id=%s action=%s skipped=%s has_draft=%s",
            result.ticket_id, result.action, result.skipped, result.response_draft is not None,
        )

        if not result.skipped and result.action == "customer_reply" and result.ticket_id:
            if result.response_draft:
                # BLOCKER 4 FIX: Pass 2 orchestrator produced a response draft
                # (clarification loop continued or workflow produced a reply).
                # Send it back to the customer via Freshdesk — same path as Pass 1.
                _resp_svc = _get_response_service(request)
                if _resp_svc is not None:
                    await _resp_svc.send_customer_reply(
                        result.ticket_id,
                        result.response_draft,
                        case_id=result.case_id or "",
                    )
                    LOGGER.warning(
                        "RETURN_CUSTOMER_REPLY_SENT_PASS2 ticket_id=%s case_id=%s",
                        result.ticket_id, result.case_id,
                    )
                else:
                    LOGGER.warning(
                        "RETURN_NO_RESPONSE_SERVICE_PASS2 ticket_id=%s",
                        result.ticket_id,
                    )
            else:
                # No orchestrator draft (e.g. orchestrator not wired, or still awaiting).
                # Fall back to the legacy RAG clarification processor if available.
                await _handle_clarification_reply(request, payload, result.ticket_id)

    except Exception as exc:
        LOGGER.warning(
            "TRACE_BG_EXCEPTION event=ticket_updated error=%s traceback=%s",
            exc, _traceback.format_exc(),
        )


async def _handle_clarification_reply(
    request: Request,
    payload: dict[str, Any],
    ticket_id: str,
) -> None:
    """
    Process a customer reply for the clarification continuation loop.

    Extracts the latest comment body as the query, resolves tenant from
    conversation state, and calls the rag_processor on app.state if available.
    """
    try:
        # Get the latest comment body to use as the query
        inner = payload.get("freshdesk_webhook", payload)
        lc = inner.get("latest_comment", {})
        query_text = lc.get("body_text") or lc.get("body", "").strip()
        if not query_text:
            # Fall back to description_text from the ticket for context
            query_text = (
                inner.get("description_text")
                or inner.get("description", "")
            ).strip()

        if not query_text:
            LOGGER.warning(
                "freshdesk.clarification_loop: no query text ticket_id=%s — skipping",
                ticket_id,
            )
            return

        # Resolve tenant from conversation state
        conv_store = _get_conv_store(request)
        tenant = ""
        if conv_store is not None:
            state = conv_store.get(ticket_id)
            if state:
                tenant = state.client_id

        # Fall back to cf_clients from payload
        if not tenant:
            cf = inner.get("ticket_custom_fields", {})
            tenant = cf.get("cf_clients", "")

        rag_processor = _get_rag_processor(request)
        if rag_processor is None:
            LOGGER.info(
                "freshdesk.clarification_loop: no rag_processor available "
                "ticket_id=%s — clarification loop requires Sprint 2.29 wiring",
                ticket_id,
            )
            return

        LOGGER.info(
            "freshdesk.clarification_loop: triggering rag ticket_id=%s tenant=%s "
            "query_chars=%d",
            ticket_id, tenant, len(query_text),
        )
        await rag_processor(ticket_id, query_text, tenant)
        LOGGER.info(
            "freshdesk.clarification_loop: completed ticket_id=%s tenant=%s",
            ticket_id, tenant,
        )

    except Exception as exc:
        LOGGER.error(
            "freshdesk.clarification_loop: error ticket_id=%s error=%s",
            ticket_id, exc,
        )


# ── Service resolution (runtime injection) ────────────────────────────────────

def _get_verifier(request: Request) -> FreshdeskWebhookVerifier | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    verifier = getattr(runtime, "freshdesk_verifier", None)
    if verifier is not None:
        return verifier
    # Env fallback — only reached when startup wiring was skipped (test/offline mode).
    # Production execution sets app.state.freshdesk_verifier once at startup.
    secret  = os.getenv("FRESHDESK_WEBHOOK_SECRET", "")
    enforce = os.getenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false").lower() == "true"
    mode    = os.getenv("FRESHDESK_WEBHOOK_MODE", "hmac").strip().lower()
    if not secret:
        return None
    return FreshdeskWebhookVerifier(secret, mode=mode, enforce=enforce)


def _get_created_handler(request: Request) -> FreshdeskTicketCreatedHandler | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return _build_offline_created_handler()
    handler = getattr(runtime, "freshdesk_ticket_created_handler", None)
    if handler is not None:
        return handler
    return _build_offline_created_handler()


def _get_updated_handler(request: Request) -> FreshdeskTicketUpdatedHandler | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return _build_offline_updated_handler()
    handler = getattr(runtime, "freshdesk_ticket_updated_handler", None)
    if handler is not None:
        return handler
    return _build_offline_updated_handler()


def _get_response_service(request: Request) -> Any | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "freshdesk_response_service", None)


def _get_idempotency_store(request: Request) -> WebhookIdempotencyStore | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "freshdesk_idempotency_store", None)


def _get_rag_processor(request: Request) -> Any | None:
    """Get optional RAG processor callable from app.state (wired in Sprint 2.29)."""
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "freshdesk_rag_processor", None)


def _get_conv_store(request: Request) -> ConversationStateStore | None:
    """Get conversation state store from app.state."""
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "freshdesk_conversation_store", None)


def _build_offline_created_handler() -> FreshdeskTicketCreatedHandler:
    return FreshdeskTicketCreatedHandler(
        idempotency_store=WebhookIdempotencyStore(),
        conversation_store=ConversationStateStore(),
    )


def _build_offline_updated_handler() -> FreshdeskTicketUpdatedHandler:
    return FreshdeskTicketUpdatedHandler(
        idempotency_store=WebhookIdempotencyStore(),
        conversation_store=ConversationStateStore(),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_iso_timestamp(ts_str: str) -> datetime | None:
    """
    Parse an ISO-8601 timestamp string into a timezone-aware datetime.

    Returns None on parse failure (replay check will be skipped if None).
    Handles both 'Z' suffix and '+00:00' offset formats.
    """
    if not ts_str:
        return None
    try:
        normalized = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        LOGGER.debug("freshdesk.timestamp_parse: failed to parse ts=%s", ts_str)
        return None


def _pre_persist(
    request: Request,
    idem_key: str,
    ticket_id: str,
    event_type: str,
    event_ts: str,
) -> None:
    """
    WAL step: write RECEIVED receipt to Supabase BEFORE returning 200 OK.

    Uses ensure_receipt() which INSERT-only (never overwrites COMPLETED records).
    Errors are logged but never propagated — the 200 is always returned.
    """
    try:
        store = _get_idempotency_store(request)
        if store is not None:
            store.ensure_receipt(idem_key, ticket_id, event_type, event_ts)
    except Exception as exc:
        LOGGER.warning("freshdesk.pre_persist: error=%s key=%s", exc, idem_key)


def _inc(request: Request, counter_name: str) -> None:
    try:
        runtime = getattr(getattr(request, "app", None), "state", None)
        if runtime is None:
            return
        mc = getattr(runtime, "metrics_collector", None)
        if mc is not None:
            mc.increment(counter_name)
    except Exception:
        pass


def _audit_signature_failure(request: Request, event_type: str, reason: str) -> None:
    try:
        runtime = getattr(getattr(request, "app", None), "state", None)
        if runtime is None:
            return
        audit = getattr(runtime, "audit_logger", None)
        if audit is None:
            return
        from case_engine.models import AuditEntry, AuditEventType
        entry = AuditEntry(
            action_type=AuditEventType.SIGNATURE_FAILURE,
            action_detail={"event_type": event_type, "reason": reason},
            outcome="REJECTED",
        )
        audit._write(entry)
    except Exception:
        pass


# Type alias for response service (avoids import cycle at module level)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from freshdesk.response_service import FreshdeskResponseService
