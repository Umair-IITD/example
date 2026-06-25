"""
tests/test_sprint2281_response_service.py

Sprint 2.28.1: FreshdeskResponseService tests.

Coverage:
  1. add_internal_note — success, returns note dict
  2. add_internal_note — FreshdeskClient failure → returns empty dict, never raises
  3. send_customer_reply — success, returns reply dict
  4. send_customer_reply — FreshdeskClient failure → returns empty dict
  5. Metrics counters incremented on success
  6. Error counter incremented on failure
  7. Latency recorded on success
  8. Audit events written for private note / public reply
  9. audit_logger=None → no error
  10. metrics_collector=None → no error
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("RAG_API_KEY", "test-rs-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.response_service import FreshdeskResponseService
from freshdesk.metrics import (
    COUNTER_FD_PRIVATE_NOTES_TOTAL,
    COUNTER_FD_PUBLIC_REPLIES_TOTAL,
    COUNTER_FD_API_ERRORS_TOTAL,
    LATENCY_FD_API_CALL_MS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_client(
    note_result: dict | None = None,
    reply_result: dict | None = None,
    note_error: Exception | None = None,
    reply_error: Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    if note_error:
        client.add_private_note = AsyncMock(side_effect=note_error)
    else:
        client.add_private_note = AsyncMock(return_value=note_result or {"id": 999})
    if reply_error:
        client.add_public_reply = AsyncMock(side_effect=reply_error)
    else:
        client.add_public_reply = AsyncMock(return_value=reply_result or {"id": 888})
    return client


def _make_metrics():
    m = MagicMock()
    m.increment = MagicMock()
    m.record_latency = MagicMock()
    return m


def _make_service(
    *,
    note_result=None,
    reply_result=None,
    note_error=None,
    reply_error=None,
    metrics=None,
    audit=None,
) -> FreshdeskResponseService:
    client = _make_mock_client(
        note_result=note_result,
        reply_result=reply_result,
        note_error=note_error,
        reply_error=reply_error,
    )
    return FreshdeskResponseService(client, metrics_collector=metrics, audit_logger=audit)


# ── Section 1: add_internal_note — success ────────────────────────────────────

class TestAddInternalNoteSuccess:
    @pytest.mark.asyncio
    async def test_returns_note_dict(self):
        svc = _make_service(note_result={"id": 555, "private": True})
        result = await svc.add_internal_note("123", "Internal note body")
        assert result["id"] == 555
        assert result["private"] is True

    @pytest.mark.asyncio
    async def test_calls_client_add_private_note(self):
        client = _make_mock_client(note_result={"id": 1})
        svc = FreshdeskResponseService(client)
        await svc.add_internal_note("123", "body")
        client.add_private_note.assert_called_once_with("123", "body")

    @pytest.mark.asyncio
    async def test_passes_ticket_id_as_given(self):
        client = _make_mock_client()
        svc = FreshdeskResponseService(client)
        await svc.add_internal_note(456, "body")
        call_args = client.add_private_note.call_args
        assert call_args[0][0] == 456


# ── Section 2: add_internal_note — failure ───────────────────────────────────

class TestAddInternalNoteFailure:
    @pytest.mark.asyncio
    async def test_exception_returns_empty_dict(self):
        from freshdesk.freshdesk_exceptions import FreshdeskServerError
        svc = _make_service(note_error=FreshdeskServerError("500", status_code=500, response_body={}))
        result = await svc.add_internal_note("123", "body")
        assert result == {}

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty_dict(self):
        svc = _make_service(note_error=RuntimeError("db error"))
        result = await svc.add_internal_note("123", "body")
        assert result == {}

    @pytest.mark.asyncio
    async def test_never_raises(self):
        svc = _make_service(note_error=Exception("unexpected"))
        # Should not raise
        result = await svc.add_internal_note("123", "body")
        assert isinstance(result, dict)


# ── Section 3: send_customer_reply — success ─────────────────────────────────

class TestSendCustomerReplySuccess:
    @pytest.mark.asyncio
    async def test_returns_reply_dict(self):
        svc = _make_service(reply_result={"id": 777, "private": False})
        result = await svc.send_customer_reply("123", "Hello customer")
        assert result["id"] == 777

    @pytest.mark.asyncio
    async def test_calls_client_add_public_reply(self):
        client = _make_mock_client(reply_result={"id": 1})
        svc = FreshdeskResponseService(client)
        await svc.send_customer_reply("123", "Customer response")
        client.add_public_reply.assert_called_once_with("123", "Customer response")


# ── Section 4: send_customer_reply — failure ─────────────────────────────────

class TestSendCustomerReplyFailure:
    @pytest.mark.asyncio
    async def test_exception_returns_empty_dict(self):
        from freshdesk.freshdesk_exceptions import FreshdeskRateLimitError
        svc = _make_service(reply_error=FreshdeskRateLimitError("429", status_code=429, response_body={}))
        result = await svc.send_customer_reply("123", "body")
        assert result == {}

    @pytest.mark.asyncio
    async def test_never_raises_on_reply_failure(self):
        svc = _make_service(reply_error=Exception("fail"))
        result = await svc.send_customer_reply("123", "body")
        assert isinstance(result, dict)


# ── Section 5: Metrics on success ────────────────────────────────────────────

class TestMetricsOnSuccess:
    @pytest.mark.asyncio
    async def test_private_note_counter_incremented(self):
        metrics = _make_metrics()
        svc = _make_service(metrics=metrics)
        await svc.add_internal_note("123", "body")
        calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert COUNTER_FD_PRIVATE_NOTES_TOTAL in calls

    @pytest.mark.asyncio
    async def test_public_reply_counter_incremented(self):
        metrics = _make_metrics()
        svc = _make_service(metrics=metrics)
        await svc.send_customer_reply("123", "body")
        calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert COUNTER_FD_PUBLIC_REPLIES_TOTAL in calls

    @pytest.mark.asyncio
    async def test_latency_recorded_for_note(self):
        metrics = _make_metrics()
        svc = _make_service(metrics=metrics)
        await svc.add_internal_note("123", "body")
        calls = [c[0][0] for c in metrics.record_latency.call_args_list]
        assert LATENCY_FD_API_CALL_MS in calls

    @pytest.mark.asyncio
    async def test_latency_recorded_for_reply(self):
        metrics = _make_metrics()
        svc = _make_service(metrics=metrics)
        await svc.send_customer_reply("123", "body")
        calls = [c[0][0] for c in metrics.record_latency.call_args_list]
        assert LATENCY_FD_API_CALL_MS in calls


# ── Section 6: Error counter on failure ──────────────────────────────────────

class TestErrorCounter:
    @pytest.mark.asyncio
    async def test_error_counter_incremented_on_note_failure(self):
        metrics = _make_metrics()
        svc = _make_service(note_error=Exception("fail"), metrics=metrics)
        await svc.add_internal_note("123", "body")
        calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert COUNTER_FD_API_ERRORS_TOTAL in calls

    @pytest.mark.asyncio
    async def test_error_counter_incremented_on_reply_failure(self):
        metrics = _make_metrics()
        svc = _make_service(reply_error=Exception("fail"), metrics=metrics)
        await svc.send_customer_reply("123", "body")
        calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert COUNTER_FD_API_ERRORS_TOTAL in calls


# ── Section 7: No metrics — no error ─────────────────────────────────────────

class TestNoMetrics:
    @pytest.mark.asyncio
    async def test_no_metrics_no_error_on_success(self):
        svc = _make_service(metrics=None)
        result = await svc.add_internal_note("123", "body")
        assert result == {"id": 999}

    @pytest.mark.asyncio
    async def test_no_metrics_no_error_on_failure(self):
        svc = _make_service(note_error=Exception("fail"), metrics=None)
        result = await svc.add_internal_note("123", "body")
        assert result == {}


# ── Section 8: Audit events ───────────────────────────────────────────────────

class TestAuditEvents:
    @pytest.mark.asyncio
    async def test_private_note_audit_written(self):
        from case_engine.models import AuditEventType
        audit = MagicMock()
        audit._write = MagicMock()
        svc = _make_service(note_result={"id": 1}, audit=audit)
        await svc.add_internal_note("123", "body", case_id="c1", client_id="unity")
        event_types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.PRIVATE_NOTE_ADDED in event_types

    @pytest.mark.asyncio
    async def test_public_reply_audit_written(self):
        from case_engine.models import AuditEventType
        audit = MagicMock()
        audit._write = MagicMock()
        svc = _make_service(reply_result={"id": 2}, audit=audit)
        await svc.send_customer_reply("123", "body", case_id="c1", client_id="unity")
        event_types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.PUBLIC_REPLY_SENT in event_types

    @pytest.mark.asyncio
    async def test_no_audit_on_failure(self):
        audit = MagicMock()
        audit._write = MagicMock()
        svc = _make_service(note_error=Exception("fail"), audit=audit)
        await svc.add_internal_note("123", "body")
        audit._write.assert_not_called()
