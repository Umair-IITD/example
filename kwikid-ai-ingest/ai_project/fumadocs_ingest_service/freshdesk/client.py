"""
freshdesk/client.py

Sprint 2.28.1: FreshdeskClient — production HTTP client for Freshdesk REST API v2.

Operations:
  get_ticket(ticket_id)                     → dict
  get_ticket_conversations(ticket_id)       → list[dict]
  add_private_note(ticket_id, body)         → dict
  add_public_reply(ticket_id, body)         → dict
  update_ticket(ticket_id, payload)         → dict
  add_tags(ticket_id, tags)                 → dict
  remove_tags(ticket_id, current, to_remove)→ dict

Design constraints:
  - Rate limit: 30 req/min (10 req/min headroom left for human agents; account cap 40/min)
  - Retries: up to 3 attempts, exponential backoff (1s, 2s, 4s), retryable on 429/5xx
  - Timeout: per FreshdeskConfig.timeout_seconds
  - Logging: structured, NEVER logs email addresses
  - Async: uses httpx.AsyncClient (called from FastAPI background tasks)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from freshdesk.freshdesk_exceptions import (
    FreshdeskApiError,
    FreshdeskAuthError,
    FreshdeskConnectionError,
    FreshdeskForbiddenError,
    FreshdeskNotFoundError,
    FreshdeskRateLimitError,
    FreshdeskServerError,
    FreshdeskTimeoutException,
    FreshdeskValidationError,
)
from freshdesk.freshdesk_models import FreshdeskConfig, FreshdeskConversation

LOGGER = logging.getLogger(__name__)

_MAX_RETRIES   = 3
_BACKOFF_BASE  = 1.0   # seconds; doubles per retry
_RATE_LIMIT_PER_MIN = 30
_RATE_WINDOW   = 60.0  # seconds


class _RateLimiter:
    """Sliding-window token bucket — 30 requests per 60-second window."""

    def __init__(self, max_requests: int = _RATE_LIMIT_PER_MIN, window: float = _RATE_WINDOW) -> None:
        self._max = max_requests
        self._window = window
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self._window
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) >= self._max:
                oldest = self._timestamps[0]
                wait = self._window - (now - oldest) + 0.05
                if wait > 0:
                    LOGGER.debug("freshdesk.rate_limiter: waiting %.2fs", wait)
                    await asyncio.sleep(wait)
                now = time.monotonic()
                cutoff = now - self._window
                self._timestamps = [t for t in self._timestamps if t > cutoff]
            self._timestamps.append(time.monotonic())


def _retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


class FreshdeskClient:
    """
    Async Freshdesk REST API v2 client.

    Thread/task safety: share one instance across all background tasks.
    The underlying httpx.AsyncClient is safe for concurrent coroutines.

    Inject http_client for testing (avoids real network calls).
    """

    def __init__(
        self,
        config: FreshdeskConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: _RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._rate_limiter = rate_limiter or _RateLimiter()
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=config.base_url,
                auth=httpx.BasicAuth(config.api_key, "X"),
                timeout=config.timeout_seconds,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            self._owns_client = True

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def get_ticket(self, ticket_id: int | str) -> dict[str, Any]:
        """GET /api/v2/tickets/{ticket_id}"""
        path = f"/api/v2/tickets/{ticket_id}"
        response = await self._request_with_retry("GET", path)
        data = response.json()
        LOGGER.info("freshdesk.get_ticket: ticket_id=%s status=%d", ticket_id, response.status_code)
        return data

    async def get_ticket_conversations(self, ticket_id: int | str) -> list[dict[str, Any]]:
        """GET /api/v2/tickets/{ticket_id}/conversations"""
        path = f"/api/v2/tickets/{ticket_id}/conversations"
        response = await self._request_with_retry("GET", path)
        data = response.json()
        LOGGER.info(
            "freshdesk.get_conversations: ticket_id=%s count=%d",
            ticket_id, len(data) if isinstance(data, list) else 0,
        )
        return data if isinstance(data, list) else []

    async def add_private_note(self, ticket_id: int | str, body: str) -> dict[str, Any]:
        """POST /api/v2/tickets/{ticket_id}/notes  (private=True)"""
        path = f"/api/v2/tickets/{ticket_id}/notes"
        payload = {"body": body, "private": True}
        response = await self._request_with_retry("POST", path, json=payload)
        data = response.json()
        LOGGER.info("freshdesk.add_private_note: ticket_id=%s note_id=%s", ticket_id, data.get("id"))
        return data

    async def add_public_reply(self, ticket_id: int | str, body: str) -> dict[str, Any]:
        """POST /api/v2/tickets/{ticket_id}/reply"""
        path = f"/api/v2/tickets/{ticket_id}/reply"
        payload = {"body": body}
        response = await self._request_with_retry("POST", path, json=payload)
        data = response.json()
        LOGGER.info("freshdesk.add_public_reply: ticket_id=%s note_id=%s", ticket_id, data.get("id"))
        return data

    async def update_ticket(self, ticket_id: int | str, payload: dict[str, Any]) -> dict[str, Any]:
        """PUT /api/v2/tickets/{ticket_id}"""
        path = f"/api/v2/tickets/{ticket_id}"
        response = await self._request_with_retry("PUT", path, json=payload)
        data = response.json()
        LOGGER.info("freshdesk.update_ticket: ticket_id=%s", ticket_id)
        return data

    async def add_tags(self, ticket_id: int | str, tags: list[str]) -> dict[str, Any]:
        """Add tags to a ticket by fetching current tags and merging."""
        ticket = await self.get_ticket(ticket_id)
        current_tags: list[str] = ticket.get("tags", [])
        merged = list(dict.fromkeys(current_tags + tags))
        return await self.update_ticket(ticket_id, {"tags": merged})

    async def remove_tags(
        self,
        ticket_id: int | str,
        current_tags: list[str],
        tags_to_remove: list[str],
    ) -> dict[str, Any]:
        """Remove specific tags from a ticket."""
        remove_set = set(tags_to_remove)
        updated = [t for t in current_tags if t not in remove_set]
        return await self.update_ticket(ticket_id, {"tags": updated})

    # ── Sprint 2.48: additional endpoints for complete API coverage ────────────

    async def list_tickets(
        self,
        *,
        status: int | None = None,
        group_id: int | None = None,
        email: str | None = None,
        page: int = 1,
        per_page: int = 30,
        order_by: str = "created_at",
        order_type: str = "desc",
    ) -> list[dict[str, Any]]:
        """
        GET /api/v2/tickets — list tickets with filters.

        Source: api_reference.md Section 3.3.
        """
        path = "/api/v2/tickets"
        params: dict[str, Any] = {
            "page":       page,
            "per_page":   min(max(per_page, 1), 100),
            "order_by":   order_by,
            "order_type": order_type,
        }
        if status is not None:
            params["status"] = status
        if group_id is not None:
            params["group_id"] = group_id
        if email:
            params["email"] = email
        response = await self._request_with_retry("GET", path, params=params)
        data = response.json()
        LOGGER.info(
            "freshdesk.list_tickets: count=%d page=%d",
            len(data) if isinstance(data, list) else 0, page,
        )
        return data if isinstance(data, list) else []

    async def search_tickets(self, query: str) -> dict[str, Any]:
        """
        GET /api/v2/search/tickets — search using Freshdesk query DSL.

        Source: api_reference.md Section 3.4.
        Freshdesk returns {"total": int, "results": list[dict]}.
        """
        path = "/api/v2/search/tickets"
        response = await self._request_with_retry(
            "GET", path, params={"query": query}
        )
        data = response.json()
        LOGGER.info(
            "freshdesk.search_tickets: total=%s",
            data.get("total") if isinstance(data, dict) else "?",
        )
        return data if isinstance(data, dict) else {"total": 0, "results": []}

    async def list_agents(self) -> list[dict[str, Any]]:
        """
        GET /api/v2/agents — enumerate configured agents.

        Source: api_reference.md Section 5.1.
        """
        path = "/api/v2/agents"
        response = await self._request_with_retry("GET", path)
        data = response.json()
        LOGGER.info(
            "freshdesk.list_agents: count=%d",
            len(data) if isinstance(data, list) else 0,
        )
        return data if isinstance(data, list) else []

    async def list_groups(self) -> list[dict[str, Any]]:
        """
        GET /api/v2/groups — enumerate configured groups.

        Source: api_reference.md Section 5.2. Live groups (kwikid.freshdesk.com):
          L1              84000293343
          L2              84000293342
          Tech Assign     84000293351
          Business Analyst 84000294040
          General         84000293360
        """
        path = "/api/v2/groups"
        response = await self._request_with_retry("GET", path)
        data = response.json()
        LOGGER.info(
            "freshdesk.list_groups: count=%d",
            len(data) if isinstance(data, list) else 0,
        )
        return data if isinstance(data, list) else []

    async def add_public_reply_with_cc(
        self,
        ticket_id: int | str,
        body: str,
        *,
        cc_emails: list[str] | None = None,
        bcc_emails: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        POST /api/v2/tickets/{ticket_id}/reply — public reply with CC / BCC.

        Source: notes_and_replies.md Section 5.2 + api_reference.md Section 4.2.
        """
        path = f"/api/v2/tickets/{ticket_id}/reply"
        payload: dict[str, Any] = {"body": body}
        if cc_emails:
            payload["cc_emails"] = list(cc_emails)
        if bcc_emails:
            payload["bcc_emails"] = list(bcc_emails)
        response = await self._request_with_retry("POST", path, json=payload)
        data = response.json()
        LOGGER.info(
            "freshdesk.add_public_reply_with_cc: ticket_id=%s cc_count=%d",
            ticket_id, len(cc_emails or []),
        )
        return data

    # ── HTTP core ─────────────────────────────────────────────────────────────

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                response = await self._client.request(
                    method, path, json=json, params=params
                )
                self._check_response(response)
                return response
            except (FreshdeskRateLimitError, FreshdeskServerError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
                    LOGGER.warning(
                        "freshdesk.retry: attempt=%d/%d path=%s status=%s backoff=%.1fs",
                        attempt, _MAX_RETRIES, path,
                        getattr(exc, "status_code", "?"), backoff,
                    )
                    await asyncio.sleep(backoff)
            except (FreshdeskAuthError, FreshdeskForbiddenError,
                    FreshdeskNotFoundError, FreshdeskValidationError, FreshdeskApiError):
                raise
            except httpx.TimeoutException as exc:
                raise FreshdeskTimeoutException(str(exc)) from exc
            except httpx.RequestError as exc:
                raise FreshdeskConnectionError(str(exc)) from exc
        raise last_exc  # type: ignore[misc]

    def _check_response(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            self._inspect_rate_limit_headers(response.headers)
            return
        try:
            body = response.json()
        except Exception:
            body = {}
        # Log rate limit context on 429 before raising
        if status == 429:
            self._inspect_rate_limit_headers(response.headers, warn=True)
        msg = f"Freshdesk HTTP {status}"
        if status == 401:
            raise FreshdeskAuthError(msg, status_code=status, response_body=body)
        if status == 403:
            raise FreshdeskForbiddenError(msg, status_code=status, response_body=body)
        if status == 404:
            raise FreshdeskNotFoundError(msg, status_code=status, response_body=body)
        if status == 422:
            raise FreshdeskValidationError(msg, status_code=status, response_body=body)
        if status == 429:
            raise FreshdeskRateLimitError(msg, status_code=status, response_body=body)
        if 500 <= status < 600:
            raise FreshdeskServerError(msg, status_code=status, response_body=body)
        raise FreshdeskApiError(msg, status_code=status, response_body=body)

    def _inspect_rate_limit_headers(
        self,
        headers: httpx.Headers,
        *,
        warn: bool = False,
    ) -> None:
        """
        Read Freshdesk rate limit response headers and emit structured logs.

        Headers (verified from live audit, Section 16 SPRINT_2_28_FINAL_FRESHDESK_INTEGRATION_AUDIT):
          x-ratelimit-total:     account-wide limit (40.0 for current account)
          x-ratelimit-remaining: requests remaining in current window
          retry-after:           seconds to wait when 429 received
        """
        remaining = headers.get("x-ratelimit-remaining")
        total = headers.get("x-ratelimit-total")
        retry_after = headers.get("retry-after")

        if remaining is not None:
            try:
                remaining_float = float(remaining)
                level = LOGGER.warning if (remaining_float < 5 or warn) else LOGGER.debug
                level(
                    "freshdesk.rate_limit: remaining=%.0f total=%s",
                    remaining_float, total or "?",
                )
            except (TypeError, ValueError):
                LOGGER.debug("freshdesk.rate_limit: unparseable remaining=%s", remaining)

        if retry_after:
            LOGGER.warning(
                "freshdesk.rate_limit: retry_after=%s — backing off", retry_after
            )

    # ── Context manager support ────────────────────────────────────────────────

    async def __aenter__(self) -> "FreshdeskClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()


def build_freshdesk_client(
    *,
    domain: str,
    api_key: str,
    timeout_seconds: float = 10.0,
    http_client: httpx.AsyncClient | None = None,
) -> FreshdeskClient:
    config = FreshdeskConfig(domain=domain, api_key=api_key, timeout_seconds=timeout_seconds)
    return FreshdeskClient(config, http_client=http_client)
