"""
freshdesk/freshdesk_provider.py

Sprint 2.4: FreshdeskProvider — concrete Provider implementation for Freshdesk REST API v2.

Satisfies the Provider ABC from case_engine.provider_interface.

Supported operations (ProviderRequest.operation):
    "add_note"       — POST /api/v2/tickets/{ticket_id}/notes
                       payload keys: "body" (str), "private" (bool, default True)
    "update_ticket"  — PUT  /api/v2/tickets/{ticket_id}
                       payload: any valid Freshdesk ticket field map

Auth strategy:
    HTTP Basic Auth — username=api_key, password="X" (literal "X" per Freshdesk convention).

Health check:
    GET /api/v2/agents/me — verifies auth and API reachability.
    200 → healthy. Any non-200 or exception → is_healthy=False. NEVER raises.

Error mapping (internal → ProviderError subclass):
    FreshdeskTimeoutException      → ProviderTimeoutError     (transient, retryable)
    FreshdeskConnectionError       → ProviderUnavailableError (transient, retryable)
    FreshdeskRateLimitError  (429) → ProviderRateLimitError   (transient, retryable)
    FreshdeskServerError     (5xx) → ProviderUnavailableError (transient, retryable)
    FreshdeskAuthError       (401) → ProviderAuthenticationError (permanent)
    FreshdeskForbiddenError  (403) → ProviderAuthorizationError  (permanent)
    FreshdeskNotFoundError   (404) → ProviderExecutionError   (permanent, RESOURCE_NOT_FOUND)
    FreshdeskConflictError   (409) → ProviderExecutionError   (permanent, CONFLICT)
    FreshdeskValidationError (422) → ProviderValidationError  (permanent)
    Other FreshdeskApiError        → ProviderExecutionError   (permanent)

Session injection:
    Pass http_client to the constructor to inject a pre-configured httpx.Client.
    This is the recommended pattern for unit testing without network access.
    When omitted, FreshdeskProvider creates and owns an httpx.Client internally.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from case_engine.provider_exceptions import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderError,
    ProviderExecutionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from case_engine.provider_interface import Provider
from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderRequest,
    ProviderResponse,
)
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

LOGGER = logging.getLogger(__name__)

_CAPABILITIES = frozenset({
    ProviderCapability.EXECUTE,
    ProviderCapability.HEALTH_CHECK,
})

_HEALTH_CHECK_PATH = "/api/v2/agents/me"
_ADD_NOTE_PATH     = "/api/v2/tickets/{ticket_id}/notes"
_UPDATE_TICKET_PATH = "/api/v2/tickets/{ticket_id}"


class FreshdeskProvider(Provider):
    """
    Freshdesk REST API v2 provider.

    Thread safety
    ─────────────
    FreshdeskProvider is stateless: no per-request state is stored on self.
    The underlying httpx.Client manages a connection pool and is thread-safe
    for concurrent requests (httpx.Client uses a transport that is thread-safe).
    Multiple executor workers may share a single FreshdeskProvider instance.

    Lifecycle
    ─────────
    When FreshdeskProvider creates its own httpx.Client (http_client=None), it
    owns the connection pool. For long-running services, keep the provider alive
    for the process lifetime and reuse the connection pool.

    For testing, inject a mock httpx.Client to avoid network calls.
    """

    _PROVIDER_NAME    = "freshdesk"
    _PROVIDER_VERSION = "1.0.0"

    def __init__(
        self,
        config: FreshdeskConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        """
        Args:
            config:      Validated Freshdesk configuration.
            http_client: Optional pre-configured httpx.Client for testing.
                         When None (production), an httpx.Client is created with
                         the config's base_url, auth, timeout, and JSON headers.
        """
        self._config = config
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.Client(
                base_url=config.base_url,
                auth=httpx.BasicAuth(config.api_key, "X"),
                timeout=config.timeout_seconds,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            self._owns_client = True

    # ── Provider ABC ───────────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return self._PROVIDER_NAME

    @property
    def provider_version(self) -> str:
        return self._PROVIDER_VERSION

    def capabilities(self) -> frozenset[ProviderCapability]:
        return _CAPABILITIES

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider_name=self._PROVIDER_NAME,
            provider_version=self._PROVIDER_VERSION,
            capabilities=_CAPABILITIES,
            description="Freshdesk REST API v2 — ticket and note operations",
        )

    def health_check(self) -> ProviderHealth:
        """
        Verify Freshdesk API reachability by calling GET /api/v2/agents/me.

        Returns ProviderHealth(is_healthy=True) on HTTP 200.
        Returns ProviderHealth(is_healthy=False) for any other outcome.
        NEVER raises — all exceptions are caught internally.
        """
        start = time.monotonic()
        try:
            response = self._client.get(_HEALTH_CHECK_PATH)
            latency_ms = int((time.monotonic() - start) * 1000)
            is_healthy = response.status_code == 200
            message = None if is_healthy else f"HTTP {response.status_code}"
            return ProviderHealth(
                provider_name=self._PROVIDER_NAME,
                provider_version=self._PROVIDER_VERSION,
                is_healthy=is_healthy,
                latency_ms=latency_ms,
                checked_at=datetime.now(tz=timezone.utc),
                message=message,
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            LOGGER.warning(
                "freshdesk.health_check: exception error=%s", exc,
            )
            return ProviderHealth(
                provider_name=self._PROVIDER_NAME,
                provider_version=self._PROVIDER_VERSION,
                is_healthy=False,
                latency_ms=latency_ms,
                checked_at=datetime.now(tz=timezone.utc),
                message=f"health_check error: {exc}",
            )

    def execute(self, request: ProviderRequest) -> ProviderResponse:
        """
        Dispatch request.operation to the appropriate Freshdesk API call.

        Raises:
            ProviderExecutionError:    unsupported operation.
            ProviderTransientError:    timeout, rate limit, or server error.
            ProviderPermanentError:    auth failure, forbidden, validation error.
        """
        try:
            if request.operation == "add_note":
                return self._execute_add_note(request)
            if request.operation == "update_ticket":
                return self._execute_update_ticket(request)
            raise ProviderExecutionError(
                f"Freshdesk provider does not support operation={request.operation!r}. "
                "Supported: 'add_note', 'update_ticket'.",
                error_code="UNSUPPORTED_OPERATION",
                provider_name=self._PROVIDER_NAME,
            )
        except ProviderError:
            raise
        except FreshdeskError as exc:
            raise self._translate_freshdesk_error(exc) from exc
        except Exception as exc:
            LOGGER.exception(
                "freshdesk.execute: unexpected error operation=%s error=%s",
                request.operation, exc,
            )
            raise ProviderError(
                f"Unexpected error in FreshdeskProvider: {exc}",
                error_code="PROVIDER_UNEXPECTED_ERROR",
                provider_name=self._PROVIDER_NAME,
            ) from exc

    # ── Operation implementations ─────────────────────────────────────────────

    def _execute_add_note(self, request: ProviderRequest) -> ProviderResponse:
        """POST /api/v2/tickets/{ticket_id}/notes"""
        ticket_id = request.ticket_id
        body: dict[str, Any] = {
            "body":    request.payload.get("body", ""),
            "private": request.payload.get("private", True),
        }
        path = _ADD_NOTE_PATH.format(ticket_id=ticket_id)
        response = self._post(path, body)

        data = response.json()
        note_id = str(data.get("id", ""))
        LOGGER.info(
            "freshdesk.add_note: ticket_id=%s note_id=%s",
            ticket_id, note_id,
        )
        return ProviderResponse(
            request_id=request.request_id,
            provider_request_id=note_id,
            success=True,
            status_code=response.status_code,
            result={"note_id": note_id, "ticket_id": ticket_id},
        )

    def _execute_update_ticket(self, request: ProviderRequest) -> ProviderResponse:
        """PUT /api/v2/tickets/{ticket_id}"""
        ticket_id = request.ticket_id
        path = _UPDATE_TICKET_PATH.format(ticket_id=ticket_id)
        response = self._put(path, request.payload)

        LOGGER.info("freshdesk.update_ticket: ticket_id=%s", ticket_id)
        return ProviderResponse(
            request_id=request.request_id,
            provider_request_id=str(ticket_id),
            success=True,
            status_code=response.status_code,
            result={"ticket_id": ticket_id},
        )

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._client.get(path)
        except httpx.TimeoutException as exc:
            raise FreshdeskTimeoutException(str(exc)) from exc
        except httpx.RequestError as exc:
            raise FreshdeskConnectionError(str(exc)) from exc
        self._check_response(response)
        return response

    def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            response = self._client.post(path, json=body)
        except httpx.TimeoutException as exc:
            raise FreshdeskTimeoutException(str(exc)) from exc
        except httpx.RequestError as exc:
            raise FreshdeskConnectionError(str(exc)) from exc
        self._check_response(response)
        return response

    def _put(self, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            response = self._client.put(path, json=body)
        except httpx.TimeoutException as exc:
            raise FreshdeskTimeoutException(str(exc)) from exc
        except httpx.RequestError as exc:
            raise FreshdeskConnectionError(str(exc)) from exc
        self._check_response(response)
        return response

    def _check_response(self, response: httpx.Response) -> None:
        """
        Raise a FreshdeskApiError subclass if the response indicates failure.
        200–299 responses are treated as success and return without raising.
        """
        status = response.status_code
        if 200 <= status < 300:
            return
        try:
            body = response.json()
        except Exception:
            body = {}
        msg = f"Freshdesk HTTP {status}"
        if status == 401:
            raise FreshdeskAuthError(msg, status_code=status, response_body=body)
        if status == 403:
            raise FreshdeskForbiddenError(msg, status_code=status, response_body=body)
        if status == 404:
            raise FreshdeskNotFoundError(msg, status_code=status, response_body=body)
        if status == 409:
            raise FreshdeskConflictError(msg, status_code=status, response_body=body)
        if status == 422:
            raise FreshdeskValidationError(msg, status_code=status, response_body=body)
        if status == 429:
            raise FreshdeskRateLimitError(msg, status_code=status, response_body=body)
        if 500 <= status < 600:
            raise FreshdeskServerError(msg, status_code=status, response_body=body)
        raise FreshdeskApiError(msg, status_code=status, response_body=body)

    def _translate_freshdesk_error(self, exc: FreshdeskError) -> ProviderError:
        """Map a FreshdeskError to the appropriate ProviderError subclass."""
        kw: dict[str, Any] = {"provider_name": self._PROVIDER_NAME}
        if isinstance(exc, FreshdeskTimeoutException):
            return ProviderTimeoutError(str(exc), **kw)
        if isinstance(exc, FreshdeskConnectionError):
            return ProviderUnavailableError(str(exc), **kw)
        if isinstance(exc, FreshdeskAuthError):
            return ProviderAuthenticationError(str(exc), **kw)
        if isinstance(exc, FreshdeskForbiddenError):
            return ProviderAuthorizationError(str(exc), **kw)
        if isinstance(exc, FreshdeskNotFoundError):
            return ProviderExecutionError(
                str(exc), error_code="RESOURCE_NOT_FOUND", **kw
            )
        if isinstance(exc, FreshdeskConflictError):
            return ProviderExecutionError(
                str(exc), error_code="CONFLICT", **kw
            )
        if isinstance(exc, FreshdeskValidationError):
            return ProviderValidationError(str(exc), **kw)
        if isinstance(exc, FreshdeskRateLimitError):
            return ProviderRateLimitError(str(exc), **kw)
        if isinstance(exc, FreshdeskServerError):
            return ProviderUnavailableError(str(exc), **kw)
        # Generic FreshdeskApiError not covered by specific subclasses
        return ProviderExecutionError(str(exc), **kw)
