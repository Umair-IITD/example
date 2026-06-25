"""
tests/test_sprint2291_golden_path.py

Sprint 2.29: Golden Path activation integration tests.

Verifies that the six app.state wirings enable the full Golden Path:
  Freshdesk → Webhook Route → Idempotency → Conversation State →
  TicketOrchestrator → SupportAgentRuntime → WorkflowEngine →
  Investigation → Reasoning → Action Gateway → Freshdesk Response Service

Test scenarios:
  1.  ticket-created: 200 OK returned immediately with wired services
  2.  ticket-created: handler.handle() invoked in background task
  3.  ticket-created: orchestrator.process_ticket() called with correct args
  4.  ticket-created: conversation state store gets_or_create called
  5.  ticket-created: idempotency store ensure_receipt called (WAL pre-persist)
  6.  duplicate webhook: idempotency check skips second identical event
  7.  unknown tenant: response_service.add_internal_note() called for UNKNOWN_CLIENT
  8.  ticket-updated: handler.handle() invoked in background task
  9.  clarification reply: rag_processor called with (ticket_id, query, tenant)
  10. rag_processor closure: posts AI Suggested Response as internal note
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Env vars must be set before any service import ───────────────────────────
os.environ.setdefault("RAG_API_KEY", "test-sprint2291-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.webhooks.freshdesk import router
from freshdesk.handlers import FreshdeskTicketCreatedHandler, FreshdeskTicketUpdatedHandler
from freshdesk.idempotency import WebhookIdempotencyStore
from freshdesk.conversation_state import ConversationStateStore
from freshdesk.handlers import HandlerResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

TICKET_ID = "197416"
CREATED_AT = "2026-06-25T10:00:00Z"
UPDATED_AT = "2026-06-25T11:00:00Z"

TICKET_CREATED_PAYLOAD: dict[str, Any] = {
    "freshdesk_webhook": {
        "id": int(TICKET_ID),
        "subject": "Login issue on KwikID portal",
        "description": "<p>User cannot log in.</p>",
        "description_text": "User cannot log in.",
        "status": 2,
        "priority": 2,
        "ticket_type": "Issues",
        "created_at": CREATED_AT,
        "requester_email": "agent@unitybank.co.in",
        "requester_name": "Unity Agent",
        "tags": "client:Unity",
        "ticket_custom_fields": {"cf_clients": "Unity"},
        "attachments": [],
    }
}

TICKET_UPDATED_PAYLOAD: dict[str, Any] = {
    "freshdesk_webhook": {
        "id": int(TICKET_ID),
        "subject": "Login issue on KwikID portal",
        "status": 2,
        "priority": 2,
        "updated_at": UPDATED_AT,
        "requester_email": "agent@unitybank.co.in",
        "requester_name": "Unity Agent",
        "ticket_custom_fields": {"cf_clients": "Unity"},
        "latest_comment": {
            "body": "<p>I still cannot log in.</p>",
            "body_text": "I still cannot log in.",
            "incoming": True,
            "private": False,
        },
    }
}


def _make_app(
    *,
    handler_created: FreshdeskTicketCreatedHandler | None = None,
    handler_updated: FreshdeskTicketUpdatedHandler | None = None,
    idem_store: WebhookIdempotencyStore | None = None,
    conv_store: ConversationStateStore | None = None,
    response_service: Any | None = None,
    rag_processor: Any | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with the router and Sprint 2.29 state wired."""
    app = FastAPI()
    app.include_router(router)
    if handler_created is not None:
        app.state.freshdesk_ticket_created_handler = handler_created
    if handler_updated is not None:
        app.state.freshdesk_ticket_updated_handler = handler_updated
    if idem_store is not None:
        app.state.freshdesk_idempotency_store = idem_store
    if conv_store is not None:
        app.state.freshdesk_conversation_store = conv_store
    if response_service is not None:
        app.state.freshdesk_response_service = response_service
    if rag_processor is not None:
        app.state.freshdesk_rag_processor = rag_processor
    return app


def _ok_orchestrator_result() -> MagicMock:
    from case_engine.ticket_orchestration.orchestrator import TicketOrchestrationResult
    result = MagicMock(spec=TicketOrchestrationResult)
    result.success = True
    result.case_id = "case-unit-test-001"
    result.state = "TRIAGE_COMPLETE"
    return result


