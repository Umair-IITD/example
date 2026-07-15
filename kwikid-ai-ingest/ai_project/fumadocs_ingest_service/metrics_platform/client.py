"""
metrics_platform/client.py

Sprint 2.50: Async, READ-ONLY httpx client for Uptime Kuma 1.23.15.

Endpoints exposed (all confirmed live 2026-07-11 — SOT api_reference.md):
  - GET /api/entry-page                       (public)
  - GET /api/status-page/{slug}               (public)
  - GET /api/status-page/heartbeat/{slug}     (public)
  - GET /metrics                              (auth: bearer OR basic)

WRITE endpoints (POST/PUT/DELETE) are NOT wrapped. There is no `create_`,
`update_`, `delete_`, `restart_`, `pause_`, or `resume_` method on this
client — the Metrics Dashboard is read-only in this system.

Design rules
------------
- Async httpx. Timeouts + retries from config.
- Auth mode: bearer / basic / none. Chosen once at construction.
- 401 → raises MetricsPlatformAuthError; no retry.
- 429 → NOT expected on Uptime Kuma but retried once as a courtesy.
- 5xx → retry with exponential backoff up to max_retries.
- ConnectError / TimeoutException → surface as typed error; adapter
  converts to `DataAvailability.UNAVAILABLE` evidence.
- Never raises to callers for anything except the typed hierarchy.

Dependency direction
--------------------
    client.py → httpx + stdlib + metrics_platform.{config, exceptions}
    client.py → NO imports from case_engine / freshdesk.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from metrics_platform.config import MetricsPlatformConfig
from metrics_platform.exceptions import (
    MetricsPlatformApiError,
    MetricsPlatformAuthError,
    MetricsPlatformConnectionError,
    MetricsPlatformForbiddenError,
    MetricsPlatformNotFoundError,
    MetricsPlatformServerError,
    MetricsPlatformTimeoutError,
)

LOGGER = logging.getLogger(__name__)

_HTTP_BACKOFF_INITIAL_S = 1.0    # doubles each retry


class UptimeKumaClient:
    """
    Async READ-ONLY client for Uptime Kuma.

    Use as an async context manager or explicitly close():

        async with UptimeKumaClient(config) as client:
            entry = await client.get_entry_page()

        # or
        client = UptimeKumaClient(config)
        try:
            entry = await client.get_entry_page()
        finally:
            await client.close()
    """

    def __init__(
        self,
        config: MetricsPlatformConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._own_client = http_client is None
        # Keep the auth handler on the instance so we apply it per-request even
        # when the caller supplies their own httpx.AsyncClient (tests do this).
        self._auth = self._build_auth(config)
        headers = {
            "Accept":     "*/*",
            "User-Agent": config.user_agent,
        }
        self._client = http_client or httpx.AsyncClient(
            base_url=config.normalized_base_url,
            timeout=httpx.Timeout(config.timeout_s),
            headers=headers,
            auth=self._auth,
        )

    # ── Context manager ──────────────────────────────────────────────────────

    async def __aenter__(self) -> "UptimeKumaClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._own_client:
            try:
                await self._client.aclose()
            except Exception as exc:
                LOGGER.debug("uptime_kuma.close error=%s", exc)

    # ── Public API — READ ONLY ───────────────────────────────────────────────

    async def get_entry_page(self) -> dict[str, Any]:
        """GET /api/entry-page  — no auth."""
        return await self._get_json("/api/entry-page", need_auth=False)

    async def get_status_page(self, slug: str) -> dict[str, Any]:
        """GET /api/status-page/{slug}  — no auth."""
        return await self._get_json(f"/api/status-page/{slug}", need_auth=False)

    async def get_heartbeats(self, slug: str) -> dict[str, Any]:
        """GET /api/status-page/heartbeat/{slug}  — no auth."""
        return await self._get_json(
            f"/api/status-page/heartbeat/{slug}", need_auth=False
        )

    async def get_prometheus_metrics(self) -> str:
        """GET /metrics  — bearer/basic auth required."""
        response = await self._request_with_retry("GET", "/metrics", need_auth=True)
        return response.text

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _get_json(self, path: str, *, need_auth: bool) -> dict[str, Any]:
        response = await self._request_with_retry("GET", path, need_auth=need_auth)
        try:
            return response.json()
        except ValueError as exc:
            raise MetricsPlatformApiError(
                f"invalid JSON from {path}: {exc}",
                status_code=response.status_code,
                response_body=response.text[:500],
            )

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        need_auth: bool,
    ) -> httpx.Response:
        """
        Perform the HTTP request with the client's retry policy.

        Non-retriable failures raise their typed exception on first hit:
        401 / 403 / 404.

        Retriable failures (429, 5xx, timeouts, transient TCP errors) are
        retried up to `max_retries` times with exponential backoff.
        """
        # Auth resolution:
        #   need_auth=True  → apply the instance auth explicitly per-request
        #                     (also covers the test case where the caller
        #                     injected an httpx.AsyncClient without auth).
        #   need_auth=False → strip any client-level auth so public endpoints
        #                     receive no Authorization header.
        if need_auth:
            auth_override = self._auth if self._auth is not None else None
        else:
            auth_override = _NO_AUTH

        attempt = 0
        max_attempts = max(1, self._config.max_retries + 1)
        while True:
            attempt += 1
            try:
                response = await self._client.request(method, path, auth=auth_override)
            except httpx.TimeoutException as exc:
                if attempt >= max_attempts:
                    raise MetricsPlatformTimeoutError(
                        f"timeout after {attempt} attempts: {exc}"
                    ) from exc
                await asyncio.sleep(_backoff_delay(attempt))
                continue
            except httpx.ConnectError as exc:
                if attempt >= max_attempts:
                    raise MetricsPlatformConnectionError(
                        f"connection error after {attempt} attempts: {exc}"
                    ) from exc
                await asyncio.sleep(_backoff_delay(attempt))
                continue
            except httpx.RequestError as exc:
                if attempt >= max_attempts:
                    raise MetricsPlatformConnectionError(
                        f"request error after {attempt} attempts: {exc}"
                    ) from exc
                await asyncio.sleep(_backoff_delay(attempt))
                continue

            self._raise_if_client_error(response, path=path)

            if 500 <= response.status_code < 600:
                if attempt >= max_attempts:
                    raise MetricsPlatformServerError(
                        f"HTTP {response.status_code} from {path} "
                        f"after {attempt} attempts",
                        status_code=response.status_code,
                        response_body=_safe_body(response),
                    )
                await asyncio.sleep(_backoff_delay(attempt))
                continue

            if response.status_code == 429:
                if attempt >= max_attempts:
                    raise MetricsPlatformApiError(
                        f"HTTP 429 rate-limited on {path}",
                        status_code=429,
                        response_body=_safe_body(response),
                    )
                await asyncio.sleep(_backoff_delay(attempt))
                continue

            # Success (2xx) or unexpected 3xx.
            return response

    def _raise_if_client_error(self, response: httpx.Response, *, path: str) -> None:
        code = response.status_code
        if code == 401:
            raise MetricsPlatformAuthError(
                f"HTTP 401 on {path}",
                status_code=401,
                response_body=_safe_body(response),
            )
        if code == 403:
            raise MetricsPlatformForbiddenError(
                f"HTTP 403 on {path}",
                status_code=403,
                response_body=_safe_body(response),
            )
        if code == 404:
            raise MetricsPlatformNotFoundError(
                f"HTTP 404 on {path}",
                status_code=404,
                response_body=_safe_body(response),
            )

    # ── Auth ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_auth(config: MetricsPlatformConfig) -> httpx.Auth | None:
        # Sprint 2.52: If explicit Prometheus basic-auth credentials are set,
        # they take precedence over `auth_mode` for the /metrics endpoint.
        # This distinguishes:
        #   1. Uptime Kuma REST /metrics — Bearer with `METRICS_PLATFORM_API_KEY`
        #   2. Separate Prometheus scrape — Basic with
        #      `METRICS_PROMETHEUS_USERNAME` + `METRICS_PROMETHEUS_PASSWORD`
        if config.has_prometheus_credentials:
            return httpx.BasicAuth(
                config.prometheus_username, config.prometheus_password,
            )
        if config.auth_mode == "bearer":
            if not config.has_api_key:
                return None
            return _BearerAuth(config.api_key)
        if config.auth_mode == "basic":
            if not config.has_api_key:
                return None
            # Uptime Kuma expects "<api_key>:" (empty password) when basic auth
            # is used with the platform API key.
            return httpx.BasicAuth(config.api_key, "")
        return None


# ── Auth helpers ─────────────────────────────────────────────────────────────

class _BearerAuth(httpx.Auth):
    """Attach `Authorization: Bearer <token>` to every outbound request."""

    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request):  # pragma: no cover — trivial
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


class _NoAuth(httpx.Auth):
    """Do not attach an Authorization header to this request."""
    def auth_flow(self, request: httpx.Request):
        # Remove any auth header the AsyncClient default would otherwise apply.
        request.headers.pop("Authorization", None)
        yield request


_NO_AUTH: httpx.Auth = _NoAuth()


# ── Utility functions ───────────────────────────────────────────────────────

def _backoff_delay(attempt: int) -> float:
    """attempt >= 1 → 1 s, 2 s, 4 s, 8 s, capped at 10 s."""
    return min(10.0, _HTTP_BACKOFF_INITIAL_S * (2 ** (attempt - 1)))


def _safe_body(response: httpx.Response) -> Any:
    """Return either parsed JSON or the first 500 chars of text — never raises."""
    try:
        return response.json()
    except Exception:
        try:
            return response.text[:500]
        except Exception:
            return None


def build_uptime_kuma_client(
    *,
    config: MetricsPlatformConfig | None = None,
) -> UptimeKumaClient:
    """Convenience factory — reads config from env when not supplied."""
    return UptimeKumaClient(config or MetricsPlatformConfig.from_env())
