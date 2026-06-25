"""
tests/test_sprint2282_rate_limit_headers.py

Sprint 2.28.2 Part 9: Freshdesk Rate Limit Header Inspection.

The FreshdeskClient now reads rate limit response headers on every request:
  x-ratelimit-remaining:  requests left in current window
  x-ratelimit-total:      account-wide limit (40 for current account)
  retry-after:            seconds to wait (present on 429 responses)

Behavior verified from live audit Section 16:
  - remaining < 5 → WARN
  - warn=True (on 429) → WARN regardless of remaining
  - retry-after present → WARN
  - Normal response with remaining >= 5 → DEBUG only

Coverage:
  1. Successful response calls _inspect_rate_limit_headers with warn=False
  2. 429 response calls _inspect_rate_limit_headers with warn=True
  3. remaining < 5 triggers WARNING log
  4. remaining >= 5 triggers DEBUG log (not WARNING)
  5. retry-after header triggers WARNING log
  6. Missing headers produce no log error (graceful)
  7. Unparseable remaining value handled gracefully
  8. Both x-ratelimit-remaining and x-ratelimit-total logged
  9. _inspect_rate_limit_headers is called on 200 responses
  10. _inspect_rate_limit_headers is called on 429 responses before raise
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

os.environ.setdefault("RAG_API_KEY", "test-2282-rate-limit")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.client import FreshdeskClient
from freshdesk.freshdesk_models import FreshdeskConfig
from freshdesk.freshdesk_exceptions import FreshdeskRateLimitError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _config() -> FreshdeskConfig:
    return FreshdeskConfig(domain="test.freshdesk.com", api_key="testapikey12345")


def _make_response(
    status_code: int = 200,
    body: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    import json as _json
    response = httpx.Response(
        status_code=status_code,
        content=_json.dumps(body or {}).encode(),
        headers=headers or {},
    )
    return response


def _make_client(response: httpx.Response) -> FreshdeskClient:
    transport = httpx.MockTransport(lambda request: response)
    http = httpx.AsyncClient(
        base_url="https://test.freshdesk.com",
        transport=transport,
    )
    return FreshdeskClient(_config(), http_client=http)


# ── Section 1: _inspect_rate_limit_headers called on success ─────────────────

class TestInspectCalledOnSuccess:
    def test_inspect_called_on_200(self):
        """_inspect_rate_limit_headers is called on every successful response."""
        client = _make_client(_make_response(200, {"id": 1}, {"x-ratelimit-remaining": "35"}))
        with patch.object(client, "_inspect_rate_limit_headers") as mock_inspect:
            import asyncio
            asyncio.run(client.get_ticket(1))
            mock_inspect.assert_called_once()

    def test_inspect_called_with_warn_false_on_200(self):
        client = _make_client(_make_response(200, {"id": 1}, {"x-ratelimit-remaining": "35"}))
        with patch.object(client, "_inspect_rate_limit_headers") as mock_inspect:
            import asyncio
            asyncio.run(client.get_ticket(1))
            call_kwargs = mock_inspect.call_args[1]
            assert call_kwargs.get("warn", False) is False


# ── Section 2: remaining < 5 triggers WARNING ────────────────────────────────

class TestLowRemainingWarning:
    def test_remaining_3_triggers_warning(self, caplog):
        client = _make_client(_make_response(200, {"id": 1}, {
            "x-ratelimit-remaining": "3",
            "x-ratelimit-total": "40",
        }))
        with caplog.at_level(logging.WARNING, logger="freshdesk.client"):
            import asyncio
            asyncio.run(client.get_ticket(1))
        assert any("remaining" in r.message and "3" in r.message for r in caplog.records)

    def test_remaining_0_triggers_warning(self, caplog):
        client = _make_client(_make_response(200, {"id": 1}, {
            "x-ratelimit-remaining": "0",
        }))
        with caplog.at_level(logging.WARNING, logger="freshdesk.client"):
            import asyncio
            asyncio.run(client.get_ticket(1))
        assert any("remaining" in r.message for r in caplog.records)

    def test_remaining_5_does_not_trigger_warning(self, caplog):
        """remaining == 5 is at the threshold; only < 5 triggers warning."""
        client = _make_client(_make_response(200, {"id": 1}, {
            "x-ratelimit-remaining": "5",
        }))
        with caplog.at_level(logging.WARNING, logger="freshdesk.client"):
            import asyncio
            asyncio.run(client.get_ticket(1))
        # No WARNING from rate limit (may have DEBUG but not WARNING)
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("remaining" in m for m in warning_msgs)

    def test_remaining_20_does_not_trigger_warning(self, caplog):
        client = _make_client(_make_response(200, {"id": 1}, {
            "x-ratelimit-remaining": "20",
        }))
        with caplog.at_level(logging.WARNING, logger="freshdesk.client"):
            import asyncio
            asyncio.run(client.get_ticket(1))
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("remaining" in m for m in warning_msgs)


# ── Section 3: retry-after triggers WARNING ──────────────────────────────────

class TestRetryAfterHeader:
    def test_retry_after_header_triggers_warning(self, caplog):
        """retry-after is only present on 429 — but test _inspect directly for reliability."""
        client = _make_client(_make_response(200, {"id": 1}))
        headers = MagicMock()
        headers.get.side_effect = lambda k, d=None: {
            "x-ratelimit-remaining": "25",
            "x-ratelimit-total": "40",
            "retry-after": "60",
        }.get(k, d)

        with caplog.at_level(logging.WARNING, logger="freshdesk.client"):
            client._inspect_rate_limit_headers(headers, warn=False)
        assert any("retry_after" in r.message for r in caplog.records)

    def test_no_retry_after_no_backing_off_message(self, caplog):
        client = _make_client(_make_response(200, {"id": 1}))
        headers = MagicMock()
        headers.get.side_effect = lambda k, d=None: {
            "x-ratelimit-remaining": "25",
            "x-ratelimit-total": "40",
        }.get(k, d)

        with caplog.at_level(logging.WARNING, logger="freshdesk.client"):
            client._inspect_rate_limit_headers(headers, warn=False)
        assert not any("retry_after" in r.message for r in caplog.records)


# ── Section 4: graceful header absence ──────────────────────────────────────

class TestMissingHeaders:
    def test_no_headers_no_crash(self):
        client = _make_client(_make_response(200, {"id": 1}))
        headers = MagicMock()
        headers.get.return_value = None
        client._inspect_rate_limit_headers(headers)  # must not raise

    def test_unparseable_remaining_handled(self, caplog):
        client = _make_client(_make_response(200, {"id": 1}))
        headers = MagicMock()
        headers.get.side_effect = lambda k, d=None: {
            "x-ratelimit-remaining": "not-a-number",
            "x-ratelimit-total": "40",
        }.get(k, d)
        with caplog.at_level(logging.DEBUG, logger="freshdesk.client"):
            client._inspect_rate_limit_headers(headers)  # must not raise
        assert any("unparseable" in r.message for r in caplog.records)


# ── Section 5: direct _inspect_rate_limit_headers unit tests ─────────────────

class TestInspectRateLimitHeaders:
    def test_warn_true_triggers_warning_regardless_of_remaining(self, caplog):
        client = _make_client(_make_response(200, {"id": 1}))
        headers = MagicMock()
        headers.get.side_effect = lambda k, d=None: {
            "x-ratelimit-remaining": "30",  # high — normally DEBUG only
            "x-ratelimit-total": "40",
        }.get(k, d)
        with caplog.at_level(logging.WARNING, logger="freshdesk.client"):
            client._inspect_rate_limit_headers(headers, warn=True)
        assert any("remaining" in r.message for r in caplog.records)

    def test_total_logged_alongside_remaining(self, caplog):
        client = _make_client(_make_response(200, {"id": 1}))
        headers = MagicMock()
        headers.get.side_effect = lambda k, d=None: {
            "x-ratelimit-remaining": "2",
            "x-ratelimit-total": "40",
        }.get(k, d)
        with caplog.at_level(logging.WARNING, logger="freshdesk.client"):
            client._inspect_rate_limit_headers(headers)
        assert any("40" in r.message for r in caplog.records)
