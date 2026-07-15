"""
case_engine/tools/framework/serialization.py

Sprint 2.45: JSON roundtrip serialization for Tool Framework models.

Provides:
  tool_execution_result_to_dict(result) → dict
  tool_execution_result_to_json(result) → str
  tool_execution_result_from_dict(d)    → ToolExecutionResult
  tool_execution_result_from_json(s)    → ToolExecutionResult

  tool_metadata_to_dict(metadata) → dict
  tool_metadata_from_dict(d)      → ToolMetadata

  tool_health_to_dict(health) → dict
  tool_health_from_dict(d)    → ToolHealth

No data loss on roundtrip.

Dependency direction:
  serialization.py → framework/models.py
  serialization.py → stdlib (json) only
"""
from __future__ import annotations

import json
from typing import Any

from case_engine.tools.framework.models import (
    ToolAuthenticationRequirement,
    ToolAvailability,
    ToolDependency,
    ToolExecutionAudit,
    ToolExecutionMetrics,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionTrace,
    ToolHealth,
    ToolMetadata,
    ToolPermissionRequirement,
    ToolRetryPolicy,
    ToolScope,
    ToolTimeout,
    _now_iso,
    _new_id,
)


class ToolFrameworkSerializationError(Exception):
    """Raised when a framework model cannot be deserialized."""
    def __init__(self, model: str, reason: str) -> None:
        self.model  = model
        self.reason = reason
        super().__init__(f"{model} deserialization error: {reason}")


def _req(d: dict[str, Any], key: str, model: str) -> Any:
    if key not in d:
        raise ToolFrameworkSerializationError(model, f"missing required key: {key!r}")
    return d[key]


# ── ToolExecutionMetrics ───────────────────────────────────────────────────────

def tool_metrics_to_dict(m: ToolExecutionMetrics) -> dict[str, Any]:
    return m.to_dict()


def tool_metrics_from_dict(d: dict[str, Any]) -> ToolExecutionMetrics:
    m = ToolExecutionMetrics(
        total_attempts=      int(d.get("total_attempts", 0)),
        successful_attempts= int(d.get("successful_attempts", 0)),
        failed_attempts=     int(d.get("failed_attempts", 0)),
        timeout_attempts=    int(d.get("timeout_attempts", 0)),
        total_duration_ms=   int(d.get("total_duration_ms", 0)),
        min_attempt_ms=      int(d.get("min_attempt_ms", 0)),
        max_attempt_ms=      int(d.get("max_attempt_ms", 0)),
    )
    return m


# ── ToolExecutionAudit ─────────────────────────────────────────────────────────

def tool_audit_to_dict(a: ToolExecutionAudit) -> dict[str, Any]:
    return a.to_dict()


def tool_audit_from_dict(d: dict[str, Any]) -> ToolExecutionAudit:
    model = "ToolExecutionAudit"
    return ToolExecutionAudit(
        invocation_id=   _req(d, "invocation_id", model),
        tool_name=       _req(d, "tool_name", model),
        request_id=      _req(d, "request_id", model),
        tenant_id=       d.get("tenant_id", ""),
        case_id=         d.get("case_id", ""),
        started_at=      _req(d, "started_at", model),
        completed_at=    _req(d, "completed_at", model),
        duration_ms=     int(d.get("duration_ms", 0)),
        attempts=        int(d.get("attempts", 0)),
        outcome=         d.get("outcome", "FAILURE"),
        error_code=      d.get("error_code"),
        inputs_redacted= d.get("inputs_redacted", {}),
        output_summary=  d.get("output_summary", {}),
    )


# ── ToolExecutionTrace ────────────────────────────────────────────────────────

def tool_trace_to_dict(t: ToolExecutionTrace) -> dict[str, Any]:
    return t.to_dict()


def tool_trace_from_dict(d: dict[str, Any]) -> ToolExecutionTrace:
    model = "ToolExecutionTrace"
    metrics_d = d.get("metrics", {})
    return ToolExecutionTrace(
        request_id=        _req(d, "request_id", model),
        tool_name=         _req(d, "tool_name", model),
        attempts=          d.get("attempts", []),
        final_outcome=     d.get("final_outcome", "FAILURE"),
        total_duration_ms= int(d.get("total_duration_ms", 0)),
        metrics=           tool_metrics_from_dict(metrics_d),
    )


# ── ToolExecutionResult ───────────────────────────────────────────────────────

def tool_execution_result_to_dict(r: ToolExecutionResult) -> dict[str, Any]:
    """Full serialization including payload (for roundtrip). Use r.to_dict() for logging."""
    d = r.to_dict()
    d["payload"] = r.payload  # include full payload for roundtrip
    return d


