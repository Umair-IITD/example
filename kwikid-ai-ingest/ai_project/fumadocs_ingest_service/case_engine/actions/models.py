"""
case_engine/actions/models.py

Sprint 2.21: Action Proposal Engine — domain models.

Defines the complete type hierarchy for the ACTIONPROPOSAL node in
flow_diagram.mermaid (Execution subgraph):

    ROOTCAUSE → ACTIONPROPOSAL → ACTIONGW → RISKCHECK

This module is the AI decision layer: "what should happen next?"
It produces typed, serializable proposals that are then passed to the
Action Gateway (ACTIONGW) for safety enforcement.

Design:
  - All types are frozen dataclasses — immutable after construction.
  - No LLM. No external I/O. Fully deterministic.
  - Every type supports to_dict() / from_dict() for JSONB round-trip.
  - Never raises on serialization.

Naming convention (to avoid collision with gateway layer):
  - ProposedActionType : action vocabulary for the proposal layer
  - ProposalRiskLevel  : SAFE | REVERSIBLE | HIGH_RISK (blueprint Section 17)
  - ActionProposalItem : a single proposed action with reasoning + risk
  - ActionProposalBundle : ordered collection of proposals for a case
  - ActionReasoning    : explains why this bundle was generated
  - ActionRiskAssessment : risk evaluation result for one proposal
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


# ── Action vocabulary ─────────────────────────────────────────────────────────

class ProposedActionType(str, Enum):
    """
    All action types the proposal engine may recommend.

    Per blueprint Section 15 (Action System) and flow_diagram.mermaid
    Execution subgraph. Ordered from least to most disruptive.
    """
    # User-facing retry / clarification
    ASK_USER_RETRY           = "ASK_USER_RETRY"
    WAIT_AND_RETRY           = "WAIT_AND_RETRY"

    # OTP delivery
    RESEND_OTP               = "RESEND_OTP"

    # Document / OCR
    RETRY_DOCUMENT_CAPTURE   = "RETRY_DOCUMENT_CAPTURE"

    # Session management
    RESET_SESSION            = "RESET_SESSION"

    # API / callback
    RETRY_CALLBACK           = "RETRY_CALLBACK"

    # Infrastructure / portal
    CHECK_SERVER_STATUS      = "CHECK_SERVER_STATUS"
    REFRESH_PORTAL           = "REFRESH_PORTAL"

    # Review / escalation
    MANUAL_REVIEW            = "MANUAL_REVIEW"
    ESCALATE_L2              = "ESCALATE_L2"
    CREATE_ASANA_TICKET      = "CREATE_ASANA_TICKET"

    # Fallback
    UNKNOWN_ACTION           = "UNKNOWN_ACTION"


# ── Risk levels ───────────────────────────────────────────────────────────────

class ProposalRiskLevel(str, Enum):
    """
    Risk tier for a proposed action.

    Mirrors blueprint Section 17:
      SAFE        — Auto execution allowed (OTP resend, OCR retry)
      REVERSIBLE  — Approval may be required (session reset, escalation)
      HIGH_RISK   — Approval required; destructive or user-impacting

    This is the proposal-layer risk tier. The Action Gateway (ACTIONGW)
    performs its own authoritative risk check on execution.
    """
    SAFE       = "SAFE"
    REVERSIBLE = "REVERSIBLE"
    HIGH_RISK  = "HIGH_RISK"


# ── Risk assessment ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionRiskAssessment:
    """
    Risk evaluation result for a single proposed action.

    Produced by RiskAssessmentEngine.assess().
    """
    assessment_id:    str
    action_type:      ProposedActionType
    risk_level:       ProposalRiskLevel
    requires_approval: bool
    risk_reason:      str
    assessed_at:      str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id":    self.assessment_id,
            "action_type":      self.action_type.value,
            "risk_level":       self.risk_level.value,
            "requires_approval": self.requires_approval,
            "risk_reason":      self.risk_reason,
            "assessed_at":      self.assessed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActionRiskAssessment":
        return cls(
            assessment_id=d.get("assessment_id", _new_id()),
            action_type=ProposedActionType(d["action_type"]),
            risk_level=ProposalRiskLevel(d["risk_level"]),
            requires_approval=d.get("requires_approval", False),
            risk_reason=d.get("risk_reason", ""),
            assessed_at=d.get("assessed_at", _now_iso()),
        )


# ── Reasoning ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionReasoning:
    """
    Explains why the proposal bundle was generated.

    Records the root cause category, investigation confidence, whether a
    SOP was found, and how the recommendation was derived.
    """
    reasoning_id:          str
    root_cause_category:   str
    investigation_confidence: float
    sop_match_found:       bool
    sop_entry_id:          str | None
    recommendation_source: str           # "sop_backed" | "rule_based" | "fallback"
    explanation:           str
    created_at:            str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reasoning_id":             self.reasoning_id,
            "root_cause_category":      self.root_cause_category,
            "investigation_confidence": self.investigation_confidence,
            "sop_match_found":          self.sop_match_found,
            "sop_entry_id":             self.sop_entry_id,
            "recommendation_source":    self.recommendation_source,
            "explanation":              self.explanation,
            "created_at":              self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActionReasoning":
        return cls(
            reasoning_id=d.get("reasoning_id", _new_id()),
            root_cause_category=d.get("root_cause_category", "UNKNOWN"),
            investigation_confidence=d.get("investigation_confidence", 0.0),
            sop_match_found=d.get("sop_match_found", False),
            sop_entry_id=d.get("sop_entry_id"),
            recommendation_source=d.get("recommendation_source", "rule_based"),
            explanation=d.get("explanation", ""),
            created_at=d.get("created_at", _now_iso()),
        )


# ── Proposal item ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionProposalItem:
    """
    A single proposed action with its risk assessment.

    priority: 1 = highest (execute first), 2 = fallback, etc.
    """
    item_id:         str
    action_type:     ProposedActionType
    priority:        int
    rationale:       str
    risk_assessment: ActionRiskAssessment
    sop_backed:      bool
    created_at:      str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id":         self.item_id,
            "action_type":     self.action_type.value,
            "priority":        self.priority,
            "rationale":       self.rationale,
            "risk_assessment": self.risk_assessment.to_dict(),
            "sop_backed":      self.sop_backed,
            "created_at":      self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActionProposalItem":
        return cls(
            item_id=d.get("item_id", _new_id()),
            action_type=ProposedActionType(d["action_type"]),
            priority=d.get("priority", 1),
            rationale=d.get("rationale", ""),
            risk_assessment=ActionRiskAssessment.from_dict(d["risk_assessment"]),
            sop_backed=d.get("sop_backed", False),
            created_at=d.get("created_at", _now_iso()),
        )


# ── Proposal bundle ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionProposalBundle:
    """
    Complete output of the Action Proposal Engine for one case.

    Contains an ordered list of proposed actions (highest priority first),
    the reasoning that generated them, and metadata for audit purposes.

    top_proposal:  The primary recommended action (proposals[0], or None if empty).
    all_safe:      True when all proposals are SAFE risk level.
    requires_approval: True when any proposal is REVERSIBLE or HIGH_RISK.
    """
    bundle_id:           str
    topic:               str
    root_cause_category: str
    proposals:           tuple["ActionProposalItem", ...]
    reasoning:           ActionReasoning
    top_proposal:        "ActionProposalItem | None"
    all_safe:            bool
    requires_approval:   bool
    proposal_count:      int
    created_at:          str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id":           self.bundle_id,
            "topic":               self.topic,
            "root_cause_category": self.root_cause_category,
            "proposals":           [p.to_dict() for p in self.proposals],
            "reasoning":           self.reasoning.to_dict(),
            "top_proposal":        self.top_proposal.to_dict() if self.top_proposal else None,
            "all_safe":            self.all_safe,
            "requires_approval":   self.requires_approval,
            "proposal_count":      self.proposal_count,
            "created_at":          self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActionProposalBundle":
        proposals = tuple(
            ActionProposalItem.from_dict(p) for p in d.get("proposals", [])
        )
        top_raw = d.get("top_proposal")
        top = ActionProposalItem.from_dict(top_raw) if top_raw else None
        return cls(
            bundle_id=d.get("bundle_id", _new_id()),
            topic=d.get("topic", ""),
            root_cause_category=d.get("root_cause_category", "UNKNOWN"),
            proposals=proposals,
            reasoning=ActionReasoning.from_dict(d.get("reasoning", {})),
            top_proposal=top,
            all_safe=d.get("all_safe", True),
            requires_approval=d.get("requires_approval", False),
            proposal_count=d.get("proposal_count", len(proposals)),
            created_at=d.get("created_at", _now_iso()),
        )
