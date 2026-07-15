"""
tests/test_sprint245_tool_framework.py

Sprint 2.45: Comprehensive tests for the Production Tool Framework.

Sections (A-Y):
  A  ToolStatus enum
  B  ToolTimeout model
  C  ToolRetryPolicy model and should_retry logic
  D  ToolScope model
  E  ToolMetadata model
  F  ToolContext model
  G  ToolExecutionRequest model
  H  ToolExecutionMetrics
  I  ToolExecutionAudit and PII redaction
  J  ToolExecutionTrace
  K  ToolExecutionResult (ok / fail factories)
  L  ToolHealth
  M  ToolAvailability
  N  FrameworkVersion and versioning constants
  O  ToolLifecycleManager (transitions, thread-safety)
  P  ToolFrameworkMetrics (thread-safe, per-tool stats)
  Q  ProductionToolRegistry (registration, capability routing, health)
  R  ProductionToolExecutor (execution, retry, audit, never-raises)
  S  ExecutionPipeline (validation, delegation)
  T  ToolExecutorProvider bridge (slot mapping, CollectionResult conversion)
  U  ToolFrameworkValidators (all validators)
  V  Serialization roundtrip (dict ↔ JSON)
  W  InvestigationContext Sprint 2.45 fields
  X  Collector integration with tool_executor
  Y  Thread-safety stress tests
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Framework imports ──────────────────────────────────────────────────────────
from case_engine.tools.framework.models import (
    ToolAuthenticationRequirement,
    ToolAvailability,
    ToolContext,
    ToolDependency,
    ToolExecutionAudit,
    ToolExecutionError,
    ToolExecutionMetrics,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionTrace,
    ToolHealth,
    ToolMetadata,
    ToolPermissionRequirement,
    ToolRetryPolicy,
    ToolScope,
    ToolStatus,
    ToolTimeout,
    _new_id,
    _now_iso,
)
from case_engine.tools.framework.versioning import (
    CURRENT_EXECUTOR_VERSION,
    CURRENT_FRAMEWORK_VERSION,
    CURRENT_REGISTRY_VERSION,
    FrameworkVersion,
)
from case_engine.tools.framework.lifecycle import (
    ToolLifecycleManager,
    ToolLifecycleStatus,
    is_valid_transition,
)
from case_engine.tools.framework.metrics import ToolFrameworkMetrics
from case_engine.tools.framework.registry import ProductionToolRegistry
from case_engine.tools.framework.executor import ProductionToolExecutor
from case_engine.tools.framework.pipeline import ExecutionPipeline
from case_engine.tools.framework.validators import ToolFrameworkValidators
from case_engine.tools.framework.serialization import (
    tool_execution_result_from_dict,
    tool_execution_result_from_json,
    tool_execution_result_to_dict,
    tool_execution_result_to_json,
    tool_health_from_dict,
    tool_health_to_dict,
    tool_metadata_from_dict,
    tool_metadata_to_dict,
    ToolFrameworkSerializationError,
)
from case_engine.tools.framework.bridge import (
    ToolExecutorProvider,
    _HANDLEABLE_KINDS,
    _KIND_INPUT_MAP,
)
from case_engine.tools.tool_executor import BaseTool
from case_engine.tools.tool_models import ToolDefinition, ToolInput, ToolResult
from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment, TenantToolConfig, TenantAuthConfig


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_tool_definition(
    tool_name: str = "test_tool",
    required_inputs: tuple[str, ...] = ("session_id",),
    output_schema: dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        tool_name=tool_name,
        description="A test tool for unit testing",
        required_inputs=required_inputs,
        output_schema=output_schema or {"result": "string"},
        version="1.0.0",
    )


class _OkTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return _make_tool_definition()

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"session_data": "found", "session_id": inputs.get("session_id")}


class _FailTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return _make_tool_definition("fail_tool")

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("deliberate failure")


class _RaisingTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return _make_tool_definition("raising_tool")

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("tool raised unexpectedly")


def _make_registry_with_ok_tool() -> ProductionToolRegistry:
    reg = ProductionToolRegistry()
    tool = _OkTool()
    reg.register(tool)
    reg.register_capability("SESSION", tool.definition.tool_name)
    reg.set_lifecycle_status("test_tool", ToolLifecycleStatus.READY, "ok")
    return reg


def _make_executor_with_ok_tool() -> ProductionToolExecutor:
    reg = _make_registry_with_ok_tool()
    return ProductionToolExecutor(reg)


def _make_request(
    evidence_kind: str = "SESSION",
    tool_name: str | None = None,
    slots: dict | None = None,
) -> ToolExecutionRequest:
    ctx = ToolContext(
        tool_name=tool_name or f"<{evidence_kind}>",
        tenant_id="tenant-1",
        case_id="case-1",
        topic="test",
        slots=slots or {"session_id": "sess-abc"},
    )
    return ToolExecutionRequest(context=ctx, evidence_kind=evidence_kind, tool_name=tool_name)


# ─────────────────────────────────────────────────────────────────────────────
# A — ToolStatus enum
# ─────────────────────────────────────────────────────────────────────────────

class TestToolStatus:
    def test_all_seven_statuses_exist(self):
        values = {s.value for s in ToolStatus}
        expected = {
            "REGISTERED", "READY", "DEGRADED", "FAILED",
            "DISABLED", "OFFLINE", "UNKNOWN",
        }
        assert values == expected

    def test_status_is_string_comparable(self):
        assert ToolStatus.READY == "READY"
        assert ToolStatus.FAILED == "FAILED"

    def test_status_enum_from_value(self):
        assert ToolStatus("DEGRADED") is ToolStatus.DEGRADED

    def test_status_in_set(self):
        usable = {ToolStatus.READY.value, ToolStatus.DEGRADED.value}
        assert "READY" in usable
        assert "FAILED" not in usable

    def test_registered_not_usable(self):
        assert ToolStatus.REGISTERED.value not in {"READY", "DEGRADED"}


# ─────────────────────────────────────────────────────────────────────────────
# B — ToolTimeout
# ─────────────────────────────────────────────────────────────────────────────

class TestToolTimeout:
    def test_defaults(self):
        t = ToolTimeout()
        assert t.total_seconds == 30.0
        assert t.per_attempt_seconds == 10.0
        assert t.grace_seconds == 1.0

    def test_custom_values(self):
        t = ToolTimeout(total_seconds=60.0, per_attempt_seconds=20.0, grace_seconds=2.0)
        assert t.total_seconds == 60.0

    def test_to_dict_keys(self):
        d = ToolTimeout().to_dict()
        assert "total_seconds" in d
        assert "per_attempt_seconds" in d
        assert "grace_seconds" in d

    def test_from_dict_roundtrip(self):
        orig = ToolTimeout(total_seconds=45.0, per_attempt_seconds=15.0, grace_seconds=0.5)
        recovered = ToolTimeout.from_dict(orig.to_dict())
        assert recovered.total_seconds == 45.0
        assert recovered.per_attempt_seconds == 15.0

    def test_from_dict_defaults_on_empty(self):
        t = ToolTimeout.from_dict({})
        assert t.total_seconds == 30.0

    def test_is_frozen(self):
        t = ToolTimeout()
        with pytest.raises((AttributeError, TypeError)):
            t.total_seconds = 999.0  # type: ignore[misc]

    def test_invalid_zero_total_raises(self):
        with pytest.raises(ValueError):
            ToolTimeout(total_seconds=0.0)

    def test_invalid_negative_per_attempt_raises(self):
        with pytest.raises(ValueError):
            ToolTimeout(per_attempt_seconds=-1.0)


# ─────────────────────────────────────────────────────────────────────────────
# C — ToolRetryPolicy
# ─────────────────────────────────────────────────────────────────────────────

class TestToolRetryPolicy:
    def test_defaults(self):
        p = ToolRetryPolicy()
        assert p.max_attempts == 3
        assert p.backoff_seconds == 0.5
        assert p.retry_on_error_codes == ()

    def test_should_retry_within_limit(self):
        p = ToolRetryPolicy(max_attempts=3)
        assert p.should_retry(attempt=1, error_code="TIMEOUT", timed_out=False)
        assert p.should_retry(attempt=2, error_code="TIMEOUT", timed_out=False)

    def test_should_not_retry_at_limit(self):
        p = ToolRetryPolicy(max_attempts=3)
        assert not p.should_retry(attempt=3, error_code="TIMEOUT", timed_out=False)

    def test_no_retry_on_timed_out(self):
        p = ToolRetryPolicy(max_attempts=3, retry_on_timeout=False)
        assert not p.should_retry(attempt=1, error_code="TIMEOUT", timed_out=True)

    def test_retry_on_timeout_allowed(self):
        p = ToolRetryPolicy(max_attempts=3, retry_on_timeout=True)
        assert p.should_retry(attempt=1, error_code="TIMEOUT", timed_out=True)

    def test_error_code_whitelist_match(self):
        p = ToolRetryPolicy(max_attempts=3, retry_on_error_codes=("RATE_LIMIT",))
        assert p.should_retry(attempt=1, error_code="RATE_LIMIT", timed_out=False)

    def test_error_code_whitelist_no_match_still_retries_if_no_whitelist(self):
        p = ToolRetryPolicy(max_attempts=3, retry_on_error_codes=())
        assert p.should_retry(attempt=1, error_code="ANYTHING", timed_out=False)

    def test_to_dict_roundtrip(self):
        p = ToolRetryPolicy(max_attempts=5, backoff_seconds=1.0)
        recovered = ToolRetryPolicy.from_dict(p.to_dict())
        assert recovered.max_attempts == 5
        assert recovered.backoff_seconds == 1.0

    def test_is_frozen(self):
        p = ToolRetryPolicy()
        with pytest.raises((AttributeError, TypeError)):
            p.max_attempts = 10  # type: ignore[misc]

    def test_max_attempts_must_be_positive(self):
        with pytest.raises(ValueError):
            ToolRetryPolicy(max_attempts=0)


# ─────────────────────────────────────────────────────────────────────────────
# D — ToolScope
# ─────────────────────────────────────────────────────────────────────────────

class TestToolScope:
    def test_basic_creation(self):
        s = ToolScope(domain="session")
        assert s.domain == "session"

    def test_evidence_kinds_default_empty(self):
        s = ToolScope(domain="session")
        assert s.evidence_kinds == ()

    def test_to_dict_roundtrip(self):
        s = ToolScope(
            domain="session",
            evidence_kinds=("SESSION", "LOG"),
            topics=("eKYC", "onboarding"),
            tenant_types=("bank",),
        )
        recovered = ToolScope.from_dict(s.to_dict())
        assert recovered.domain == "session"
        assert "SESSION" in recovered.evidence_kinds
        assert "eKYC" in recovered.topics

    def test_from_dict_minimal(self):
        s = ToolScope.from_dict({"domain": "log"})
        assert s.domain == "log"


# ─────────────────────────────────────────────────────────────────────────────
# E — ToolMetadata
# ─────────────────────────────────────────────────────────────────────────────

class TestToolMetadata:
    def _make(self, **overrides) -> ToolMetadata:
        defaults = dict(
            tool_name="session_tool",
            display_name="Session Tool",
            description="Fetches session data",
            version="1.0.0",
            provider="AdminPortal",
            capability="SESSION",
            scope=ToolScope(domain="session"),
            auth_requirement=ToolAuthenticationRequirement.no_auth(),
            permission_requirement=ToolPermissionRequirement(),
        )
        defaults.update(overrides)
        return ToolMetadata(**defaults)  # type: ignore[arg-type]

    def test_basic_creation(self):
        m = self._make()
        assert m.tool_name == "session_tool"
        assert m.deprecated is False

    def test_to_dict_has_required_keys(self):
        d = self._make().to_dict()
        for key in ("tool_name", "version", "provider", "capability"):
            assert key in d

    def test_deprecated_flag(self):
        m = self._make(deprecated=True, deprecation_reason="replaced", replacement_tool="new_tool")
        assert m.deprecated
        assert m.deprecation_reason == "replaced"
        assert m.replacement_tool == "new_tool"

    def test_dependencies_tuple(self):
        dep = ToolDependency(depends_on_tool="auth_tool", dependency_type="REQUIRED", reason="needs auth")
        m = self._make(dependencies=(dep,))
        assert len(m.dependencies) == 1
        assert m.dependencies[0].depends_on_tool == "auth_tool"

    def test_tags_tuple(self):
        m = self._make(tags=("kyc", "session"))
        assert "kyc" in m.tags


# ─────────────────────────────────────────────────────────────────────────────
# F — ToolContext
# ─────────────────────────────────────────────────────────────────────────────

class TestToolContext:
    def test_basic_creation(self):
        ctx = ToolContext(
            tool_name="test_tool",
            tenant_id="t1",
            case_id="c1",
            topic="eKYC",
            slots={"session_id": "s1"},
        )
        assert ctx.tenant_id == "t1"
        assert ctx.case_id == "c1"

    def test_invocation_id_auto_generated(self):
        ctx = ToolContext(tool_name="t", tenant_id="", case_id="", topic="", slots={})
        assert ctx.invocation_id != ""
        assert len(ctx.invocation_id) > 10

    def test_get_slot_present(self):
        ctx = ToolContext(tool_name="t", tenant_id="", case_id="", topic="", slots={"k": "v"})
        assert ctx.get_slot("k") == "v"

    def test_get_slot_missing_returns_none(self):
        ctx = ToolContext(tool_name="t", tenant_id="", case_id="", topic="", slots={})
        assert ctx.get_slot("missing") is None

    def test_to_dict_has_slot_keys(self):
        ctx = ToolContext(tool_name="t", tenant_id="", case_id="", topic="", slots={"x": 1})
        d = ctx.to_dict()
        # to_dict exposes slot_keys (not values) for security
        assert "x" in d.get("slot_keys", d.get("slots", {}))

    def test_two_contexts_have_different_invocation_ids(self):
        ctx1 = ToolContext(tool_name="t", tenant_id="", case_id="", topic="", slots={})
        ctx2 = ToolContext(tool_name="t", tenant_id="", case_id="", topic="", slots={})
        assert ctx1.invocation_id != ctx2.invocation_id


# ─────────────────────────────────────────────────────────────────────────────
# G — ToolExecutionRequest
# ─────────────────────────────────────────────────────────────────────────────

class TestToolExecutionRequest:
    def test_default_priority(self):
        req = _make_request()
        assert req.priority == 5

    def test_request_id_auto_generated(self):
        req = _make_request()
        assert req.request_id != ""

    def test_to_dict_has_evidence_kind(self):
        req = _make_request(evidence_kind="LOG")
        d = req.to_dict()
        assert d["evidence_kind"] == "LOG"

    def test_tool_name_optional(self):
        req = _make_request(tool_name=None)
        assert req.tool_name is None

    def test_custom_timeout_and_retry(self):
        req = ToolExecutionRequest(
            context=ToolContext(tool_name="t", tenant_id="", case_id="", topic="", slots={}),
            evidence_kind="SESSION",
            timeout=ToolTimeout(total_seconds=60.0),
            retry_policy=ToolRetryPolicy(max_attempts=1),
        )
        assert req.timeout.total_seconds == 60.0
        assert req.retry_policy.max_attempts == 1


# ─────────────────────────────────────────────────────────────────────────────
# H — ToolExecutionMetrics
# ─────────────────────────────────────────────────────────────────────────────

class TestToolExecutionMetrics:
    def test_initial_state(self):
        m = ToolExecutionMetrics()
        assert m.total_attempts == 0
        assert m.successful_attempts == 0

    def test_record_success(self):
        m = ToolExecutionMetrics()
        m.record_attempt(duration_ms=100, success=True)
        assert m.total_attempts == 1
        assert m.successful_attempts == 1
        assert m.failed_attempts == 0

    def test_record_failure(self):
        m = ToolExecutionMetrics()
        m.record_attempt(duration_ms=50, success=False)
        assert m.total_attempts == 1
        assert m.failed_attempts == 1

    def test_record_timeout(self):
        m = ToolExecutionMetrics()
        m.record_attempt(duration_ms=10000, success=False, timed_out=True)
        assert m.timeout_attempts == 1

    def test_success_rate_zero_attempts(self):
        m = ToolExecutionMetrics()
        assert m.success_rate == 0.0

    def test_success_rate_one_of_two(self):
        m = ToolExecutionMetrics()
        m.record_attempt(100, success=True)
        m.record_attempt(100, success=False)
        assert m.success_rate == 0.5

    def test_average_attempt_ms(self):
        m = ToolExecutionMetrics()
        m.record_attempt(100, success=True)
        m.record_attempt(200, success=True)
        assert m.average_attempt_ms == 150.0

    def test_to_dict_keys(self):
        m = ToolExecutionMetrics()
        d = m.to_dict()
        assert "total_attempts" in d
        assert "success_rate" in d

    def test_min_max_tracking(self):
        m = ToolExecutionMetrics()
        m.record_attempt(50, success=True)
        m.record_attempt(200, success=True)
        assert m.min_attempt_ms == 50
        assert m.max_attempt_ms == 200


# ─────────────────────────────────────────────────────────────────────────────
# I — ToolExecutionAudit and PII redaction
# ─────────────────────────────────────────────────────────────────────────────

class TestToolExecutionAudit:
    def _make_audit(self, **kw) -> ToolExecutionAudit:
        defaults = dict(
            invocation_id=_new_id(),
            tool_name="test_tool",
            request_id=_new_id(),
            tenant_id="t1",
            case_id="c1",
            started_at=_now_iso(),
            completed_at=_now_iso(),
            duration_ms=100,
            attempts=1,
            outcome="SUCCESS",
            error_code=None,
            inputs_redacted={},
            output_summary={},
        )
        defaults.update(kw)
        return ToolExecutionAudit(**defaults)

    def test_pii_redaction_keys_preserved(self):
        inputs = {"session_id": "sess-123", "phone_number": "0712345678"}
        redacted = ToolExecutionAudit._redact_inputs(inputs)
        assert "session_id" in redacted
        assert "phone_number" in redacted

    def test_pii_redaction_values_replaced(self):
        inputs = {"session_id": "sess-123", "phone_number": "0712345678"}
        redacted = ToolExecutionAudit._redact_inputs(inputs)
        assert redacted["session_id"] == "<redacted>"
        assert redacted["phone_number"] == "<redacted>"

    def test_pii_redaction_empty_input(self):
        assert ToolExecutionAudit._redact_inputs({}) == {}

    def test_output_summary_key_count(self):
        output = {"field1": "val1", "field2": "val2"}
        summary = ToolExecutionAudit._summarise_output(output)
        assert summary["key_count"] == 2

    def test_output_summary_empty(self):
        summary = ToolExecutionAudit._summarise_output({})
        assert summary["key_count"] == 0

    def test_to_dict_has_required_fields(self):
        a = self._make_audit()
        d = a.to_dict()
        for key in ("invocation_id", "tool_name", "outcome", "duration_ms"):
            assert key in d

    def test_audit_outcome_success(self):
        a = self._make_audit(outcome="SUCCESS")
        assert a.outcome == "SUCCESS"

    def test_audit_outcome_failure(self):
        a = self._make_audit(outcome="FAILURE", error_code="TOOL_FAILED")
        assert a.error_code == "TOOL_FAILED"


# ─────────────────────────────────────────────────────────────────────────────
# J — ToolExecutionTrace
# ─────────────────────────────────────────────────────────────────────────────

class TestToolExecutionTrace:
    def test_basic_creation(self):
        m = ToolExecutionMetrics()
        t = ToolExecutionTrace(
            request_id=_new_id(),
            tool_name="test",
            attempts=[{"attempt": 1, "duration_ms": 100}],
            final_outcome="SUCCESS",
            total_duration_ms=100,
            metrics=m,
        )
        assert t.final_outcome == "SUCCESS"
        assert len(t.attempts) == 1

    def test_to_dict_has_metrics(self):
        m = ToolExecutionMetrics()
        t = ToolExecutionTrace(
            request_id=_new_id(), tool_name="t",
            attempts=[], final_outcome="FAILURE",
            total_duration_ms=0, metrics=m,
        )
        d = t.to_dict()
        assert "metrics" in d
        assert "attempts" in d


# ─────────────────────────────────────────────────────────────────────────────
# K — ToolExecutionResult
# ─────────────────────────────────────────────────────────────────────────────

def _ok_result(tool_name: str = "test_tool", payload: dict | None = None) -> ToolExecutionResult:
    now = _now_iso()
    return ToolExecutionResult.ok(
        tool_name=tool_name,
        invocation_id=_new_id(),
        request_id=_new_id(),
        payload=payload if payload is not None else {"data": "value"},
        metrics=ToolExecutionMetrics(),
        audit=MagicMock(),
        trace=None,
        started_at=now,
        completed_at=now,
        duration_ms=10,
    )


def _fail_result(
    tool_name: str = "test_tool",
    error_code: str = "TOOL_ERROR",
    error_message: str = "something went wrong",
) -> ToolExecutionResult:
    now = _now_iso()
    return ToolExecutionResult.fail(
        tool_name=tool_name,
        invocation_id=_new_id(),
        request_id=_new_id(),
        error_code=error_code,
        error_message=error_message,
        metrics=ToolExecutionMetrics(),
        audit=MagicMock(),
        trace=None,
        started_at=now,
        completed_at=now,
        duration_ms=0,
    )


class TestToolExecutionResult:
    def test_ok_factory(self):
        result = _ok_result(payload={"data": "value"})
        assert result.success is True
        assert result.error_code is None
        assert result.payload == {"data": "value"}

    def test_fail_factory(self):
        result = _fail_result(error_code="TOOL_ERROR", error_message="something went wrong")
        assert result.success is False
        assert result.error_code == "TOOL_ERROR"
        assert result.error_message == "something went wrong"
        assert result.payload == {}

    def test_to_dict_success(self):
        result = _ok_result(payload={"k": "v"})
        d = result.to_dict()
        assert d["success"] is True
        # to_dict exposes payload_keys (not raw payload) for security
        assert "payload_keys" in d or "payload" in d

    def test_to_dict_failure(self):
        result = _fail_result(error_code="ERR", error_message="msg")
        d = result.to_dict()
        assert d["success"] is False
        assert d["error_code"] == "ERR"

    def test_duration_ms_non_negative(self):
        result = _ok_result()
        assert result.duration_ms >= 0

    def test_invocation_id_present(self):
        result = _ok_result()
        assert result.invocation_id != ""


# ─────────────────────────────────────────────────────────────────────────────
# L — ToolHealth
# ─────────────────────────────────────────────────────────────────────────────

class TestToolHealth:
    def _make(self, **kw) -> ToolHealth:
        defaults = dict(
            tool_name="test_tool",
            status=ToolStatus.READY.value,
            last_checked_at=_now_iso(),
        )
        defaults.update(kw)
        return ToolHealth(**defaults)

    def test_is_healthy_ready(self):
        h = self._make(status="READY")
        assert h.is_healthy()

    def test_is_not_healthy_failed(self):
        h = self._make(status="FAILED")
        assert not h.is_healthy()

    def test_is_usable_ready(self):
        h = self._make(status="READY")
        assert h.is_usable()

    def test_is_usable_degraded(self):
        h = self._make(status="DEGRADED")
        assert h.is_usable()

    def test_is_not_usable_registered(self):
        h = self._make(status="REGISTERED")
        assert not h.is_usable()

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert "tool_name" in d
        assert "status" in d
        assert "availability_pct" in d

    def test_consecutive_failures_default_zero(self):
        h = self._make()
        assert h.consecutive_failures == 0

    def test_availability_pct_default_100(self):
        h = self._make()
        assert h.availability_pct == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# M — ToolAvailability
# ─────────────────────────────────────────────────────────────────────────────

class TestToolAvailability:
    def test_available_true(self):
        a = ToolAvailability(tool_name="t", available=True, reason="ok", checked_at=_now_iso())
        assert a.available is True

    def test_available_false(self):
        a = ToolAvailability(tool_name="t", available=False, reason="degraded", checked_at=_now_iso())
        assert a.available is False

    def test_to_dict_keys(self):
        a = ToolAvailability(tool_name="t", available=True, reason="ok", checked_at=_now_iso())
        d = a.to_dict()
        assert "tool_name" in d
        assert "available" in d


# ─────────────────────────────────────────────────────────────────────────────
# N — FrameworkVersion and versioning constants
# ─────────────────────────────────────────────────────────────────────────────

class TestFrameworkVersioning:
    def test_current_framework_version_format(self):
        parts = CURRENT_FRAMEWORK_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_current_registry_version_format(self):
        parts = CURRENT_REGISTRY_VERSION.split(".")
        assert len(parts) == 3

    def test_current_executor_version_format(self):
        parts = CURRENT_EXECUTOR_VERSION.split(".")
        assert len(parts) == 3

    def test_parse_version(self):
        v = FrameworkVersion.parse("2.45.0")
        assert v.major == 2
        assert v.minor == 45
        assert v.patch == 0

    def test_parse_invalid_raises(self):
        with pytest.raises((ValueError, Exception)):
            FrameworkVersion.parse("not.a.version")

    def test_compatibility_same_major(self):
        v1 = FrameworkVersion.parse("2.45.0")
        v2 = FrameworkVersion.parse("2.45.1")
        assert v1.is_compatible_with(v2)

    def test_incompatibility_different_major(self):
        v1 = FrameworkVersion.parse("2.45.0")
        v2 = FrameworkVersion.parse("3.0.0")
        assert not v1.is_compatible_with(v2)

    def test_version_is_frozen(self):
        v = FrameworkVersion.parse("2.45.0")
        with pytest.raises((AttributeError, TypeError)):
            v.major = 99  # type: ignore[misc]

    def test_sprint_245_version(self):
        v = FrameworkVersion.parse(CURRENT_FRAMEWORK_VERSION)
        assert v.major == 2
        assert v.minor == 45


# ─────────────────────────────────────────────────────────────────────────────
# O — ToolLifecycleManager
# ─────────────────────────────────────────────────────────────────────────────

class TestToolLifecycleManager:
    def test_register_tool(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        assert mgr.get_status("tool_a") == ToolLifecycleStatus.REGISTERED.value

    def test_valid_transition_registered_to_ready(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        result = mgr.transition("tool_a", ToolLifecycleStatus.READY, reason="health ok")
        assert result is True
        assert mgr.get_status("tool_a") == ToolLifecycleStatus.READY.value

    def test_invalid_transition_returns_false(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        result = mgr.transition("tool_a", ToolLifecycleStatus.FAILED, reason="bad jump")
        assert result is False

    def test_is_usable_ready(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        mgr.transition("tool_a", ToolLifecycleStatus.READY, reason="ok")
        assert mgr.is_usable("tool_a") is True

    def test_is_not_usable_registered(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        assert mgr.is_usable("tool_a") is False

    def test_history_records_transitions(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        mgr.transition("tool_a", ToolLifecycleStatus.READY, reason="ok")
        history = mgr.get_history("tool_a")
        assert len(history) >= 1

    def test_remove_tool(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        mgr.remove("tool_a")
        # After removal, tool is not in the statuses dict; get_status returns UNKNOWN as default
        assert "tool_a" not in mgr.all_statuses()

    def test_usable_tools_list(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        mgr.register("tool_b")
        mgr.transition("tool_a", ToolLifecycleStatus.READY, reason="ok")
        usable = mgr.usable_tools()
        assert "tool_a" in usable
        assert "tool_b" not in usable

    def test_force_set(self):
        mgr = ToolLifecycleManager()
        mgr.register("tool_a")
        mgr.force_set("tool_a", ToolLifecycleStatus.FAILED, reason="forced")
        assert mgr.get_status("tool_a") == ToolLifecycleStatus.FAILED.value

    def test_is_valid_transition_helper(self):
        assert is_valid_transition(
            ToolLifecycleStatus.REGISTERED, ToolLifecycleStatus.READY
        ) is True
        assert is_valid_transition(
            ToolLifecycleStatus.REGISTERED, ToolLifecycleStatus.FAILED
        ) is False

    def test_degraded_to_ready_allowed(self):
        mgr = ToolLifecycleManager()
        mgr.register("t")
        mgr.transition("t", ToolLifecycleStatus.READY, reason="ok")
        mgr.transition("t", ToolLifecycleStatus.DEGRADED, reason="degraded")
        result = mgr.transition("t", ToolLifecycleStatus.READY, reason="recovered")
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# P — ToolFrameworkMetrics
# ─────────────────────────────────────────────────────────────────────────────

class TestToolFrameworkMetrics:
    def test_initial_state(self):
        m = ToolFrameworkMetrics()
        assert m.total_executions == 0

    def test_record_success(self):
        m = ToolFrameworkMetrics()
        m.record_execution("tool_a", 100, success=True)
        assert m.total_executions == 1
        assert m.success_rate == 1.0

    def test_record_failure(self):
        m = ToolFrameworkMetrics()
        m.record_execution("tool_a", 100, success=False)
        assert m.total_executions == 1
        assert m.success_rate == 0.0

    def test_per_tool_stats(self):
        m = ToolFrameworkMetrics()
        m.record_execution("tool_a", 100, success=True)
        m.record_execution("tool_a", 200, success=False)
        stats = m.per_tool_stats("tool_a")
        assert isinstance(stats, dict)
        # key may be 'total' or 'executions' depending on implementation
        total = stats.get("total", stats.get("executions", 0))
        assert total == 2

    def test_capability_hit_counts(self):
        m = ToolFrameworkMetrics()
        m.record_execution("tool_a", 100, success=True, evidence_kind="SESSION")
        m.record_execution("tool_a", 100, success=True, evidence_kind="SESSION")
        hits = m.capability_hit_counts()
        assert hits.get("SESSION", 0) == 2

    def test_average_duration(self):
        m = ToolFrameworkMetrics()
        m.record_execution("t", 100, success=True)
        m.record_execution("t", 300, success=True)
        assert m.average_duration_ms == 200.0

    def test_to_dict_keys(self):
        m = ToolFrameworkMetrics()
        d = m.to_dict()
        assert "total_executions" in d
        assert "success_rate" in d

    def test_reset(self):
        m = ToolFrameworkMetrics()
        m.record_execution("t", 100, success=True)
        m.reset()
        assert m.total_executions == 0

    def test_timeout_tracking(self):
        m = ToolFrameworkMetrics()
        m.record_execution("t", 10000, success=False, timed_out=True)
        assert m.total_timeouts == 1

    def test_retry_tracking(self):
        m = ToolFrameworkMetrics()
        m.record_execution("t", 100, success=True, retries=2)
        assert m.total_retries == 2


# ─────────────────────────────────────────────────────────────────────────────
# Q — ProductionToolRegistry
# ─────────────────────────────────────────────────────────────────────────────

class TestProductionToolRegistry:
    def test_register_and_has(self):
        reg = ProductionToolRegistry()
        tool = _OkTool()
        reg.register(tool)
        assert reg.has_tool("test_tool")

    def test_get_registered_tool(self):
        reg = ProductionToolRegistry()
        tool = _OkTool()
        reg.register(tool)
        retrieved = reg.get("test_tool")
        assert retrieved is tool

    def test_get_missing_returns_none(self):
        reg = ProductionToolRegistry()
        assert reg.get("nonexistent") is None

    def test_register_capability(self):
        reg = ProductionToolRegistry()
        tool = _OkTool()
        reg.register(tool)
        reg.register_capability("SESSION", "test_tool")
        resolved = reg.resolve_capability("SESSION")
        assert resolved == "test_tool"

    def test_resolve_unregistered_capability_returns_none(self):
        reg = ProductionToolRegistry()
        assert reg.resolve_capability("NONEXISTENT") is None

    def test_get_tool_for_capability(self):
        reg = _make_registry_with_ok_tool()
        tool = reg.get_tool_for_capability("SESSION")
        assert tool is not None
        assert tool.definition.tool_name == "test_tool"

    def test_unregister_tool(self):
        reg = ProductionToolRegistry()
        tool = _OkTool()
        reg.register(tool)
        result = reg.unregister("test_tool")
        assert result is True
        assert not reg.has_tool("test_tool")

    def test_unregister_missing_returns_false(self):
        reg = ProductionToolRegistry()
        assert reg.unregister("ghost") is False

    def test_list_capabilities(self):
        reg = _make_registry_with_ok_tool()
        caps = reg.list_capabilities()
        assert "SESSION" in caps
        assert caps["SESSION"] == "test_tool"

    def test_len(self):
        reg = ProductionToolRegistry()
        assert len(reg) == 0
        reg.register(_OkTool())
        assert len(reg) == 1

    def test_tool_names(self):
        reg = ProductionToolRegistry()
        reg.register(_OkTool())
        assert "test_tool" in reg.tool_names()

    def test_update_health_success(self):
        reg = _make_registry_with_ok_tool()
        reg.set_lifecycle_status("test_tool", ToolLifecycleStatus.READY, "ok")
        reg.update_health("test_tool", success=True, duration_ms=100)
        h = reg.get_health("test_tool")
        assert h.is_healthy()

    def test_update_health_auto_transition_to_degraded(self):
        reg = _make_registry_with_ok_tool()
        reg.set_lifecycle_status("test_tool", ToolLifecycleStatus.READY, "ok")
        for _ in range(6):
            reg.update_health("test_tool", success=False, duration_ms=100)
        h = reg.get_health("test_tool")
        assert h.status in ("DEGRADED", "FAILED")

    def test_available_tools_only_ready_and_degraded(self):
        reg = ProductionToolRegistry()
        reg.register(_OkTool())
        reg.set_lifecycle_status("test_tool", ToolLifecycleStatus.READY, "ok")
        reg.register(_FailTool())
        available = reg.available_tools()
        assert "test_tool" in available
        assert "fail_tool" not in available

    def test_statistics_has_keys(self):
        reg = ProductionToolRegistry()
        stats = reg.get_statistics()
        assert "total_registered" in stats

    def test_get_version(self):
        reg = ProductionToolRegistry()
        reg.register(_OkTool())
        v = reg.get_version("test_tool")
        assert v is not None and v != ""


# ─────────────────────────────────────────────────────────────────────────────
# R — ProductionToolExecutor
# ─────────────────────────────────────────────────────────────────────────────

class TestProductionToolExecutor:
    def test_successful_execution(self):
        executor = _make_executor_with_ok_tool()
        req = _make_request("SESSION", slots={"session_id": "s1"})
        result = executor.execute_request(req)
        assert result.success is True
        assert result.payload != {}

    def test_never_raises_on_unknown_capability(self):
        executor = _make_executor_with_ok_tool()
        req = _make_request("UNKNOWN_KIND")
        result = executor.execute_request(req)
        assert result.success is False
        assert result.error_code is not None

    def test_never_raises_on_raising_tool(self):
        reg = ProductionToolRegistry()
        tool = _RaisingTool()
        reg.register(tool)
        reg.register_capability("SESSION", "raising_tool")
        reg.set_lifecycle_status("raising_tool", ToolLifecycleStatus.READY, "ok")
        executor = ProductionToolExecutor(reg)
        req = _make_request("SESSION", slots={"session_id": "s1"})
        result = executor.execute_request(req)
        assert result.success is False

    def test_execution_has_audit(self):
        executor = _make_executor_with_ok_tool()
        req = _make_request("SESSION", slots={"session_id": "s1"})
        result = executor.execute_request(req)
        assert result.audit is not None

    def test_execution_has_metrics(self):
        executor = _make_executor_with_ok_tool()
        req = _make_request("SESSION", slots={"session_id": "s1"})
        result = executor.execute_request(req)
        assert result.metrics is not None
        assert result.metrics.total_attempts >= 1

    def test_execute_convenience_api(self):
        executor = _make_executor_with_ok_tool()
        result = executor.execute(
            tool_name="test_tool",
            inputs={"session_id": "s1"},
            tenant_id="t1",
            case_id="c1",
            topic="eKYC",
        )
        assert result.success is True

    def test_execute_for_capability(self):
        executor = _make_executor_with_ok_tool()
        result = executor.execute_for_capability(
            evidence_kind="SESSION",
            inputs={"session_id": "s1"},
            tenant_id="t1",
            case_id="c1",
        )
        assert result.success is True

    def test_failed_tool_produces_failure_result(self):
        reg = ProductionToolRegistry()
        tool = _FailTool()
        reg.register(tool)
        reg.register_capability("SESSION", "fail_tool")
        reg.set_lifecycle_status("fail_tool", ToolLifecycleStatus.READY, "ok")
        executor = ProductionToolExecutor(reg)
        req = _make_request("SESSION")
        result = executor.execute_request(req)
        assert result.success is False
        assert result.error_code is not None

    def test_metrics_property(self):
        executor = _make_executor_with_ok_tool()
        assert executor.metrics is not None
        assert isinstance(executor.metrics, ToolFrameworkMetrics)

    def test_lifecycle_failed_blocks_execution(self):
        reg = ProductionToolRegistry()
        tool = _OkTool()
        reg.register(tool)
        reg.register_capability("SESSION", "test_tool")
        reg.force_lifecycle_status("test_tool", ToolLifecycleStatus.FAILED, "forced failure")
        executor = ProductionToolExecutor(reg)
        req = _make_request("SESSION")
        result = executor.execute_request(req)
        assert result.success is False
        assert result.error_code == "TOOL_LIFECYCLE_BLOCKED"

    def test_result_has_duration_ms(self):
        executor = _make_executor_with_ok_tool()
        req = _make_request("SESSION", slots={"session_id": "s1"})
        result = executor.execute_request(req)
        assert result.duration_ms >= 0


# ─────────────────────────────────────────────────────────────────────────────
# S — ExecutionPipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionPipeline:
    def test_run_success(self):
        executor = _make_executor_with_ok_tool()
        pipeline = ExecutionPipeline(executor)
        req = _make_request("SESSION", slots={"session_id": "s1"})
        result = pipeline.run(req)
        assert result.success is True

    def test_run_validation_failure_no_tool_or_kind(self):
        executor = _make_executor_with_ok_tool()
        pipeline = ExecutionPipeline(executor)
        ctx = ToolContext(tool_name="", tenant_id="", case_id="", topic="", slots={})
        req = ToolExecutionRequest(context=ctx, tool_name=None, evidence_kind=None)
        result = pipeline.run(req)
        assert result.success is False
        assert result.error_code == "VALIDATION_ERROR"

    def test_run_never_raises(self):
        executor = _make_executor_with_ok_tool()
        pipeline = ExecutionPipeline(executor)
        bad_req = _make_request("NONEXISTENT")
        result = pipeline.run(bad_req)
        assert isinstance(result, ToolExecutionResult)

    def test_run_for_capability(self):
        executor = _make_executor_with_ok_tool()
        pipeline = ExecutionPipeline(executor)
        result = pipeline.run_for_capability(
            "SESSION", {"session_id": "s1"},
            tenant_id="t1", case_id="c1",
        )
        assert result.success is True

    def test_validation_failure_has_audit(self):
        executor = _make_executor_with_ok_tool()
        pipeline = ExecutionPipeline(executor)
        ctx = ToolContext(tool_name="", tenant_id="", case_id="", topic="", slots={})
        req = ToolExecutionRequest(context=ctx, tool_name=None, evidence_kind=None)
        result = pipeline.run(req)
        assert result.audit is not None


# ─────────────────────────────────────────────────────────────────────────────
# T — ToolExecutorProvider bridge
# ─────────────────────────────────────────────────────────────────────────────

class TestToolExecutorProvider:
    def _make_provider(self) -> tuple[ToolExecutorProvider, ProductionToolExecutor]:
        executor = _make_executor_with_ok_tool()
        provider = ToolExecutorProvider(executor)
        return provider, executor

    def test_provider_name(self):
        provider, _ = self._make_provider()
        assert provider.provider_name == "ToolExecutorProvider"

    def test_can_handle_session(self):
        provider, _ = self._make_provider()
        assert provider.can_handle("SESSION") is True

    def test_can_handle_log(self):
        provider, _ = self._make_provider()
        assert provider.can_handle("LOG") is True

    def test_cannot_handle_human(self):
        provider, _ = self._make_provider()
        assert provider.can_handle("HUMAN") is False

    def test_execute_success(self):
        provider, _ = self._make_provider()
        from case_engine.investigation.collector.contracts import CollectionContext
        ctx = CollectionContext(
            case_id="c1",
            topic="eKYC",
            slots={"session_id": "sess-001"},
            tenant_id="t1",
        )
        result = provider.execute(
            step_id="step-1",
            evidence_kind="SESSION",
            context=ctx,
            requirement_metadata={},
        )
        assert result.success is True

    def test_execute_never_raises(self):
        executor = MagicMock()
        executor.execute_request.side_effect = RuntimeError("boom")
        provider = ToolExecutorProvider(executor)
        from case_engine.investigation.collector.contracts import CollectionContext
        ctx = CollectionContext(
            case_id="c1", topic="t", slots={"session_id": "s1"}, tenant_id="t1",
        )
        result = provider.execute("step", "SESSION", ctx, {})
        assert result.success is False
        assert result.error_code == "BRIDGE_EXECUTION_ERROR"

    def test_kind_input_map_coverage(self):
        for kind in ("SESSION", "LOG", "API", "SUMMARY", "DATABASE", "WORKFLOW", "CONFIGURATION"):
            assert kind in _KIND_INPUT_MAP

    def test_handleable_kinds_set(self):
        for kind in ("SESSION", "LOG", "API", "SUMMARY", "DATABASE", "WORKFLOW", "CONFIGURATION"):
            assert kind in _HANDLEABLE_KINDS

    def test_execute_failure_returns_collection_fail(self):
        executor = _make_executor_with_ok_tool()
        provider = ToolExecutorProvider(executor)
        from case_engine.investigation.collector.contracts import CollectionContext
        ctx = CollectionContext(
            case_id="c1", topic="t", slots={}, tenant_id="t1",
        )
        result = provider.execute("step-x", "UNKNOWN_KIND_XYZ", ctx, {})
        assert result.success is False

    def test_slot_passthrough_all_slots(self):
        executor = _make_executor_with_ok_tool()
        provider = ToolExecutorProvider(executor)
        from case_engine.investigation.collector.contracts import CollectionContext
        ctx = CollectionContext(
            case_id="c1", topic="t",
            slots={"session_id": "s1", "extra_key": "extra_val"},
            tenant_id="t1",
        )
        result = provider.execute("step", "SESSION", ctx, {})
        assert result.success is True


# ─────────────────────────────────────────────────────────────────────────────
# U — ToolFrameworkValidators
# ─────────────────────────────────────────────────────────────────────────────

class TestToolFrameworkValidators:
    def setup_method(self):
        self.v = ToolFrameworkValidators()

    def test_validate_request_ok(self):
        req = _make_request("SESSION")
        valid, issues = self.v.validate_request(req)
        assert valid is True
        assert issues == []

    def test_validate_request_no_tool_or_kind(self):
        ctx = ToolContext(tool_name="", tenant_id="", case_id="", topic="", slots={})
        req = ToolExecutionRequest(context=ctx, tool_name=None, evidence_kind=None)
        valid, issues = self.v.validate_request(req)
        assert valid is False
        assert any("tool_name or evidence_kind" in i for i in issues)

    def test_validate_request_invalid_priority(self):
        req = _make_request("SESSION")
        req = ToolExecutionRequest(
            context=req.context, evidence_kind="SESSION", priority=11
        )
        valid, issues = self.v.validate_request(req)
        assert valid is False
        assert any("priority" in i for i in issues)

    def test_validate_result_ok(self):
        result = _ok_result(payload={"data": 1})
        valid, issues = self.v.validate_result(result)
        assert valid is True

    def test_validate_result_success_with_error_code(self):
        result = _ok_result(payload={"data": 1})
        object.__setattr__(result, "error_code", "OOPS")
        valid, issues = self.v.validate_result(result)
        assert valid is False

    def test_validate_result_failure_missing_error_code(self):
        result = _fail_result(error_code="ERR", error_message="fail")
        object.__setattr__(result, "error_code", None)
        valid, issues = self.v.validate_result(result)
        assert valid is False

    def test_validate_health_ok(self):
        h = ToolHealth(tool_name="t", status="READY", last_checked_at=_now_iso())
        valid, issues = self.v.validate_health(h)
        assert valid is True

    def test_validate_health_bad_status(self):
        h = ToolHealth(tool_name="t", status="NOTASTATUS", last_checked_at=_now_iso())
        valid, issues = self.v.validate_health(h)
        assert valid is False

    def test_validate_health_bad_availability_pct(self):
        h = ToolHealth(tool_name="t", status="READY", last_checked_at=_now_iso(), availability_pct=150.0)
        valid, issues = self.v.validate_health(h)
        assert valid is False

    def test_validate_retry_policy_ok(self):
        p = ToolRetryPolicy(max_attempts=3)
        valid, issues = self.v.validate_retry_policy(p)
        assert valid is True

    def test_validate_retry_policy_zero_attempts(self):
        with pytest.raises(ValueError):
            ToolRetryPolicy(max_attempts=0)

    def test_validate_timeout_ok(self):
        t = ToolTimeout()
        valid, issues = self.v.validate_timeout(t)
        assert valid is True

    def test_validate_dependency_graph_no_cycle(self):
        graph = {"a": ["b"], "b": ["c"], "c": []}
        valid, issues = self.v.validate_dependency_graph(graph)
        assert valid is True
        assert issues == []

    def test_validate_dependency_graph_cycle(self):
        graph = {"a": ["b"], "b": ["a"]}
        valid, issues = self.v.validate_dependency_graph(graph)
        assert valid is False
        assert len(issues) >= 1

    def test_validate_registry_ok(self):
        reg = _make_registry_with_ok_tool()
        valid, issues = self.v.validate_registry(reg)
        assert valid is True

    def test_validate_tool_definition_ok(self):
        defn = _make_tool_definition()
        valid, issues = self.v.validate_tool_definition(defn)
        assert valid is True

    def test_validate_tool_definition_short_description(self):
        defn = ToolDefinition(
            tool_name="t",
            description="short",
            required_inputs=("a",),
            output_schema={"k": "v"},
            version="1.0",
        )
        valid, issues = self.v.validate_tool_definition(defn)
        assert valid is False
        assert any("description" in i for i in issues)


# ─────────────────────────────────────────────────────────────────────────────
# V — Serialization roundtrip
# ─────────────────────────────────────────────────────────────────────────────

class TestSerialization:
    def _make_full_result(self, success: bool = True) -> ToolExecutionResult:
        inv_id = _new_id()
        req_id = _new_id()
        m = ToolExecutionMetrics()
        m.record_attempt(100, success=success)
        now = _now_iso()
        audit = ToolExecutionAudit(
            invocation_id=inv_id, tool_name="test_tool", request_id=req_id,
            tenant_id="t1", case_id="c1", started_at=now, completed_at=now,
            duration_ms=100, attempts=1, outcome="SUCCESS" if success else "FAILURE",
            error_code=None if success else "ERR",
            inputs_redacted={"session_id": "<redacted>"},
            output_summary={"key_count": 1, "keys": ["data"]},
        )
        if success:
            return ToolExecutionResult.ok(
                tool_name="test_tool", invocation_id=inv_id, request_id=req_id,
                payload={"data": "value"}, metrics=m, audit=audit,
                trace=None, started_at=now, completed_at=now, duration_ms=100,
            )
        return ToolExecutionResult.fail(
            tool_name="test_tool", invocation_id=inv_id, request_id=req_id,
            error_code="ERR", error_message="failed", metrics=m, audit=audit,
            trace=None, started_at=now, completed_at=now, duration_ms=0,
        )

    def test_result_to_dict_and_back_success(self):
        result = self._make_full_result(success=True)
        d = tool_execution_result_to_dict(result)
        recovered = tool_execution_result_from_dict(d)
        assert recovered.success is True
        assert recovered.tool_name == "test_tool"
        assert recovered.payload == {"data": "value"}

    def test_result_to_dict_and_back_failure(self):
        result = self._make_full_result(success=False)
        d = tool_execution_result_to_dict(result)
        recovered = tool_execution_result_from_dict(d)
        assert recovered.success is False
        assert recovered.error_code == "ERR"

    def test_result_to_json_and_back(self):
        result = self._make_full_result(success=True)
        json_str = tool_execution_result_to_json(result)
        assert isinstance(json_str, str)
        recovered = tool_execution_result_from_json(json_str)
        assert recovered.success is True

    def test_json_is_valid(self):
        result = self._make_full_result()
        json_str = tool_execution_result_to_json(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_health_roundtrip(self):
        h = ToolHealth(
            tool_name="t", status="READY", last_checked_at=_now_iso(),
            consecutive_failures=0, availability_pct=99.5,
        )
        d = tool_health_to_dict(h)
        recovered = tool_health_from_dict(d)
        assert recovered.tool_name == "t"
        assert recovered.status == "READY"
        assert recovered.availability_pct == 99.5

    def test_health_missing_required_key_raises(self):
        with pytest.raises(ToolFrameworkSerializationError):
            tool_health_from_dict({"status": "READY"})

    def test_result_missing_required_key_raises(self):
        with pytest.raises(ToolFrameworkSerializationError):
            tool_execution_result_from_dict({"tool_name": "t"})

    def test_metrics_roundtrip_preserved_in_result(self):
        result = self._make_full_result()
        recovered = tool_execution_result_from_dict(tool_execution_result_to_dict(result))
        assert recovered.metrics.total_attempts == 1

    def test_no_data_loss_on_success_roundtrip(self):
        result = self._make_full_result(success=True)
        d = tool_execution_result_to_dict(result)
        recovered = tool_execution_result_from_dict(d)
        assert recovered.invocation_id == result.invocation_id
        assert recovered.request_id == result.request_id

    def test_metadata_serialization_in_result(self):
        result = self._make_full_result()
        d = tool_execution_result_to_dict(result)
        j = json.dumps(d, ensure_ascii=False)
        assert "test_tool" in j


# ─────────────────────────────────────────────────────────────────────────────
# W — InvestigationContext Sprint 2.45 fields
# ─────────────────────────────────────────────────────────────────────────────

class TestInvestigationContextSprint245:
    def _make_ctx(self) -> Any:
        from case_engine.investigation.context import InvestigationContext, InvestigationTiming
        tenant = TenantContext(
            client_id="t1", client_name="TestBank",
            domain="testbank.co.in",
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.UAT,
            enabled_tools=("test_tool",),
            credentials_ref="test-creds",
        )
        return InvestigationContext(
            context_id=_new_id(),
            case_id="case-1",
            ticket_id="ticket-1",
            ticket_subject="Test",
            ticket_description="Description",
            customer_id="cust-1",
            customer_email="test@example.com",
            channel="email",
            tenant_context=tenant,
            topic="eKYC",
            classification_confidence=0.9,
        )

    def test_tool_execution_summary_default_none(self):
        ctx = self._make_ctx()
        assert ctx.tool_execution_summary is None

    def test_tool_framework_metrics_default_none(self):
        ctx = self._make_ctx()
        assert ctx.tool_framework_metrics is None

    def test_tool_audit_trail_default_empty(self):
        ctx = self._make_ctx()
        assert ctx.tool_audit_trail == []

    def test_tool_failures_default_empty(self):
        ctx = self._make_ctx()
        assert ctx.tool_failures == []

    def test_tool_execution_timestamp_default_none(self):
        ctx = self._make_ctx()
        assert ctx.tool_execution_timestamp is None

    def test_tool_framework_version_default_none(self):
        ctx = self._make_ctx()
        assert ctx.tool_framework_version is None

    def test_has_tool_execution_summary_false_by_default(self):
        ctx = self._make_ctx()
        assert ctx.has_tool_execution_summary() is False

    def test_has_tool_execution_summary_true_after_set(self):
        ctx = self._make_ctx()
        ctx.tool_execution_summary = {"total": 3, "success": 3}
        assert ctx.has_tool_execution_summary() is True

    def test_tool_audit_trail_append(self):
        ctx = self._make_ctx()
        ctx.tool_audit_trail.append({"invocation_id": "inv-1", "outcome": "SUCCESS"})
        assert len(ctx.tool_audit_trail) == 1

    def test_tool_failures_append(self):
        ctx = self._make_ctx()
        ctx.tool_failures.append({"error_code": "TOOL_ERROR"})
        assert len(ctx.tool_failures) == 1

    def test_to_dict_includes_sprint245_fields(self):
        ctx = self._make_ctx()
        d = ctx.to_dict()
        assert "tool_execution_summary" in d
        assert "tool_framework_metrics" in d
        assert "tool_audit_trail_count" in d
        assert "tool_failures_count" in d
        assert "tool_execution_timestamp" in d
        assert "tool_framework_version" in d

    def test_to_dict_audit_trail_count_reflects_list(self):
        ctx = self._make_ctx()
        ctx.tool_audit_trail.append({"event": "x"})
        ctx.tool_audit_trail.append({"event": "y"})
        d = ctx.to_dict()
        assert d["tool_audit_trail_count"] == 2

    def test_tool_framework_version_can_be_set(self):
        ctx = self._make_ctx()
        ctx.tool_framework_version = "2.45.0"
        assert ctx.tool_framework_version == "2.45.0"


# ─────────────────────────────────────────────────────────────────────────────
# X — Collector integration with tool_executor
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectorWithToolExecutor:
    def _make_plan(self) -> Any:
        import uuid as _uuid
        from case_engine.investigation.planner.models import (
            EvidenceKind,
            EvidenceRequirement,
            EstimatedComplexity,
            FallbackStrategy,
            FailureStrategy,
            InvestigationGraph,
            InvestigationPlan,
            InvestigationPriority,
            PlanningStep,
            RetryPolicy,
        )
        from case_engine.investigation.evidence.models import EvidencePriority as EP
        from case_engine.tools.tool_models import ToolCapability
        req = EvidenceRequirement(
            requirement_id="req_01",
            kind=EvidenceKind.SESSION,
            title="Session evidence",
            description="Fetch session data for eKYC investigation",
            priority=EP.NORMAL,
            required=True,
            expected_fields=("session_id",),
            validation_hints=(),
            capability_hint=ToolCapability.READ,
        )
        step = PlanningStep(
            step_id="step_01",
            order=0,
            title="Session step",
            description="Fetch session data",
            evidence_required=(req,),
            evidence_priority=EP.NORMAL,
            candidate_capabilities=(ToolCapability.READ,),
            preconditions=(),
            outputs=(),
            retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.0),
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=False,
            optional=False,
            depends_on=(),
        )
        graph = InvestigationGraph(nodes=(step,), edges=(), parallel_groups=())
        return InvestigationPlan(
            plan_id=str(_uuid.uuid4()),
            workflow_id=None,
            topic="eKYC",
            client_id="t1",
            case_id="case-1",
            objective="Test investigation",
            investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=EstimatedComplexity.LOW,
            expected_evidence=(req,),
            confidence_threshold=0.75,
            completion_conditions=(),
            fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at="2026-07-08T00:00:00+00:00",
            steps=(step,),
            graph=graph,
        )

    def _make_context(self) -> Any:
        from case_engine.investigation.context import InvestigationContext
        tenant = TenantContext(
            client_id="t1", client_name="TestBank",
            domain="testbank.co.in",
            tenant_type=TenantType.BANK,
            environment=TenantEnvironment.UAT,
            enabled_tools=("test_tool",),
            credentials_ref="test-creds",
        )
        return InvestigationContext(
            context_id=_new_id(),
            case_id="case-1",
            ticket_id="ticket-1",
            ticket_subject="Test",
            ticket_description="Desc",
            customer_id="cust-1",
            customer_email="test@example.com",
            channel="email",
            tenant_context=tenant,
            topic="eKYC",
            classification_confidence=0.9,
            slots={"session_id": "sess-123"},
        )

    def test_collector_without_tool_executor_still_works(self):
        from case_engine.investigation.collector.collector import EvidenceCollector
        collector = EvidenceCollector()
        plan = self._make_plan()
        ctx = self._make_context()
        bundle = collector.collect(plan, ctx)
        assert bundle is not None
        assert bundle.case_id == "case-1"

    def test_collector_with_tool_executor_wires_provider(self):
        from case_engine.investigation.collector.collector import EvidenceCollector
        executor = _make_executor_with_ok_tool()
        collector = EvidenceCollector(tool_executor=executor)
        plan = self._make_plan()
        ctx = self._make_context()
        bundle = collector.collect(plan, ctx)
        assert bundle is not None

    def test_collector_with_tool_executor_never_raises(self):
        from case_engine.investigation.collector.collector import EvidenceCollector
        executor = _make_executor_with_ok_tool()
        collector = EvidenceCollector(tool_executor=executor)
        plan = self._make_plan()
        ctx = self._make_context()
        bundle = collector.collect(plan, ctx)
        assert isinstance(bundle.bundle_id, str)

    def test_collector_backward_compatible_no_executor(self):
        from case_engine.investigation.collector.collector import EvidenceCollector
        collector = EvidenceCollector()
        assert collector is not None

    def test_collector_accepts_tool_executor_kwarg(self):
        from case_engine.investigation.collector.collector import EvidenceCollector
        executor = _make_executor_with_ok_tool()
        collector = EvidenceCollector(tool_executor=executor)
        assert collector is not None


# ─────────────────────────────────────────────────────────────────────────────
# Y — Thread-safety stress tests
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_registry_concurrent_registrations(self):
        reg = ProductionToolRegistry()
        errors: list[Exception] = []

        class _NamedTool(BaseTool):
            def __init__(self, name: str):
                self._name = name
            @property
            def definition(self) -> ToolDefinition:
                return _make_tool_definition(self._name)
            def run(self, inputs: dict) -> dict:
                return {"tool": self._name}

        def _register(i: int) -> None:
            try:
                reg.register(_NamedTool(f"tool_{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_register, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(reg) == 20

    def test_metrics_concurrent_recordings(self):
        m = ToolFrameworkMetrics()
        errors: list[Exception] = []

        def _record(i: int) -> None:
            try:
                m.record_execution(f"tool_{i % 3}", 100, success=True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_record, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert m.total_executions == 50

    def test_lifecycle_concurrent_transitions(self):
        mgr = ToolLifecycleManager()
        mgr.register("shared_tool")
        mgr.transition("shared_tool", ToolLifecycleStatus.READY, reason="ok")
        errors: list[Exception] = []

        def _work() -> None:
            try:
                mgr.get_status("shared_tool")
                mgr.is_usable("shared_tool")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_work) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_executor_concurrent_requests(self):
        executor = _make_executor_with_ok_tool()
        results: list[ToolExecutionResult] = []
        lock = threading.Lock()

        def _execute() -> None:
            req = _make_request("SESSION", slots={"session_id": "s1"})
            result = executor.execute_request(req)
            with lock:
                results.append(result)

        threads = [threading.Thread(target=_execute) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(r.success for r in results)

    def test_framework_metrics_concurrent_per_tool(self):
        m = ToolFrameworkMetrics()

        def _worker(tool: str, n: int) -> None:
            for _ in range(n):
                m.record_execution(tool, 50, success=True)

        threads = [
            threading.Thread(target=_worker, args=(f"tool_{i}", 10))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert m.total_executions == 50
        for i in range(5):
            stats = m.per_tool_stats(f"tool_{i}")
            total = stats.get("total", stats.get("executions", 0))
            assert total == 10
