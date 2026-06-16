"""
case_engine/action_gateway/models.py

Sprint 2.22: Action Gateway domain models.

These models represent the ACTIONGW → RISKCHECK → APPROVAL routing layer,
sitting between the ACTIONPROPOSAL output (Sprint 2.21) and the execution
gateway (Sprint 2.1 ActionGateway / ActionRequest lifecycle).

Flow:
    ActionProposalBundle
    → ActionGatewayDecision  (ACTIONGW validation)
    → GatewayRiskLevel       (RISKCHECK routing)
    → GatewayApprovalDecision (APPROVAL routing)
    → ActionGatewayResult    (output to workflow engine)

All models: frozen dataclasses, to_dict/from_dict, JSON-serializable.
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


# ── Risk level (mirrors ProposalRiskLevel, bridges to execution gateway) ──────

class GatewayRiskLevel(str, Enum):
    """
    Risk classification at the RISKCHECK node.

    Aligned with ProposalRiskLevel from Sprint 2.21.
    Maps onto routing decision: SAFE → EXECUTE, REVERSIBLE/HIGH_RISK → APPROVAL.
    """
    SAFE      = "SAFE"
    REVERSIBLE = "REVERSIBLE"
    HIGH_RISK = "HIGH_RISK"


# ── Validation status ─────────────────────────────────────────────────────────

class GatewayValidationStatus(str, Enum):
    """Result of ACTIONGW validation checks."""
    VALID   = "VALID"
    INVALID = "INVALID"
    BLOCKED = "BLOCKED"


# ── Approval status ───────────────────────────────────────────────────────────

class GatewayApprovalStatus(str, Enum):
    """
    Decision from APPROVAL node.

    APPROVED  — action may proceed to executor (SAFE auto-approval or human APPROVED)
    PENDING   — awaiting human decision (REVERSIBLE / HIGH_RISK)
    REJECTED  — human rejected; action must not execute
    BYPASSED  — approval not required (SAFE path)
    """
    APPROVED  = "APPROVED"
    PENDING   = "PENDING"
    REJECTED  = "REJECTED"
    BYPASSED  = "BYPASSED"


# ── ActionGatewayDecision ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionGatewayDecision:
    """
    Output of the ACTIONGW validation layer.

    Contains all checks performed before risk routing.
    Immutable — represents a point-in-time decision snapshot.
    """
    decision_id:               str
    validation_status:         GatewayValidationStatus
    risk_level:                GatewayRiskLevel
    requires_approval:         bool
    validation_failures:       tuple[str, ...]      # empty if VALID
    bundle_id:                 str | None
    top_action_type:           str | None
    confidence_check_passed:   bool
    investigation_check_passed: bool
    sop_check_passed:          bool
    decided_at:                str

    def is_valid(self) -> bool:
        return self.validation_status == GatewayValidationStatus.VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id":                self.decision_id,
            "validation_status":          self.validation_status.value,
            "risk_level":                 self.risk_level.value,
            "requires_approval":          self.requires_approval,
            "validation_failures":        list(self.validation_failures),
            "bundle_id":                  self.bundle_id,
            "top_action_type":            self.top_action_type,
            "confidence_check_passed":    self.confidence_check_passed,
            "investigation_check_passed": self.investigation_check_passed,
            "sop_check_passed":           self.sop_check_passed,
            "decided_at":                 self.decided_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActionGatewayDecision":
        return cls(
            decision_id=d.get("decision_id", _new_id()),
            validation_status=GatewayValidationStatus(
                d.get("validation_status", GatewayValidationStatus.BLOCKED.value)
            ),
            risk_level=GatewayRiskLevel(
                d.get("risk_level", GatewayRiskLevel.HIGH_RISK.value)
            ),
            requires_approval=d.get("requires_approval", True),
            validation_failures=tuple(d.get("validation_failures", [])),
            bundle_id=d.get("bundle_id"),
            top_action_type=d.get("top_action_type"),
            confidence_check_passed=d.get("confidence_check_passed", False),
            investigation_check_passed=d.get("investigation_check_passed", False),
            sop_check_passed=d.get("sop_check_passed", False),
            decided_at=d.get("decided_at", _now_iso()),
        )


# ── GatewayApprovalDecision ───────────────────────────────────────────────────

@dataclass(frozen=True)
class GatewayApprovalDecision:
    """
    Output of the APPROVAL node.

    For SAFE actions: status=APPROVED, approver="auto_approval" (bypassed).
    For REVERSIBLE/HIGH_RISK: status=PENDING until human acts.
    HIGH_RISK guardrail: never auto-approved — always status=PENDING minimum.
    """
    approval_id:    str
    status:         GatewayApprovalStatus
    approver:       str | None
    reason:         str
    notes:          str | None
    requires_human: bool
    decided_at:     str

    def can_execute(self) -> bool:
        return self.status == GatewayApprovalStatus.APPROVED

    def is_pending(self) -> bool:
        return self.status == GatewayApprovalStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id":    self.approval_id,
            "status":         self.status.value,
            "approver":       self.approver,
            "reason":         self.reason,
            "notes":          self.notes,
            "requires_human": self.requires_human,
            "decided_at":     self.decided_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GatewayApprovalDecision":
        return cls(
            approval_id=d.get("approval_id", _new_id()),
            status=GatewayApprovalStatus(
                d.get("status", GatewayApprovalStatus.PENDING.value)
            ),
            approver=d.get("approver"),
            reason=d.get("reason", ""),
            notes=d.get("notes"),
            requires_human=d.get("requires_human", True),
            decided_at=d.get("decided_at", _now_iso()),
        )


# ── ActionGatewayResult ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionGatewayResult:
    """
    Full output of the ActionGatewayService.process() orchestration.

    Persisted as JSONB in WorkflowExecutionResult.gateway_result.

    status values:
        APPROVED         — SAFE action, auto-approved, can proceed to executor
        PENDING_APPROVAL — REVERSIBLE/HIGH_RISK, waiting for human decision
        REJECTED         — Human rejected
        BLOCKED          — Validation failed (no investigation, low confidence, etc.)
        ERROR            — Internal error in gateway (never raises, returns ERROR result)
    """
    result_id:         str
    status:            str
    gateway_decision:  ActionGatewayDecision
    approval_decision: GatewayApprovalDecision | None
    risk_level:        GatewayRiskLevel
    can_execute:       bool
    block_reason:      str | None
    created_at:        str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":         self.result_id,
            "status":            self.status,
            "gateway_decision":  self.gateway_decision.to_dict(),
            "approval_decision": self.approval_decision.to_dict() if self.approval_decision else None,
            "risk_level":        self.risk_level.value,
            "can_execute":       self.can_execute,
            "block_reason":      self.block_reason,
            "created_at":        self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActionGatewayResult":
        gd_raw = d.get("gateway_decision") or {}
        ad_raw = d.get("approval_decision")
        return cls(
            result_id=d.get("result_id", _new_id()),
            status=d.get("status", "ERROR"),
            gateway_decision=ActionGatewayDecision.from_dict(gd_raw),
            approval_decision=GatewayApprovalDecision.from_dict(ad_raw) if ad_raw else None,
            risk_level=GatewayRiskLevel(d.get("risk_level", GatewayRiskLevel.HIGH_RISK.value)),
            can_execute=d.get("can_execute", False),
            block_reason=d.get("block_reason"),
            created_at=d.get("created_at", _now_iso()),
        )
