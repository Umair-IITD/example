"""
tests/test_sprint2281_webhook_verifier.py

Sprint 2.28.1: FreshdeskWebhookVerifier tests.

Coverage:
  1. build_webhook_verifier factory
  2. Empty / invalid secret raises ValueError
  3. HMAC computation correctness
  4. X-Webhook-Token header — valid, invalid, missing
  5. X-Freshdesk-Signature header — valid, invalid, fallback
  6. Replay protection — too old, too new, within window
  7. enforce=True → reject invalid; enforce=False → allow with warning
  8. Case-insensitive header lookup
  9. VerificationResult dataclass
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone, timedelta

import pytest

os.environ.setdefault("RAG_API_KEY", "test-verifier-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.verifier import FreshdeskWebhookVerifier, VerificationResult, build_webhook_verifier


SECRET = "test-webhook-secret-32chars-long!!"
BODY   = b'{"freshdesk_webhook":{"id":123}}'


def _compute_token(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── Section 1: Factory ────────────────────────────────────────────────────────

class TestFactory:
    def test_factory_creates_verifier(self):
        v = build_webhook_verifier(webhook_secret=SECRET)
        assert isinstance(v, FreshdeskWebhookVerifier)

    def test_factory_enforce_true_default(self):
        v = build_webhook_verifier(webhook_secret=SECRET)
        assert v._enforce is True

    def test_factory_enforce_false(self):
        v = build_webhook_verifier(webhook_secret=SECRET, enforce=False)
        assert v._enforce is False

    def test_factory_custom_replay_window(self):
        v = build_webhook_verifier(webhook_secret=SECRET, replay_window_seconds=120)
        assert v._replay_window == 120

    def test_empty_secret_raises(self):
        with pytest.raises(ValueError, match="webhook_secret"):
            FreshdeskWebhookVerifier("")


# ── Section 2: VerificationResult ────────────────────────────────────────────

class TestVerificationResult:
    def test_ok_result(self):
        r = VerificationResult.ok(header_used="x-webhook-token")
        assert r.valid is True
        assert r.reason == "OK"
        assert r.header_used == "x-webhook-token"

    def test_fail_result(self):
        r = VerificationResult.fail("HMAC mismatch")
        assert r.valid is False
        assert r.reason == "HMAC mismatch"

    def test_frozen(self):
        r = VerificationResult.ok()
        with pytest.raises(Exception):
            r.valid = False  # type: ignore


# ── Section 3: X-Webhook-Token (n8n path) ────────────────────────────────────

class TestXWebhookToken:
    def setup_method(self):
        self.verifier = FreshdeskWebhookVerifier(SECRET, enforce=True)
        self.token = _compute_token(SECRET, BODY)

    def test_valid_token_passes(self):
        headers = {"X-Webhook-Token": self.token}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is True
        assert result.header_used == "x-webhook-token"

    def test_invalid_token_rejected(self):
        headers = {"X-Webhook-Token": "bad" + self.token}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is False
        assert "mismatch" in result.reason.lower()

    def test_empty_token_rejected(self):
        headers = {"X-Webhook-Token": ""}
        # Empty token goes to signature fallback; no X-Freshdesk-Signature either
        result = self.verifier.verify(headers, BODY)
        # An empty string in the header — not hmac-valid, goes to fallback or no-header path
        assert result.valid is False

    def test_token_with_whitespace_stripped(self):
        headers = {"X-Webhook-Token": "  " + self.token + "  "}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is True

    def test_case_insensitive_header_lookup(self):
        headers = {"x-webhook-token": self.token}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is True

    def test_uppercase_header_name(self):
        headers = {"X-WEBHOOK-TOKEN": self.token}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is True


# ── Section 4: X-Freshdesk-Signature (direct Freshdesk path) ─────────────────

class TestXFreshdeskSignature:
    def setup_method(self):
        self.verifier = FreshdeskWebhookVerifier(SECRET, enforce=True)
        self.sig = _compute_token(SECRET, BODY)

    def test_valid_signature_passes(self):
        headers = {"X-Freshdesk-Signature": self.sig}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is True
        assert result.header_used == "x-freshdesk-signature"

    def test_invalid_signature_rejected(self):
        headers = {"X-Freshdesk-Signature": "invalid-sig"}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is False

    def test_webhook_token_takes_priority_over_signature(self):
        valid_token = _compute_token(SECRET, BODY)
        headers = {
            "X-Webhook-Token": valid_token,
            "X-Freshdesk-Signature": "bad-sig",
        }
        result = self.verifier.verify(headers, BODY)
        assert result.valid is True
        assert result.header_used == "x-webhook-token"


# ── Section 5: No headers ─────────────────────────────────────────────────────

class TestNoHeaders:
    def test_no_headers_rejected_when_enforce(self):
        verifier = FreshdeskWebhookVerifier(SECRET, enforce=True)
        result = verifier.verify({}, BODY)
        assert result.valid is False
        assert "No webhook signature" in result.reason

    def test_no_headers_allowed_when_not_enforce(self):
        verifier = FreshdeskWebhookVerifier(SECRET, enforce=False)
        result = verifier.verify({}, BODY)
        assert result.valid is True
        assert "BYPASS" in result.reason


# ── Section 6: Replay protection ─────────────────────────────────────────────

class TestReplayProtection:
    def setup_method(self):
        self.verifier = FreshdeskWebhookVerifier(SECRET, enforce=True, replay_window_seconds=300)
        self.token = _compute_token(SECRET, BODY)

    def test_recent_timestamp_passes(self):
        now = datetime.now(tz=timezone.utc)
        headers = {"X-Webhook-Token": self.token}
        result = self.verifier.verify(headers, BODY, event_timestamp=now)
        assert result.valid is True

    def test_old_timestamp_rejected(self):
        old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=301)
        headers = {"X-Webhook-Token": self.token}
        result = self.verifier.verify(headers, BODY, event_timestamp=old_ts)
        assert result.valid is False
        assert "Replay" in result.reason

    def test_timestamp_at_boundary_passes(self):
        # Exactly at the boundary (300s old)
        ts = datetime.now(tz=timezone.utc) - timedelta(seconds=299)
        headers = {"X-Webhook-Token": self.token}
        result = self.verifier.verify(headers, BODY, event_timestamp=ts)
        assert result.valid is True

    def test_future_timestamp_rejected(self):
        future_ts = datetime.now(tz=timezone.utc) + timedelta(seconds=120)
        headers = {"X-Webhook-Token": self.token}
        result = self.verifier.verify(headers, BODY, event_timestamp=future_ts)
        assert result.valid is False
        assert "skew" in result.reason.lower()

    def test_none_timestamp_skips_replay_check(self):
        headers = {"X-Webhook-Token": self.token}
        result = self.verifier.verify(headers, BODY, event_timestamp=None)
        assert result.valid is True

    def test_naive_datetime_treated_as_utc(self):
        naive_ts = datetime.utcnow() - timedelta(seconds=10)
        headers = {"X-Webhook-Token": self.token}
        result = self.verifier.verify(headers, BODY, event_timestamp=naive_ts)
        assert result.valid is True


# ── Section 7: enforce=False behavior ────────────────────────────────────────

class TestEnforceFalse:
    def setup_method(self):
        self.verifier = FreshdeskWebhookVerifier(SECRET, enforce=False)

    def test_invalid_token_allowed_when_not_enforce(self):
        headers = {"X-Webhook-Token": "invalid"}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is True
        assert "BYPASS" in result.reason

    def test_old_timestamp_allowed_when_not_enforce(self):
        old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=400)
        headers = {"X-Webhook-Token": _compute_token(SECRET, BODY)}
        result = self.verifier.verify(headers, BODY, event_timestamp=old_ts)
        assert result.valid is True
        assert "BYPASS" in result.reason

    def test_valid_token_still_passes_when_not_enforce(self):
        token = _compute_token(SECRET, BODY)
        headers = {"X-Webhook-Token": token}
        result = self.verifier.verify(headers, BODY)
        assert result.valid is True
        # OK result — no BYPASS prefix
        assert result.reason == "OK"


# ── Section 8: HMAC computation ──────────────────────────────────────────────

class TestHmacComputation:
    def test_hmac_uses_sha256(self):
        verifier = FreshdeskWebhookVerifier(SECRET, enforce=True)
        expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
        computed = verifier._compute_hmac(BODY)
        assert computed == expected

    def test_different_body_different_hmac(self):
        verifier = FreshdeskWebhookVerifier(SECRET, enforce=True)
        h1 = verifier._compute_hmac(b"body1")
        h2 = verifier._compute_hmac(b"body2")
        assert h1 != h2

    def test_empty_body_computable(self):
        verifier = FreshdeskWebhookVerifier(SECRET, enforce=True)
        result = verifier._compute_hmac(b"")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex is 64 chars
