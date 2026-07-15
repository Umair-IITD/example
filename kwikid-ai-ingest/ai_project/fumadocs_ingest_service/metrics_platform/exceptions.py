"""
metrics_platform/exceptions.py

Sprint 2.50: Typed exception hierarchy for the Uptime Kuma client.

All exceptions inherit from MetricsPlatformError. Callers can catch the base
to convert transport failures into deterministic Evidence with data_available=False.

Dependency direction:
    exceptions.py → stdlib only.
"""
from __future__ import annotations

from typing import Any


class MetricsPlatformError(RuntimeError):
    """Base error for all Uptime Kuma transport / API failures."""


class MetricsPlatformApiError(MetricsPlatformError):
    """
    HTTP error response from Uptime Kuma.

    Carries the HTTP status_code and (best-effort) parsed response_body.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class MetricsPlatformAuthError(MetricsPlatformApiError):
    """HTTP 401 — API key missing / invalid on `/metrics`."""


class MetricsPlatformForbiddenError(MetricsPlatformApiError):
    """HTTP 403 — API key valid but lacks permission."""


class MetricsPlatformNotFoundError(MetricsPlatformApiError):
    """HTTP 404 — slug or route unknown."""


class MetricsPlatformServerError(MetricsPlatformApiError):
    """HTTP 5xx — Uptime Kuma server error; transient."""


class MetricsPlatformConnectionError(MetricsPlatformError):
    """TCP/DNS-level connection failure to Uptime Kuma."""


class MetricsPlatformTimeoutError(MetricsPlatformError):
    """Read or connect timeout to Uptime Kuma."""
