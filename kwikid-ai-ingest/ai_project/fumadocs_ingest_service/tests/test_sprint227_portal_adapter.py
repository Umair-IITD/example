"""
tests/test_sprint227_portal_adapter.py

Sprint 2.27: Tests for AdminPortalAdapter (placeholder).
"""
import pytest

from case_engine.adapters.portal_adapter import AdminPortalAdapter
from case_engine.adapters.models import (
    AdapterOperation,
    AdapterRequest,
    AdapterStatus,
    AdapterType,
)


def _make_request(
    operation: AdapterOperation,
    action_type: str = "vkyc_session_reset",
    payload: dict | None = None,
) -> AdapterRequest:
    return AdapterRequest.create(
        adapter_type=AdapterType.ADMIN_PORTAL,
        operation=operation,
        payload=payload or {},
        case_id="case-portal-001",
        action_type=action_type,
    )


class TestAdminPortalAdapterIdentity:
    def test_adapter_name(self):
        a = AdminPortalAdapter()
        assert "portal" in a.adapter_name.lower() or "admin" in a.adapter_name.lower()

    def test_adapter_type(self):
        a = AdminPortalAdapter()
        assert a.adapter_type == AdapterType.ADMIN_PORTAL

    def test_supports_read(self):
        a = AdminPortalAdapter()
        assert a.supports(AdapterOperation.READ)

    def test_supports_write(self):
        a = AdminPortalAdapter()
        assert a.supports(AdapterOperation.WRITE)

    def test_supports_update(self):
        a = AdminPortalAdapter()
        assert a.supports(AdapterOperation.UPDATE)

    def test_supports_create(self):
        a = AdminPortalAdapter()
        assert a.supports(AdapterOperation.CREATE)

    def test_supports_execute(self):
        a = AdminPortalAdapter()
        assert a.supports(AdapterOperation.EXECUTE)

    def test_does_not_support_search(self):
        a = AdminPortalAdapter()
        assert not a.supports(AdapterOperation.SEARCH)


class TestAdminPortalAdapterExecute:
    def test_read_success(self):
        a = AdminPortalAdapter()
        resp = a.execute(_make_request(AdapterOperation.READ))
        assert resp.status == AdapterStatus.SUCCESS

    def test_write_success(self):
        a = AdminPortalAdapter()
        resp = a.execute(_make_request(AdapterOperation.WRITE))
        assert resp.status == AdapterStatus.SUCCESS

    def test_update_success(self):
        a = AdminPortalAdapter()
        resp = a.execute(_make_request(AdapterOperation.UPDATE))
        assert resp.status == AdapterStatus.SUCCESS

    def test_create_success(self):
        a = AdminPortalAdapter()
        resp = a.execute(_make_request(AdapterOperation.CREATE))
        assert resp.status == AdapterStatus.SUCCESS

    def test_execute_vkyc_success(self):
        a = AdminPortalAdapter()
        req = _make_request(AdapterOperation.EXECUTE, action_type="vkyc_session_reset")
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_execute_ocr_success(self):
        a = AdminPortalAdapter()
        req = _make_request(AdapterOperation.EXECUTE, action_type="document_ocr_reprocess")
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_execute_agent_session_success(self):
        a = AdminPortalAdapter()
        req = _make_request(AdapterOperation.EXECUTE, action_type="agent_session_refresh")
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_execute_callback_retry_success(self):
        a = AdminPortalAdapter()
        req = _make_request(AdapterOperation.EXECUTE, action_type="api_callback_retry")
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_execute_unknown_action_success(self):
        a = AdminPortalAdapter()
        req = _make_request(AdapterOperation.EXECUTE, action_type="unknown_action")
        resp = a.execute(req)
        assert resp.status == AdapterStatus.SUCCESS

    def test_search_unsupported(self):
        a = AdminPortalAdapter()
        resp = a.execute(_make_request(AdapterOperation.SEARCH))
        assert resp.status == AdapterStatus.UNSUPPORTED

    def test_response_adapter_type_correct(self):
        a = AdminPortalAdapter()
        resp = a.execute(_make_request(AdapterOperation.READ))
        assert resp.adapter_type == AdapterType.ADMIN_PORTAL

    def test_never_raises(self):
        a = AdminPortalAdapter()
        for op in AdapterOperation:
            resp = a.execute(_make_request(op))
            assert resp is not None


class TestAdminPortalAdapterHealthCheck:
    def test_health_check_returns_dict(self):
        h = AdminPortalAdapter().health_check()
        assert isinstance(h, dict)

    def test_health_check_has_healthy_key(self):
        h = AdminPortalAdapter().health_check()
        assert "healthy" in h

    def test_health_check_is_true(self):
        h = AdminPortalAdapter().health_check()
        assert h["healthy"] is True
