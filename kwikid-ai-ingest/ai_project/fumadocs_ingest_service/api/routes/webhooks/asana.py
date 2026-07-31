"""
api/routes/webhooks/asana.py

Sprint 2.6x: Asana webhook receiver — closes the L2 resolution loop.

Route:
  POST /webhooks/asana/task-completed

Handles TWO distinct request shapes on the same endpoint, exactly as Asana's
webhook protocol requires:

  1. Handshake (fires once, immediately after AsanaClient.create_webhook()):
     Asana POSTs a request carrying an X-Hook-Secret header and (usually) an
     empty body. We must echo that header back with 200/204 to confirm the
     subscription. The secret is persisted via AsanaWebhookSecretStore for
     verifying every future delivery.

  2. Event delivery (fires whenever a watched field changes):
     Asana POSTs a request carrying an X-Hook-Signature header —
     HMAC-SHA256(secret, raw_body) — which we verify before trusting the
     payload. We only act on "task completed" changes (see asana/webhook.py).

Design (mirrors api/routes/webhooks/freshdesk.py conventions):
  - Read raw body bytes FIRST — signature is computed over the exact bytes
    Asana sent, not a re-serialized JSON object.
  - Return 200 quickly; do the actual resolution-loop work in a
    BackgroundTask so we never risk Asana's delivery timeout.
  - Never raise: any failure is caught, logged, and still returns 200 (Asana
    only cares that we ack) — but nothing is acted upon in that case.
  - AsanaEventIdempotencyStore deduplicates redeliveries within a process
    lifetime; post-restart redeliveries are safe (ticket already RESOLVED).

On a verified "task completed" event this route:
  1. Checks AsanaEventIdempotencyStore — skips if already processed.
  2. Marks the internal EngineeringTicket RESOLVED.
  3. GETs the current Freshdesk ticket, runs ClosureFieldGuard.
  4. If guard passes: runs the resolution reply through ReplySafetyGate
     (Sprint 2.63.1 — fixed template, confidence=1.0, still subject to
     kill-switch/duplicate/force-escalation checks). If allowed, sends the
     customer resolution reply, then PUTs status=4 + closure fields
     (cf_sop_status, cf_resolution_classification, type) in a single combined
     call — satisfying ticket_lifecycle.md §6.
  5. If ClosureFieldGuard blocks: posts an internal note for manual closure.
  6. If ReplySafetyGate blocks: posts an internal draft note and does NOT
     close the ticket — same manual-handling shape as a guard block.

Closure-field mapping (confirmed in sprint-2-6-3.md §3.2):
  cf_sop_status              = "No SOP Available"
  cf_resolution_classification = "Permanent Fix Applied by Dev"
  status                     = 4 (Resolved; 4→5 is automatic via Freshdesk SLA)
  type                       = "Issues"
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response, status
from fastapi.responses import JSONResponse

from api.error_models import error_body
from asana.webhook import (
    AsanaEventIdempotencyStore,
    AsanaWebhookSecretStore,
    extract_completed_task_events,
    verify_signature,
)
from freshdesk.closure_guard import ClosureFieldGuard
from freshdesk.templates import build_draft_reply_note

LOGGER = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 1 * 1024 * 1024  # 1 MB, matches the Freshdesk webhook gate

router = APIRouter(prefix="/webhooks/asana", tags=["asana-webhooks"])


# ── app.state accessors (mirrors api/routes/webhooks/freshdesk.py) ───────────

def _get_secret_store(request: Request) -> AsanaWebhookSecretStore | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "asana_webhook_secret_store", None)


def _get_engineering_service(request: Request) -> Any | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "engineering_escalation_service", None)


def _get_response_service(request: Request) -> Any | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "freshdesk_response_service", None)


def _get_project_gid(request: Request) -> str:
    runtime = getattr(getattr(request, "app", None), "state", None)
    return getattr(runtime, "asana_project_gid", "") if runtime is not None else ""


def _get_idempotency_store(request: Request) -> AsanaEventIdempotencyStore | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "asana_idempotency_store", None)


def _get_safety_gate(request: Request) -> Any | None:
    runtime = getattr(getattr(request, "app", None), "state", None)
    if runtime is None:
        return None
    return getattr(runtime, "reply_safety_gate", None)


# ── Route ──────────────────────────────────────────────────────────────────

@router.post("/task-completed")
async def receive_asana_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """
    Single endpoint for both the Asana handshake and ongoing event delivery.
    """
    raw_body = await request.body()
    if len(raw_body) > _MAX_PAYLOAD_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content=error_body("PAYLOAD_TOO_LARGE", "Webhook payload exceeds size limit"),
        )

    hook_secret_header = request.headers.get("X-Hook-Secret")
    hook_signature_header = request.headers.get("X-Hook-Signature")

    secret_store = _get_secret_store(request)
    project_gid = _get_project_gid(request)

    # ── 1. Handshake ──────────────────────────────────────────────────────
    # Asana sends this exactly once, right after webhook registration. We
    # must echo the header back to confirm the subscription.
    if hook_secret_header:
        if secret_store is not None and project_gid:
            secret_store.set(project_gid, hook_secret_header)
        LOGGER.info("asana.webhook.handshake received project_gid=%s", project_gid)
        return Response(
            status_code=status.HTTP_200_OK,
            headers={"X-Hook-Secret": hook_secret_header},
        )

    # ── 2. Event delivery ─────────────────────────────────────────────────
    if secret_store is None or not project_gid:
        LOGGER.warning("asana.webhook.event received but secret store/project not configured")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": "not_configured"},
        )

    stored_secret = secret_store.get(project_gid)
    if not verify_signature(stored_secret or "", raw_body, hook_signature_header):
        LOGGER.warning("asana.webhook.event signature verification FAILED")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=error_body("INVALID_SIGNATURE", "Asana webhook signature verification failed"),
        )

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as exc:
        LOGGER.warning("asana.webhook.event JSON parse failed error=%s", exc)
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ignored", "reason": "bad_json"})

    completed_events = extract_completed_task_events(payload)
    if not completed_events:
        # Most Asana deliveries are irrelevant field changes we don't care
        # about — this is the expected common case, not an error.
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok", "acted_on": 0})

    engineering_service = _get_engineering_service(request)
    response_service = _get_response_service(request)
    idempotency_store = _get_idempotency_store(request)
    safety_gate = _get_safety_gate(request)
    for event in completed_events:
        background_tasks.add_task(
            _handle_task_completed,
            event.task_gid,
            engineering_service,
            response_service,
            idempotency_store,
            safety_gate,
        )

    LOGGER.info("asana.webhook.event queued task_completed_count=%d", len(completed_events))
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok", "acted_on": len(completed_events)})


# ── Background processing ─────────────────────────────────────────────────

async def _handle_task_completed(
    task_gid: str,
    engineering_service: Any,
    response_service: Any,
    idempotency_store: Any,
    safety_gate: Any = None,
) -> None:
    """
    Full L2 resolution loop: resolve engineering ticket, notify customer, close Freshdesk ticket.

    Never raises — runs as a detached BackgroundTask.
    """
    # ── 0. Idempotency ────────────────────────────────────────────────────────
    if idempotency_store is not None and idempotency_store.has_processed(task_gid):
        LOGGER.info("asana.webhook.task_completed: already processed, skipping task_gid=%s", task_gid)
        return

    if engineering_service is None:
        LOGGER.warning("asana.webhook.task_completed: no engineering_escalation_service wired")
        return

    try:
        # ── 1. Resolve internal engineering ticket ────────────────────────────
        ticket = engineering_service.get_ticket_by_external_id(task_gid)
        if ticket is None:
            LOGGER.info("asana.webhook.task_completed: no matching ticket external_id=%s", task_gid)
            return

        result = engineering_service.resolve_ticket(ticket.ticket_id)
        if not result.success:
            LOGGER.warning(
                "asana.webhook.task_completed: resolve_ticket failed ticket_id=%s error=%s",
                ticket.ticket_id, result.error_msg,
            )
            return

        # Mark as processed before Freshdesk writes — prevents duplicate
        # customer replies from Asana redeliveries within this process lifetime.
        if idempotency_store is not None:
            idempotency_store.mark_processed(task_gid)

        LOGGER.info(
            "asana.webhook.task_completed: resolved ticket_id=%s case_id=%s task_gid=%s",
            ticket.ticket_id, ticket.case_id, task_gid,
        )

        if response_service is None or not ticket.freshdesk_ticket_id:
            return

        # ── 2. ClosureFieldGuard — confirm cf_clients is set before status=4 ─
        # Mapping confirmed in sprint-2-6-3.md §3.2:
        #   cf_sop_status = "No SOP Available"  (escalated because no SOP existed)
        #   cf_resolution_classification = "Permanent Fix Applied by Dev"
        #   status = 4 (Resolved; 4→5 transition is automatic via Freshdesk SLA)
        #   type = "Issues" (most common AI-actionable ticket type)
        closure_custom_fields: dict[str, Any] = {
            "cf_sop_status": "No SOP Available",
            "cf_resolution_classification": "Permanent Fix Applied by Dev",
        }
        closure_payload: dict[str, Any] = {
            "custom_fields": closure_custom_fields,
            "status": 4,
            "type": "Issues",
        }

        current_ticket = await response_service.get_ticket(
            ticket.freshdesk_ticket_id,
            case_id=ticket.case_id,
        )

        guard = ClosureFieldGuard()
        decision = guard.guard_status_transition(closure_payload, current_ticket)
        if not decision.allowed:
            LOGGER.warning(
                "asana.webhook.task_completed: ClosureFieldGuard blocked ticket_id=%s reason=%s",
                ticket.freshdesk_ticket_id, decision.reason,
            )
            await response_service.add_internal_note(
                ticket.freshdesk_ticket_id,
                (
                    "<p><strong>Engineering marked the linked Asana task complete "
                    "but automatic closure was blocked.</strong></p>"
                    f"<p>Guard reason: {decision.reason}</p>"
                    "<p>Please manually populate the required closure fields and resolve this ticket.</p>"
                ),
                case_id=ticket.case_id,
            )
            return

        # ── 3. Notify customer, then close ticket ─────────────────────────────
        customer_reply = (
            "<p>Hi,</p>"
            "<p>We're happy to let you know that the issue you reported has been resolved "
            "by our engineering team.</p>"
            "<p>The fix has been deployed. Please verify on your end and let us know if "
            "you experience any further problems.</p>"
            "<p>Thank you for your patience.</p>"
        )

        # Sprint 2.63.1: ReplySafetyGate — fixed, deterministic template
        # (confidence=1.0), but still subject to kill-switch, duplicate, and
        # force-escalation checks. If blocked, do NOT close the ticket either —
        # post an internal note and leave it for manual handling, same shape as
        # the ClosureFieldGuard-blocked branch above.
        if safety_gate is None:
            LOGGER.error(
                "asana.webhook.task_completed: reply_safety_gate.NOT_WIRED — "
                "refusing to auto-send freshdesk_ticket_id=%s", ticket.freshdesk_ticket_id,
            )
            await response_service.add_internal_note(
                ticket.freshdesk_ticket_id,
                build_draft_reply_note(
                    customer_reply,
                    reason_not_auto_sent="ReplySafetyGate not wired on this instance",
                ),
                case_id=ticket.case_id,
            )
            return

        decision = safety_gate.check(
            ticket_id=str(ticket.freshdesk_ticket_id),
            body_html=customer_reply,
            confidence=1.0,
            impact=None,
        )
        if not decision.allowed:
            LOGGER.warning(
                "asana.webhook.task_completed: reply_safety_gate.BLOCKED "
                "freshdesk_ticket_id=%s outcome=%s reason=%s",
                ticket.freshdesk_ticket_id, decision.outcome.value, decision.reason,
            )
            if decision.should_draft:
                await response_service.add_internal_note(
                    ticket.freshdesk_ticket_id,
                    build_draft_reply_note(customer_reply, reason_not_auto_sent=decision.reason),
                    case_id=ticket.case_id,
                )
            return

        await response_service.send_customer_reply(
            ticket.freshdesk_ticket_id,
            customer_reply,
            case_id=ticket.case_id,
        )

        # Combined PUT: custom_fields + status=4 + type="Issues" in a single call
        # (ticket_lifecycle.md §6 requires all required fields + status in one PUT).
        await response_service.update_ticket_fields(
            ticket.freshdesk_ticket_id,
            closure_custom_fields,
            status=4,
            ticket_type="Issues",
            case_id=ticket.case_id,
        )

        # Sprint 2.63.2: board-level "Done" signal on the Asana task itself,
        # distinct from the `completed` checkbox dev already set — never
        # raises, never blocks closure if it fails.
        engineering_service.notify_asana_progress(ticket, "Done")

        LOGGER.info(
            "asana.webhook.task_completed: closed freshdesk_ticket_id=%s case_id=%s",
            ticket.freshdesk_ticket_id, ticket.case_id,
        )

    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("asana.webhook.task_completed: unexpected error task_gid=%s error=%s", task_gid, exc)
