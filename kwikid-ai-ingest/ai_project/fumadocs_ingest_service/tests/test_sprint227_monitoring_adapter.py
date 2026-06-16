"""
tests/test_sprint227_monitoring_adapter.py

Sprint 2.27: Tests for MonitoringAdapter (placeholder).
"""
import pytest

from case_engine.adapters.monitoring_adapter import MonitoringAdapter
from case_engine.adapters.models import (
    AdapterOperation,
    AdapterRequest,
    AdapterStatus,
    AdapterType,
)


def _make_request(operation: AdapterOperation, payload: dict | None = None) -> AdapterRequest:
    return AdapterRequest.create(
        adapter_type=AdapterType.MONITORING,
        operation=operation,
        payload=payload or {"metric_name": "kwikid.test", "value": 1},
        case_id="case-mon-001",
        action_type="emit_metric",
    )


class TestMonitoringAdapterIdentity:
    def test_adapter_name(self):
        a = MonitoringAdapter()
        assert "monitoring" in a.adapter_name.lower()

    def test_adapter_type(self):
        a = MonitoringAdapter()
        assert a.adapter_type == AdapterType.MONITORING

    def test_supports_read(self):
        assert MonitoringAdapter().supports(AdapterOperation.READ)

    def test_supports_write(self):
        assert MonitoringAdapter().supports(AdapterOperation.WRITE)

    def test_supports_execute(self):
        assert MonitoringAdapter().supports(AdapterOperation.EXECUTE)

    def test_does_not_support_update(self):
        assert not MonitoringAdapter().supports(AdapterOperation.UPDATE)

    def test_does_not_support_create(self):
        assert not MonitoringAdapter().supports(AdapterOperation.CREATE)

    def test_does_not_support_search(self):
        assert not MonitoringAdapter().supports(AdapterOperation.SEARCH)


class TestMonitoringAdapterExecute:
    def test_write_success(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.WRITE))
        assert resp.status == AdapterStatus.SUCCESS

    def test_write_response_has_metric_name(self):
        resp = MonitoringAdapter().execute(
            _make_request(AdapterOperation.WRITE, payload={"metric_name": "kwikid.otp"})
        )
        assert resp.data.get("metric_name") == "kwikid.otp"

    def test_write_response_has_emitted_at(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.WRITE))
        assert "emitted_at" in resp.data

    def test_read_success(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.READ))
        assert resp.status == AdapterStatus.SUCCESS

    def test_read_response_has_value(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.READ))
        assert "value" in resp.data

    def test_execute_success(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.EXECUTE))
        assert resp.status == AdapterStatus.SUCCESS

    def test_execute_response_has_fired(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.EXECUTE))
        assert "fired" in resp.data

    def test_update_unsupported(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.UPDATE))
        assert resp.status == AdapterStatus.UNSUPPORTED

    def test_create_unsupported(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.CREATE))
        assert resp.status == AdapterStatus.UNSUPPORTED

    def test_search_unsupported(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.SEARCH))
        assert resp.status == AdapterStatus.UNSUPPORTED

    def test_response_adapter_type_correct(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.WRITE))
        assert resp.adapter_type == AdapterType.MONITORING

    def test_response_request_id_matches(self):
        req  = _make_request(AdapterOperation.WRITE)
        resp = MonitoringAdapter().execute(req)
        assert resp.request_id == req.request_id

    def test_never_raises(self):
        a = MonitoringAdapter()
        for op in AdapterOperation:
            resp = a.execute(_make_request(op))
            assert resp is not None

    def test_data_mock_flag_true(self):
        resp = MonitoringAdapter().execute(_make_request(AdapterOperation.WRITE))
        assert resp.data.get("mock") is True


class TestMonitoringAdapterHealthCheck:
    def test_returns_dict(self):
        assert isinstance(MonitoringAdapter().health_check(), dict)

    def test_has_healthy_key(self):
        assert "healthy" in MonitoringAdapter().health_check()

    def test_is_healthy(self):
        assert MonitoringAdapter().health_check()["healthy"] is True

    def test_has_mode_placeholder(self):
        assert MonitoringAdapter().health_check().get("mode") == "placeholder"
