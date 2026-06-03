"""
webhook — Sprint 2.6 webhook foundation contracts.

Public API:
    WebhookEvent            — canonical parsed form of an inbound webhook payload
    WebhookValidationResult — outcome of HMAC/signature verification
    WebhookProcessor        — ABC for provider-specific webhook processors
"""
from webhook.models import WebhookEvent, WebhookProcessor, WebhookValidationResult

__all__ = [
    "WebhookEvent",
    "WebhookValidationResult",
    "WebhookProcessor",
]
