"""
case_engine/investigation/models.py

Sprint 2.18: Evidence domain models for the Investigation Layer.

This module defines every data type produced and consumed during the
L1 investigation workflow:

  InvestigationStep      — one tool invocation step inside a plan
  InvestigationPlan      — ordered plan produced by InvestigationPlanner
  Evidence               — base class for a single piece of collected evidence
  UserEvidence           — from GetUserDetailsTool
  SessionEvidence        — from GetSessionDetailsTool
  LogEvidence            — from GetFailureReasonTool
  SummaryEvidence        — from GetCaseHistoryTool
  VideoEvidence          — future video analysis (structural placeholder)
  MetricEvidence         — future metrics analysis (structural placeholder)
  EvidenceBundle         — aggregate of all evidence items for one investigation
  RootCauseAnalysis      — deterministic root cause output
  InvestigationResult    — complete investigation outcome (plan+bundle+RC+observation)

All types are:
  - Fully typed (no Any in public fields)
  - Serializable via to_dict()
  - Immutable where correctness requires it (InvestigationStep, InvestigationPlan)
  - Never raise on to_dict() / construction
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


# ── Enumerations ───────────────────────────────────────────────────────────────

class EvidenceType(str, Enum):
    """Category of an evidence item."""
    USER    = "USER"
    SESSION = "SESSION"
    LOG     = "LOG"
    SUMMARY = "SUMMARY"
    VIDEO   = "VIDEO"
    METRIC  = "METRIC"


class EvidenceSource(str, Enum):
    """Tool that produced the evidence."""
    GET_USER_DETAILS      = "GetUserDetailsTool"
    GET_SESSION_DETAILS   = "GetSessionDetailsTool"
    GET_FAILURE_REASON    = "GetFailureReasonTool"
    GET_CASE_HISTORY      = "GetCaseHistoryTool"
    GET_ONBOARDING_STATUS = "GetOnboardingStatusTool"
    # Sprint 2.50 — Uptime Kuma monitoring dashboard
    METRIC_TOOL           = "MetricTool"
    SERVER_TOOL           = "ServerTool"


class RootCauseCategory(str, Enum):
    """Deterministic root cause categories, per blueprint Section 10."""
    # Network / infrastructure
    NETWORK_FAILURE      = "NETWORK_FAILURE"
    TIMEOUT              = "TIMEOUT"
    QUOTA_EXCEEDED       = "QUOTA_EXCEEDED"
    # Session / VKYC
    EXPIRED_SESSION      = "EXPIRED_SESSION"
    REPEATED_FAILURE     = "REPEATED_FAILURE"
    LIVENESS_FAILURE     = "LIVENESS_FAILURE"
    DOCUMENT_FAILURE     = "DOCUMENT_FAILURE"
    VALIDATION_FAILURE   = "VALIDATION_FAILURE"
    KYC_REJECTED         = "KYC_REJECTED"
    # Delivery / callback
    SMS_DELIVERY_FAILURE = "SMS_DELIVERY_FAILURE"
    CALLBACK_FAILURE     = "CALLBACK_FAILURE"
    # Onboarding
    ONBOARDING_BLOCKED   = "ONBOARDING_BLOCKED"
    # Portal
    PORTAL_UNAVAILABLE   = "PORTAL_UNAVAILABLE"
    # Fallback
    UNKNOWN              = "UNKNOWN"


class RecommendedAction(str, Enum):
    """Action recommended after root cause determination, per blueprint Section 15."""
    SESSION_RESET   = "SESSION_RESET"
    OTP_RESEND      = "OTP_RESEND"
    PORTAL_REFRESH  = "PORTAL_REFRESH"
    CALLBACK_RETRY  = "CALLBACK_RETRY"
    AUTO_ADVANCE    = "AUTO_ADVANCE"
    MANUAL_REVIEW   = "MANUAL_REVIEW"
    ESCALATE        = "ESCALATE"
    RETRY           = "RETRY"


# ── Investigation Plan ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InvestigationStep:
    """
    One step in an InvestigationPlan.

    Maps a tool invocation to the slot that provides its required input.
    """
    step_id:       str   # stable identifier, e.g. "step_get_session"
    sequence:      int   # 0-based ordering
    tool_name:     str   # e.g. "GetSessionDetailsTool"
    purpose:       str   # human-readable rationale
    required_slot: str   # slot_name that provides the input value
    input_key:     str   # key the tool expects, e.g. "session_id"

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":       self.step_id,
            "sequence":      self.sequence,
            "tool_name":     self.tool_name,
            "purpose":       self.purpose,
            "required_slot": self.required_slot,
            "input_key":     self.input_key,
        }


@dataclass(frozen=True)
class InvestigationPlan:
    """
    Ordered set of investigation steps produced by InvestigationPlanner.

    Immutable after construction. Created once per investigation run.
    """
    plan_id:     str
    case_id:     str
    topic:       str
    workflow_id: str | None
    steps:       tuple[InvestigationStep, ...]
    created_at:  str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id":     self.plan_id,
            "case_id":     self.case_id,
            "topic":       self.topic,
            "workflow_id": self.workflow_id,
            "step_count":  len(self.steps),
            "steps":       [s.to_dict() for s in self.steps],
            "created_at":  self.created_at,
        }


# ── Evidence ───────────────────────────────────────────────────────────────────

@dataclass
class Evidence:
    """
    Base class for a single piece of collected investigation evidence.

    One Evidence object corresponds to one ToolResult from one tool invocation.
    """
    evidence_id:   str
    evidence_type: EvidenceType
    source:        EvidenceSource
    tool_name:     str
    payload:       dict[str, Any]
    collected_at:  str
    invocation_id: str
    success:       bool
    error_code:    str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id":   self.evidence_id,
            "evidence_type": self.evidence_type.value,
            "source":        self.source.value,
            "tool_name":     self.tool_name,
            "payload":       self.payload,
            "collected_at":  self.collected_at,
            "invocation_id": self.invocation_id,
            "success":       self.success,
            "error_code":    self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class UserEvidence(Evidence):
    """
    Evidence from GetUserDetailsTool.

    Carries KYC status, account state, risk tier.
    Per blueprint Section 7: user identification evidence.
    """


@dataclass
class SessionEvidence(Evidence):
    """
    Evidence from GetSessionDetailsTool.

    Carries session status, attempt count, failure code, reset eligibility.
    Per blueprint Section 9: session lookup evidence.
    """


@dataclass
class LogEvidence(Evidence):
    """
    Evidence from GetFailureReasonTool.

    Carries failure category, failure code, transience flag, recommended action.
    Per blueprint Section 10: log analysis evidence.
    """


@dataclass
class SummaryEvidence(Evidence):
    """
    Evidence from GetCaseHistoryTool.

    Carries case history, repeat patterns, escalation rate.
    Per blueprint Section 11: summary analysis evidence.
    """


@dataclass
class VideoEvidence(Evidence):
    """
    Future evidence from video analysis.

    Per blueprint Section 12: video analysis evidence.
    Structurally complete — tool implementation deferred to Sprint 2.19+.
    """


@dataclass
class MetricEvidence(Evidence):
    """
    Future evidence from metrics/observability tools.

    Structurally complete — tool implementation deferred to Sprint 2.19+.
    """


# ── Evidence Bundle ────────────────────────────────────────────────────────────

@dataclass
class EvidenceBundle:
    """
    Aggregate of all evidence collected during one investigation run.

    Produced by EvidenceCollector. Consumed by RootCauseEngine and
    ObservationGenerator.
    """
    bundle_id:    str
    case_id:      str
    topic:        str
    plan_id:      str
    items:        list[Evidence]
    collected_at: str

    @property
    def successful_items(self) -> list[Evidence]:
        """Evidence items from successful tool invocations only."""
        return [e for e in self.items if e.success]

    def get_by_type(self, evidence_type: EvidenceType) -> list[Evidence]:
        """Return all evidence items of a given type."""
        return [e for e in self.items if e.evidence_type == evidence_type]

    def get_by_source(self, source: EvidenceSource) -> Evidence | None:
        """Return the first successful evidence item from a given source tool."""
        for e in self.items:
            if e.source == source and e.success:
                return e
        return None

    def evidence_ids(self) -> list[str]:
        """Return IDs of all successful evidence items."""
        return [e.evidence_id for e in self.successful_items]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id":    self.bundle_id,
            "case_id":      self.case_id,
            "topic":        self.topic,
            "plan_id":      self.plan_id,
            "total_items":  len(self.items),
            "success_count": len(self.successful_items),
            "items":        [e.to_dict() for e in self.items],
            "collected_at": self.collected_at,
        }


# ── Root Cause Analysis ────────────────────────────────────────────────────────

@dataclass
class RootCauseAnalysis:
    """
    Deterministic root cause output from RootCauseEngine.

    Per blueprint Section 13: root cause + confidence + recommended action.
    Fully auditable — all fields are derived from explicit rules applied to
    the EvidenceBundle, never invented.
    """
    analysis_id:        str
    case_id:            str
    topic:              str
    category:           RootCauseCategory
    confidence:         float           # 0.0 – 1.0
    explanation:        str             # human-readable finding
    evidence_ids:       list[str]       # IDs of evidence items that drove this conclusion
    recommended_action: RecommendedAction
    escalate:           bool            # True if human escalation is required
    analysed_at:        str

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id":        self.analysis_id,
            "case_id":            self.case_id,
            "topic":              self.topic,
            "category":           self.category.value,
            "confidence":         self.confidence,
            "explanation":        self.explanation,
            "evidence_ids":       self.evidence_ids,
            "recommended_action": self.recommended_action.value,
            "escalate":           self.escalate,
            "analysed_at":        self.analysed_at,
        }


# ── Investigation Result ───────────────────────────────────────────────────────

@dataclass
class InvestigationResult:
    """
    Complete output of one investigation run.

    Contains all intermediate and final artifacts so callers can inspect
    any layer of the investigation pipeline.
    """
    result_id:    str
    case_id:      str
    plan:         InvestigationPlan
    bundle:       EvidenceBundle
    root_cause:   RootCauseAnalysis
    observation:  str           # L1-style Freshdesk internal note text
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":    self.result_id,
            "case_id":      self.case_id,
            "plan":         self.plan.to_dict(),
            "evidence":     self.bundle.to_dict(),
            "root_cause":   self.root_cause.to_dict(),
            "observation":  self.observation,
            "completed_at": self.completed_at,
        }