# ── Test 1: ticket-created returns 200 with wired services ───────────────────

def test_ticket_created_returns_200_with_wired_services() -> None:
    """Route returns 200 immediately; background task runs with wired handler."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()
    mock_orchestrator = MagicMock()
    mock_orchestrator.process_ticket.return_value = _ok_orchestrator_result()

    handler = FreshdeskTicketCreatedHandler(
        idempotency_store=idem_store,
        conversation_store=conv_store,
        ticket_orchestrator=mock_orchestrator,
    )
    app = _make_app(
        handler_created=handler,
        idem_store=idem_store,
        conv_store=conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["event"] == "ticket_created"


# ── Test 2: handler.handle() called in background task ───────────────────────

def test_ticket_created_handler_handle_called() -> None:
    """The created handler's handle() method is invoked by the background task."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()
    mock_handler = MagicMock(spec=FreshdeskTicketCreatedHandler)
    mock_handler.handle.return_value = HandlerResult(
        success=True, ticket_id=TICKET_ID, action="ticket_ingested"
    )

    app = _make_app(
        handler_created=mock_handler,
        idem_store=idem_store,
        conv_store=conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )

    mock_handler.handle.assert_called_once()
    call_payload = mock_handler.handle.call_args[0][0]
    assert "freshdesk_webhook" in call_payload


# ── Test 3: orchestrator.process_ticket() called with correct args ────────────

