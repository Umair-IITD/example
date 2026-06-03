"""
tests/test_sprint23_provider_models.py

Sprint 2.3 — Provider model contract tests.

Covers:
  ProviderCapability
    - All required enum values exist
    - Values compare equal to their string representation
    - Usable in frozenset operations

  ProviderRequest
    - Frozen (immutable references)
    - All required fields present
    - timeout_seconds > 0 validation
    - Default values are correct
    - payload and metadata default to empty dict

  ProviderResponse
    - Frozen
    - All required fields present
    - Default values are correct
    - success=True and success=False both valid

  ProviderHealth
    - Frozen
    - latency_ms >= 0 validation
    - All fields accessible
    - is_healthy=False with message is valid

  ProviderMetadata
    - Frozen
    - capabilities is frozenset
    - All fields accessible
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderRequest,
    ProviderResponse,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _request(**overrides) -> ProviderRequest:
    defaults = dict(
        request_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        action_id="action-1",
        case_id="case-1",
        ticket_id="ticket-1",
        client="acme",
        operation="reset_otp",
        payload={"account_id": "ACC-123"},
    )
    defaults.update(overrides)
    return ProviderRequest(**defaults)


# ── ProviderCapability ─────────────────────────────────────────────────────────


class TestProviderCapability:
    def test_execute_exists(self) -> None:
        assert ProviderCapability.EXECUTE

    def test_rollback_exists(self) -> None:
        assert ProviderCapability.ROLLBACK

    def test_idempotent_exists(self) -> None:
        assert ProviderCapability.IDEMPOTENT

    def test_health_check_exists(self) -> None:
        assert ProviderCapability.HEALTH_CHECK

    def test_execute_value(self) -> None:
        assert ProviderCapability.EXECUTE == "execute"

    def test_rollback_value(self) -> None:
        assert ProviderCapability.ROLLBACK == "rollback"

    def test_idempotent_value(self) -> None:
        assert ProviderCapability.IDEMPOTENT == "idempotent"

    def test_health_check_value(self) -> None:
        assert ProviderCapability.HEALTH_CHECK == "health_check"

    def test_usable_in_frozenset(self) -> None:
        caps = frozenset({ProviderCapability.EXECUTE, ProviderCapability.ROLLBACK})
        assert ProviderCapability.EXECUTE in caps
        assert ProviderCapability.ROLLBACK in caps
        assert ProviderCapability.IDEMPOTENT not in caps

    def test_string_comparison(self) -> None:
        assert ProviderCapability.EXECUTE == "execute"

    def test_four_values_total(self) -> None:
        assert len(list(ProviderCapability)) == 4


# ── ProviderRequest ────────────────────────────────────────────────────────────


class TestProviderRequest:
    def test_basic_construction(self) -> None:
        req = _request()
        assert req.operation == "reset_otp"
        assert req.client == "acme"

    def test_is_frozen(self) -> None:
        req = _request()
        with pytest.raises((AttributeError, TypeError)):
            req.operation = "other"  # type: ignore[misc]

    def test_request_id_set(self) -> None:
        rid = str(uuid.uuid4())
        req = _request(request_id=rid)
        assert req.request_id == rid

    def test_trace_id_set(self) -> None:
        tid = str(uuid.uuid4())
        req = _request(trace_id=tid)
        assert req.trace_id == tid

    def test_default_timeout(self) -> None:
        req = _request()
        assert req.timeout_seconds == 30.0

    def test_custom_timeout(self) -> None:
        req = _request(timeout_seconds=5.0)
        assert req.timeout_seconds == 5.0

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            _request(timeout_seconds=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            _request(timeout_seconds=-1.0)

    def test_default_metadata_is_empty_dict(self) -> None:
        req = _request()
        assert req.metadata == {}

    def test_custom_metadata(self) -> None:
        req = _request(metadata={"priority": "high"})
        assert req.metadata == {"priority": "high"}

    def test_payload_set(self) -> None:
        req = _request(payload={"account_id": "ACC-999"})
        assert req.payload == {"account_id": "ACC-999"}

    def test_created_at_defaults_to_now(self) -> None:
        before = _now()
        req = _request()
        after = _now()
        assert before <= req.created_at <= after

    def test_custom_created_at(self) -> None:
        t = _now()
        req = _request(created_at=t)
        assert req.created_at == t

    def test_all_required_fields(self) -> None:
        req = _request()
        for field in ("request_id", "trace_id", "action_id", "case_id",
                      "ticket_id", "client", "operation", "payload"):
            assert hasattr(req, field)


# ── ProviderResponse ───────────────────────────────────────────────────────────


class TestProviderResponse:
    def _response(self, **overrides) -> ProviderResponse:
        defaults = dict(
            request_id=str(uuid.uuid4()),
            provider_request_id="FD-99999",
            success=True,
        )
        defaults.update(overrides)
        return ProviderResponse(**defaults)

    def test_basic_construction(self) -> None:
        r = self._response()
        assert r.success is True
        assert r.provider_request_id == "FD-99999"

    def test_is_frozen(self) -> None:
        r = self._response()
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]

    def test_success_false(self) -> None:
        r = self._response(success=False)
        assert r.success is False

    def test_provider_request_id_none(self) -> None:
        r = self._response(provider_request_id=None)
        assert r.provider_request_id is None

    def test_default_status_code_is_none(self) -> None:
        r = self._response()
        assert r.status_code is None

    def test_custom_status_code(self) -> None:
        r = self._response(status_code=200)
        assert r.status_code == 200

    def test_default_result_is_empty_dict(self) -> None:
        r = self._response()
        assert r.result == {}

    def test_custom_result(self) -> None:
        r = self._response(result={"ticket_id": "TKT-1"})
        assert r.result == {"ticket_id": "TKT-1"}

    def test_default_metadata_is_empty_dict(self) -> None:
        r = self._response()
        assert r.metadata == {}

    def test_executed_at_defaults_to_now(self) -> None:
        before = _now()
        r = self._response()
        after = _now()
        assert before <= r.executed_at <= after

    def test_request_id_correlation(self) -> None:
        rid = str(uuid.uuid4())
        r = self._response(request_id=rid)
        assert r.request_id == rid


# ── ProviderHealth ─────────────────────────────────────────────────────────────


class TestProviderHealth:
    def _health(self, **overrides) -> ProviderHealth:
        defaults = dict(
            provider_name="freshdesk",
            provider_version="1.0.0",
            is_healthy=True,
            latency_ms=5,
            checked_at=_now(),
        )
        defaults.update(overrides)
        return ProviderHealth(**defaults)

    def test_basic_construction(self) -> None:
        h = self._health()
        assert h.is_healthy is True
        assert h.provider_name == "freshdesk"

    def test_is_frozen(self) -> None:
        h = self._health()
        with pytest.raises((AttributeError, TypeError)):
            h.is_healthy = False  # type: ignore[misc]

    def test_unhealthy(self) -> None:
        h = self._health(is_healthy=False, message="connection refused")
        assert h.is_healthy is False
        assert h.message == "connection refused"

    def test_zero_latency_valid(self) -> None:
        h = self._health(latency_ms=0)
        assert h.latency_ms == 0

    def test_negative_latency_raises(self) -> None:
        with pytest.raises(ValueError, match="latency_ms"):
            self._health(latency_ms=-1)

    def test_message_defaults_none(self) -> None:
        h = self._health()
        assert h.message is None

    def test_all_fields_accessible(self) -> None:
        t = _now()
        h = self._health(latency_ms=42, checked_at=t)
        assert h.latency_ms == 42
        assert h.checked_at == t
        assert h.provider_version == "1.0.0"


# ── ProviderMetadata ───────────────────────────────────────────────────────────


class TestProviderMetadata:
    def _metadata(self, **overrides) -> ProviderMetadata:
        defaults = dict(
            provider_name="freshdesk",
            provider_version="1.0.0",
            capabilities=frozenset({ProviderCapability.EXECUTE}),
        )
        defaults.update(overrides)
        return ProviderMetadata(**defaults)

    def test_basic_construction(self) -> None:
        m = self._metadata()
        assert m.provider_name == "freshdesk"
        assert m.provider_version == "1.0.0"

    def test_is_frozen(self) -> None:
        m = self._metadata()
        with pytest.raises((AttributeError, TypeError)):
            m.provider_name = "other"  # type: ignore[misc]

    def test_capabilities_is_frozenset(self) -> None:
        m = self._metadata()
        assert isinstance(m.capabilities, frozenset)

    def test_capability_in_set(self) -> None:
        m = self._metadata(capabilities=frozenset({
            ProviderCapability.EXECUTE,
            ProviderCapability.ROLLBACK,
        }))
        assert ProviderCapability.EXECUTE in m.capabilities
        assert ProviderCapability.ROLLBACK in m.capabilities
        assert ProviderCapability.IDEMPOTENT not in m.capabilities

    def test_description_defaults_empty(self) -> None:
        m = self._metadata()
        assert m.description == ""

    def test_custom_description(self) -> None:
        m = self._metadata(description="Freshdesk ticket operations")
        assert m.description == "Freshdesk ticket operations"

    def test_empty_capabilities(self) -> None:
        m = self._metadata(capabilities=frozenset())
        assert m.capabilities == frozenset()
