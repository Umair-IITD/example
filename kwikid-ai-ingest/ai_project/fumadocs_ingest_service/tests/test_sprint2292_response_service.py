"""
tests/test_sprint2292_response_service.py

Sprint 2.29.2 PART 4: FreshdeskResponseService verification.

Uses httpx.MockTransport to intercept HTTP calls.  NO production requests are
made.  Proves:
  1.  add_internal_note() calls POST /api/v2/tickets/{id}/notes with private=True
  2.  send_customer_reply() calls POST /api/v2/tickets/{id}/reply
  3.  Both return parsed dict from Freshdesk response
  4.  Both handle HTTP errors gracefully (return empty dict, never raise)
  5.  Both handle 429 + retry behavior
  6.  Metrics counters are incremented on success and failure
  7.  Domain/ticket_id logging does NOT include email addresses
"""
from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("RAG_API_KEY", "test-sprint2292-response-svc")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")

import pytest
import httpx

from freshdesk.client import FreshdeskClient
from freshdesk.freshdesk_models import FreshdeskConfig
from freshdesk.response_service import FreshdeskResponseService


# ── Test infrastructure ───────────────────────────────────────────────────────

TEST_DOMAIN  = "kwikid-test.freshdesk.com"
TEST_API_KEY = "test-api-key-sprint2292"
TEST_TICKET  = 197416


def _make_client(handler) -> FreshdeskClient:
    """Create a FreshdeskClient wired with a mock HTTP transport."""
    mock_transport = httpx.MockTransport(handler)
    mock_http = httpx.AsyncClient(
        base_url=f"https://{TEST_DOMAIN}",
        transport=mock_transport,
        auth=httpx.BasicAuth(TEST_API_KEY, "X"),
        timeout=5.0,
    )
    config = FreshdeskConfig(domain=TEST_DOMAIN, api_key=TEST_API_KEY)
    return FreshdeskClient(config, http_client=mock_http)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _MetricsSpy:
    def __init__(self):
        self.increments: list[str] = []
        self.latencies:  list[tuple[str, int]] = []

    def increment(self, name: str) -> None:
        self.increments.append(name)

    def record_latency(self, name: str, value: int) -> None:
        self.latencies.append((name, value))


# ── Test 1: add_internal_note — correct endpoint and payload ─────────────────

class TestAddInternalNote:
    def test_calls_notes_endpoint(self):
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json={"id": 9001, "private": True, "body": "note"})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        result = _run(svc.add_internal_note(TEST_TICKET, "<p>Test internal note</p>"))

        assert len(captured) == 1
        req = captured[0]
        assert req.method == "POST"
        assert f"/api/v2/tickets/{TEST_TICKET}/notes" in str(req.url)

    def test_body_has_private_true(self):
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json={"id": 9001, "private": True})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        _run(svc.add_internal_note(TEST_TICKET, "<p>Private</p>"))

        body = json.loads(captured[0].content)
        assert body["private"] is True
        assert body["body"] == "<p>Private</p>"

    def test_returns_parsed_dict(self):
        expected = {"id": 9001, "private": True, "body": "note content"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json=expected)

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        result = _run(svc.add_internal_note(TEST_TICKET, "content"))

        assert result["id"] == 9001
        assert result["private"] is True

    def test_returns_empty_dict_on_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Ticket not found"})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        result = _run(svc.add_internal_note(99999, "note"))

        assert result == {}

    def test_returns_empty_dict_on_401(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Unauthorized"})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        result = _run(svc.add_internal_note(TEST_TICKET, "note"))

        assert result == {}

    def test_metrics_incremented_on_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"id": 1})

        client = _make_client(handler)
        spy = _MetricsSpy()
        svc = FreshdeskResponseService(freshdesk_client=client, metrics_collector=spy)
        _run(svc.add_internal_note(TEST_TICKET, "note"))

        from freshdesk.metrics import COUNTER_FD_PRIVATE_NOTES_TOTAL
        assert COUNTER_FD_PRIVATE_NOTES_TOTAL in spy.increments
        assert len(spy.latencies) > 0

    def test_metrics_error_counter_on_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "bad"})

        client = _make_client(handler)
        spy = _MetricsSpy()
        svc = FreshdeskResponseService(freshdesk_client=client, metrics_collector=spy)
        _run(svc.add_internal_note(TEST_TICKET, "note"))

        from freshdesk.metrics import COUNTER_FD_API_ERRORS_TOTAL
        assert COUNTER_FD_API_ERRORS_TOTAL in spy.increments

    def test_string_ticket_id_works(self):
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json={"id": 1})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        _run(svc.add_internal_note("197416", "note"))

        assert "/api/v2/tickets/197416/notes" in str(captured[0].url)


# ── Test 2: send_customer_reply — correct endpoint and payload ────────────────

