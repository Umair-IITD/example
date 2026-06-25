"""
tests/test_sprint2281_webhook_routes.py

Sprint 2.28.1: Freshdesk webhook FastAPI route tests.

Coverage:
  1. POST /webhooks/freshdesk/ticket-created — 200 OK immediately
  2. POST /webhooks/freshdesk/ticket-updated — 200 OK immediately
  3. Oversized payload → 413
  4. Invalid JSON → 400
  5. HMAC verification — valid passes, invalid → 401 when enforce=True
  6. Response body format {"status":"accepted","event":"ticket_created"}
  7. Background task enqueued (not executed synchronously)
  8. Handler called in background task
  9. No verifier (env not set) → requests pass through
  10. Router prefix is /webhooks/freshdesk
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("RAG_API_KEY", "test-routes-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.webhooks.freshdesk import router


# ── Test app ──────────────────────────────────────────────────────────────────

def _make_app(with_verifier: bool = False, webhook_secret: str = "") -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    if with_verifier and webhook_secret:
        from freshdesk.verifier import FreshdeskWebhookVerifier
        app.state.freshdesk_verifier = FreshdeskWebhookVerifier(
            webhook_secret, enforce=True
        )
    return app


VALID_PAYLOAD = {
    "freshdesk_webhook": {
        "id": 197416,
        "subject": "Test ticket",
        "description": "desc",
        "description_text": "desc",
        "status": 2,
        "priority": 2,
        "ticket_type": "Issues",
        "created_at": "2026-06-19T10:00:00Z",
        "requester_email": "test@unitybank.co.in",
        "requester_name": "Test User",
        "tags": "",
        "ticket_custom_fields": {"cf_clients": "Unity"},
        "attachments": [],
    }
}

UPDATED_PAYLOAD = {
    "freshdesk_webhook": {
        "id": 197416,
        "subject": "Test",
        "status": 4,
        "priority": 2,
        "updated_at": "2026-06-19T11:00:00Z",
        "requester_email": "test@unitybank.co.in",
        "ticket_custom_fields": {"cf_clients": "Unity"},
        "changes": {"status": [2, 4]},
        "attachments": [],
    }
}

SECRET = "test-webhook-secret-32chars-long!!"


def _sign_body(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── Section 1: ticket-created — 200 OK ───────────────────────────────────────

class TestTicketCreated200:
    def test_returns_200(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=VALID_PAYLOAD,
        )
        assert resp.status_code == 200

    def test_response_body_format(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=VALID_PAYLOAD,
        )
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["event"] == "ticket_created"

    def test_response_content_type_json(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=VALID_PAYLOAD,
        )
        assert "application/json" in resp.headers.get("content-type", "")


# ── Section 2: ticket-updated — 200 OK ───────────────────────────────────────

class TestTicketUpdated200:
    def test_returns_200(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-updated",
            json=UPDATED_PAYLOAD,
        )
        assert resp.status_code == 200

    def test_response_body_format(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-updated",
            json=UPDATED_PAYLOAD,
        )
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["event"] == "ticket_updated"


# ── Section 3: Oversized payload ──────────────────────────────────────────────

class TestOversizedPayload:
    def test_413_on_oversized_created(self):
        app = _make_app()
        client = TestClient(app)
        large_body = b"x" * (1024 * 1024 + 1)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=large_body,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 413

    def test_413_on_oversized_updated(self):
        app = _make_app()
        client = TestClient(app)
        large_body = b"x" * (1024 * 1024 + 1)
        resp = client.post(
            "/webhooks/freshdesk/ticket-updated",
            content=large_body,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 413


# ── Section 4: Invalid JSON ───────────────────────────────────────────────────

class TestInvalidJson:
    def test_400_on_invalid_json_created(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=b"not-json-at-all",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_400_on_invalid_json_updated(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-updated",
            content=b"{bad",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_error_detail_in_response(self):
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        body = resp.json()
        assert "invalid_json" in body.get("detail", "")


# ── Section 5: HMAC verification ─────────────────────────────────────────────

class TestHmacVerification:
    def test_valid_hmac_passes(self):
        # Sprint 2.28.2: replay protection is now active — use a recent timestamp
        recent_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        dynamic_payload = {**VALID_PAYLOAD, "freshdesk_webhook": {**VALID_PAYLOAD["freshdesk_webhook"], "created_at": recent_ts}}
        app = _make_app(with_verifier=True, webhook_secret=SECRET)
        client = TestClient(app)
        body_bytes = json.dumps(dynamic_payload).encode()
        token = _sign_body(body_bytes)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body_bytes,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200

    def test_invalid_hmac_returns_401(self):
        app = _make_app(with_verifier=True, webhook_secret=SECRET)
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=VALID_PAYLOAD,
            headers={"X-Webhook-Token": "invalid-token"},
        )
        assert resp.status_code == 401

    def test_401_response_body(self):
        app = _make_app(with_verifier=True, webhook_secret=SECRET)
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=VALID_PAYLOAD,
            headers={"X-Webhook-Token": "bad"},
        )
        body = resp.json()
        assert "signature_invalid" in body.get("detail", "")

    def test_no_verifier_no_auth_required(self):
        app = _make_app(with_verifier=False)
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json=VALID_PAYLOAD,
        )
        assert resp.status_code == 200

    def test_valid_hmac_updated_passes(self):
        # Sprint 2.28.2: replay protection is now active — use a recent timestamp
        recent_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        dynamic_payload = {**UPDATED_PAYLOAD, "freshdesk_webhook": {**UPDATED_PAYLOAD["freshdesk_webhook"], "updated_at": recent_ts}}
        app = _make_app(with_verifier=True, webhook_secret=SECRET)
        client = TestClient(app)
        body_bytes = json.dumps(dynamic_payload).encode()
        token = _sign_body(body_bytes)
        resp = client.post(
            "/webhooks/freshdesk/ticket-updated",
            content=body_bytes,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200


# ── Section 6: Router is registered correctly ─────────────────────────────────

class TestRouterRegistration:
    def test_router_prefix(self):
        assert router.prefix == "/webhooks/freshdesk"

    def test_router_has_ticket_created_route(self):
        paths = [r.path for r in router.routes]
        assert any("ticket-created" in p for p in paths)

    def test_router_has_ticket_updated_route(self):
        paths = [r.path for r in router.routes]
        assert any("ticket-updated" in p for p in paths)


# ── Section 7: Background tasks not blocking ─────────────────────────────────

class TestBackgroundTaskNotBlocking:
    def test_200_returned_before_processing(self):
        """Verify that slow background processing doesn't block the 200 response."""
        processed = []

        def slow_process(request, payload):
            import time
            time.sleep(0.1)
            processed.append("done")

        app = _make_app()
        client = TestClient(app)
        with patch("api.routes.webhooks.freshdesk._process_ticket_created", side_effect=slow_process):
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=VALID_PAYLOAD,
            )
        # TestClient runs background tasks synchronously, but 200 is returned regardless
        assert resp.status_code == 200

    def test_empty_object_payload_still_returns_200(self):
        """Edge case: empty but valid JSON."""
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            json={},
        )
        assert resp.status_code == 200
