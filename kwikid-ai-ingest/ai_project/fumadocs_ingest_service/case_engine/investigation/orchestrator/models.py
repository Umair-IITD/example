"""
case_engine/investigation/orchestrator/models.py

Sprint 2.46: Domain models for the Investigation Orchestrator.

Models:
  CancellationToken              — caller-signalled cancellation (thread-safe)
  InvestigationSession           — owns lifecycle, audit, metrics, errors (PART B)
  OrchestratorInvestigationResult — canonical typed output model (PART D)

Dependency direction:
  models.py → orchestrator/audit.py (OrchestratorAudit)
  models.py → orchestrator/metrics.py (OrchestratorMetrics)
  models.py → orchestrator/state_machine.py (OrchestratorLifecycleState, OrchestratorStateMachine)
  models.py → investigation/models.py (EvidenceBundle, InvestigationPlan) via TYPE_CHECKING
  models.py → investigation/root_cause/models.py (RootCauseAnalysis) via TYPE_CHECKING
  models.py → investigation/observation/models.py (Observation) via TYPE_CHECKING
  models.py → knowledge/base.py (KnowledgeEntry) via TYPE_CHECKING
  models.py → stdlib only for logic
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, TYPE_CHECKING

from case_engine.investigation.orchestrator.audit import OrchestratorAudit
from case_engine.investigation.orchestrator.metrics import OrchestratorMetrics
from case_engine.investigation.orchestrator.state_machine import (
    OrchestratorLifecycleState,
    OrchestratorStateMachine,
)

if TYPE_CHECKING:
    from case_engine.investigation.context import InvestigationContext
    from case_engine.investigation.models import EvidenceBundle
    from case_engine.investigation.observation.models import Observation
    from case_engine.investigation.planner.models import InvestigationPlan
    from case_engine.investigation.root_cause.models import RootCauseAnalysis
    from case_engine.knowledge.base import KnowledgeEntry


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── CancellationToken ──────────────────────────────────────────────────────────

class CancellationToken:
    """
    Thread-safe caller-signalled cancellation.

    Create one token per investigation, pass it to investigate().
    Call cancel() from any thread to signal that the investigation should stop.
    The pipeline checks this token between stages.
    """

    def __init__(self) -> None:
        self._lock:      Lock = Lock()
        self._cancelled: bool = False
        self._reason:    str  = ""

    def cancel(self, reason: str = "") -> None:
        with self._lock:
            if not self._cancelled:
                self._cancelled = True
                self._reason    = reason

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cancelled": self._cancelled,
                "reason":    self._reason,
            }


# ── InvestigationSession ───────────────────────────────────────────────────────

@dataclass
class InvestigationSession:
    """
    Owns the complete state of one investigation pipeline run.

    Created by InvestigationOrchestrator at the start of investigate().
    Passed through all pipeline stages for mutation.
    Not persisted directly — callers persist the OrchestratorInvestigationResult.

    Fields:
      session_id          — unique session identifier (UUID)
      case_id             — case being investigated
      started_at          — ISO 8601 session start timestamp
      context             — InvestigationContext (shared mutable accumulator)
      state_machine       — deterministic lifecycle state machine
      metrics             — per-session stage durations and counters
      audit               — deterministic audit timeline (PII-free)
      errors              — list of stage error records
      stage_transitions   — list of (from, to, timestamp) records
      timeout_at          — ISO 8601 hard deadline (None = no timeout)
      completed_at        — ISO 8601 completion timestamp
      cancelled_at        — ISO 8601 cancellation timestamp
      cancellation_reason — why the session was cancelled
    """
    session_id:          str
    case_id:             str
    started_at:          str
    context:             "InvestigationContext"
    state_machine:       OrchestratorStateMachine = field(
        default_factory=OrchestratorStateMachine
    )
    metrics:             OrchestratorMetrics      = field(
        default_factory=OrchestratorMetrics
    )
    audit:               OrchestratorAudit        = field(init=False)
    errors:              list[dict[str, Any]]     = field(default_factory=list)
    stage_transitions:   list[dict[str, Any]]     = field(default_factory=list)
    timeout_at:          str | None               = None
    completed_at:        str | None               = None
    cancelled_at:        str | None               = None
    cancellation_reason: str | None               = None

    def __post_init__(self) -> None:
        self.audit = OrchestratorAudit(
            session_id=self.session_id,
            case_id=self.case_id,
            started_at=self.started_at,
        )

    @classmethod
    def create(
        cls,
        context: "InvestigationContext",
        timeout_seconds: float | None = None,
    ) -> "InvestigationSession":
        """Factory: create a fresh session for this context."""
        now_str = _now_iso()
        sid     = _new_id()
        timeout_at = None
        if timeout_seconds is not None:
            now_dt     = datetime.fromisoformat(now_str)
            timeout_at = (now_dt + timedelta(seconds=timeout_seconds)).isoformat()
        return cls(
            session_id=sid,
            case_id=context.case_id,
            started_at=now_str,
            context=context,
            timeout_at=timeout_at,
        )

    @property
    def lifecycle_state(self) -> OrchestratorLifecycleState:
        return self.state_machine.state

    def is_timed_out(self) -> bool:
        """Return True if the session has exceeded its timeout."""
        if self.timeout_at is None:
            return False
        now     = datetime.now(tz=timezone.utc)
        timeout = datetime.fromisoformat(self.timeout_at)
        if timeout.tzinfo is None:
            timeout = timeout.replace(tzinfo=timezone.utc)
        return now >= timeout

    def add_error(self, stage: str, error_code: str, message: str) -> None:
        self.errors.append({
            "stage":      stage,
            "error_code": error_code,
            "message":    message[:500],
            "timestamp":  _now_iso(),
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id":          self.session_id,
            "case_id":             self.case_id,
            "started_at":          self.started_at,
            "completed_at":        self.completed_at,
            "cancelled_at":        self.cancelled_at,
            "cancellation_reason": self.cancellation_reason,
            "timeout_at":          self.timeout_at,
            "lifecycle_state":     self.lifecycle_state.value,
            "state_machine":       self.state_machine.to_dict(),
            "metrics":             self.metrics.to_dict(),
            "audit":               self.audit.to_dict(),
            "errors":              list(self.errors),
            "stage_transitions":   list(self.stage_transitions),
            "error_count":         len(self.errors),
        }


# ── OrchestratorInvestigationResult ───────────────────────────────────────────

@dataclass
class OrchestratorInvestigationResult:
    """
    Canonical, strongly-typed output of InvestigationOrchestrator.investigate().

    Returned for every call regardless of success, failure, or cancellation.
    Status field identifies the outcome.

    Fields:
      result_id            — unique result ID (UUID)
      session_id           — session that produced this result
      case_id              — case this investigation belongs to
      topic                — investigation topic (e.g. "VKYC_SESSION_FAILURE")
      status               — "COMPLETED" | "FAILED" | "CANCELLED" | "PARTIAL"
      plan                 — InvestigationPlan if planning succeeded, else None
      evidence             — EvidenceBundle if collection succeeded, else None
      knowledge_entries    — knowledge entries enriched during knowledge stage
      root_cause           — Sprint 2.43 RootCauseAnalysis if stage succeeded
      observation          — Sprint 2.44 Observation if stage succeeded
      metrics              — per-session stage durations and counts
      audit_timeline       — deterministic audit records (PII-free)
      errors               — list of stage error records
      stage_timings        — {stage_name: duration_ms}
      pipeline_duration_ms — total pipeline wall-clock duration
      started_at           — ISO 8601 pipeline start
      completed_at         — ISO 8601 pipeline end
      cancellation_reason  — why cancelled (if CANCELLED), else None
      recovery_hint        — human-readable retry suggestion (if failed/cancelled)
    """
    result_id:            str
    session_id:           str
    case_id:              str
    topic:                str
    status:               str
    plan:                 "InvestigationPlan | None"
    evidence:             "EvidenceBundle | None"
    knowledge_entries:    "list[KnowledgeEntry]"
    root_cause:           "RootCauseAnalysis | None"
    observation:          "Observation | None"
    metrics:              OrchestratorMetrics
    audit_timeline:       OrchestratorAudit
    errors:               list[dict[str, Any]]
    stage_timings:        dict[str, int]
    pipeline_duration_ms: int
    started_at:           str
    completed_at:         str
    cancellation_reason:  str | None
    recovery_hint:        str | None

    # ── Convenience predicates ──────────────────────────────────────────────────

    @property
    def is_completed(self) -> bool:
        return self.status == "COMPLETED"

    @property
    def is_failed(self) -> bool:
        return self.status == "FAILED"

    @property
    def is_cancelled(self) -> bool:
        return self.status == "CANCELLED"

    @property
    def is_partial(self) -> bool:
        return self.status == "PARTIAL"

    @property
    def has_plan(self) -> bool:
        return self.plan is not None

    @property
    def has_evidence(self) -> bool:
        return self.evidence is not None

    @property
    def has_root_cause(self) -> bool:
        return self.root_cause is not None

    @property
    def has_observation(self) -> bool:
        return self.observation is not None

    @property
    def has_knowledge(self) -> bool:
        return len(self.knowledge_entries) > 0

    def to_dict(self) -> dict[str, Any]:
        from case_engine.investigation.orchestrator.serialization import result_to_dict
        return result_to_dict(self)

    def to_json(self) -> str:
        from case_engine.investigation.orchestrator.serialization import result_to_json
        return result_to_json(self)