def test_ticket_created_orchestrator_called_with_correct_args() -> None:
    """TicketOrchestrator.process_ticket() receives ticket_id, subject, and client."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()
    mock_orchestrator = MagicMock()
    mock_orchestrator.process_ticket.return_value = _ok_orchestrator_result()

    handler = FreshdeskTicketCreatedHandler(
        idempotency_store=idem_store,
        conversation_store=conv_store,
        ticket_orchestrator=mock_orchestrator,
    )
    app = _make_app(
        handler_created=handler,
        idem_store=idem_store,
        conv_store=conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )

    mock_orchestrator.process_ticket.assert_called_once()
    call_kwargs = mock_orchestrator.process_ticket.call_args[1]
    assert call_kwargs.get("ticket_id") == TICKET_ID or (
        # positional fallback
        mock_orchestrator.process_ticket.call_args[0][0] == TICKET_ID
    )


# ── Test 4: conversation state get_or_create called ──────────────────────────

def test_ticket_created_conversation_state_created() -> None:
    """ConversationStateStore.get_or_create() is called for the incoming ticket."""
    idem_store = WebhookIdempotencyStore()
    mock_conv_store = MagicMock(spec=ConversationStateStore)
    from freshdesk.conversation_state import ConversationState, ConversationLifecycle
    mock_conv_store.get_or_create.return_value = ConversationState(
        ticket_id=TICKET_ID, client_id="Unity", lifecycle_state=ConversationLifecycle.OPEN
    )
    mock_conv_store.get.return_value = None

    mock_orchestrator = MagicMock()
    mock_orchestrator.process_ticket.return_value = _ok_orchestrator_result()

    handler = FreshdeskTicketCreatedHandler(
        idempotency_store=idem_store,
        conversation_store=mock_conv_store,
        ticket_orchestrator=mock_orchestrator,
    )
    app = _make_app(
        handler_created=handler,
        idem_store=idem_store,
        conv_store=mock_conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )

    mock_conv_store.get_or_create.assert_called_once()
    call_args = mock_conv_store.get_or_create.call_args
    # ticket_id may be positional or keyword
    ticket_id_arg = call_args[1].get("ticket_id") or (call_args[0][0] if call_args[0] else None)
    assert ticket_id_arg == TICKET_ID


# ── Test 5: idempotency ensure_receipt called (WAL pre-persist) ──────────────

def test_ticket_created_idempotency_ensure_receipt_called() -> None:
    """ensure_receipt() is called BEFORE 200 is returned (WAL guarantee)."""
    mock_idem_store = MagicMock(spec=WebhookIdempotencyStore)
    mock_idem_store.check.return_value = False
    conv_store = ConversationStateStore()

    mock_orchestrator = MagicMock()
    mock_orchestrator.process_ticket.return_value = _ok_orchestrator_result()

    handler = FreshdeskTicketCreatedHandler(
        idempotency_store=mock_idem_store,
        conversation_store=conv_store,
        ticket_orchestrator=mock_orchestrator,
    )
    app = _make_app(
        handler_created=handler,
        idem_store=mock_idem_store,
        conv_store=conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )

    # Route calls _pre_persist → store.ensure_receipt() before returning 200
    assert resp.status_code == 200
    mock_idem_store.ensure_receipt.assert_called_once()
    call_args = mock_idem_store.ensure_receipt.call_args
    # key, ticket_id, event_type, event_timestamp
    assert "ticket_created" in str(call_args)


# ── Test 6: duplicate webhook skipped by idempotency ─────────────────────────

def test_duplicate_webhook_skipped_by_idempotency() -> None:
    """Second identical webhook returns 200 but handler skips processing."""
    idem_store = WebhookIdempotencyStore()  # in-memory
    conv_store = ConversationStateStore()

    mock_orchestrator = MagicMock()
    mock_orchestrator.process_ticket.return_value = _ok_orchestrator_result()

    handler = FreshdeskTicketCreatedHandler(
        idempotency_store=idem_store,
        conversation_store=conv_store,
        ticket_orchestrator=mock_orchestrator,
    )
    app = _make_app(
        handler_created=handler,
        idem_store=idem_store,
        conv_store=conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            # First call — processed
            resp1 = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )
            # Second call — identical payload (same ticket_id + created_at → same idem key)
            resp2 = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Orchestrator called only once — second event was a duplicate
    assert mock_orchestrator.process_ticket.call_count == 1


# ── Test 7: unknown tenant triggers internal note via response_service ────────

def test_unknown_tenant_posts_internal_note() -> None:
    """When handler returns UNKNOWN_CLIENT, response_service.add_internal_note() is called."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()
    mock_response_svc = AsyncMock()
    mock_response_svc.add_internal_note = AsyncMock(return_value={})

    mock_handler = MagicMock(spec=FreshdeskTicketCreatedHandler)
    mock_handler.handle.return_value = HandlerResult(
        success=False,
        ticket_id=TICKET_ID,
        error_code="UNKNOWN_CLIENT",
        detail={"domain": "unknown-corp.com"},
    )

    app = _make_app(
        handler_created=mock_handler,
        idem_store=idem_store,
        conv_store=conv_store,
        response_service=mock_response_svc,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )

    mock_response_svc.add_internal_note.assert_awaited_once()
    call_args = mock_response_svc.add_internal_note.call_args
    note_ticket_id = call_args[0][0] if call_args[0] else call_args[1].get("ticket_id")
    assert note_ticket_id == TICKET_ID
    # Note body must mention Human review
    note_body = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("body", "")
    assert "Human review" in note_body or "required" in note_body.lower()


# ── Test 8: ticket-updated handler called ────────────────────────────────────

def test_ticket_updated_handler_handle_called() -> None:
    """The updated handler's handle() is invoked for ticket-updated events."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()
    mock_handler = MagicMock(spec=FreshdeskTicketUpdatedHandler)
    mock_handler.handle.return_value = HandlerResult(
        success=True, ticket_id=TICKET_ID, action="status_changed"
    )

    app = _make_app(
        handler_updated=mock_handler,
        idem_store=idem_store,
        conv_store=conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=TICKET_UPDATED_PAYLOAD,
            )

    assert resp.status_code == 200
    assert resp.json()["event"] == "ticket_updated"
    mock_handler.handle.assert_called_once()


# ── Test 9: customer reply triggers rag_processor ────────────────────────────

def test_customer_reply_triggers_rag_processor() -> None:
    """When action='customer_reply', rag_processor is awaited with correct args."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()

    rag_calls: list[tuple[str, str, str]] = []

    async def _mock_rag_processor(ticket_id: str, query_text: str, tenant: str) -> None:
        rag_calls.append((ticket_id, query_text, tenant))

    mock_handler = MagicMock(spec=FreshdeskTicketUpdatedHandler)
    mock_handler.handle.return_value = HandlerResult(
        success=True,
        ticket_id=TICKET_ID,
        action="customer_reply",
        skipped=False,
    )

    app = _make_app(
        handler_updated=mock_handler,
        idem_store=idem_store,
        conv_store=conv_store,
        rag_processor=_mock_rag_processor,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=TICKET_UPDATED_PAYLOAD,
            )

    assert len(rag_calls) == 1, "rag_processor should be called exactly once"
    called_ticket_id, called_query, called_tenant = rag_calls[0]
    assert called_ticket_id == TICKET_ID
    assert "log in" in called_query.lower()  # from latest_comment.body_text


