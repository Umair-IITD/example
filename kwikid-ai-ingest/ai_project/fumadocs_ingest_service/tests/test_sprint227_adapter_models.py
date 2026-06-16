"""
tests/test_sprint227_adapter_models.py

Sprint 2.27: Tests for AdapterType, AdapterOperation, AdapterStatus,
AdapterRequest, AdapterResponse, AdapterExecutionResult.
"""
import pytest
import uuid
from datetime import datetime, timezone

from case_engine.adapters.models import (
    AdapterExecutionResult,
    AdapterOperation,
    AdapterRequest,
    AdapterResponse,
    AdapterStatus,
    AdapterType,
)


# ── AdapterType ───────────────────────────────────────────────────────────────

class TestAdapterType:
    def test_all_values_exist(self):
        assert AdapterType.FRESHDESK.value    == "FRESHDESK"
        assert AdapterType.ADMIN_PORTAL.value == "ADMIN_PORTAL"
        assert AdapterType.ASANA.value        == "ASANA"
        assert AdapterType.MONITORING.value   == "MONITORING"

    def test_is_str_enum(self):
        assert isinstance(AdapterType.FRESHDESK, str)
        assert AdapterType.FRESHDESK == "FRESHDESK"

    def test_four_types_total(self):
        assert len(AdapterType) == 4


# ── AdapterOperation ──────────────────────────────────────────────────────────

class TestAdapterOperation:
    def test_all_operations_exist(self):
        ops = {op.value for op in AdapterOperation}
        assert "READ"    in ops
        assert "WRITE"   in ops
        assert "UPDATE"  in ops
        assert "CREATE"  in ops
        assert "SEARCH"  in ops
        assert "EXECUTE" in ops

    def test_six_operations_total(self):
        assert len(AdapterOperation) == 6

    def test_is_str_enum(self):
        assert isinstance(AdapterOperation.READ, str)


# ── AdapterStatus ─────────────────────────────────────────────────────────────

class TestAdapterStatus:
    def test_all_statuses_exist(self):
        statuses = {s.value for s in AdapterStatus}
        assert "SUCCESS"     in statuses
        assert "FAILED"      in statuses
        assert "RETRYABLE"   in statuses
        assert "BLOCKED"     in statuses
        assert "UNSUPPORTED" in statuses

    def test_five_statuses_total(self):
        assert len(AdapterStatus) == 5


# ── AdapterRequest ────────────────────────────────────────────────────────────

class TestAdapterRequest:
    def _make(self, **kwargs) -> AdapterRequest:
        defaults = dict(
            adapter_type=AdapterType.FRESHDESK,
            operation=AdapterOperation.READ,
            payload={"key": "val"},
            case_id="case-001",
            action_type="otp_resend",
        )
        defaults.update(kwargs)
        return AdapterRequest.create(**defaults)

    def test_create_populates_request_id(self):
        req = self._make()
        assert req.request_id
        uuid.UUID(req.request_id)  # raises if invalid

    def test_create_sets_all_fields(self):
        req = self._make()
        assert req.adapter_type == AdapterType.FRESHDESK
        assert req.operation    == AdapterOperation.READ
        assert req.payload      == {"key": "val"}
        assert req.case_id      == "case-001"
        assert req.action_type  == "otp_resend"

    def test_frozen(self):
        req = self._make()
        with pytest.raises((AttributeError, TypeError)):
            req.case_id = "other"  # type: ignore[misc]

    def test_metadata_defaults_to_empty_dict(self):
        req = self._make()
        assert req.metadata == {}

    def test_metadata_can_be_overridden(self):
        req = self._make(metadata={"source": "test"})
        assert req.metadata == {"source": "test"}

    def test_to_dict_contains_all_keys(self):
        req = self._make()
        d = req.to_dict()
        assert "request_id"   in d
        assert "adapter_type" in d
        assert "operation"    in d
        assert "payload"      in d
        assert "case_id"      in d
        assert "action_type"  in d
        assert "metadata"     in d

    def test_from_dict_roundtrip(self):
        req = self._make()
        d   = req.to_dict()
        req2 = AdapterRequest.from_dict(d)
        assert req2.request_id   == req.request_id
        assert req2.adapter_type == req.adapter_type
        assert req2.operation    == req.operation
        assert req2.case_id      == req.case_id

    def test_different_requests_have_unique_ids(self):
        r1 = self._make()
        r2 = self._make()
        assert r1.request_id != r2.request_id

    def test_payload_isolation(self):
        original = {"k": "v"}
        req = self._make(payload=original)
        original["k"] = "MUTATED"
        assert req.payload["k"] == "v"


