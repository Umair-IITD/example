"""
freshdesk — Sprint 2.4 Freshdesk provider implementation.

Public API:
    FreshdeskConfig        — validated configuration (domain, api_key, timeout)
    FreshdeskProvider      — Provider implementation for the Freshdesk REST API v2

    FreshdeskError            — base for all internal Freshdesk transport errors
    FreshdeskApiError         — wraps HTTP error responses (carries status_code)
    FreshdeskConnectionError  — TCP-level connection failures
    FreshdeskTimeoutException — request timeout

Dependency direction: freshdesk → case_engine (never the reverse)
"""
from freshdesk.freshdesk_exceptions import (
    FreshdeskApiError,
    FreshdeskAuthError,
    FreshdeskConflictError,
    FreshdeskConnectionError,
    FreshdeskError,
    FreshdeskForbiddenError,
    FreshdeskNotFoundError,
    FreshdeskRateLimitError,
    FreshdeskServerError,
    FreshdeskTimeoutException,
    FreshdeskValidationError,
)
from freshdesk.freshdesk_models import FreshdeskConfig
from freshdesk.freshdesk_provider import FreshdeskProvider

__all__ = [
    "FreshdeskConfig",
    "FreshdeskProvider",
    "FreshdeskError",
    "FreshdeskApiError",
    "FreshdeskConnectionError",
    "FreshdeskTimeoutException",
    "FreshdeskAuthError",
    "FreshdeskForbiddenError",
    "FreshdeskNotFoundError",
    "FreshdeskConflictError",
    "FreshdeskValidationError",
    "FreshdeskRateLimitError",
    "FreshdeskServerError",
]