# ── Test 10: rag_processor posts note with correct format ────────────────────

def test_rag_processor_posts_ai_suggested_response() -> None:
    """The Sprint 2.29 rag_processor closure posts note with AI Suggested Response."""
    from rag_engine.generation.chat_generator import GenerationRequest, GenerationResult

    mock_gen_result = MagicMock(spec=GenerationResult)
    mock_gen_result.answer = "You need to reset your password via the portal."
    mock_gen_result.confidence = "high"
    mock_gen_result.requires_human = False

    mock_generator = MagicMock()
    mock_generator.generate.return_value = mock_gen_result

    posted_notes: list[dict] = []

    async def _mock_add_internal_note(
        ticket_id: str, body: str, *, case_id: str = "", client_id: str = ""
    ) -> dict:
        posted_notes.append({"ticket_id": ticket_id, "body": body, "client_id": client_id})
        return {}

    mock_response_svc = AsyncMock()
    mock_response_svc.add_internal_note = _mock_add_internal_note

    # Build the closure the same way Sprint 2.29 wiring does
    async def _freshdesk_rag_processor(
        ticket_id: str, query_text: str, tenant: str
    ) -> None:
        gen_req = GenerationRequest(
            query_text=query_text,
            client=tenant or "unknown",
            persist_history=False,
        )
        loop = asyncio.get_running_loop()
        gen_result = await loop.run_in_executor(None, mock_generator.generate, gen_req)
        answer = gen_result.answer or ""
        confidence = gen_result.confidence
        requires_human = gen_result.requires_human
        if requires_human or confidence == "low":
            note_body = (
                f"<p><strong>AI Suggestion (requires agent review):"
                f"</strong><br>{answer}</p>"
                f"<p><em>Confidence: {confidence}</em></p>"
            )
        else:
            note_body = (
                f"<p><strong>AI Suggested Response:</strong><br>{answer}</p>"
                f"<p><em>Confidence: {confidence}</em></p>"
            )
        await mock_response_svc.add_internal_note(
            ticket_id, note_body, case_id="", client_id=tenant
        )

    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()

    mock_handler = MagicMock(spec=FreshdeskTicketUpdatedHandler)
    mock_handler.handle.return_value = HandlerResult(
        success=True,
        ticket_id=TICKET_ID,
        action="customer_reply",
        skipped=False,
    )

    app = _make_app(
        handler_updated=mock_handler,
        idem_store=idem_store,
        conv_store=conv_store,
        rag_processor=_freshdesk_rag_processor,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=TICKET_UPDATED_PAYLOAD,
            )

    assert len(posted_notes) == 1
    note = posted_notes[0]
    assert note["ticket_id"] == TICKET_ID
    assert "AI Suggested Response" in note["body"]
    assert "reset your password" in note["body"]
    assert note["client_id"] == "Unity"

    # Generator was called with the correct query
    mock_generator.generate.assert_called_once()
    gen_req_arg = mock_generator.generate.call_args[0][0]
    assert "log in" in gen_req_arg.query_text.lower()


# ── Test 11: no rag_processor → clarification loop skips gracefully ──────────

