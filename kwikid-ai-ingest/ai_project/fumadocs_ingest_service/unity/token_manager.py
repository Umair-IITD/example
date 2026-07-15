"""
unity/token_manager.py

Sprint 2.51: JWT token lifecycle for Unity Admin Portal.

Responsibilities:
- Generate token via POST /v1/agent/generate_token
- Cache with proactive expiry buffer (config.token_refresh_buffer_s)
- Refresh automatically before expiry
- Force-invalidate on 401
- Never expose token in logs / exceptions

Design rules:
- Async-safe: asyncio.Lock guards refresh so only one in-flight refresh at
  a time even under concurrent tool invocations.
- JWT `exp` decoded WITHOUT signature verification — we trust Unity to
  issue only valid tokens; we only need the expiry to schedule refresh.
- Never raise from `get()` unless every retry has failed — return the
  fresh token or raise UnityAuthenticationFailure.

Dependency direction:
    token_manager.py → httpx + stdlib + unity.{config, exceptions, traces}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass

import httpx

from unity.config import UnityConfig
from unity.exceptions import (
    UnityAuthenticationFailure,
    UnityNetworkFailure,
    UnityTimeoutFailure,
    UnityUnexpectedResponse,
)
from unity.traces import (
    TRACE_ENTER_UNITY_TOKEN,
    TRACE_EXIT_UNITY_TOKEN,
    TRACE_UNITY_AUTH_FAILURE,
    emit_unity_trace,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CachedToken:
    """Immutable snapshot of the current cached token."""
    value: str
    exp:   float

    @property
    def masked(self) -> str:
        """8-char prefix + '****' — safe to log for correlation only."""
        if not self.value:
            return ""
        return f"{self.value[:8]}****" if len(self.value) > 8 else "****"


class UnityTokenManager:
    """
    Async, thread-safe JWT cache with proactive refresh.

    Use pattern:
        mgr = UnityTokenManager(config)
        try:
            token = await mgr.get_token(http_client)
            # ... use token ...
        finally:
            pass  # nothing to close

        # On 401:
        mgr.invalidate()
    """

    def __init__(self, config: UnityConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._current: _CachedToken | None = None

    # ── Public API ───────────────────────────────────────────────────────────

    async def get_token(self, http_client: httpx.AsyncClient) -> str:
        """
        Return a valid Unity JWT. Fetches a fresh one if the cache is empty
        or the buffer window has been crossed.
        """
        if self._is_current_valid():
            return self._current.value  # type: ignore[union-attr]

        async with self._lock:
            # Double-check after acquiring the lock.
            if self._is_current_valid():
                return self._current.value  # type: ignore[union-attr]

            self._current = await self._fetch(http_client)
            return self._current.value

    def invalidate(self) -> None:
        """Clear the cache; next call to get_token refreshes."""
        self._current = None

    @property
    def is_cached(self) -> bool:
        return self._current is not None

    # ── Internal ─────────────────────────────────────────────────────────────

    def _is_current_valid(self) -> bool:
        c = self._current
        if c is None or not c.value:
            return False
        return time.time() < (c.exp - self._config.token_refresh_buffer_s)

    async def _fetch(self, http_client: httpx.AsyncClient) -> _CachedToken:
        emit_unity_trace(
            TRACE_ENTER_UNITY_TOKEN,
            tool="unity.token", tenant=self._config.domain,
            endpoint="/v1/agent/generate_token", status="DISPATCHED",
        )
        if not self._config.password:
            emit_unity_trace(
                TRACE_UNITY_AUTH_FAILURE,
                tool="unity.token", tenant=self._config.domain,
                endpoint="/v1/agent/generate_token", status="MISSING_CREDENTIALS",
            )
            raise UnityAuthenticationFailure(
                "Unity password not configured (UNITY_PASSWORD env var)",
                cause="MISSING_CREDENTIALS",
            )

        payload = {
            "username": self._config.username,
            "password": self._config.password,
        }
        try:
            response = await http_client.post(
                "/v1/agent/generate_token",
                json=payload,
                timeout=httpx.Timeout(
                    connect=self._config.timeout_connect_s,
                    read=self._config.timeout_read_s,
                    write=self._config.timeout_read_s,
                    pool=self._config.timeout_connect_s,
                ),
            )
        except httpx.TimeoutException as exc:
            emit_unity_trace(
                TRACE_UNITY_AUTH_FAILURE,
                tool="unity.token", tenant=self._config.domain,
                endpoint="/v1/agent/generate_token", status="TIMEOUT",
            )
            raise UnityTimeoutFailure(
                f"token fetch timed out after {self._config.timeout_read_s}s"
            ) from exc
        except (httpx.ConnectError, httpx.RequestError) as exc:
            emit_unity_trace(
                TRACE_UNITY_AUTH_FAILURE,
                tool="unity.token", tenant=self._config.domain,
                endpoint="/v1/agent/generate_token", status="NETWORK_ERROR",
            )
            raise UnityNetworkFailure(f"token fetch failed: {exc}") from exc

        if response.status_code == 401 or response.status_code == 403:
            emit_unity_trace(
                TRACE_UNITY_AUTH_FAILURE,
                tool="unity.token", tenant=self._config.domain,
                endpoint="/v1/agent/generate_token",
                status=f"HTTP_{response.status_code}",
            )
            raise UnityAuthenticationFailure(
                f"token endpoint returned HTTP {response.status_code}",
                cause=f"HTTP_{response.status_code}",
            )

        if response.status_code >= 500:
            emit_unity_trace(
                TRACE_UNITY_AUTH_FAILURE,
                tool="unity.token", tenant=self._config.domain,
                endpoint="/v1/agent/generate_token",
                status=f"HTTP_{response.status_code}",
            )
            raise UnityAuthenticationFailure(
                f"token endpoint returned HTTP {response.status_code}",
                cause=f"HTTP_{response.status_code}",
            )

        try:
            data = response.json()
        except ValueError as exc:
            emit_unity_trace(
                TRACE_UNITY_AUTH_FAILURE,
                tool="unity.token", tenant=self._config.domain,
                endpoint="/v1/agent/generate_token", status="INVALID_JSON",
            )
            raise UnityUnexpectedResponse(
                f"token endpoint returned invalid JSON: {exc}",
                body=response.text[:200],
            ) from exc

        # SOT: response field is `Token` (capital T)
        token = data.get("Token") or data.get("token")
        if not token or not isinstance(token, str):
            emit_unity_trace(
                TRACE_UNITY_AUTH_FAILURE,
                tool="unity.token", tenant=self._config.domain,
                endpoint="/v1/agent/generate_token", status="NO_TOKEN_FIELD",
            )
            raise UnityUnexpectedResponse(
                "token endpoint response missing `Token` field",
                body={"keys": sorted(data.keys()) if isinstance(data, dict) else "-"},
            )

        exp = _decode_jwt_exp(token, default=time.time() + 3600)
        cached = _CachedToken(value=token, exp=exp)
        emit_unity_trace(
            TRACE_EXIT_UNITY_TOKEN,
            tool="unity.token", tenant=self._config.domain,
            endpoint="/v1/agent/generate_token",
            status=f"OK:{cached.masked}",
        )
        return cached


# ── JWT decoding (signature-unaware; we only need `exp`) ─────────────────────

def _decode_jwt_exp(token: str, *, default: float) -> float:
    """
    Extract the `exp` claim from the JWT payload without verifying the
    signature. Returns `default` if the token is malformed or has no `exp`.
    Never raises.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return default
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        obj = json.loads(decoded)
        exp = obj.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
        return default
    except Exception:
        return default
