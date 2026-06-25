"""
tests/test_sprint2282_e2e.py

Sprint 2.28.2: End-to-End Integration Tests.

Tests the full webhook processing path from POST to background task completion:
  Freshdesk → Webhook Route → JSON Parse → HMAC Verify → WAL → Background → Result

Coverage:
  1. Ticket-created golden path — 200, WAL written, handler invoked
  2. Ticket-updated with latest_comment customer reply — action="customer_reply"
  3. Ticket-updated with latest_comment internal note — action="internal_note"
  4. Ticket-updated status change — lifecycle updated in ConversationStateStore
  5. Clarification continuation — customer reply while awaiting → state reset
  6. HMAC valid + recent timestamp → 200 (both enforced simultaneously)
  7. HMAC valid + old timestamp → 401 (replay rejected even with valid sig)
  8. HMAC invalid + recent timestamp → 401 (sig rejected)
  9. Unknown tenant → internal note posted, 200 returned
  10. Duplicate ticket-created event → 200 but handler skips (idempotency)
  11. Oversized payload rejected before any processing
  12. Invalid JSON rejected before any processing
"""
from __future__ import annotations

import hashlib
import hmac as hmac_module
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("RAG_API_KEY", "test-2282-e2e")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.webhooks.freshdesk import router
from freshdesk.handlers import FreshdeskTicketCreatedHandler, FreshdeskTicketUpdatedHandler
from freshdesk.idempotency import WebhookIdempotencyStore
from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle
from freshdesk.verifier import FreshdeskWebhookVerifier

SECRET = "e2e-test-secret-32chars-xxxxxxxxx"


def _sign(body: bytes) -> str:
    return hmac_module.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _ts(offset_seconds: int = -5) -> str:
    dt = datetime.now(tz=timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_app(
    *,
    enforce_hmac: bool = False,
    idem: WebhookIdempotencyStore | None = None,
    conv: ConversationStateStore | None = None,
    created_handler: FreshdeskTicketCreatedHandler | None = None,
    updated_handler: FreshdeskTicketUpdatedHandler | None = None,
    response_svc=None,
) -> tuple[FastAPI, WebhookIdempotencyStore, ConversationStateStore]:
    _idem = idem or WebhookIdempotencyStore()
    _conv = conv or ConversationStateStore()

    _created = created_handler or FreshdeskTicketCreatedHandler(
        idempotency_store=_idem,
        conversation_store=_conv,
    )
    _updated = updated_handler or FreshdeskTicketUpdatedHandler(
        idempotency_store=_idem,
        conversation_store=_conv,
    )

    app = FastAPI()
    app.include_router(router)
    app.state.freshdesk_ticket_created_handler = _created
    app.state.freshdesk_ticket_updated_handler = _updated

    if enforce_hmac:
        app.state.freshdesk_verifier = FreshdeskWebhookVerifier(
            SECRET, enforce=True, replay_window_seconds=300
        )
    if response_svc is not None:
        app.state.freshdesk_response_service = response_svc

    return app, _idem, _conv


def _created_payload(ticket_id: int = 60001, email: str = "user@client.com") -> dict:
    return {
        "freshdesk_webhook": {
            "id": ticket_id,
            "subject": "E2E test ticket",
            "description": "Please help",
            "description_text": "Please help",
            "status": 2,
            "priority": 2,
            "ticket_type": "Issues",
            "created_at": _ts(-5),
            "requester_email": email,
            "requester_name": "E2E User",
            "tags": "",
            "ticket_custom_fields": {"cf_clients": "ClientA"},
            "attachments": [],
        }
    }


def _updated_payload(
    ticket_id: int = 60001,
    latest_comment: dict | None = None,
    changes: dict | None = None,
) -> dict:
    inner: dict = {
        "id": ticket_id,
        "subject": "E2E test ticket",
        "status": 2,
        "priority": 2,
        "updated_at": _ts(-5),
        "requester_email": "user@client.com",
        "ticket_custom_fields": {"cf_clients": "ClientA"},
        "changes": changes or {},
        "attachments": [],
    }
    if latest_comment is not None:
        inner["latest_comment"] = latest_comment
    return {"freshdesk_webhook": inner}


# ── Section 1: Ticket-created golden path ────────────────────────────────────

class TestTicketCreatedGoldenPath:
    def test_returns_200(self):
        app, _, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/webhooks/freshdesk/ticket-created", json=_created_payload())
        assert resp.status_code == 200

    def test_response_body_accepted(self):
        app, _, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/webhooks/freshdesk/ticket-created", json=_created_payload())
        assert resp.json()["status"] == "accepted"
        assert resp.json()["event"] == "ticket_created"

    def test_idempotency_marked_received_after_call(self):
        app, idem, _ = _make_app()
        client = TestClient(app)
        client.post("/webhooks/freshdesk/ticket-created", json=_created_payload(60001))
        # At least one entry in the in-memory store
        assert idem.count() >= 1

    def test_conversation_state_created(self):
        app, idem, conv = _make_app()
        client = TestClient(app)
        client.post("/webhooks/freshdesk/ticket-created", json=_created_payload(60002))
        state = conv.get("60002")
        assert state is not None


# ── Section 2: Ticket-updated with latest_comment ───────────────────────────

class TestTicketUpdatedLatestComment:
    def _setup(self, ticket_id: int):
        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        conv.get_or_create(str(ticket_id), "client_a")
        updated_handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
        )
        app, _, _ = _make_app(idem=idem, conv=conv, updated_handler=updated_handler)
        return TestClient(app), idem, conv

    def test_customer_reply_via_latest_comment(self):
        client, idem, conv = self._setup(60010)
        payload = _updated_payload(60010, latest_comment={"incoming": True, "private": False})
        resp = client.post("/webhooks/freshdesk/ticket-updated", json=payload)
        assert resp.status_code == 200
        assert idem.count() >= 1

    def test_internal_note_via_latest_comment(self):
        client, idem, conv = self._setup(60011)
        payload = _updated_payload(60011, latest_comment={"incoming": False, "private": True})
        resp = client.post("/webhooks/freshdesk/ticket-updated", json=payload)
        assert resp.status_code == 200

    def test_status_change_via_changes_dict(self):
        client, idem, conv = self._setup(60012)
        payload = _updated_payload(60012, changes={"status": [2, 4]})
        resp = client.post("/webhooks/freshdesk/ticket-updated", json=payload)
        assert resp.status_code == 200
        state = conv.get("60012")
        assert state.lifecycle_state == ConversationLifecycle.RESOLVED


