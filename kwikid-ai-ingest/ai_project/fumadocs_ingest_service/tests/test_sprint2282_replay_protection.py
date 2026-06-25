"""
tests/test_sprint2282_replay_protection.py

Sprint 2.28.2 Part 8: Replay Protection — NOW ACTIVE.

Before this sprint, verifier.verify() was called WITHOUT event_timestamp=.
The replay check code existed but was never reached (None timestamp → skipped).

After Sprint 2.28.2:
  - Route handler parses JSON FIRST (before HMAC) to extract created_at/updated_at
  - Calls _parse_iso_timestamp() to produce a timezone-aware datetime
  - Passes event_timestamp= to verifier.verify()
  - Replay protection is now active for every verified request

Coverage:
  1. Old event (> 300s ago) → 401 when enforce=True
  2. Future event (> 60s ahead) → 401 when enforce=True
  3. Recent event (< 300s ago) → 200 OK
  4. No timestamp in payload → no replay check (passes through)
  5. _parse_iso_timestamp() handles ISO-8601 with Z suffix
  6. _parse_iso_timestamp() handles +00:00 offset
  7. _parse_iso_timestamp() returns None for empty string
  8. _parse_iso_timestamp() returns None for unparseable string
  9. Route extracts created_at for ticket-created events
  10. Route extracts updated_at for ticket-updated events
  11. Enforce=False → old event still passes (bypass mode)
  12. Clock skew: event 61s in future → rejected
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

os.environ.setdefault("RAG_API_KEY", "test-2282-replay")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.webhooks.freshdesk import router, _parse_iso_timestamp
from freshdesk.verifier import FreshdeskWebhookVerifier

SECRET = "test-replay-secret-32chars-xxxxx"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _make_app(enforce: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.freshdesk_verifier = FreshdeskWebhookVerifier(
        SECRET, enforce=enforce, replay_window_seconds=300
    )
    return app


def _created_payload(created_at: str) -> dict:
    return {
        "freshdesk_webhook": {
            "id": 77001,
            "subject": "Test",
            "description": "d",
            "description_text": "d",
            "status": 2,
            "priority": 2,
            "ticket_type": "Issues",
            "created_at": created_at,
            "requester_email": "u@c.com",
            "requester_name": "User",
            "tags": "",
            "ticket_custom_fields": {},
            "attachments": [],
        }
    }


def _updated_payload(updated_at: str) -> dict:
    return {
        "freshdesk_webhook": {
            "id": 77001,
            "subject": "Test",
            "status": 2,
            "priority": 2,
            "updated_at": updated_at,
            "requester_email": "u@c.com",
            "ticket_custom_fields": {},
            "changes": {},
            "attachments": [],
        }
    }


def _now_minus(seconds: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_plus(seconds: int) -> str:
    dt = datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Section 1: _parse_iso_timestamp ──────────────────────────────────────────

class TestParseIsoTimestamp:
    def test_z_suffix_parsed(self):
        result = _parse_iso_timestamp("2026-06-19T10:00:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_plus_zero_offset_parsed(self):
        result = _parse_iso_timestamp("2026-06-19T10:00:00+00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_empty_string_returns_none(self):
        assert _parse_iso_timestamp("") is None

    def test_invalid_string_returns_none(self):
        assert _parse_iso_timestamp("not-a-date") is None

    def test_none_string_returns_none(self):
        # Defensive: extra edge case
        result = _parse_iso_timestamp("null")
        assert result is None

    def test_correct_utc_value(self):
        result = _parse_iso_timestamp("2026-06-19T10:00:00Z")
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 19
        assert result.hour == 10


# ── Section 2: replay protection — old events rejected ───────────────────────

class TestOldEventRejected:
    def test_event_301s_old_rejected(self):
        app = _make_app(enforce=True)
        client = TestClient(app)
        old_ts = _now_minus(301)
        payload = _created_payload(old_ts)
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 401

    def test_event_600s_old_rejected(self):
        app = _make_app(enforce=True)
        client = TestClient(app)
        old_ts = _now_minus(600)
        payload = _created_payload(old_ts)
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 401

    def test_updated_event_301s_old_rejected(self):
        app = _make_app(enforce=True)
        client = TestClient(app)
        old_ts = _now_minus(301)
        payload = _updated_payload(old_ts)
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-updated",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 401


# ── Section 3: replay protection — recent events pass ────────────────────────

class TestRecentEventAccepted:
    def test_event_10s_old_accepted(self):
        app = _make_app(enforce=True)
        client = TestClient(app)
        recent_ts = _now_minus(10)
        payload = _created_payload(recent_ts)
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200

    def test_event_299s_old_accepted(self):
        app = _make_app(enforce=True)
        client = TestClient(app)
        recent_ts = _now_minus(299)
        payload = _created_payload(recent_ts)
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200


# ── Section 4: future events rejected (clock skew) ───────────────────────────

class TestFutureEventRejected:
    def test_event_61s_in_future_rejected(self):
        app = _make_app(enforce=True)
        client = TestClient(app)
        future_ts = _now_plus(61)
        payload = _created_payload(future_ts)
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 401

    def test_event_59s_in_future_accepted(self):
        """Events 59s in the future are within clock skew tolerance."""
        app = _make_app(enforce=True)
        client = TestClient(app)
        future_ts = _now_plus(59)
        payload = _created_payload(future_ts)
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200


# ── Section 5: enforce=False — replay check bypassed ─────────────────────────

class TestEnforceFalseBypass:
    def test_old_event_passes_in_non_enforce_mode(self):
        app = _make_app(enforce=False)
        client = TestClient(app)
        old_ts = _now_minus(600)
        payload = _created_payload(old_ts)
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200


# ── Section 6: no timestamp in payload — no replay check ─────────────────────

class TestNoTimestampInPayload:
    def test_no_created_at_no_replay_check(self):
        """If created_at is missing, _parse_iso_timestamp returns None → replay skipped."""
        app = _make_app(enforce=True)
        client = TestClient(app)
        payload = {
            "freshdesk_webhook": {
                "id": 77002,
                "subject": "No timestamp",
                "status": 2,
                "priority": 2,
                # No created_at
                "requester_email": "u@c.com",
                "ticket_custom_fields": {},
                "attachments": [],
            }
        }
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200


# ── Section 7: correct timestamp field used per event type ───────────────────

class TestCorrectTimestampField:
    def test_ticket_created_uses_created_at(self):
        """Route must extract created_at (not updated_at) for ticket-created."""
        app = _make_app(enforce=True)
        client = TestClient(app)
        recent_ts = _now_minus(5)
        payload = {
            "freshdesk_webhook": {
                "id": 77003,
                "subject": "T",
                "description": "d",
                "description_text": "d",
                "status": 2,
                "priority": 2,
                "ticket_type": "Issues",
                "created_at": recent_ts,  # recent → should pass
                "updated_at": _now_minus(600),  # old — if this is used, would fail
                "requester_email": "u@c.com",
                "requester_name": "User",
                "tags": "",
                "ticket_custom_fields": {},
                "attachments": [],
            }
        }
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-created",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200

    def test_ticket_updated_uses_updated_at(self):
        """Route must extract updated_at (not created_at) for ticket-updated."""
        app = _make_app(enforce=True)
        client = TestClient(app)
        recent_ts = _now_minus(5)
        payload = {
            "freshdesk_webhook": {
                "id": 77004,
                "subject": "T",
                "status": 2,
                "priority": 2,
                "created_at": _now_minus(600),  # old — if this is used, would fail
                "updated_at": recent_ts,  # recent → should pass
                "requester_email": "u@c.com",
                "ticket_custom_fields": {},
                "changes": {},
                "attachments": [],
            }
        }
        body = json.dumps(payload).encode()
        token = _sign(body)
        resp = client.post(
            "/webhooks/freshdesk/ticket-updated",
            content=body,
            headers={"content-type": "application/json", "X-Webhook-Token": token},
        )
        assert resp.status_code == 200
