"""
api/routes/webhook.py

POST /webhook/{client} — inbound Freshdesk webhook endpoint.

Flow:
  1. Read raw request body (needed for HMAC over exact bytes)
  2. Validate HMAC signature via WebhookProcessor.validate()
  3. Parse JSON payload defensively
  4. Build WebhookEvent from request data
  5. Call WebhookProcessor.parse() → ActionProposal | None
  6. If proposal: create synthetic Case + call gateway.propose()
  7. Catch DuplicateActionError → idempotent 200 (actions_created=0)
  8. Return 200 with summary

Security:
  - Raw body is read before JSON parsing — HMAC must be computed over
    the exact bytes Freshdesk signed
  - Signature header is read from X-Freshdesk-Signature
  - hmac.compare_digest() is used inside FreshdeskWebhookProcessor (never here)
  - We never log the signature value or the raw body

Error codes:
  INVALID_SIGNATURE  — HMAC check failed → 403
  INVALID_PAYLOAD    — JSON parsing failed → 400
  PARSE_ERROR        — processor raised (should not happen by contract) → 400
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from case_engine.action_gateway import DuplicateActionError
from case_engine.models import Case

LOGGER = logging.getLogger(__name__)

router = APIRouter()


# NON_PRODUCTION_PATH: Sprint 2.1 legacy entry. Bypasses TicketOrchestrator.
# Use POST /tickets/process (api/routes/tickets.py) for new integrations.
@router.post("/webhook/{client}")
async def receive_webhook(client: str, request: Request) -> JSONResponse:
    """
    Accept and process an inbound Freshdesk webhook.

    This handler is async because reading the raw request body requires
    `await request.body()`. All other handlers in this package are sync.
    """
    stack = request.app.state.stack
    processor = request.app.state.processor

    # ── 1. Read raw body (needed for HMAC) ────────────────────────────────────
    raw_body = await request.body()

    # ── 2. Validate HMAC ──────────────────────────────────────────────────────
    signature = request.headers.get("x-freshdesk-signature", "")
    validation = processor.validate(raw_body, signature)
    if not validation.is_valid:
        LOGGER.warning(
            "webhook.receive: HMAC validation failed client=%s reason=%s",
            client, validation.reason,
        )
        return JSONResponse(
            status_code=403,
            content=error_body("INVALID_SIGNATURE", "Webhook signature verification failed"),
        )

    # ── 3. Parse JSON defensively ─────────────────────────────────────────────
    try:
        payload: dict = await request.json() if raw_body else {}
    except Exception:
        try:
            import json
            payload = json.loads(raw_body)
        except Exception as exc:
            LOGGER.warning("webhook.receive: JSON parse failed client=%s error=%s", client, exc)
            return JSONResponse(
                status_code=400,
                content=error_body("INVALID_PAYLOAD", "Request body is not valid JSON"),
            )

    # ── 4. Build WebhookEvent ─────────────────────────────────────────────────
    from webhook.models import WebhookEvent
    event_type = payload.get("event_type") or payload.get("type") or "unknown"
    ticket_id = str(
        payload.get("ticket_id")
        or (payload.get("freshdesk_webhook") or {}).get("ticket_id")
        or payload.get("id")
        or ""
    )
    event_id = str(payload.get("event_id") or uuid.uuid4())

    event = WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        ticket_id=ticket_id,
        client=client,
        payload=payload,
    )

    # ── 5. Parse event → proposal ─────────────────────────────────────────────
    proposal = processor.parse(event)
    if proposal is None:
        LOGGER.debug(
            "webhook.receive: event_type=%s not actionable — client=%s", event_type, client
        )
        return JSONResponse(
            status_code=200,
            content={
                "client": client,
                "event_type": event_type,
                "actions_created": 0,
                "note": "event received but not actionable",
            },
        )

    # ── 6. Propose action via gateway ─────────────────────────────────────────
    # Deterministic case_id: same (client, ticket_id) always yields the same case_id.
    # This makes the idempotency key stable across duplicate webhook deliveries.
    case_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"webhook-case:{client}:{event.ticket_id}"))
    case = Case(
        case_id=case_id,
        ticket_id=event.ticket_id,
        client=client,
    )

    try:
        action = stack.gateway.propose(case, proposal)
        LOGGER.info(
            "webhook.receive: action proposed action_id=%s state=%s client=%s",
            action.action_id, action.current_state.value, client,
        )
        return JSONResponse(
            status_code=200,
            content={
                "client": client,
                "event_type": event_type,
                "actions_created": 1,
                "action_id": action.action_id,
                "action_state": action.current_state.value,
            },
        )

    # ── 7. Idempotent duplicate ───────────────────────────────────────────────
    except DuplicateActionError as exc:
        LOGGER.info(
            "webhook.receive: duplicate action action_id=%s client=%s — idempotent 200",
            exc.existing.action_id, client,
        )
        return JSONResponse(
            status_code=200,
            content={
                "client": client,
                "event_type": event_type,
                "actions_created": 0,
                "action_id": exc.existing.action_id,
                "action_state": exc.existing.current_state.value,
                "note": "duplicate — existing action returned",
            },
        )

    except Exception as exc:
        LOGGER.exception(
            "webhook.receive: unexpected error client=%s error=%s", client, exc
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to process webhook event"),
        )
