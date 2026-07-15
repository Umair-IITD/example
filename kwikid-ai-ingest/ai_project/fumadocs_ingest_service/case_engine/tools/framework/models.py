"""
case_engine/tools/framework/models.py

Sprint 2.45: Tool Framework domain models.

Everything strongly typed. Everything immutable where appropriate.
Everything serializable. No placeholders.

Does NOT redefine ToolDefinition, ToolInput, ToolResult, ToolProvider,
ToolCapability (those live in case_engine/tools/tool_models.py Sprint 2.17).
Adds the richer production models required for the full execution pipeline.

Dependency direction:
  models.py → stdlib only
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Tool status ────────────────────────────────────────────────────────────────

class ToolStatus(str, Enum):
    """
    Externally visible status of a registered tool.
    Mirrors ToolLifecycleStatus values so callers can import from one place.
    """
    REGISTERED = "REGISTERED"
    READY      = "READY"
    DEGRADED   = "DEGRADED"
    FAILED     = "FAILED"
    DISABLED   = "DISABLED"
    OFFLINE    = "OFFLINE"
    UNKNOWN    = "UNKNOWN"


# ── Policies ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolTimeout:
    """Timeout policy for a tool invocation."""
    total_seconds:       float = 30.0   # Max wall time for the entire request
    per_attempt_seconds: float = 10.0   # Max wall time per single attempt
    grace_seconds:       float = 1.0    # Extra buffer before hard abort

    def __post_init__(self) -> None:
        if self.total_seconds <= 0:
            raise ValueError("total_seconds must be positive")
        if self.per_attempt_seconds <= 0:
            raise ValueError("per_attempt_seconds must be positive")
        if self.grace_seconds < 0:
            raise ValueError("grace_seconds must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_seconds":       self.total_seconds,
            "per_attempt_seconds": self.per_attempt_seconds,
            "grace_seconds":       self.grace_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolTimeout":
        return cls(
            total_seconds=float(d.get("total_seconds", 30.0)),
            per_attempt_seconds=float(d.get("per_attempt_seconds", 10.0)),
            grace_seconds=float(d.get("grace_seconds", 1.0)),
        )


@dataclass(frozen=True)
class ToolRetryPolicy:
    """Retry policy for a tool invocation."""
    max_attempts:          int           = 3
    backoff_seconds:       float         = 0.5
    retry_on_timeout:      bool          = True
    retry_on_error_codes:  tuple[str, ...] = ()
    jitter:                bool          = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")

    def should_retry(self, attempt: int, error_code: str | None, timed_out: bool) -> bool:
        """Return True if another attempt should be made."""
        if attempt >= self.max_attempts:
            return False
        if timed_out:
            return self.retry_on_timeout
        if error_code and self.retry_on_error_codes:
            return error_code in self.retry_on_error_codes
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts":         self.max_attempts,
            "backoff_seconds":      self.backoff_seconds,
            "retry_on_timeout":     self.retry_on_timeout,
            "retry_on_error_codes": list(self.retry_on_error_codes),
            "jitter":               self.jitter,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolRetryPolicy":
        return cls(
            max_attempts=int(d.get("max_attempts", 3)),
            backoff_seconds=float(d.get("backoff_seconds", 0.5)),
            retry_on_timeout=bool(d.get("retry_on_timeout", True)),
            retry_on_error_codes=tuple(d.get("retry_on_error_codes", ())),
            jitter=bool(d.get("jitter", False)),
        )


# ── Tool scope and dependency ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolScope:
    """
    Defines what a tool covers in the investigation domain.

    domain:         Primary investigation domain ("SESSION", "USER", "LOGS", …)
    evidence_kinds: EvidenceKind values this tool can satisfy
    topics:         Investigation topics this tool is relevant for
    tenant_types:   Tenant types that support this tool ("BANK", "NBFC", …)
    """
    domain:        str
    evidence_kinds: tuple[str, ...] = ()
    topics:         tuple[str, ...] = ()
    tenant_types:   tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain":        self.domain,
            "evidence_kinds": list(self.evidence_kinds),
            "topics":         list(self.topics),
            "tenant_types":   list(self.tenant_types),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolScope":
        return cls(
            domain=d["domain"],
            evidence_kinds=tuple(d.get("evidence_kinds", ())),
            topics=tuple(d.get("topics", ())),
            tenant_types=tuple(d.get("tenant_types", ())),
        )


@dataclass(frozen=True)
class ToolDependency:
    """Declares that one tool depends on another."""
    depends_on_tool: str
    dependency_type: str = "OPTIONAL"  # "REQUIRED" | "OPTIONAL" | "EXCLUSIVE"
    reason:          str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "depends_on_tool": self.depends_on_tool,
            "dependency_type": self.dependency_type,
            "reason":          self.reason,
        }


@dataclass(frozen=True)
class ToolAuthenticationRequirement:
    """Describes what authentication a tool needs at execution time."""
    auth_type:      str              # "API_KEY" | "BEARER_TOKEN" | "BASIC" | "NONE"
    credential_ref: str              # Reference key into credential store
    scopes:         tuple[str, ...] = ()
    optional:       bool            = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "auth_type":      self.auth_type,
            "credential_ref": self.credential_ref,
            "scopes":         list(self.scopes),
            "optional":       self.optional,
        }

    @classmethod
    def no_auth(cls) -> "ToolAuthenticationRequirement":
        return cls(auth_type="NONE", credential_ref="")


@dataclass(frozen=True)
class ToolPermissionRequirement:
    """Permission levels needed to invoke a tool."""
    required_permissions: tuple[str, ...] = ()
    permission_level:     str             = "READ"   # "READ"|"WRITE"|"EXECUTE"|"ADMIN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_permissions": list(self.required_permissions),
            "permission_level":     self.permission_level,
        }


# ── Rich tool metadata ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolMetadata:
    """
    Rich metadata for a production tool.

    Registered alongside the BaseTool implementation in ProductionToolRegistry.
    Describes capabilities, policies, auth requirements, and deprecation state.
    """
    tool_name:              str
    display_name:           str
    description:            str
    version:                str
    provider:               str  # ToolProvider value (e.g., "ADMIN_PORTAL")
    capability:             str  # ToolCapability value (e.g., "READ")
    scope:                  ToolScope
    auth_requirement:       ToolAuthenticationRequirement
    permission_requirement: ToolPermissionRequirement
    timeout:                ToolTimeout     = field(default_factory=ToolTimeout)
    retry_policy:           ToolRetryPolicy = field(default_factory=ToolRetryPolicy)
    dependencies:           tuple[ToolDependency, ...] = ()
    tags:                   tuple[str, ...]            = ()
    deprecated:             bool                       = False
    deprecation_reason:     str | None                 = None
    replacement_tool:       str | None                 = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":              self.tool_name,
            "display_name":           self.display_name,
            "description":            self.description,
            "version":                self.version,
            "provider":               self.provider,
            "capability":             self.capability,
            "scope":                  self.scope.to_dict(),
            "auth_requirement":       self.auth_requirement.to_dict(),
            "permission_requirement": self.permission_requirement.to_dict(),
            "timeout":                self.timeout.to_dict(),
            "retry_policy":           self.retry_policy.to_dict(),
            "dependencies":           [d.to_dict() for d in self.dependencies],
            "tags":                   list(self.tags),
            "deprecated":             self.deprecated,
            "deprecation_reason":     self.deprecation_reason,
            "replacement_tool":       self.replacement_tool,
        }


# ── Execution context ──────────────────────────────────────────────────────────

@dataclass
class ToolContext:
    """
    Lightweight context passed to tool adapters and the executor
    for each invocation. Carries identity and routing information.
    """
    tool_name:          str
    invocation_id:      str              = field(default_factory=_new_id)
    tenant_id:          str              = ""
    case_id:            str              = ""
    topic:              str              = ""
    slots:              dict[str, Any]   = field(default_factory=dict)
    auth_token:         str | None       = None
    execution_metadata: dict[str, Any]   = field(default_factory=dict)
    requested_at:       str              = field(default_factory=_now_iso)

    def get_slot(self, name: str, default: Any = None) -> Any:
        return self.slots.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":     self.tool_name,
            "invocation_id": self.invocation_id,
            "tenant_id":     self.tenant_id,
            "case_id":       self.case_id,
            "topic":         self.topic,
            "slot_keys":     list(self.slots.keys()),
            "requested_at":  self.requested_at,
        }


# ── Execution request ──────────────────────────────────────────────────────────

@dataclass
class ToolExecutionRequest:
    """
    What the Evidence Collector (or any caller) sends to the Tool Executor.

    Callers specify either tool_name (direct) or evidence_kind (capability-based).
    The executor resolves evidence_kind → tool_name via the registry capability map.

    Priority: 1 (highest urgency) to 10 (lowest).
    """
    context:       ToolContext
    tool_name:     str | None            = None
    evidence_kind: str | None            = None
    priority:      int                   = 5
    timeout:       ToolTimeout | None    = None
    retry_policy:  ToolRetryPolicy | None = None
    request_id:    str                   = field(default_factory=_new_id)
    requested_at:  str                   = field(default_factory=_now_iso)

    def resolved_tool_name(self) -> str | None:
        """Return the directly specified tool_name (may be None)."""
        return self.tool_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id":    self.request_id,
            "tool_name":     self.tool_name,
            "evidence_kind": self.evidence_kind,
            "priority":      self.priority,
            "requested_at":  self.requested_at,
            "context":       self.context.to_dict(),
        }


# ── Execution metrics ──────────────────────────────────────────────────────────

@dataclass
class ToolExecutionMetrics:
    """Per-execution metrics snapshot."""
    total_attempts:      int = 0
    successful_attempts: int = 0
    failed_attempts:     int = 0
    timeout_attempts:    int = 0
    total_duration_ms:   int = 0
    min_attempt_ms:      int = 0
    max_attempt_ms:      int = 0

    @property
    def average_attempt_ms(self) -> int:
        if self.total_attempts == 0:
            return 0
        return self.total_duration_ms // self.total_attempts

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.successful_attempts / self.total_attempts

    @property
    def timeout_occurred(self) -> bool:
        return self.timeout_attempts > 0

    def record_attempt(
        self,
        duration_ms: int,
        *,
        success: bool,
        timed_out: bool = False,
    ) -> None:
        self.total_attempts      += 1
        self.total_duration_ms   += duration_ms
        if success:
            self.successful_attempts += 1
        elif timed_out:
            self.timeout_attempts += 1
            self.failed_attempts  += 1
        else:
            self.failed_attempts  += 1
        if self.total_attempts == 1:
            self.min_attempt_ms = duration_ms
            self.max_attempt_ms = duration_ms
        else:
            self.min_attempt_ms = min(self.min_attempt_ms, duration_ms)
            self.max_attempt_ms = max(self.max_attempt_ms, duration_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_attempts":      self.total_attempts,
            "successful_attempts": self.successful_attempts,
            "failed_attempts":     self.failed_attempts,
            "timeout_attempts":    self.timeout_attempts,
            "total_duration_ms":   self.total_duration_ms,
            "min_attempt_ms":      self.min_attempt_ms,
            "max_attempt_ms":      self.max_attempt_ms,
            "average_attempt_ms":  self.average_attempt_ms,
            "success_rate":        self.success_rate,
            "timeout_occurred":    self.timeout_occurred,
        }


# ── Execution audit ────────────────────────────────────────────────────────────

@dataclass
class ToolExecutionAudit:
    """
    Immutable audit record for one tool execution.

    inputs_redacted: Only keys are logged — not raw values (PII protection).
    output_summary:  Counts and structure — not raw payload values.
    """
    invocation_id:   str
    tool_name:       str
    request_id:      str
    tenant_id:       str
    case_id:         str
    started_at:      str
    completed_at:    str
    duration_ms:     int
    attempts:        int
    outcome:         str              # "SUCCESS" | "FAILURE" | "TIMEOUT" | "SKIPPED"
    error_code:      str | None
    inputs_redacted: dict[str, Any]   # {"session_id": "<redacted>", …}
    output_summary:  dict[str, Any]   # {"key_count": 7, "keys": [...]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id":   self.invocation_id,
            "tool_name":       self.tool_name,
            "request_id":      self.request_id,
            "tenant_id":       self.tenant_id,
            "case_id":         self.case_id,
            "started_at":      self.started_at,
            "completed_at":    self.completed_at,
            "duration_ms":     self.duration_ms,
            "attempts":        self.attempts,
            "outcome":         self.outcome,
            "error_code":      self.error_code,
            "inputs_redacted": self.inputs_redacted,
            "output_summary":  self.output_summary,
        }

    @classmethod
    def _redact_inputs(cls, inputs: dict[str, Any]) -> dict[str, Any]:
        """Replace all values with '<redacted>' to avoid PII in audit logs."""
        return {k: "<redacted>" for k in inputs}

    @classmethod
    def _summarise_output(cls, payload: dict[str, Any]) -> dict[str, Any]:
        return {"key_count": len(payload), "keys": sorted(payload.keys())}


# ── Execution trace ────────────────────────────────────────────────────────────

@dataclass
class ToolExecutionTrace:
    """Full trace of one tool execution (all attempts)."""
    request_id:       str
    tool_name:        str
    attempts:         list[dict[str, Any]]  # per-attempt records
    final_outcome:    str
    total_duration_ms: int
    metrics:          ToolExecutionMetrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id":        self.request_id,
            "tool_name":         self.tool_name,
            "attempts":          self.attempts,
            "final_outcome":     self.final_outcome,
            "total_duration_ms": self.total_duration_ms,
            "metrics":           self.metrics.to_dict(),
        }


# ── Health and availability ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolHealth:
    """Point-in-time health snapshot for a registered tool."""
    tool_name:            str
    status:               str   # ToolStatus / ToolLifecycleStatus value
    last_checked_at:      str
    consecutive_failures: int   = 0
    last_success_at:      str | None = None
    last_failure_at:      str | None = None
    latency_p50_ms:       int   = 0
    latency_p99_ms:       int   = 0
    availability_pct:     float = 100.0

    def is_healthy(self) -> bool:
        return self.status in ("READY", "DEGRADED")

    def is_usable(self) -> bool:
        return self.status in ("READY", "DEGRADED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":            self.tool_name,
            "status":               self.status,
            "last_checked_at":      self.last_checked_at,
            "consecutive_failures": self.consecutive_failures,
            "last_success_at":      self.last_success_at,
            "last_failure_at":      self.last_failure_at,
            "latency_p50_ms":       self.latency_p50_ms,
            "latency_p99_ms":       self.latency_p99_ms,
            "availability_pct":     self.availability_pct,
        }


@dataclass(frozen=True)
class ToolAvailability:
    """Simple availability check result for a tool."""
    tool_name:  str
    available:  bool
    reason:     str      = ""
    checked_at: str      = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":  self.tool_name,
            "available":  self.available,
            "reason":     self.reason,
            "checked_at": self.checked_at,
        }


# ── Execution error ────────────────────────────────────────────────────────────

@dataclass
class ToolExecutionError:
    """Structured error from a single tool execution attempt."""
    error_code:    str
    error_message: str
    tool_name:     str
    invocation_id: str
    is_retryable:  bool = False
    is_timeout:    bool = False
    raised_at:     str  = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code":    self.error_code,
            "error_message": self.error_message,
            "tool_name":     self.tool_name,
            "invocation_id": self.invocation_id,
            "is_retryable":  self.is_retryable,
            "is_timeout":    self.is_timeout,
            "raised_at":     self.raised_at,
        }


# ── Execution result ───────────────────────────────────────────────────────────

@dataclass
class ToolExecutionResult:
    """
    Complete result of one tool execution request.

    Returned by ProductionToolExecutor.execute_request().
    Never None — the executor always returns a result, even on failure.
    """
    success:        bool
    tool_name:      str
    invocation_id:  str
    request_id:     str
    payload:        dict[str, Any]
    error_code:     str | None
    error_message:  str | None
    metrics:        ToolExecutionMetrics
    audit:          ToolExecutionAudit
    trace:          ToolExecutionTrace | None
    started_at:     str
    completed_at:   str
    duration_ms:    int

    @classmethod
    def ok(
        cls,
        tool_name:      str,
        invocation_id:  str,
        request_id:     str,
        payload:        dict[str, Any],
        metrics:        ToolExecutionMetrics,
        audit:          ToolExecutionAudit,
        trace:          ToolExecutionTrace | None,
        started_at:     str,
        completed_at:   str,
        duration_ms:    int,
    ) -> "ToolExecutionResult":
        return cls(
            success=True,
            tool_name=tool_name,
            invocation_id=invocation_id,
            request_id=request_id,
            payload=payload,
            error_code=None,
            error_message=None,
            metrics=metrics,
            audit=audit,
            trace=trace,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

    @classmethod
    def fail(
        cls,
        tool_name:     str,
        invocation_id: str,
        request_id:    str,
        error_code:    str,
        error_message: str,
        metrics:       ToolExecutionMetrics,
        audit:         ToolExecutionAudit,
        trace:         ToolExecutionTrace | None,
        started_at:    str,
        completed_at:  str,
        duration_ms:   int,
    ) -> "ToolExecutionResult":
        return cls(
            success=False,
            tool_name=tool_name,
            invocation_id=invocation_id,
            request_id=request_id,
            payload={},
            error_code=error_code,
            error_message=error_message,
            metrics=metrics,
            audit=audit,
            trace=trace,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success":       self.success,
            "tool_name":     self.tool_name,
            "invocation_id": self.invocation_id,
            "request_id":    self.request_id,
            "error_code":    self.error_code,
            "error_message": self.error_message,
            "started_at":    self.started_at,
            "completed_at":  self.completed_at,
            "duration_ms":   self.duration_ms,
            "payload_keys":  sorted(self.payload.keys()),
            "metrics":       self.metrics.to_dict(),
            "audit":         self.audit.to_dict(),
            "trace":         self.trace.to_dict() if self.trace else None,
        }