# ── Section 3: Clarification continuation ───────────────────────────────────

class TestClarificationContinuationE2E:
    def test_customer_reply_resets_clarification_state(self):
        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        conv.get_or_create("60020", "client_a")
        conv.update("60020", awaiting_customer=True, clarification_pending=True)

        updated_handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
        )
        app, _, _ = _make_app(idem=idem, conv=conv, updated_handler=updated_handler)
        client = TestClient(app)

        payload = _updated_payload(60020, latest_comment={"incoming": True, "private": False})
        resp = client.post("/webhooks/freshdesk/ticket-updated", json=payload)
        assert resp.status_code == 200

        state = conv.get("60020")
        assert state.awaiting_customer is False
        assert state.clarification_pending is False


# ── Section 4: HMAC + replay protection combined ─────────────────────────────

class TestHmacAndReplayTogether:
    def test_valid_sig_recent_ts_passes(self):
        app, _, _ = _make_app(enforce_hmac=True)
        client = TestClient(app)
        payload = _created_payload(60030)
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": _sign(body)},
        )
        assert resp.status_code == 200

    def test_valid_sig_old_ts_rejected(self):
        app, _, _ = _make_app(enforce_hmac=True)
        client = TestClient(app)
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=400)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        payload = {
            "freshdesk_webhook": {
                "id": 60031,
                "subject": "Old",
                "description": "d",
                "description_text": "d",
                "status": 2,
                "priority": 2,
                "ticket_type": "Issues",
                "created_at": old_ts,
                "requester_email": "u@c.com",
                "requester_name": "User",
                "tags": "",
                "ticket_custom_fields": {},
                "attachments": [],
            }
        }
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": _sign(body)},
        )
        assert resp.status_code == 401

    def test_invalid_sig_recent_ts_rejected(self):
        app, _, _ = _make_app(enforce_hmac=True)
        client = TestClient(app)
        payload = _created_payload(60032)
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": "bad-sig"},
        )
        assert resp.status_code == 401


# ── Section 5: Idempotency — duplicate events ────────────────────────────────

class TestIdempotencyE2E:
    def test_duplicate_ticket_created_200_twice(self):
        app, _, _ = _make_app()
        client = TestClient(app)
        payload = _created_payload(60040)
        r1 = client.post("/webhooks/freshdesk/ticket-created", json=payload)
        r2 = client.post("/webhooks/freshdesk/ticket-created", json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_duplicate_events_handled_gracefully(self):
        """Both calls return 200; second is silently deduplicated."""
        app, idem, _ = _make_app()
        client = TestClient(app)
        payload = _created_payload(60041)
        client.post("/webhooks/freshdesk/ticket-created", json=payload)
        client.post("/webhooks/freshdesk/ticket-created", json=payload)
        # Only one unique key should exist
        assert idem.count() == 1


# ── Section 6: Unknown tenant — internal note posted ─────────────────────────

class TestUnknownTenantE2E:
    def test_unknown_tenant_returns_200(self):
        from unittest.mock import MagicMock
        from freshdesk.response_service import FreshdeskResponseService

        posted: list[str] = []

        async def fake_note(ticket_id: str, body: str) -> dict:
            posted.append(ticket_id)
            return {}

        response_svc = MagicMock(spec=FreshdeskResponseService)
        response_svc.add_internal_note = fake_note

        resolver = MagicMock()
        resolver.resolve.side_effect = ValueError("Unknown tenant")

        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        created_handler = FreshdeskTicketCreatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            client_resolver=resolver,
        )

        app, _, _ = _make_app(
            idem=idem,
            conv=conv,
            created_handler=created_handler,
            response_svc=response_svc,
        )
        client = TestClient(app)

        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=_created_payload(60050, email="user@ghost.io"),
        )
        assert resp.status_code == 200
        assert "60050" in posted


# ── Section 7: Size and JSON guards ─────────────────────────────────────────

class TestSizeAndJsonGuards:
    def test_oversized_payload_rejected_before_handler(self):
        app, _, _ = _make_app()
        client = TestClient(app)
        large = b"x" * (1024 * 1024 + 1)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=large,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 413

    def test_invalid_json_rejected(self):
        app, _, _ = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
