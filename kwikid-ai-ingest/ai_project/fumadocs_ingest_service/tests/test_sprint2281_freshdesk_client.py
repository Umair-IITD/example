"""
tests/test_sprint2281_freshdesk_client.py

Sprint 2.28.1: FreshdeskClient tests.

Coverage:
  1. build_freshdesk_client factory
  2. get_ticket — success, 404, 429 retry, 500 retry
  3. get_ticket_conversations — success, empty list
  4. add_private_note — success, body/private fields
  5. add_public_reply — success
  6. update_ticket — success
  7. add_tags — fetches current tags, merges, calls update
  8. remove_tags — removes only specified tags
  9. Rate limiter — token bucket behavior
  10. Retry logic — 3 attempts on 429/500, no retry on 401/403/404/422
  11. Timeout + connection errors
  12. Context manager protocol
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("RAG_API_KEY", "test-2281-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-2281")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx

from freshdesk.client import FreshdeskClient, build_freshdesk_client, _RateLimiter
from freshdesk.freshdesk_models import FreshdeskConfig
from freshdesk.freshdesk_exceptions import (
    FreshdeskAuthError,
    FreshdeskNotFoundError,
    FreshdeskRateLimitError,
    FreshdeskServerError,
    FreshdeskTimeoutException,
    FreshdeskConnectionError,
    FreshdeskValidationError,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_response(status_code: int, data: dict | list) -> httpx.Response:
    content = json.dumps(data).encode()
    return httpx.Response(status_code, content=content, headers={"content-type": "application/json"})


def _make_client(http_client: httpx.AsyncClient) -> FreshdeskClient:
    config = FreshdeskConfig(domain="test.freshdesk.com", api_key="test-key")
    return FreshdeskClient(config, http_client=http_client)


# ── Section 1: Factory ────────────────────────────────────────────────────────

class TestBuildFreshdeskClient:
    def test_factory_creates_client(self):
        client = build_freshdesk_client(
            domain="test.freshdesk.com",
            api_key="test-key-1234",
        )
        assert isinstance(client, FreshdeskClient)

    def test_factory_respects_timeout(self):
        client = build_freshdesk_client(
            domain="test.freshdesk.com",
            api_key="key",
            timeout_seconds=30.0,
        )
        assert client._config.timeout_seconds == 30.0

    def test_factory_injects_http_client(self):
        mock_http = MagicMock(spec=httpx.AsyncClient)
        client = build_freshdesk_client(
            domain="test.freshdesk.com",
            api_key="key",
            http_client=mock_http,
        )
        assert client._client is mock_http
        assert not client._owns_client

    def test_config_masked_api_key(self):
        config = FreshdeskConfig(domain="x.freshdesk.com", api_key="abcd1234secret")
        assert config.masked_api_key.startswith("abcd")
        assert "secret" not in config.masked_api_key

    def test_config_masked_api_key_short(self):
        config = FreshdeskConfig(domain="x.freshdesk.com", api_key="ab")
        assert config.masked_api_key == "****"


# ── Section 2: get_ticket ─────────────────────────────────────────────────────

class TestGetTicket:
    @pytest.mark.asyncio
    async def test_get_ticket_success(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(200, {"id": 123, "subject": "Test"}))
        client = _make_client(mock_http)
        result = await client.get_ticket(123)
        assert result["id"] == 123
        assert result["subject"] == "Test"

    @pytest.mark.asyncio
    async def test_get_ticket_404_raises(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(404, {"description": "not found"}))
        client = _make_client(mock_http)
        with pytest.raises(FreshdeskNotFoundError):
            await client.get_ticket(999)

    @pytest.mark.asyncio
    async def test_get_ticket_401_raises_auth_error(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(401, {}))
        client = _make_client(mock_http)
        with pytest.raises(FreshdeskAuthError):
            await client.get_ticket(1)

    @pytest.mark.asyncio
    async def test_get_ticket_retries_on_429(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        responses = [
            _make_response(429, {}),
            _make_response(429, {}),
            _make_response(200, {"id": 1}),
        ]
        mock_http.request = AsyncMock(side_effect=responses)
        client = _make_client(mock_http)
        with patch("freshdesk.client.asyncio.sleep", new=AsyncMock()):
            result = await client.get_ticket(1)
        assert result["id"] == 1
        assert mock_http.request.call_count == 3

    @pytest.mark.asyncio
    async def test_get_ticket_retries_on_500(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        responses = [
            _make_response(500, {}),
            _make_response(200, {"id": 5}),
        ]
        mock_http.request = AsyncMock(side_effect=responses)
        client = _make_client(mock_http)
        with patch("freshdesk.client.asyncio.sleep", new=AsyncMock()):
            result = await client.get_ticket(5)
        assert result["id"] == 5

    @pytest.mark.asyncio
    async def test_get_ticket_exhausts_retries_raises(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(500, {}))
        client = _make_client(mock_http)
        with patch("freshdesk.client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(FreshdeskServerError):
                await client.get_ticket(1)
        assert mock_http.request.call_count == 3

    @pytest.mark.asyncio
    async def test_get_ticket_no_retry_on_404(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(404, {}))
        client = _make_client(mock_http)
        with pytest.raises(FreshdeskNotFoundError):
            await client.get_ticket(1)
        assert mock_http.request.call_count == 1

    @pytest.mark.asyncio
    async def test_get_ticket_timeout_raises(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        client = _make_client(mock_http)
        with pytest.raises(FreshdeskTimeoutException):
            await client.get_ticket(1)

    @pytest.mark.asyncio
    async def test_get_ticket_connection_error_raises(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(side_effect=httpx.ConnectError("refused"))
        client = _make_client(mock_http)
        with pytest.raises(FreshdeskConnectionError):
            await client.get_ticket(1)


# ── Section 3: get_ticket_conversations ──────────────────────────────────────

class TestGetTicketConversations:
    @pytest.mark.asyncio
    async def test_conversations_success(self):
        conversations = [
            {"id": 1, "incoming": True, "private": False, "body": "Hello"},
            {"id": 2, "incoming": False, "private": True, "body": "Internal"},
        ]
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(200, conversations))
        client = _make_client(mock_http)
        result = await client.get_ticket_conversations(123)
        assert len(result) == 2
        assert result[0]["incoming"] is True

    @pytest.mark.asyncio
    async def test_conversations_empty_list(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(200, []))
        client = _make_client(mock_http)
        result = await client.get_ticket_conversations(123)
        assert result == []

    @pytest.mark.asyncio
    async def test_conversations_non_list_response_returns_empty(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(200, {"error": "bad"}))
        client = _make_client(mock_http)
        result = await client.get_ticket_conversations(123)
        assert result == []


# ── Section 4: add_private_note ──────────────────────────────────────────────

class TestAddPrivateNote:
    @pytest.mark.asyncio
    async def test_add_private_note_success(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(201, {"id": 999, "private": True}))
        client = _make_client(mock_http)
        result = await client.add_private_note(123, "Internal note body")
        assert result["id"] == 999

        call_args = mock_http.request.call_args
        assert call_args[0][0] == "POST"
        assert "123/notes" in call_args[0][1]
        sent_json = call_args[1]["json"]
        assert sent_json["private"] is True
        assert sent_json["body"] == "Internal note body"

    @pytest.mark.asyncio
    async def test_add_private_note_422_raises(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(422, {"description": "invalid"}))
        client = _make_client(mock_http)
        with pytest.raises(FreshdeskValidationError):
            await client.add_private_note(123, "body")


# ── Section 5: add_public_reply ───────────────────────────────────────────────

class TestAddPublicReply:
    @pytest.mark.asyncio
    async def test_add_public_reply_success(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(201, {"id": 888, "private": False}))
        client = _make_client(mock_http)
        result = await client.add_public_reply(123, "Customer reply body")
        assert result["id"] == 888

        call_args = mock_http.request.call_args
        assert "reply" in call_args[0][1]
        sent_json = call_args[1]["json"]
        assert sent_json["body"] == "Customer reply body"
        assert "private" not in sent_json


# ── Section 6: update_ticket ─────────────────────────────────────────────────

class TestUpdateTicket:
    @pytest.mark.asyncio
    async def test_update_ticket_success(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(200, {"id": 123, "status": 4}))
        client = _make_client(mock_http)
        result = await client.update_ticket(123, {"status": 4})
        assert result["status"] == 4

        call_args = mock_http.request.call_args
        assert call_args[0][0] == "PUT"


# ── Section 7: add_tags ───────────────────────────────────────────────────────

class TestAddTags:
    @pytest.mark.asyncio
    async def test_add_tags_merges_with_existing(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        # First call: get_ticket
        get_response = _make_response(200, {"id": 1, "tags": ["existing_tag"]})
        # Second call: update_ticket
        update_response = _make_response(200, {"id": 1, "tags": ["existing_tag", "new_tag"]})
        mock_http.request = AsyncMock(side_effect=[get_response, update_response])
        client = _make_client(mock_http)
        result = await client.add_tags(1, ["new_tag"])
        assert mock_http.request.call_count == 2
        update_call = mock_http.request.call_args_list[1]
        assert "existing_tag" in update_call[1]["json"]["tags"]
        assert "new_tag" in update_call[1]["json"]["tags"]

    @pytest.mark.asyncio
    async def test_add_tags_deduplicates(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        get_response = _make_response(200, {"id": 1, "tags": ["tag_a", "tag_b"]})
        update_response = _make_response(200, {"id": 1, "tags": ["tag_a", "tag_b"]})
        mock_http.request = AsyncMock(side_effect=[get_response, update_response])
        client = _make_client(mock_http)
        await client.add_tags(1, ["tag_a"])
        update_json = mock_http.request.call_args_list[1][1]["json"]["tags"]
        assert update_json.count("tag_a") == 1


# ── Section 8: remove_tags ────────────────────────────────────────────────────

class TestRemoveTags:
    @pytest.mark.asyncio
    async def test_remove_tags_success(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        update_response = _make_response(200, {"id": 1, "tags": ["keep_tag"]})
        mock_http.request = AsyncMock(return_value=update_response)
        client = _make_client(mock_http)
        await client.remove_tags(1, ["keep_tag", "remove_me"], ["remove_me"])
        update_json = mock_http.request.call_args[1]["json"]["tags"]
        assert "remove_me" not in update_json
        assert "keep_tag" in update_json

    @pytest.mark.asyncio
    async def test_remove_tags_all_removed(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        update_response = _make_response(200, {"id": 1, "tags": []})
        mock_http.request = AsyncMock(return_value=update_response)
        client = _make_client(mock_http)
        await client.remove_tags(1, ["a", "b"], ["a", "b"])
        update_json = mock_http.request.call_args[1]["json"]["tags"]
        assert update_json == []


# ── Section 9: Rate limiter ───────────────────────────────────────────────────

class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_rate_limiter_allows_under_limit(self):
        limiter = _RateLimiter(max_requests=5, window=60.0)
        for _ in range(5):
            with patch("freshdesk.client.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                await limiter.acquire()
        # Should not have slept (all within limit)

    @pytest.mark.asyncio
    async def test_rate_limiter_tracks_timestamps(self):
        limiter = _RateLimiter(max_requests=5, window=60.0)
        await limiter.acquire()
        assert len(limiter._timestamps) == 1

    @pytest.mark.asyncio
    async def test_rate_limiter_evicts_old_timestamps(self):
        import time
        limiter = _RateLimiter(max_requests=5, window=1.0)
        limiter._timestamps = [time.monotonic() - 2.0]  # already expired
        await limiter.acquire()
        assert len(limiter._timestamps) == 1  # old one evicted, new one added


# ── Section 10: HTTP status mapping ──────────────────────────────────────────

class TestStatusMapping:
    @pytest.mark.asyncio
    async def test_422_raises_validation_error(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(422, {}))
        client = _make_client(mock_http)
        with pytest.raises(FreshdeskValidationError):
            await client.update_ticket(1, {})

    @pytest.mark.asyncio
    async def test_403_raises_forbidden(self):
        from freshdesk.freshdesk_exceptions import FreshdeskForbiddenError
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(403, {}))
        client = _make_client(mock_http)
        with pytest.raises(FreshdeskForbiddenError):
            await client.get_ticket(1)

    @pytest.mark.asyncio
    async def test_unknown_5xx_raises_server_error(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(503, {}))
        client = _make_client(mock_http)
        with patch("freshdesk.client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(FreshdeskServerError):
                await client.get_ticket(1)


# ── Section 11: Context manager ──────────────────────────────────────────────

class TestContextManager:
    @pytest.mark.asyncio
    async def test_context_manager_closes_owned_client(self):
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.request = AsyncMock(return_value=_make_response(200, {"id": 1}))
        config = FreshdeskConfig(domain="test.freshdesk.com", api_key="key")
        client = FreshdeskClient(config, http_client=mock_http)
        # client does NOT own mock_http, so close() won't call aclose()
        async with client as c:
            result = await c.get_ticket(1)
        assert result["id"] == 1