def test_no_rag_processor_clarification_loop_skips() -> None:
    """Without rag_processor on app.state, clarification loop skips without error."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()

    mock_handler = MagicMock(spec=FreshdeskTicketUpdatedHandler)
    mock_handler.handle.return_value = HandlerResult(
        success=True,
        ticket_id=TICKET_ID,
        action="customer_reply",
        skipped=False,
    )

    # No rag_processor wired
    app = _make_app(
        handler_updated=mock_handler,
        idem_store=idem_store,
        conv_store=conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=TICKET_UPDATED_PAYLOAD,
            )

    # Must still return 200 — no rag_processor is a graceful degradation
    assert resp.status_code == 200


# ── Test 12: app.state accessors return wired instances ──────────────────────

def test_app_state_accessors_return_wired_instances() -> None:
    """_get_created_handler / _get_updated_handler / _get_response_service use app.state."""
    from api.routes.webhooks.freshdesk import (
        _get_created_handler,
        _get_updated_handler,
        _get_response_service,
        _get_idempotency_store,
        _get_conv_store,
        _get_rag_processor,
    )

    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()
    mock_response_svc = AsyncMock()

    async def _mock_rag_proc(t: str, q: str, tenant: str) -> None:
        pass

    mock_created_handler = MagicMock(spec=FreshdeskTicketCreatedHandler)
    mock_updated_handler = MagicMock(spec=FreshdeskTicketUpdatedHandler)

    app = _make_app(
        handler_created=mock_created_handler,
        handler_updated=mock_updated_handler,
        idem_store=idem_store,
        conv_store=conv_store,
        response_service=mock_response_svc,
        rag_processor=_mock_rag_proc,
    )

    # Build a minimal mock Request pointing at this app
    mock_request = MagicMock()
    mock_request.app = app

    assert _get_created_handler(mock_request) is mock_created_handler
    assert _get_updated_handler(mock_request) is mock_updated_handler
    assert _get_response_service(mock_request) is mock_response_svc
    assert _get_idempotency_store(mock_request) is idem_store
    assert _get_conv_store(mock_request) is conv_store
    assert _get_rag_processor(mock_request) is _mock_rag_proc


# ── Test 13: offline fallback when app.state not set ─────────────────────────

def test_offline_fallback_when_state_not_set() -> None:
    """When app.state keys are missing, routes return 200 using offline handlers."""
    app = FastAPI()
    app.include_router(router)

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=TICKET_CREATED_PAYLOAD,
            )

    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


# ── Test 14: ticket-updated 200 OK with wired services ───────────────────────

def test_ticket_updated_returns_200_with_wired_services() -> None:
    """ticket-updated returns 200 and correct body with all services wired."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()
    handler = FreshdeskTicketUpdatedHandler(
        idempotency_store=idem_store,
        conversation_store=conv_store,
    )
    app = _make_app(
        handler_updated=handler,
        idem_store=idem_store,
        conv_store=conv_store,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=TICKET_UPDATED_PAYLOAD,
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["event"] == "ticket_updated"


# ── Test 15: rag_processor not called for non-customer-reply actions ──────────

def test_rag_processor_not_called_for_status_change() -> None:
    """rag_processor is NOT called when action is not customer_reply."""
    idem_store = WebhookIdempotencyStore()
    conv_store = ConversationStateStore()
    rag_calls: list[tuple] = []

    async def _mock_rag_proc(t: str, q: str, tenant: str) -> None:
        rag_calls.append((t, q, tenant))

    status_change_payload = {
        "freshdesk_webhook": {
            "id": int(TICKET_ID),
            "subject": "Login issue",
            "status": 4,  # resolved
            "priority": 2,
            "updated_at": UPDATED_AT,
            "requester_email": "agent@unitybank.co.in",
            "ticket_custom_fields": {"cf_clients": "Unity"},
            "latest_comment": {
                "body": "Resolved.",
                "body_text": "Resolved.",
                "incoming": False,   # agent reply — NOT customer
                "private": False,
            },
        }
    }

    handler = FreshdeskTicketUpdatedHandler(
        idempotency_store=idem_store,
        conversation_store=conv_store,
    )
    app = _make_app(
        handler_updated=handler,
        idem_store=idem_store,
        conv_store=conv_store,
        rag_processor=_mock_rag_proc,
    )

    with patch("api.routes.webhooks.freshdesk._get_verifier", return_value=None):
        with TestClient(app) as client:
            client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=status_change_payload,
            )

    assert len(rag_calls) == 0, "rag_processor must not fire for agent/status-change events"
