"""
freshdesk/verifier.py

Sprint 2.28.1: FreshdeskWebhookVerifier — webhook signature validation + replay protection.
Sprint 2.29.1: Added `mode` parameter to support both static and HMAC verification.

Verification modes:
  "static" — Freshdesk → FastAPI (direct, no n8n).
              Freshdesk Dispatch'r sends the raw secret as the X-Webhook-Token value.
              Verified by direct constant-time comparison against the stored secret.
              Use FRESHDESK_WEBHOOK_MODE=static in development/testing.

  "hmac"   — Freshdesk → n8n → FastAPI (production target).
              n8n computes HMAC-SHA256(secret, raw_body) and injects the hex digest
              into the X-Webhook-Token header before forwarding to FastAPI.
              Use FRESHDESK_WEBHOOK_MODE=hmac (default) in production.

Switching between modes requires only an environment variable change:
  FRESHDESK_WEBHOOK_MODE=static   ← dev / Freshdesk direct
  FRESHDESK_WEBHOOK_MODE=hmac     ← production / n8n in the middle
No code changes, no route changes, no architecture changes.

Signature sources (tried in order):
  1. X-Webhook-Token  (primary)  — n8n header / Freshdesk static header
  2. X-Freshdesk-Signature (fallback) — direct Freshdesk native signature

Security rules (NON-NEGOTIABLE):
  - ALL comparisons use hmac.compare_digest() — constant-time, prevents timing attacks.
  - NEVER use == for secret or signature comparison.
  - Replay protection: reject events with event_timestamp older than replay_window_seconds.
  - Enforce flag: if enforce=True, reject any request that fails verification.
                  if enforce=False (dev mode), log warning but allow through.

Design:
  - FreshdeskWebhookVerifier is stateless: no per-request state on self.
  - Pass the result object to handlers; never raise from verify().
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

LOGGER = logging.getLogger(__name__)

_TOKEN_HEADER     = "x-webhook-token"
_SIGNATURE_HEADER = "x-freshdesk-signature"


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    reason: str = ""
    header_used: str = ""

    @classmethod
    def ok(cls, header_used: str = "") -> "VerificationResult":
        return cls(valid=True, reason="OK", header_used=header_used)

    @classmethod
    def fail(cls, reason: str) -> "VerificationResult":
        return cls(valid=False, reason=reason)


class FreshdeskWebhookVerifier:
    """
    Validates incoming webhook requests from Freshdesk (via n8n or direct).

    Args:
        webhook_secret:          Shared secret.
                                 static mode: compared directly against X-Webhook-Token.
                                 hmac mode:   used to compute HMAC-SHA256(secret, body).
        mode:                    "static" or "hmac" (default "hmac").
                                 Set via FRESHDESK_WEBHOOK_MODE env var at startup.
        enforce:                 If True, invalid requests are rejected with valid=False.
                                 If False (dev mode), warnings are logged but valid=True.
        replay_window_seconds:   Max age of a valid event timestamp (default 300s = 5 min).
    """

    def __init__(
        self,
        webhook_secret: str,
        *,
        mode: str = "hmac",
        enforce: bool = True,
        replay_window_seconds: int = 300,
    ) -> None:
        if not webhook_secret:
            raise ValueError("webhook_secret must be non-empty")
        if mode not in {"static", "hmac"}:
            raise ValueError(f"mode must be 'static' or 'hmac', got {mode!r}")
        self._secret_str = webhook_secret          # for static direct comparison
        self._secret = webhook_secret.encode()     # for HMAC computation
        self._mode = mode
        self._enforce = enforce
        self._replay_window = replay_window_seconds

    @property
    def mode(self) -> str:
        """Verification mode: 'static' (direct comparison) or 'hmac' (HMAC-SHA256)."""
        return self._mode

    def verify(
        self,
        headers: dict[str, str],
        body: bytes,
        *,
        event_timestamp: datetime | None = None,
    ) -> VerificationResult:
        """
        Verify a webhook request.

        Args:
            headers:         HTTP headers (case-insensitive lookup applied internally).
            body:            Raw request body bytes.
            event_timestamp: Optional event timestamp for replay protection.
                             If None, replay check is skipped.

        Returns:
            VerificationResult with valid=True/False and reason.
            Never raises.
        """
        lower_headers = {k.lower(): v for k, v in headers.items()}

        # Replay protection first (cheap check before token/HMAC computation)
        if event_timestamp is not None:
            replay_result = self._check_replay(event_timestamp)
            if not replay_result.valid:
                return self._enforce_result(replay_result)

        # Try X-Webhook-Token first (primary: n8n in hmac mode / Freshdesk raw in static mode)
        token = lower_headers.get(_TOKEN_HEADER)
        if token:
            return self._enforce_result(
                self._verify_token(token.strip(), body, _TOKEN_HEADER)
            )

        # Try X-Freshdesk-Signature (fallback)
        sig = lower_headers.get(_SIGNATURE_HEADER)
        if sig:
            return self._enforce_result(
                self._verify_token(sig.strip(), body, _SIGNATURE_HEADER)
            )

        # No signature header present
        return self._enforce_result(
            VerificationResult.fail("No webhook signature header found")
        )

    def _verify_token(
        self, provided: str, body: bytes, header_name: str
    ) -> VerificationResult:
        """Compare provided token against secret using the configured mode."""
        if self._mode == "static":
            # Freshdesk Dispatch'r sends the raw secret as the token value.
            # Constant-time direct comparison — no HMAC computation.
            if hmac.compare_digest(provided, self._secret_str.strip()):
                return VerificationResult.ok(header_used=header_name)
            return VerificationResult.fail(
                f"Token mismatch on {header_name} (static mode)"
            )
        # mode == "hmac": n8n pre-computed HMAC-SHA256(secret, body)
        expected = self._compute_hmac(body)
        if hmac.compare_digest(expected, provided):
            return VerificationResult.ok(header_used=header_name)
        return VerificationResult.fail(f"HMAC mismatch on {header_name}")

    def _compute_hmac(self, body: bytes) -> str:
        """Compute HMAC-SHA256(secret, body) → hex digest."""
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def _check_replay(self, event_timestamp: datetime) -> VerificationResult:
        if event_timestamp.tzinfo is None:
            event_timestamp = event_timestamp.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        age_seconds = (now - event_timestamp).total_seconds()
        if age_seconds > self._replay_window:
            return VerificationResult.fail(
                f"Replay attack: event is {age_seconds:.0f}s old (max {self._replay_window}s)"
            )
        if age_seconds < -60:
            return VerificationResult.fail(
                f"Clock skew: event timestamp is {-age_seconds:.0f}s in the future"
            )
        return VerificationResult.ok()

    def _enforce_result(self, result: VerificationResult) -> VerificationResult:
        if result.valid:
            return result
        if self._enforce:
            LOGGER.warning("freshdesk.verifier: REJECTED reason=%s", result.reason)
            return result
        LOGGER.warning(
            "freshdesk.verifier: enforce=False, ALLOWING despite failure reason=%s",
            result.reason,
        )
        return VerificationResult(valid=True, reason=f"BYPASS:{result.reason}")


def build_webhook_verifier(
    *,
    webhook_secret: str,
    mode: str = "hmac",
    enforce: bool = True,
    replay_window_seconds: int = 300,
) -> FreshdeskWebhookVerifier:
    return FreshdeskWebhookVerifier(
        webhook_secret,
        mode=mode,
        enforce=enforce,
        replay_window_seconds=replay_window_seconds,
    )
