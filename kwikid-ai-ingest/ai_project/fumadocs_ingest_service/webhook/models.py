"""
webhook/models.py

Sprint 2.6: Webhook foundation — transport-agnostic contracts.

No FastAPI imports. No HTTP logic. No Freshdesk-specific logic.
Sprint 2.7 builds FastAPI route handlers on top of these contracts.

Design principles:
  WebhookEvent            — canonical parsed form of any inbound webhook payload.
  WebhookValidationResult — outcome of HMAC/signature verification.
  WebhookProcessor        — ABC for provider-specific processors (injected into routes).

Contract:
  1. validate() is called with raw_body + signature before any parsing.
  2. If is_valid=False, the request is rejected. parse() must not be called.
  3. parse() converts a validated event into an ActionProposal or None.
  4. None means "this event is known but not actionable" (not an error).
  5. Both validate() and parse() must never raise — return failure types instead.

This contract keeps Sprint 2.7 route handlers thin: they validate, parse,
propose, and return. All business logic stays in the gateway/executor layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from case_engine.action_models import ActionProposal


@dataclass(frozen=True)
class WebhookEvent:
    """
    Canonical parsed form of an inbound webhook payload.

    Transport-agnostic: the route handler populates this from the HTTP request,
    then passes it to WebhookProcessor.parse(). The processor never sees raw
    HTTP primitives — it only sees WebhookEvent.

    Attributes:
        event_id:       Provider-assigned event identifier (for deduplication).
        event_type:     Provider-specific event category (e.g. "ticket.update").
        ticket_id:      Freshdesk ticket identifier (as text, e.g. "TKT-001").
        client:         Tenant slug derived from the webhook routing key.
        payload:        Full parsed event body (provider-specific structure).
        received_at:    Wall-clock time when the route handler received the event.
        raw_signature:  HMAC/JWT signature header value (for audit logging).
    """
    event_id: str
    event_type: str
    ticket_id: str
    client: str
    payload: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
    raw_signature: str = ""


@dataclass(frozen=True)
class WebhookValidationResult:
    """
    Result of webhook signature/HMAC verification.

    is_valid=False: reject the request with HTTP 403 (Sprint 2.7).
    reason: logged server-side only — never exposed to the caller.

    Factory methods:
        WebhookValidationResult.ok()            → valid
        WebhookValidationResult.reject(reason)  → invalid with reason
    """
    is_valid: bool
    reason: str | None = None

    @classmethod
    def ok(cls) -> "WebhookValidationResult":
        """Return a successful validation result."""
        return cls(is_valid=True)

    @classmethod
    def reject(cls, reason: str) -> "WebhookValidationResult":
        """Return a rejected validation result with a reason for logging."""
        return cls(is_valid=False, reason=reason)


class WebhookProcessor(ABC):
    """
    Abstract base for provider-specific webhook processors.

    Implementors (Sprint 2.7+):
      - FreshdeskWebhookProcessor   — Freshdesk HMAC-SHA256 signature verification
      - ZendeskWebhookProcessor     — future
      - StripeWebhookProcessor      — future

    The processor is injected into the FastAPI route handler (Sprint 2.7),
    keeping the handler transport-aware but provider-agnostic. This allows
    processors to be swapped without changing route handler logic.

    Contract:
      1. validate() must be called before parse().
      2. If validate() returns is_valid=False, parse() MUST NOT be called.
      3. parse() may return None to signal "not actionable" (e.g. ticket_created
         events that do not yet map to any action type). This is not an error.
      4. Both methods must NEVER raise. Return failure types instead.
    """

    @abstractmethod
    def validate(self, raw_body: bytes, signature: str) -> WebhookValidationResult:
        """
        Verify the webhook signature/HMAC against the raw request body.

        Args:
            raw_body:  Raw request body bytes (before JSON parsing).
                       MUST be the exact bytes used to compute the HMAC.
            signature: Signature header value from the HTTP request.
                       Format depends on the provider (e.g. "sha256=<hex>").

        Returns:
            WebhookValidationResult.ok() if the signature is valid.
            WebhookValidationResult.reject(reason) on any failure.

        Must not raise. Catch all exceptions internally.
        """

    @abstractmethod
    def parse(self, event: WebhookEvent) -> ActionProposal | None:
        """
        Parse a validated WebhookEvent into an ActionProposal.

        Called only after validate() returned is_valid=True.

        Args:
            event: Validated and parsed webhook event.

        Returns:
            ActionProposal if the event should trigger an action proposal.
            None if the event is recognised but not actionable (not an error).

        Must not raise. Catch all exceptions internally and return None.
        """