def tool_execution_result_to_json(r: ToolExecutionResult) -> str:
    return json.dumps(tool_execution_result_to_dict(r), ensure_ascii=False)


def tool_execution_result_from_dict(d: dict[str, Any]) -> ToolExecutionResult:
    model = "ToolExecutionResult"
    metrics_d = d.get("metrics", {})
    audit_d   = d.get("audit")
    trace_d   = d.get("trace")
    audit = tool_audit_from_dict(audit_d) if audit_d else ToolExecutionAudit(
        invocation_id=_new_id(), tool_name="<unknown>", request_id="<unknown>",
        tenant_id="", case_id="", started_at=_now_iso(), completed_at=_now_iso(),
        duration_ms=0, attempts=0, outcome="FAILURE", error_code=None,
        inputs_redacted={}, output_summary={},
    )
    trace = tool_trace_from_dict(trace_d) if trace_d else None
    return ToolExecutionResult(
        success=        bool(_req(d, "success", model)),
        tool_name=      _req(d, "tool_name", model),
        invocation_id=  _req(d, "invocation_id", model),
        request_id=     _req(d, "request_id", model),
        payload=        d.get("payload", {}),
        error_code=     d.get("error_code"),
        error_message=  d.get("error_message"),
        metrics=        tool_metrics_from_dict(metrics_d),
        audit=          audit,
        trace=          trace,
        started_at=     d.get("started_at", _now_iso()),
        completed_at=   d.get("completed_at", _now_iso()),
        duration_ms=    int(d.get("duration_ms", 0)),
    )


def tool_execution_result_from_json(s: str) -> ToolExecutionResult:
    return tool_execution_result_from_dict(json.loads(s))


# ── ToolHealth ─────────────────────────────────────────────────────────────────

def tool_health_to_dict(h: ToolHealth) -> dict[str, Any]:
    return h.to_dict()


def tool_health_from_dict(d: dict[str, Any]) -> ToolHealth:
    model = "ToolHealth"
    return ToolHealth(
        tool_name=            _req(d, "tool_name", model),
        status=               _req(d, "status", model),
        last_checked_at=      _req(d, "last_checked_at", model),
        consecutive_failures= int(d.get("consecutive_failures", 0)),
        last_success_at=      d.get("last_success_at"),
        last_failure_at=      d.get("last_failure_at"),
        latency_p50_ms=       int(d.get("latency_p50_ms", 0)),
        latency_p99_ms=       int(d.get("latency_p99_ms", 0)),
        availability_pct=     float(d.get("availability_pct", 100.0)),
    )


# ── ToolMetadata ───────────────────────────────────────────────────────────────

def tool_metadata_to_dict(m: ToolMetadata) -> dict[str, Any]:
    return m.to_dict()


def tool_metadata_from_dict(d: dict[str, Any]) -> ToolMetadata:
    model = "ToolMetadata"
    scope_d  = d.get("scope", {"domain": ""})
    auth_d   = d.get("auth_requirement", {"auth_type": "NONE", "credential_ref": ""})
    perm_d   = d.get("permission_requirement", {})
    timeout_d = d.get("timeout", {})
    retry_d   = d.get("retry_policy", {})
    return ToolMetadata(
        tool_name=              _req(d, "tool_name", model),
        display_name=           _req(d, "display_name", model),
        description=            _req(d, "description", model),
        version=                _req(d, "version", model),
        provider=               _req(d, "provider", model),
        capability=             _req(d, "capability", model),
        scope=                  ToolScope.from_dict(scope_d),
        auth_requirement=       ToolAuthenticationRequirement(
            auth_type=      auth_d.get("auth_type", "NONE"),
            credential_ref= auth_d.get("credential_ref", ""),
            scopes=         tuple(auth_d.get("scopes", ())),
            optional=       bool(auth_d.get("optional", False)),
        ),
        permission_requirement= ToolPermissionRequirement(
            required_permissions= tuple(perm_d.get("required_permissions", ())),
            permission_level=     perm_d.get("permission_level", "READ"),
        ),
        timeout=   ToolTimeout.from_dict(timeout_d) if timeout_d else ToolTimeout(),
        retry_policy= ToolRetryPolicy.from_dict(retry_d) if retry_d else ToolRetryPolicy(),
        dependencies= tuple(
            ToolDependency(
                depends_on_tool= dep["depends_on_tool"],
                dependency_type= dep.get("dependency_type", "OPTIONAL"),
                reason=          dep.get("reason", ""),
            )
            for dep in d.get("dependencies", [])
        ),
        tags=               tuple(d.get("tags", ())),
        deprecated=         bool(d.get("deprecated", False)),
        deprecation_reason= d.get("deprecation_reason"),
        replacement_tool=   d.get("replacement_tool"),
    )
