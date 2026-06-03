"""
freshdesk/freshdesk_exceptions.py

Sprint 2.4: Internal Freshdesk exception hierarchy.

These exceptions represent raw transport/HTTP outcomes from the Freshdesk
API layer. They are INTERNAL to the freshdesk package and are NOT exposed to
executors or the runtime.

FreshdeskProvider.execute() and health_check() catch these exceptions and
translate them to case_engine ProviderError subclasses via _translate_freshdesk_error().
Executors never see FreshdeskError; they see ProviderError and its subclasses.

Hierarchy:
    FreshdeskError               — base for all Freshdesk transport errors
    ├── FreshdeskApiError        — HTTP error response (carries status_code)
    │   ├── FreshdeskAuthError       — 401 Unauthorized
    │   ├── FreshdeskForbiddenError  — 403 Forbidden
    │   ├── FreshdeskNotFoundError   — 404 Not Found
    │   ├── FreshdeskConflictError   — 409 Conflict
    │   ├── FreshdeskValidationError — 422 Unprocessable Entity
    │   ├── FreshdeskRateLimitError  — 429 Too Many Requests
    │   └── FreshdeskServerError    — 5xx Server Error
    ├── FreshdeskConnectionError — TCP-level connection failure
    └── FreshdeskTimeoutException — request timed out
"""
from __future__ import annotations

from typing import Any


class FreshdeskError(RuntimeError):
    """Base class for all Freshdesk transport and API errors."""


class FreshdeskApiError(FreshdeskError):
    """
    HTTP error response from the Freshdesk API.

    Carries the HTTP status_code and the parsed response_body (may be empty
    if the response body is not valid JSON or is absent).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response_body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body: dict[str, Any] = response_body or {}


class FreshdeskAuthError(FreshdeskApiError):
    """HTTP 401 — API key is invalid or missing."""


class FreshdeskForbiddenError(FreshdeskApiError):
    """HTTP 403 — authenticated agent lacks permission for this operation."""


class FreshdeskNotFoundError(FreshdeskApiError):
    """HTTP 404 — requested resource (ticket, note, agent) does not exist."""


class FreshdeskConflictError(FreshdeskApiError):
    """HTTP 409 — operation conflicts with current resource state."""


class FreshdeskValidationError(FreshdeskApiError):
    """HTTP 422 — request payload failed Freshdesk schema validation."""


class FreshdeskRateLimitError(FreshdeskApiError):
    """HTTP 429 — API rate limit exceeded; caller should back off and retry."""


class FreshdeskServerError(FreshdeskApiError):
    """HTTP 5xx — Freshdesk server-side error; transient."""


class FreshdeskConnectionError(FreshdeskError):
    """TCP-level connection failure (DNS, refused, reset)."""


class FreshdeskTimeoutException(FreshdeskError):
    """HTTP request timed out (connect or read timeout)."""
