"""
webhook/freshdesk_processor.py

Sprint 2.7: FreshdeskWebhookProcessor — concrete WebhookProcessor for Freshdesk.

Responsibilities:
  1. HMAC-SHA256 signature validation (X-Freshdesk-Signature header).
  2. Parsing Freshdesk events into ActionProposal objects.
  3. Never raising — all exceptions are caught internally.

HMAC contract (Freshdesk):
  Freshdesk computes HMAC-SHA256(secret, raw_body) and sends the hex digest
  in the X-Freshdesk-Signature header. We verify using hmac.compare_digest()
  (constant-time) to prevent timing-attack recovery of the secret.

enforce_hmac=False:
  In development or when FRESHDESK_WEBHOOK_ENFORCE_HMAC=false, validation is
  bypassed. The flag must be explicitly False — default is True (fail closed).

Event → ActionProposal mapping:
  ticket_created  → add_note SAFE proposal (log receipt)
  ticket_updated  → add_note SAFE proposal (log update)
  note_added      → add_note SAFE proposal (acknowledge note)
  <anything else> → None (not actionable, not an error)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from case_engine.action_models import ActionProposal
from case_engine.action_state import ActionRiskLevel
from webhook.models import WebhookEvent, WebhookProcessor, WebhookValidationResult

LOGGER = logging.getLogger(__name__)

# Freshdesk sends the signature in this header (normalised to lowercase by FastAPI)
_SIGNATURE_HEADER = "x-freshdesk-signature"

# Events we recognise and convert into proposals
_ACTIONABLE_EVENTS = frozenset({"ticket_created", "ticket_updated", "note_added"})


class FreshdeskWebhookProcessor(WebhookProcessor):
    """
    Freshdesk-specific webhook processor.

    Args:
        secret:        HMAC-SHA256 shared secret (bytes or str).
                       When None and enforce_hmac=True, all requests are rejected.
        enforce_hmac:  When False, HMAC validation is skipped entirely.
                       Defaults to True (production mode).
    """

    def __init__(
        self,
        secret: bytes | str | None,
        *,
        enforce_hmac: bool = True,
    ) -> None:
        if isinstance(secret, str):
            self._secret: bytes | None = secret.encode("utf-8") if secret else None
        else:
            self._secret = secret
        self._enforce_hmac = enforce_hmac

    # ── Validation ─────────────────────────────────────────────────────────────

    def validate(self, raw_body: bytes, signature: str) -> WebhookValidationResult:
        """
        Verify the Freshdesk HMAC-SHA256 signature.

        When enforce_hmac=False, always returns ok() (development mode).
        Uses hmac.compare_digest() for constant-time comparison.
        Never raises.
        """
        try:
            if not self._enforce_hmac:
                return WebhookValidationResult.ok()

            if not self._secret:
                LOGGER.warning(
                    "freshdesk_processor.validate: enforce_hmac=True but no secret configured"
                )
                return WebhookValidationResult.reject("HMAC secret not configured")

            if not signature:
                return WebhookValidationResult.reject("Missing signature header")

            expected = hmac.new(self._secret, raw_body, hashlib.sha256).hexdigest()
            # Strip any provider prefix (some providers send "sha256=<hex>")
            received = signature.removeprefix("sha256=").strip()

            if not hmac.compare_digest(expected, received):
                return WebhookValidationResult.reject("Signature mismatch")

            return WebhookValidationResult.ok()

        except Exception as exc:
            LOGGER.error("freshdesk_processor.validate: unexpected error: %s", exc)
            return WebhookValidationResult.reject(f"Validation error: {type(exc).__name__}")

    # ── Parsing ────────────────────────────────────────────────────────────────

    def parse(self, event: WebhookEvent) -> ActionProposal | None:
        """
        Convert a validated WebhookEvent into an ActionProposal.

        Returns None for unknown or non-actionable event types.
        Never raises.
        """
        try:
            if event.event_type not in _ACTIONABLE_EVENTS:
                LOGGER.debug(
                    "freshdesk_processor.parse: unrecognised event_type=%s — skipping",
                    event.event_type,
                )
                return None

            return self._build_proposal(event)

        except Exception as exc:
            LOGGER.error(
                "freshdesk_processor.parse: unexpected error event_id=%s error=%s",
                event.event_id, exc,
            )
            return None

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_proposal(self, event: WebhookEvent) -> ActionProposal:
        """Build a SAFE add_note proposal from a recognised Freshdesk event."""
        body = _format_note_body(event)
        return ActionProposal(
            action_type="add_note",
            action_namespace="ticket",
            risk_level=ActionRiskLevel.SAFE,
            action_params={
                "body": body,
                "private": True,
            },
            proposed_by="freshdesk_webhook",
        )


def _format_note_body(event: WebhookEvent) -> str:
    """Format a human-readable note body from a Freshdesk event."""
    descriptions: dict[str, str] = {
        "ticket_created": "Ticket received via Freshdesk webhook. Automated processing initiated.",
        "ticket_updated": "Ticket update received via Freshdesk webhook. Re-evaluating for automated action.",
        "note_added":     "Note added to ticket via Freshdesk. Logged for audit trail.",
    }
    base = descriptions.get(event.event_type, f"Freshdesk event: {event.event_type}")
    return f"[KwikID Automation] {base} (event_id={event.event_id})"


def build_freshdesk_processor(
    secret: str | None = None,
    *,
    enforce_hmac: bool = True,
) -> FreshdeskWebhookProcessor:
    """
    Factory used by the FastAPI app factory (api/app.py).

    Reads FRESHDESK_WEBHOOK_SECRET and FRESHDESK_WEBHOOK_ENFORCE_HMAC
    from the environment when not supplied explicitly.
    """
    import os
    if secret is None:
        secret = os.environ.get("FRESHDESK_WEBHOOK_SECRET") or None
    if enforce_hmac:
        raw = os.environ.get("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "true").lower()
        enforce_hmac = raw not in ("false", "0", "no")
    return FreshdeskWebhookProcessor(secret=secret, enforce_hmac=enforce_hmac)
