"""
tests/test_sprint227_freshdesk_adapter.py

Sprint 2.27: Tests for FreshdeskAdapter (placeholder).
"""
import pytest

from case_engine.adapters.freshdesk_adapter import FreshdeskAdapter
from case_engine.adapters.models import (
    AdapterOperation,
    AdapterRequest,
    AdapterStatus,
    AdapterType,
)


def _make_request(operation: AdapterOperation, payload: dict | None = None) -> AdapterRequest:
    return AdapterRequest.create(
        adapter_type=AdapterType.FRESHDESK,
        operation=operation,
        payload=payload or {},
        case_id="case-fresh-001",
        action_type="otp_resend",
    )


class TestFreshdeskAdapterIdentity:
    def test_adapter_name(self):
        a = FreshdeskAdapter()
        assert "freshdesk" in a.adapter_name.lower()

    def test_adapter_type(self):
        a = FreshdeskAdapter()
        assert a.adapter_type == AdapterType.FRESHDESK

    def test_supported_ops_includes_read(self):
        a = FreshdeskAdapter()
        assert a.supports(AdapterOperation.READ)

    def test_supported_ops_includes_write(self):
        a = FreshdeskAdapter()
        assert a.supports(AdapterOperation.WRITE)

    def test_supported_ops_includes_update(self):
        a = FreshdeskAdapter()
        assert a.supports(AdapterOperation.UPDATE)

    def test_supported_ops_includes_create(self):
        a = FreshdeskAdapter()
        assert a.supports(AdapterOperation.CREATE)

    def test_does_not_support_search(self):
        a = FreshdeskAdapter()
        assert not a.supports(AdapterOperation.SEARCH)

    def test_supports_execute_for_otp_actions(self):
        a = FreshdeskAdapter()
        assert a.supports(AdapterOperation.EXECUTE)


class TestFreshdeskAdapterExecute:
    def test_read_returns_success(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.READ)
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS
        assert resp.is_success() is True

    def test_write_returns_success(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.WRITE)
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_update_returns_success(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.UPDATE)
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_create_returns_success(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.CREATE)
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_search_returns_unsupported(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.SEARCH)
        resp = a.execute(req)
        assert resp.status == AdapterStatus.UNSUPPORTED

    def test_execute_returns_success_for_otp_actions(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.EXECUTE)
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_response_adapter_type_is_freshdesk(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.READ)
        resp = a.execute(req)
        assert resp.adapter_type == AdapterType.FRESHDESK

    def test_response_request_id_matches(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.READ)
        resp = a.execute(req)
        assert resp.request_id == req.request_id

    def test_response_data_is_dict(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.READ)
        resp = a.execute(req)
        assert isinstance(resp.data, dict)

    def test_response_has_duration_ms(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.READ)
        resp = a.execute(req)
        assert resp.duration_ms >= 0

    def test_response_has_responded_at(self):
        a = FreshdeskAdapter()
        req = _make_request(AdapterOperation.READ)
        resp = a.execute(req)
        assert resp.responded_at

    def test_never_raises(self):
        a = FreshdeskAdapter()
        for op in AdapterOperation:
            req = _make_request(op)
            resp = a.execute(req)  # must not raise
            assert resp is not None


class TestFreshdeskAdapterHealthCheck:
    def test_health_check_returns_dict(self):
        a = FreshdeskAdapter()
        h = a.health_check()
        assert isinstance(h, dict)

    def test_health_check_has_healthy_key(self):
        a = FreshdeskAdapter()
        h = a.health_check()
        assert "healthy" in h

    def test_health_check_healthy_is_true(self):
        a = FreshdeskAdapter()
        h = a.health_check()
        assert h["healthy"] is True

    def test_health_check_never_raises(self):
        a = FreshdeskAdapter()
        h = a.health_check()
        assert h is not None
