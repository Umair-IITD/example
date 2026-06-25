"""
tests/test_sprint2282_unknown_tenant.py

Sprint 2.28.2 Part 5: Unknown Tenant Safe Handling.

Before this sprint, an UNKNOWN_CLIENT error in the ticket-created handler caused a
silent drop — no Freshdesk note, no audit event, no human visibility.

After Sprint 2.28.2:
  - HandlerResult returns error_code="UNKNOWN_CLIENT" and detail={"domain": ...}
  - The async background task detects UNKNOWN_CLIENT and posts an internal Freshdesk
    note via response_service.add_internal_note()
  - Automation is never executed for unknown tenants
  - Audit event CLIENT_RESOLUTION_FAILED is written

Coverage:
  1. Handler returns UNKNOWN_CLIENT when resolver raises
  2. Handler result detail contains domain (no email — PII protection)
  3. CLIENT_RESOLUTION_FAILED audit event written
  4. Automation NOT executed for unknown tenant
  5. Route background task calls add_internal_note on UNKNOWN_CLIENT
  6. Note body mentions domain, not email
  7. No response_service → warning logged, no crash
  8. Internal note is posted ONCE per unknown tenant event
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

os.environ.setdefault("RAG_API_KEY", "test-2282-unknown-tenant")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.handlers import FreshdeskTicketCreatedHandler, HandlerResult
from freshdesk.idempotency import WebhookIdempotencyStore
from freshdesk.conversation_state import ConversationStateStore


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _ticket_payload(
    ticket_id: int = 55001,
    email: str = "user@unknownclient.io",
) -> dict:
    return {
        "freshdesk_webhook": {
            "id": ticket_id,
            "subject": "Help needed",
            "description": "desc",
            "description_text": "desc",
            "status": 2,
            "priority": 2,
            "ticket_type": "Issues",
            "created_at": "2026-06-19T10:00:00Z",
            "requester_email": email,
            "requester_name": "Unknown User",
            "tags": "",
            "ticket_custom_fields": {},
            "attachments": [],
        }
    }


def _handler_with_failing_resolver(
    *,
    audit=None,
    orchestrator=None,
) -> FreshdeskTicketCreatedHandler:
    idem = WebhookIdempotencyStore()
    conv = ConversationStateStore()

    resolver = MagicMock()
    resolver.resolve.side_effect = ValueError("Client not found for domain unknownclient.io")

    return FreshdeskTicketCreatedHandler(
        idempotency_store=idem,
        conversation_store=conv,
        client_resolver=resolver,
        ticket_orchestrator=orchestrator,
        audit_logger=audit,
    )


# ── Section 1: HandlerResult for unknown tenant ───────────────────────────────

class TestHandlerResultUnknownTenant:
    def test_returns_failure(self):
        h = _handler_with_failing_resolver()
        result = h.handle(_ticket_payload())
        assert result.success is False

    def test_error_code_unknown_client(self):
        h = _handler_with_failing_resolver()
        result = h.handle(_ticket_payload())
        assert result.error_code == "UNKNOWN_CLIENT"

    def test_ticket_id_present_in_result(self):
        h = _handler_with_failing_resolver()
        result = h.handle(_ticket_payload(ticket_id=55001))
        assert result.ticket_id == "55001"

    def test_detail_contains_domain_not_email(self):
        h = _handler_with_failing_resolver()
        result = h.handle(_ticket_payload(email="agent@secretcorp.net"))
        assert result.detail is not None
        domain = result.detail.get("domain", "")
        assert "secretcorp.net" in domain
        assert "agent" not in domain  # PII: local part must not appear

    def test_detail_domain_extracted_correctly(self):
        h = _handler_with_failing_resolver()
        result = h.handle(_ticket_payload(email="someone@acme.com"))
        assert result.detail["domain"] == "acme.com"


# ── Section 2: Audit event ────────────────────────────────────────────────────

class TestUnknownTenantAuditEvent:
    def test_client_resolution_failed_audit_written(self):
        from case_engine.models import AuditEventType
        audit = MagicMock()
        audit._write = MagicMock()
        h = _handler_with_failing_resolver(audit=audit)
        h.handle(_ticket_payload())
        event_types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.CLIENT_RESOLUTION_FAILED in event_types

    def test_audit_domain_in_detail(self):
        audit = MagicMock()
        audit._write = MagicMock()
        h = _handler_with_failing_resolver(audit=audit)
        h.handle(_ticket_payload(email="user@targetdomain.org"))
        details = [c[0][0].action_detail for c in audit._write.call_args_list]
        assert any("targetdomain.org" in str(d) for d in details)


# ── Section 3: Orchestrator NOT called for unknown tenant ─────────────────────

class TestOrchestrationNotExecuted:
    def test_orchestrator_not_called(self):
        orchestrator = MagicMock()
        orchestrator.process_ticket = MagicMock()
        h = _handler_with_failing_resolver(orchestrator=orchestrator)
        h.handle(_ticket_payload())
        orchestrator.process_ticket.assert_not_called()


# ── Section 4: Route-level background task — internal note posted ─────────────

class TestRouteUnknownTenantNote:
    def _make_app(self) -> "FastAPI":
        from fastapi import FastAPI
        from api.routes.webhooks.freshdesk import router
        app = FastAPI()
        app.include_router(router)
        return app

    def test_internal_note_posted_on_unknown_client(self):
        """End-to-end: unknown client triggers add_internal_note in background task."""
        from fastapi.testclient import TestClient
        from freshdesk.response_service import FreshdeskResponseService

        posted_notes: list[dict] = []

        async def fake_note(ticket_id: str, body: str) -> dict:
            posted_notes.append({"ticket_id": ticket_id, "body": body})
            return {"id": 999}

        response_svc = MagicMock(spec=FreshdeskResponseService)
        response_svc.add_internal_note = fake_note

        resolver = MagicMock()
        resolver.resolve.side_effect = ValueError("Unknown client")

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        created_handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            client_resolver=resolver,
        )

        app = self._make_app()
        app.state.freshdesk_ticket_created_handler = created_handler
        app.state.freshdesk_response_service = response_svc

        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=_ticket_payload(ticket_id=55001, email="x@unknown.com"),
        )
        assert resp.status_code == 200
        assert len(posted_notes) == 1
        assert posted_notes[0]["ticket_id"] == "55001"

    def test_note_body_contains_domain_not_email(self):
        from fastapi.testclient import TestClient
        from freshdesk.response_service import FreshdeskResponseService

        posted_bodies: list[str] = []

        async def fake_note(ticket_id: str, body: str) -> dict:
            posted_bodies.append(body)
            return {"id": 999}

        response_svc = MagicMock(spec=FreshdeskResponseService)
        response_svc.add_internal_note = fake_note

        resolver = MagicMock()
        resolver.resolve.side_effect = ValueError("Unknown client")

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        created_handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            client_resolver=resolver,
        )

        app = self._make_app()
        app.state.freshdesk_ticket_created_handler = created_handler
        app.state.freshdesk_response_service = response_svc

        client = TestClient(app)
        client.post(
            "/webhooks/freshdesk/ticket-created",
            json=_ticket_payload(ticket_id=55002, email="user@secretcorp.net"),
        )
        assert len(posted_bodies) == 1
        body = posted_bodies[0]
        assert "secretcorp.net" in body
        assert "user@secretcorp.net" not in body  # PII protection

    def test_no_response_service_no_crash(self):
        """If response_service is not wired, no crash — just a warning log."""
        from fastapi.testclient import TestClient

        resolver = MagicMock()
        resolver.resolve.side_effect = ValueError("Unknown client")

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        created_handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            client_resolver=resolver,
        )

        app = self._make_app()
        app.state.freshdesk_ticket_created_handler = created_handler
        # Intentionally NOT setting freshdesk_response_service

        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=_ticket_payload(ticket_id=55003),
        )
        # Must still return 200 and not crash
        assert resp.status_code == 200

    def test_note_posted_exactly_once(self):
        """Each unknown tenant event posts exactly one internal note."""
        from fastapi.testclient import TestClient
        from freshdesk.response_service import FreshdeskResponseService

        posted_notes: list[dict] = []

        async def fake_note(ticket_id: str, body: str) -> dict:
            posted_notes.append({"ticket_id": ticket_id, "body": body})
            return {"id": 999}

        response_svc = MagicMock(spec=FreshdeskResponseService)
        response_svc.add_internal_note = fake_note

        resolver = MagicMock()
        resolver.resolve.side_effect = ValueError("Unknown client")

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        created_handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            client_resolver=resolver,
        )

        app = self._make_app()
        app.state.freshdesk_ticket_created_handler = created_handler
        app.state.freshdesk_response_service = response_svc

        client = TestClient(app)
        # Two identical payloads — second is duplicate → idempotency blocks second note
        payload = _ticket_payload(ticket_id=55004, email="x@ghost.io")
        client.post("/webhooks/freshdesk/ticket-created", json=payload)
        client.post("/webhooks/freshdesk/ticket-created", json=payload)
        # Second call is duplicate → skipped → no second note
        assert len(posted_notes) == 1
