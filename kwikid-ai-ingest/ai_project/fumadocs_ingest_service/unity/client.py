"""
unity/client.py

Sprint 2.51: Async READ-ONLY httpx client for Unity Admin Portal.

Endpoints exposed (SOT api_reference.md):
  - POST /v1/agent/generate_token           (delegated to UnityTokenManager)
  - GET  /api/v1/getAllUserSession/{domain}/{phone_number}
  - GET  /v1/session/get_details/{session_id}
  - GET  /v1/health / /api/v1/health

Write endpoints (POST /v1/agent/sendLink/) are NOT wrapped — read-only
integration constraint enforced structurally.

Design rules:
- Uses the custom `auth: <token>` header (NOT `Authorization: Bearer <token>`).
- On 401 → invalidate token cache + retry once.
- On 5xx → retry with exponential backoff up to max_retries.
- Never raises to callers except the typed exceptions in unity/exceptions.py.

Dependency direction:
    client.py → httpx + stdlib + unity.{config, exceptions, token_manager, traces}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from unity.config import UnityConfig
from unity.exceptions import (
    UnityApiError,
    UnityAuthenticationFailure,
    UnityNetworkFailure,
    UnityServiceUnavailable,
    UnitySessionNotFound,
    UnityTimeoutFailure,
    UnityUnexpectedResponse,
)
from unity.token_manager import UnityTokenManager
from unity.traces import (
    TRACE_ENTER_UNITY_DETAILS,
    TRACE_ENTER_UNITY_LOOKUP,
    TRACE_EXIT_UNITY_DETAILS,
    TRACE_EXIT_UNITY_LOOKUP,
    TRACE_UNITY_API_FAILURE,
    emit_unity_trace,
)

LOGGER = logging.getLogger(__name__)

_RETRIABLE_STATUS = {500, 502, 503, 504}


class UnityClient:
    """
    Async client for Unity Admin Portal READ endpoints.

    Instantiated per investigation (short-lived) OR per app lifespan
    (long-lived; token cache is on the instance). Use async context manager:

        async with UnityClient(config) as client:
            sessions = await client.get_all_user_sessions("7045722923")
    """

    def __init__(
        self,
        config: UnityConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        token_manager: UnityTokenManager | None = None,
    ) -> None:
        self._config = config
        self._own_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=config.normalized_base_url,
            headers={
                "Accept":     "application/json",
                "User-Agent": config.user_agent,
            },
            timeout=httpx.Timeout(
                connect=config.timeout_connect_s,
                read=config.timeout_read_s,
                write=config.timeout_read_s,
                pool=config.timeout_connect_s,
            ),
        )
        self._tokens = token_manager or UnityTokenManager(config)

    # ── Context manager ─────────────────────────────────────────────────────

    async def __aenter__(self) -> "UnityClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._own_client:
            try:
                await self._client.aclose()
            except Exception as exc:
                LOGGER.debug("unity.close: error=%s", exc)

    # ── Public API — READ ONLY ──────────────────────────────────────────────

    async def health(self) -> str:
        """GET /v1/health — plain-text response 'ok'."""
        response = await self._client.get("/v1/health")
        return response.text.strip()

    async def get_all_user_sessions(self, phone_number: str) -> dict[str, Any]:
        """
        GET /api/v1/getAllUserSession/{domain}/{phone_number}

        Never raises UnitySessionNotFound — an empty session_count is a
        valid business outcome.
        """
        path = f"/api/v1/getAllUserSession/{self._config.domain}/{phone_number}"
        emit_unity_trace(
            TRACE_ENTER_UNITY_LOOKUP,
            tool="unity.client", tenant=self._config.domain,
            endpoint=path, status="DISPATCHED",
        )
        try:
            body = await self._authenticated_get_json(path)
            count = int(body.get("session_count") or 0)
            emit_unity_trace(
                TRACE_EXIT_UNITY_LOOKUP,
                tool="unity.client", tenant=self._config.domain,
                endpoint=path, status=f"OK:{count}",
            )
            return body
        except Exception as exc:
            emit_unity_trace(
                TRACE_EXIT_UNITY_LOOKUP,
                tool="unity.client", tenant=self._config.domain,
                endpoint=path, status=f"ERROR:{type(exc).__name__}",
            )
            raise

    async def get_session_details(self, session_id: str) -> dict[str, Any]:
        """
        GET /v1/session/get_details/{session_id}

        Raises UnitySessionNotFound when Unity returns 400
        (`{"e":"list index out of range","msg":"Invalid session id"...}`) or
        an empty session_data body.
        """
        path = f"/v1/session/get_details/{session_id}"
        emit_unity_trace(
            TRACE_ENTER_UNITY_DETAILS,
            tool="unity.client", tenant=self._config.domain,
            endpoint=path, status="DISPATCHED",
        )
        try:
            response = await self._authenticated_get(path)
        except Exception as exc:
            emit_unity_trace(
                TRACE_EXIT_UNITY_DETAILS,
                tool="unity.client", tenant=self._config.domain,
                endpoint=path, status=f"ERROR:{type(exc).__name__}",
            )
            raise

        if response.status_code == 400:
            emit_unity_trace(
                TRACE_EXIT_UNITY_DETAILS,
                tool="unity.client", tenant=self._config.domain,
                endpoint=path, status="NOT_FOUND",
            )
            raise UnitySessionNotFound(session_id, kind="session_id")

        try:
            body = response.json()
        except ValueError as exc:
            emit_unity_trace(
                TRACE_EXIT_UNITY_DETAILS,
                tool="unity.client", tenant=self._config.domain,
                endpoint=path, status="INVALID_JSON",
            )
            raise UnityUnexpectedResponse(
                f"invalid JSON from {path}: {exc}", body=response.text[:200]
            ) from exc

        emit_unity_trace(
            TRACE_EXIT_UNITY_DETAILS,
            tool="unity.client", tenant=self._config.domain,
            endpoint=path, status="OK",
        )
        return body

    # ── Internal ────────────────────────────────────────────────────────────

    async def _authenticated_get(self, path: str) -> httpx.Response:
        """
        GET with `auth:` header and full retry policy.

        Behaviour:
          - 401 first hit → invalidate cache + retry once with fresh token
          - 5xx → exponential backoff up to max_retries
          - Timeouts / network errors → typed exception
          - Other 4xx → raise UnityApiError (except 400 which caller handles)
        """
        max_attempts = max(1, self._config.max_retries + 1)
        attempt = 0
        did_reauth = False

        while True:
            attempt += 1
            token = await self._tokens.get_token(self._client)
            try:
                response = await self._client.get(
                    path,
                    headers={"auth": token},
                )
            except httpx.TimeoutException as exc:
                emit_unity_trace(
                    TRACE_UNITY_API_FAILURE,
                    tool="unity.client", tenant=self._config.domain,
                    endpoint=path, status="TIMEOUT",
                )
                if attempt >= max_attempts:
                    raise UnityTimeoutFailure(
                        f"timeout on {path} after {attempt} attempts"
                    ) from exc
                await asyncio.sleep(self._backoff(attempt))
                continue
            except (httpx.ConnectError, httpx.RequestError) as exc:
                emit_unity_trace(
                    TRACE_UNITY_API_FAILURE,
                    tool="unity.client", tenant=self._config.domain,
                    endpoint=path, status="NETWORK",
                )
                if attempt >= max_attempts:
                    raise UnityNetworkFailure(
                        f"connection error on {path}: {exc}"
                    ) from exc
                await asyncio.sleep(self._backoff(attempt))
                continue

            code = response.status_code

            if code == 401:
                emit_unity_trace(
                    TRACE_UNITY_API_FAILURE,
                    tool="unity.client", tenant=self._config.domain,
                    endpoint=path, status="HTTP_401",
                )
                if did_reauth:
                    raise UnityAuthenticationFailure(
                        f"still 401 after re-auth on {path}",
                        cause="RE_AUTH_FAILED",
                    )
                self._tokens.invalidate()
                did_reauth = True
                continue    # retry immediately with fresh token

            if code in _RETRIABLE_STATUS:
                emit_unity_trace(
                    TRACE_UNITY_API_FAILURE,
                    tool="unity.client", tenant=self._config.domain,
                    endpoint=path, status=f"HTTP_{code}",
                )
                if attempt >= max_attempts:
                    raise UnityServiceUnavailable(
                        f"HTTP {code} on {path} after {attempt} attempts",
                        status_code=code,
                    )
                await asyncio.sleep(self._backoff(attempt))
                continue

            if code == 403:
                raise UnityAuthenticationFailure(
                    f"HTTP 403 on {path}",
                    cause="FORBIDDEN",
                )
            if code == 404:
                raise UnityApiError(
                    f"HTTP 404 on {path}",
                    status_code=404,
                    response_body=_safe_body(response),
                )

            return response

    async def _authenticated_get_json(self, path: str) -> dict[str, Any]:
        response = await self._authenticated_get(path)
        try:
            body = response.json()
        except ValueError as exc:
            raise UnityUnexpectedResponse(
                f"invalid JSON from {path}: {exc}", body=response.text[:200]
            ) from exc
        if not isinstance(body, dict):
            raise UnityUnexpectedResponse(
                f"unexpected shape from {path}: expected object, got {type(body).__name__}",
                body=body,
            )
        return body

    def _backoff(self, attempt: int) -> float:
        return self._config.retry_backoff_s * (2 ** (attempt - 1))


# ── Utilities ─────────────────────────────────────────────────────────────

def _safe_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        try:
            return response.text[:200]
        except Exception:
            return None


def build_unity_client(*, config: UnityConfig | None = None) -> UnityClient:
    """Convenience factory that reads config from env when omitted."""
    return UnityClient(config or UnityConfig.from_env())
