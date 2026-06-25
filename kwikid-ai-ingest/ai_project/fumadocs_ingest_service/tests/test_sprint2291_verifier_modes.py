"""
tests/test_sprint2291_verifier_modes.py

Sprint 2.29.1: Unified webhook authentication — mode-aware FreshdeskWebhookVerifier.

Proves:
  1.  static mode accepts correct secret
  2.  static mode rejects wrong secret
  3.  hmac mode accepts valid HMAC signature
  4.  hmac mode rejects invalid signature
  5.  replay protection still works in both modes
  6.  future-timestamp clock-skew still rejected in both modes
  7.  ticket-created route accepts static-mode request
  8.  ticket-updated route accepts static-mode request
  9.  _get_verifier() returns app.state.freshdesk_verifier when set
  10. env fallback reads FRESHDESK_WEBHOOK_MODE and passes it to the verifier
  11. mode property is readable
  12. invalid mode raises ValueError at construction
  13. build_webhook_verifier() factory honours mode param
  14. static mode is constant-time (no body dependency)
  15. switching env var from static → hmac changes verifier behaviour
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# ── Env vars before any import ────────────────────────────────────────────────
os.environ.setdefault("RAG_API_KEY", "test-sprint2291-modes-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from freshdesk.verifier import FreshdeskWebhookVerifier, VerificationResult, build_webhook_verifier
from api.routes.webhooks.freshdesk import router, _get_verifier


# ── Constants ─────────────────────────────────────────────────────────────────

SECRET = "kwikid-static-webhook-secret-2291"
BODY   = b'{"freshdesk_webhook":{"id":197416}}'


def _compute_hmac(secret: str, body: bytes) -> str:
    return hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _recent_ts() -> str:
    """Return an ISO-8601 timestamp 30 seconds in the past — always within replay window."""
    return (datetime.now(tz=timezone.utc) - timedelta(seconds=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# ── Test 1: static mode accepts correct secret ───────────────────────────────

class TestStaticModeAcceptsCorrectSecret:
    def test_x_webhook_token_matches_secret(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        result = v.verify({"X-Webhook-Token": SECRET}, BODY)
        assert result.valid is True
        assert result.header_used == "x-webhook-token"

    def test_x_freshdesk_signature_matches_secret(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        result = v.verify({"X-Freshdesk-Signature": SECRET}, BODY)
        assert result.valid is True
        assert result.header_used == "x-freshdesk-signature"

    def test_static_mode_passes_regardless_of_body_content(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        different_body = b'{"freshdesk_webhook":{"id":999}}'
        result = v.verify({"X-Webhook-Token": SECRET}, different_body)
        assert result.valid is True

    def test_static_mode_strips_whitespace_from_token(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        result = v.verify({"X-Webhook-Token": f"  {SECRET}  "}, BODY)
        assert result.valid is True


# ── Test 2: static mode rejects wrong secret ─────────────────────────────────

class TestStaticModeRejectsWrongSecret:
    def test_wrong_token_rejected(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        result = v.verify({"X-Webhook-Token": "wrong-secret"}, BODY)
        assert result.valid is False
        assert "mismatch" in result.reason.lower()
        assert "static" in result.reason.lower()

    def test_hmac_digest_of_secret_is_rejected_in_static_mode(self):
        # Sending the HMAC digest (what n8n would send) fails in static mode
        hmac_value = _compute_hmac(SECRET, BODY)
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        result = v.verify({"X-Webhook-Token": hmac_value}, BODY)
        assert result.valid is False

    def test_empty_token_falls_through_to_no_header(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        result = v.verify({"X-Webhook-Token": ""}, BODY)
        assert result.valid is False
        assert "No webhook signature" in result.reason

    def test_wrong_token_enforce_false_bypasses(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=False)
        result = v.verify({"X-Webhook-Token": "wrong"}, BODY)
        assert result.valid is True
        assert "BYPASS" in result.reason


# ── Test 3: hmac mode accepts valid HMAC signature ───────────────────────────

class TestHmacModeAcceptsValidSignature:
    def setup_method(self):
        self.v = FreshdeskWebhookVerifier(SECRET, mode="hmac", enforce=True)
        self.valid_token = _compute_hmac(SECRET, BODY)

    def test_valid_hmac_on_x_webhook_token_passes(self):
        result = self.v.verify({"X-Webhook-Token": self.valid_token}, BODY)
        assert result.valid is True
        assert result.header_used == "x-webhook-token"

    def test_valid_hmac_on_x_freshdesk_signature_passes(self):
        result = self.v.verify({"X-Freshdesk-Signature": self.valid_token}, BODY)
        assert result.valid is True
        assert result.header_used == "x-freshdesk-signature"

    def test_hmac_depends_on_body(self):
        wrong_body_token = _compute_hmac(SECRET, b"different-body")
        result = self.v.verify({"X-Webhook-Token": wrong_body_token}, BODY)
        assert result.valid is False


# ── Test 4: hmac mode rejects invalid signature ───────────────────────────────

class TestHmacModeRejectsInvalidSignature:
    def setup_method(self):
        self.v = FreshdeskWebhookVerifier(SECRET, mode="hmac", enforce=True)

    def test_raw_secret_is_rejected_in_hmac_mode(self):
        # Freshdesk direct (static token) fails when verifier expects HMAC
        result = self.v.verify({"X-Webhook-Token": SECRET}, BODY)
        assert result.valid is False
        assert "mismatch" in result.reason.lower()

    def test_wrong_hmac_rejected(self):
        bad_token = _compute_hmac("wrong-secret", BODY)
        result = self.v.verify({"X-Webhook-Token": bad_token}, BODY)
        assert result.valid is False

    def test_no_header_rejected_when_enforce(self):
        result = self.v.verify({}, BODY)
        assert result.valid is False
        assert "No webhook signature" in result.reason


# ── Test 5: replay protection works in both modes ────────────────────────────

class TestReplayProtectionBothModes:
    def test_old_event_rejected_static_mode(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=400)
        result = v.verify({"X-Webhook-Token": SECRET}, BODY, event_timestamp=old_ts)
        assert result.valid is False
        assert "Replay" in result.reason

    def test_old_event_rejected_hmac_mode(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="hmac", enforce=True)
        token = _compute_hmac(SECRET, BODY)
        old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=400)
        result = v.verify({"X-Webhook-Token": token}, BODY, event_timestamp=old_ts)
        assert result.valid is False
        assert "Replay" in result.reason

    def test_recent_event_passes_static_mode(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        now = datetime.now(tz=timezone.utc)
        result = v.verify({"X-Webhook-Token": SECRET}, BODY, event_timestamp=now)
        assert result.valid is True

    def test_recent_event_passes_hmac_mode(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="hmac", enforce=True)
        token = _compute_hmac(SECRET, BODY)
        now = datetime.now(tz=timezone.utc)
        result = v.verify({"X-Webhook-Token": token}, BODY, event_timestamp=now)
        assert result.valid is True

    def test_replay_check_happens_before_token_check(self):
        # Even a valid token must fail if the timestamp is too old
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=600)
        result = v.verify({"X-Webhook-Token": SECRET}, BODY, event_timestamp=old_ts)
        assert result.valid is False
        assert "Replay" in result.reason


# ── Test 6: future-timestamp clock skew rejected in both modes ────────────────

class TestClockSkewBothModes:
    def test_future_timestamp_rejected_static_mode(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        future_ts = datetime.now(tz=timezone.utc) + timedelta(seconds=120)
        result = v.verify({"X-Webhook-Token": SECRET}, BODY, event_timestamp=future_ts)
        assert result.valid is False
        assert "skew" in result.reason.lower()

    def test_future_timestamp_rejected_hmac_mode(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="hmac", enforce=True)
        token = _compute_hmac(SECRET, BODY)
        future_ts = datetime.now(tz=timezone.utc) + timedelta(seconds=120)
        result = v.verify({"X-Webhook-Token": token}, BODY, event_timestamp=future_ts)
        assert result.valid is False
        assert "skew" in result.reason.lower()


# ── Test 7: ticket-created route works in static mode ─────────────────────────

def _make_ticket_created_payload() -> dict:
    return {
        "freshdesk_webhook": {
            "id": 197416,
            "subject": "Login issue",
            "description": "Cannot log in.",
            "description_text": "Cannot log in.",
            "status": 2,
            "priority": 2,
            "ticket_type": "Issues",
            "created_at": _recent_ts(),
            "requester_email": "agent@unitybank.co.in",
            "requester_name": "Unity Agent",
            "tags": "client:Unity",
            "ticket_custom_fields": {"cf_clients": "Unity"},
            "attachments": [],
        }
    }


def _make_ticket_updated_payload() -> dict:
    return {
        "freshdesk_webhook": {
            "id": 197416,
            "subject": "Login issue",
            "status": 2,
            "priority": 2,
            "updated_at": _recent_ts(),
            "requester_email": "agent@unitybank.co.in",
            "ticket_custom_fields": {"cf_clients": "Unity"},
            "latest_comment": {
                "body": "Still broken.",
                "body_text": "Still broken.",
                "incoming": True,
                "private": False,
            },
        }
    }


def _make_app_with_static_verifier(*, enforce: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.freshdesk_verifier = FreshdeskWebhookVerifier(
        SECRET, mode="static", enforce=enforce
    )
    return app


def _make_app_with_hmac_verifier(*, enforce: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.freshdesk_verifier = FreshdeskWebhookVerifier(
        SECRET, mode="hmac", enforce=enforce
    )
    return app


class TestTicketCreatedRouteStaticMode:
    def test_correct_static_secret_returns_200(self):
        app = _make_app_with_static_verifier()
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=_make_ticket_created_payload(),
                headers={"X-Webhook-Token": SECRET},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_wrong_secret_returns_401(self):
        app = _make_app_with_static_verifier(enforce=True)
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=_make_ticket_created_payload(),
                headers={"X-Webhook-Token": "wrong-secret"},
            )
        assert resp.status_code == 401

    def test_hmac_digest_as_token_fails_in_static_mode(self):
        # Sending n8n-style HMAC digest to a static-mode verifier must fail
        body = json.dumps(_make_ticket_created_payload()).encode()
        hmac_token = _compute_hmac(SECRET, body)
        app = _make_app_with_static_verifier(enforce=True)
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Token": hmac_token,
                },
            )
        assert resp.status_code == 401


# ── Test 8: ticket-updated route works in static mode ─────────────────────────

class TestTicketUpdatedRouteStaticMode:
    def test_correct_static_secret_returns_200(self):
        app = _make_app_with_static_verifier()
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=_make_ticket_updated_payload(),
                headers={"X-Webhook-Token": SECRET},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_wrong_secret_returns_401(self):
        app = _make_app_with_static_verifier(enforce=True)
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-updated",
                json=_make_ticket_updated_payload(),
                headers={"X-Webhook-Token": "wrong"},
            )
        assert resp.status_code == 401


# ── Test 9: _get_verifier() returns app.state.freshdesk_verifier when set ────

class TestGetVerifierUsesAppState:
    def test_returns_app_state_verifier_instance(self):
        wired = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        app = FastAPI()
        app.include_router(router)
        app.state.freshdesk_verifier = wired

        mock_request = MagicMock()
        mock_request.app = app

        result = _get_verifier(mock_request)
        assert result is wired

    def test_wired_verifier_mode_is_static(self):
        wired = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        app = FastAPI()
        app.state.freshdesk_verifier = wired

        mock_request = MagicMock()
        mock_request.app = app

        v = _get_verifier(mock_request)
        assert v.mode == "static"

    def test_wired_verifier_mode_is_hmac(self):
        wired = FreshdeskWebhookVerifier(SECRET, mode="hmac", enforce=True)
        app = FastAPI()
        app.state.freshdesk_verifier = wired

        mock_request = MagicMock()
        mock_request.app = app

        v = _get_verifier(mock_request)
        assert v.mode == "hmac"


# ── Test 10: env fallback reads FRESHDESK_WEBHOOK_MODE ───────────────────────

class TestEnvFallbackReadsMode:
    def test_static_mode_env_builds_static_verifier(self):
        app = FastAPI()
        app.include_router(router)
        # No app.state.freshdesk_verifier — forces env fallback

        mock_request = MagicMock()
        mock_request.app = app

        with patch.dict(os.environ, {
            "FRESHDESK_WEBHOOK_SECRET": SECRET,
            "FRESHDESK_WEBHOOK_MODE": "static",
            "FRESHDESK_WEBHOOK_ENFORCE_HMAC": "true",
        }):
            v = _get_verifier(mock_request)

        assert v is not None
        assert v.mode == "static"

    def test_hmac_mode_env_builds_hmac_verifier(self):
        app = FastAPI()
        app.include_router(router)

        mock_request = MagicMock()
        mock_request.app = app

        with patch.dict(os.environ, {
            "FRESHDESK_WEBHOOK_SECRET": SECRET,
            "FRESHDESK_WEBHOOK_MODE": "hmac",
            "FRESHDESK_WEBHOOK_ENFORCE_HMAC": "false",
        }):
            v = _get_verifier(mock_request)

        assert v is not None
        assert v.mode == "hmac"

    def test_no_secret_env_returns_none(self):
        app = FastAPI()
        app.include_router(router)

        mock_request = MagicMock()
        mock_request.app = app

        with patch.dict(os.environ, {"FRESHDESK_WEBHOOK_SECRET": ""}):
            v = _get_verifier(mock_request)

        assert v is None


# ── Test 11: mode property is readable ────────────────────────────────────────

class TestModeProperty:
    def test_static_mode_property(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static")
        assert v.mode == "static"

    def test_hmac_mode_property_default(self):
        v = FreshdeskWebhookVerifier(SECRET)
        assert v.mode == "hmac"

    def test_hmac_mode_property_explicit(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="hmac")
        assert v.mode == "hmac"


# ── Test 12: invalid mode raises ValueError ───────────────────────────────────

class TestInvalidModeRaises:
    def test_invalid_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="mode must be"):
            FreshdeskWebhookVerifier(SECRET, mode="digest")

    def test_empty_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="mode must be"):
            FreshdeskWebhookVerifier(SECRET, mode="")

    def test_uppercase_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="mode must be"):
            FreshdeskWebhookVerifier(SECRET, mode="STATIC")


# ── Test 13: build_webhook_verifier factory honours mode ──────────────────────

class TestFactoryHonoursMode:
    def test_factory_static_mode(self):
        v = build_webhook_verifier(webhook_secret=SECRET, mode="static")
        assert v.mode == "static"
        assert isinstance(v, FreshdeskWebhookVerifier)

    def test_factory_hmac_mode(self):
        v = build_webhook_verifier(webhook_secret=SECRET, mode="hmac")
        assert v.mode == "hmac"

    def test_factory_default_is_hmac(self):
        v = build_webhook_verifier(webhook_secret=SECRET)
        assert v.mode == "hmac"

    def test_factory_static_verify_works(self):
        v = build_webhook_verifier(webhook_secret=SECRET, mode="static", enforce=True)
        result = v.verify({"X-Webhook-Token": SECRET}, BODY)
        assert result.valid is True


# ── Test 14: static mode has no body dependency ───────────────────────────────

class TestStaticModeBodyIndependence:
    def test_same_secret_different_bodies_both_pass(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        body_a = b'{"id": 1}'
        body_b = b'{"id": 9999, "subject": "different"}'
        assert v.verify({"X-Webhook-Token": SECRET}, body_a).valid is True
        assert v.verify({"X-Webhook-Token": SECRET}, body_b).valid is True

    def test_hmac_mode_is_body_dependent(self):
        v = FreshdeskWebhookVerifier(SECRET, mode="hmac", enforce=True)
        body_a = b'{"id": 1}'
        body_b = b'{"id": 9999}'
        token_a = _compute_hmac(SECRET, body_a)
        # token_a is valid for body_a but not body_b
        assert v.verify({"X-Webhook-Token": token_a}, body_a).valid is True
        assert v.verify({"X-Webhook-Token": token_a}, body_b).valid is False


# ── Test 15: switching env var changes verifier behaviour ─────────────────────

class TestModeSwitchingViaEnv:
    def test_switch_static_to_hmac_changes_verification(self):
        """The same raw secret token passes in static mode, fails in hmac mode."""
        static_v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        hmac_v   = FreshdeskWebhookVerifier(SECRET, mode="hmac",   enforce=True)

        # Raw secret as token
        headers = {"X-Webhook-Token": SECRET}

        assert static_v.verify(headers, BODY).valid is True   # passes in static
        assert hmac_v.verify(headers, BODY).valid is False     # fails in hmac

    def test_switch_hmac_to_static_changes_verification(self):
        """An HMAC digest passes in hmac mode, fails in static mode."""
        static_v = FreshdeskWebhookVerifier(SECRET, mode="static", enforce=True)
        hmac_v   = FreshdeskWebhookVerifier(SECRET, mode="hmac",   enforce=True)

        digest = _compute_hmac(SECRET, BODY)
        headers = {"X-Webhook-Token": digest}

        assert hmac_v.verify(headers, BODY).valid is True      # passes in hmac
        assert static_v.verify(headers, BODY).valid is False   # fails in static

    def test_ticket_created_route_accepts_hmac_token_in_hmac_mode(self):
        """With hmac verifier, HMAC-signed request passes; raw secret fails."""
        body_bytes = json.dumps(_make_ticket_created_payload()).encode()
        hmac_token = _compute_hmac(SECRET, body_bytes)

        app = _make_app_with_hmac_verifier(enforce=True)
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Token": hmac_token,
                },
            )
        assert resp.status_code == 200

    def test_ticket_created_route_rejects_raw_secret_in_hmac_mode(self):
        """With hmac verifier, sending the raw secret (Freshdesk direct) must fail."""
        app = _make_app_with_hmac_verifier(enforce=True)
        with TestClient(app) as client:
            resp = client.post(
                "/webhooks/freshdesk/ticket-created",
                json=_make_ticket_created_payload(),
                headers={"X-Webhook-Token": SECRET},
            )
        assert resp.status_code == 401