# ── AdapterResponse ───────────────────────────────────────────────────────────

class TestAdapterResponse:
    def _make(self, status=AdapterStatus.SUCCESS, **kwargs) -> AdapterResponse:
        defaults = dict(
            response_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            adapter_type=AdapterType.FRESHDESK,
            operation=AdapterOperation.READ,
            status=status,
            data={"result": "ok"},
            error_code=None,
            error_message=None,
            duration_ms=10,
            responded_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        defaults.update(kwargs)
        return AdapterResponse(**defaults)

    def test_is_success_true_for_success(self):
        r = self._make(status=AdapterStatus.SUCCESS)
        assert r.is_success() is True

    def test_is_success_false_for_failed(self):
        r = self._make(status=AdapterStatus.FAILED)
        assert r.is_success() is False

    def test_is_retryable_true_for_retryable(self):
        r = self._make(status=AdapterStatus.RETRYABLE)
        assert r.is_retryable() is True

    def test_is_retryable_false_for_success(self):
        r = self._make(status=AdapterStatus.SUCCESS)
        assert r.is_retryable() is False

    def test_is_blocked_true_for_blocked(self):
        r = self._make(status=AdapterStatus.BLOCKED)
        assert r.is_blocked() is True

    def test_is_blocked_false_for_success(self):
        r = self._make(status=AdapterStatus.SUCCESS)
        assert r.is_blocked() is False

    def test_blocked_factory(self):
        r = AdapterResponse.blocked(
            request_id="req-1",
            adapter_type=AdapterType.ASANA,
            operation=AdapterOperation.CREATE,
            reason="no adapter",
        )
        assert r.status == AdapterStatus.BLOCKED
        assert r.error_code is not None
        assert "no adapter" in (r.error_message or "")

    def test_unsupported_factory(self):
        r = AdapterResponse.unsupported(
            request_id="req-1",
            adapter_type=AdapterType.ASANA,
            operation=AdapterOperation.SEARCH,
        )
        assert r.status == AdapterStatus.UNSUPPORTED
        assert r.error_code is not None

    def test_frozen(self):
        r = self._make()
        with pytest.raises((AttributeError, TypeError)):
            r.status = AdapterStatus.FAILED  # type: ignore[misc]

    def test_duration_ms_non_negative(self):
        r = self._make(duration_ms=0)
        assert r.duration_ms >= 0


# ── AdapterExecutionResult ────────────────────────────────────────────────────

class TestAdapterExecutionResult:
    def _make_request(self) -> AdapterRequest:
        return AdapterRequest.create(
            adapter_type=AdapterType.FRESHDESK,
            operation=AdapterOperation.READ,
            payload={},
            case_id="c1",
            action_type="otp_resend",
        )

    def _make_response(self, status=AdapterStatus.SUCCESS) -> AdapterResponse:
        return AdapterResponse(
            response_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            adapter_type=AdapterType.FRESHDESK,
            operation=AdapterOperation.READ,
            status=status,
            data={},
            error_code=None,
            error_message=None,
            duration_ms=5,
            responded_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def test_from_response_success(self):
        req  = self._make_request()
        resp = self._make_response(status=AdapterStatus.SUCCESS)
        result = AdapterExecutionResult.from_response(req, resp, "freshdesk-placeholder")
        assert result.success is True
        assert result.adapter_name == "freshdesk-placeholder"
        assert result.request.request_id == req.request_id

    def test_from_response_failed(self):
        req  = self._make_request()
        resp = self._make_response(status=AdapterStatus.FAILED)
        result = AdapterExecutionResult.from_response(req, resp, "freshdesk-placeholder")
        assert result.success is False

    def test_from_response_retryable(self):
        req  = self._make_request()
        resp = self._make_response(status=AdapterStatus.RETRYABLE)
        result = AdapterExecutionResult.from_response(req, resp, "freshdesk-placeholder")
        assert result.success   is False
        assert result.retryable is True

    def test_from_response_blocked_not_retryable(self):
        req  = self._make_request()
        resp = self._make_response(status=AdapterStatus.BLOCKED)
        result = AdapterExecutionResult.from_response(req, resp, "router")
        assert result.success   is False
        assert result.retryable is False

    def test_frozen(self):
        req  = self._make_request()
        resp = self._make_response()
        result = AdapterExecutionResult.from_response(req, resp, "adapter")
        with pytest.raises((AttributeError, TypeError)):
            result.success = False  # type: ignore[misc]