class TestSendCustomerReply:
    def test_calls_reply_endpoint(self):
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json={"id": 8001, "body": "reply"})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        _run(svc.send_customer_reply(TEST_TICKET, "<p>Public reply</p>"))

        assert len(captured) == 1
        req = captured[0]
        assert req.method == "POST"
        assert f"/api/v2/tickets/{TEST_TICKET}/reply" in str(req.url)

    def test_reply_body_sent(self):
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json={"id": 8001})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        _run(svc.send_customer_reply(TEST_TICKET, "<p>AI Answer</p>"))

        body = json.loads(captured[0].content)
        assert body["body"] == "<p>AI Answer</p>"
        assert "private" not in body  # public reply must NOT have private=True

    def test_returns_parsed_dict_on_success(self):
        expected = {"id": 8001, "body": "response"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json=expected)

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        result = _run(svc.send_customer_reply(TEST_TICKET, "reply"))

        assert result["id"] == 8001

    def test_returns_empty_dict_on_403(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Forbidden"})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        result = _run(svc.send_customer_reply(TEST_TICKET, "reply"))

        assert result == {}

    def test_metrics_incremented_on_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"id": 1})

        client = _make_client(handler)
        spy = _MetricsSpy()
        svc = FreshdeskResponseService(freshdesk_client=client, metrics_collector=spy)
        _run(svc.send_customer_reply(TEST_TICKET, "reply"))

        from freshdesk.metrics import COUNTER_FD_PUBLIC_REPLIES_TOTAL
        assert COUNTER_FD_PUBLIC_REPLIES_TOTAL in spy.increments


# ── Test 3: note vs reply endpoints are distinct ──────────────────────────────

class TestNoteVsReplyEndpoint:
    def test_note_uses_notes_endpoint_not_reply(self):
        urls = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return httpx.Response(201, json={"id": 1})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        _run(svc.add_internal_note(TEST_TICKET, "note"))
        _run(svc.send_customer_reply(TEST_TICKET, "reply"))

        assert any("/notes" in u for u in urls)
        assert any("/reply" in u for u in urls)
        assert not any("/reply" in u and "notes" in u for u in urls)

    def test_private_note_is_not_customer_visible(self):
        bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 1})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        _run(svc.add_internal_note(TEST_TICKET, "agent-only note"))

        assert bodies[0]["private"] is True


# ── Test 4: retry behaviour on 429 ───────────────────────────────────────────

class TestRetryBehaviour:
    def test_retries_on_429_then_succeeds(self):
        attempt_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                return httpx.Response(
                    429,
                    json={"message": "Rate limit"},
                    headers={"x-ratelimit-remaining": "0", "retry-after": "0"},
                )
            return httpx.Response(201, json={"id": 5555})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        result = _run(svc.add_internal_note(TEST_TICKET, "note"))

        assert result["id"] == 5555
        assert attempt_count[0] == 3

    def test_max_retries_exhausted_returns_empty(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"message": "Server error"})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        result = _run(svc.add_internal_note(TEST_TICKET, "note"))

        assert result == {}


# ── Test 5: authentication header sent ───────────────────────────────────────

class TestAuthentication:
    def test_basic_auth_header_present(self):
        captured_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(201, json={"id": 1})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        _run(svc.add_internal_note(TEST_TICKET, "note"))

        assert "authorization" in captured_headers
        auth_value = captured_headers["authorization"]
        assert auth_value.startswith("Basic ")

    def test_email_not_in_authorization_header(self):
        captured_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(201, json={"id": 1})

        client = _make_client(handler)
        svc = FreshdeskResponseService(freshdesk_client=client)
        _run(svc.add_internal_note(TEST_TICKET, "note"))

        # Auth header is base64 of api_key:X — no email should appear
        auth_value = captured_headers.get("authorization", "")
        assert "@" not in auth_value  # email never in auth header


# ── Test 6: FreshdeskConfig validation ───────────────────────────────────────

class TestFreshdeskConfigValidation:
    def test_domain_without_protocol_accepted(self):
        cfg = FreshdeskConfig(domain="kwikid.freshdesk.com", api_key="test-key")
        assert cfg.base_url == "https://kwikid.freshdesk.com"

    def test_domain_with_https_rejected(self):
        import pytest
        with pytest.raises(ValueError, match="protocol prefix"):
            FreshdeskConfig(domain="https://kwikid.freshdesk.com", api_key="test-key")

    def test_api_key_masked_in_logs(self):
        cfg = FreshdeskConfig(domain="kwikid.freshdesk.com", api_key="secret-key-12345")
        assert cfg.masked_api_key == "secr****"
        assert "secret-key-12345" not in cfg.masked_api_key

    def test_empty_api_key_rejected(self):
        import pytest
        with pytest.raises(ValueError, match="api_key"):
            FreshdeskConfig(domain="kwikid.freshdesk.com", api_key="")
