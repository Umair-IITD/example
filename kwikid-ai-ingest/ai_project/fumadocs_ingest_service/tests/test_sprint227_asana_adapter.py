"""
tests/test_sprint227_asana_adapter.py

Sprint 2.27: Tests for AsanaAdapter (placeholder).
"""
import pytest

from case_engine.adapters.asana_adapter import AsanaAdapter
from case_engine.adapters.models import (
    AdapterOperation,
    AdapterRequest,
    AdapterStatus,
    AdapterType,
)


def _make_request(operation: AdapterOperation, payload: dict | None = None) -> AdapterRequest:
    return AdapterRequest.create(
        adapter_type=AdapterType.ASANA,
        operation=operation,
        payload=payload or {"title": "L2 escalation"},
        case_id="case-asana-001",
        action_type="create_l2_ticket",
    )


class TestAsanaAdapterIdentity:
    def test_adapter_name(self):
        a = AsanaAdapter()
        assert "asana" in a.adapter_name.lower()

    def test_adapter_type(self):
        a = AsanaAdapter()
        assert a.adapter_type == AdapterType.ASANA

    def test_supports_read(self):
        assert AsanaAdapter().supports(AdapterOperation.READ)

    def test_supports_create(self):
        assert AsanaAdapter().supports(AdapterOperation.CREATE)

    def test_supports_update(self):
        assert AsanaAdapter().supports(AdapterOperation.UPDATE)

    def test_does_not_support_write(self):
        assert not AsanaAdapter().supports(AdapterOperation.WRITE)

    def test_does_not_support_execute(self):
        assert not AsanaAdapter().supports(AdapterOperation.EXECUTE)

    def test_does_not_support_search(self):
        assert not AsanaAdapter().supports(AdapterOperation.SEARCH)


class TestAsanaAdapterExecute:
    def test_read_success(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.READ))
        assert resp.status == AdapterStatus.SUCCESS

    def test_create_success(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.CREATE))
        assert resp.status == AdapterStatus.SUCCESS

    def test_create_returns_task_id(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.CREATE))
        assert "task_id" in resp.data
        assert resp.data["task_id"].startswith("ASANA-")

    def test_update_success(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.UPDATE))
        assert resp.status == AdapterStatus.SUCCESS

    def test_write_unsupported(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.WRITE))
        assert resp.status == AdapterStatus.UNSUPPORTED

    def test_execute_unsupported(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.EXECUTE))
        assert resp.status == AdapterStatus.UNSUPPORTED

    def test_search_unsupported(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.SEARCH))
        assert resp.status == AdapterStatus.UNSUPPORTED

    def test_response_adapter_type_correct(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.CREATE))
        assert resp.adapter_type == AdapterType.ASANA

    def test_response_request_id_matches(self):
        req  = _make_request(AdapterOperation.CREATE)
        resp = AsanaAdapter().execute(req)
        assert resp.request_id == req.request_id

    def test_never_raises(self):
        a = AsanaAdapter()
        for op in AdapterOperation:
            resp = a.execute(_make_request(op))
            assert resp is not None

    def test_response_data_is_dict(self):
        resp = AsanaAdapter().execute(_make_request(AdapterOperation.CREATE))
        assert isinstance(resp.data, dict)


class TestAsanaAdapterHealthCheck:
    def test_returns_dict(self):
        assert isinstance(AsanaAdapter().health_check(), dict)

    def test_has_healthy_key(self):
        assert "healthy" in AsanaAdapter().health_check()

    def test_is_healthy(self):
        assert AsanaAdapter().health_check()["healthy"] is True
