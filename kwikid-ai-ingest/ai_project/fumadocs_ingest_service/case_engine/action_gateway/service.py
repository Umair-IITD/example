"""
case_engine/action_gateway/service.py

Sprint 2.22: ActionGatewayService — full ACTIONGW → RISKCHECK → APPROVAL orchestration.

Per flow_diagram.mermaid:
    ACTIONPROPOSAL → ACTIONGW → RISKCHECK{Risk Level}
                                     |SAFE|        → EXECUTE
                                     |REVERSIBLE|  → APPROVAL
                                     |HIGH_RISK|   → APPROVAL
    APPROVAL → HUMANAPPROVER → APPROVED? → Yes → EXECUTE
                                          → No  → ESCALATE

This service is the bridge between Sprint 2.21 (ActionProposalBundle) and
Sprint 2.1 (ActionGateway execution lifecycle).

Process pipeline:
    1. ProposalGateway.validate()  → ActionGatewayDecision (ACTIONGW node)
    2. GatewayRiskEngine.route()   → routing decision (RISKCHECK node)
    3. ApprovalEngine.decide()     → GatewayApprovalDecision (APPROVAL node)
    4. Emit audit events
    5. Return ActionGatewayResult dict

Output status:
    APPROVED         — SAFE action auto-approved, can proceed to executor
    PENDING_APPROVAL — REVERSIBLE/HIGH_RISK, waiting for human decision
    BLOCKED          — validation failed (no investigation, low confidence, etc.)
    ERROR            — internal service error (never raises — returns ERROR result)

Deterministic. Never raises. No LLM.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.action_gateway.approval_engine import ApprovalEngine
from case_engine.action_gateway.gateway import ProposalGateway
from case_engine.action_gateway.models import (
    ActionGatewayDecision,
    ActionGatewayResult,
    GatewayApprovalDecision,
    GatewayApprovalStatus,
    GatewayRiskLevel,
    GatewayValidationStatus,
)
from case_engine.action_gateway.risk_engine import GatewayRiskEngine

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class ActionGatewayService:
    """
    ACTIONGW → RISKCHECK → APPROVAL orchestration.

    Bridges the Sprint 2.21 ActionProposalBundle to the Sprint 2.22 gateway
    decision pipeline. Does not interact with Sprint 2.1 execution gateway
    directly — that remains the responsibility of the WorkflowEngine.

    Stateless. Deterministic. Never raises.
    """

    def __init__(
        self,
        proposal_gateway: ProposalGateway | None = None,
        risk_engine:      GatewayRiskEngine | None = None,
        approval_engine:  ApprovalEngine | None = None,
        audit_logger:     Any = None,
    ) -> None:
        self._proposal_gateway = proposal_gateway or ProposalGateway()
        self._risk_engine      = risk_engine      or GatewayRiskEngine()
        self._approval_engine  = approval_engine  or ApprovalEngine()
        self._audit            = audit_logger

    def process(
        self,
        topic:                  str,
        action_proposal_result: dict[str, Any] | None,
        investigation_result:   dict[str, Any] | None,
        knowledge_result:       dict[str, Any] | None,
        case:                   Any = None,
        workflow_id:            str = "",
        step_id:                str = "",
    ) -> dict[str, Any]:
        """
        Run the full ACTIONGW → RISKCHECK → APPROVAL pipeline.

        Returns ActionGatewayResult.to_dict() — JSONB-compatible.
        Never raises.
        """
        try:
            return self._process(
                topic=topic,
                action_proposal_result=action_proposal_result or {},
                investigation_result=investigation_result or {},
                knowledge_result=knowledge_result or {},
                case=case,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            LOGGER.exception(
                "action_gateway_service.process failed topic=%s — returning ERROR result",
                topic,
            )
            return self._error_result(topic=topic, workflow_id=workflow_id).to_dict()

    # ── Private ───────────────────────────────────────────────────────────────

    def _process(
        self,
        topic:                  str,
        action_proposal_result: dict[str, Any],
        investigation_result:   dict[str, Any],
        knowledge_result:       dict[str, Any],
        case:                   Any,
        workflow_id:            str,
        step_id:                str,
    ) -> dict[str, Any]:
        bundle_id   = action_proposal_result.get("bundle_id", "")
        top_proposal: dict[str, Any] = action_proposal_result.get("top_proposal") or {}
        action_type = top_proposal.get("action_type", "")
        case_id     = getattr(case, "case_id", "") if case else ""

        # Audit: gateway started
        self._emit_gateway_started(case, topic, bundle_id, workflow_id, step_id)

        # ── Step 1: ACTIONGW validation ───────────────────────────────────────
        gateway_decision: ActionGatewayDecision = self._proposal_gateway.validate(
            topic=topic,
            action_proposal_result=action_proposal_result,
            investigation_result=investigation_result,
            knowledge_result=knowledge_result,
        )

        if gateway_decision.validation_status != GatewayValidationStatus.VALID:
            block_reason = "; ".join(gateway_decision.validation_failures) or "validation_failed"
            result = ActionGatewayResult(
                result_id=_new_id(),
                status="BLOCKED",
                gateway_decision=gateway_decision,
                approval_decision=None,
                risk_level=GatewayRiskLevel.HIGH_RISK,
                can_execute=False,
                block_reason=block_reason,
                created_at=_now_iso(),
            )
            self._emit_gateway_completed(case, topic, result, workflow_id, step_id)
            return result.to_dict()

        # ── Step 2: RISKCHECK — extract risk level and route ──────────────────
        risk_level = gateway_decision.risk_level
        route = self._risk_engine.route(risk_level)

        LOGGER.info(
            "action_gateway_service: topic=%s bundle_id=%s risk=%s route=%s",
            topic, bundle_id, risk_level.value, route,
        )

        # ── Step 3: APPROVAL ──────────────────────────────────────────────────
        # Emit approval requested for non-SAFE actions
        if risk_level != GatewayRiskLevel.SAFE:
            self._emit_approval_requested(case, topic, bundle_id, risk_level, workflow_id, step_id)

        approval_decision: GatewayApprovalDecision = self._approval_engine.decide(
            risk_level=risk_level,
            bundle_id=bundle_id,
            case_id=case_id,
            topic=topic,
            action_type=action_type,
        )

        # ── Step 4: Determine result status ───────────────────────────────────
        if approval_decision.status == GatewayApprovalStatus.APPROVED:
            status = "APPROVED"
            can_execute = True
            self._emit_approval_granted(case, topic, bundle_id, approval_decision.approver or "auto_approval", workflow_id, step_id)
        elif approval_decision.status == GatewayApprovalStatus.REJECTED:
            status = "REJECTED"
            can_execute = False
            self._emit_approval_rejected(case, topic, bundle_id, approval_decision.approver or "system", workflow_id, step_id)
        else:
            # PENDING (REVERSIBLE or HIGH_RISK)
            status = "PENDING_APPROVAL"
            can_execute = False

        result = ActionGatewayResult(
            result_id=_new_id(),
            status=status,
            gateway_decision=gateway_decision,
            approval_decision=approval_decision,
            risk_level=risk_level,
            can_execute=can_execute,
            block_reason=None,
            created_at=_now_iso(),
        )

        self._emit_gateway_completed(case, topic, result, workflow_id, step_id)
        return result.to_dict()

    def _error_result(self, topic: str, workflow_id: str) -> ActionGatewayResult:
        """Build an ERROR ActionGatewayResult for unrecoverable internal failures."""
        from case_engine.action_gateway.models import ActionGatewayDecision, GatewayValidationStatus
        decision = ActionGatewayDecision(
            decision_id=_new_id(),
            validation_status=GatewayValidationStatus.BLOCKED,
            risk_level=GatewayRiskLevel.HIGH_RISK,
            requires_approval=True,
            validation_failures=("internal_service_error",),
            bundle_id=None,
            top_action_type=None,
            confidence_check_passed=False,
            investigation_check_passed=False,
            sop_check_passed=False,
            decided_at=_now_iso(),
        )
        return ActionGatewayResult(
            result_id=_new_id(),
            status="ERROR",
            gateway_decision=decision,
            approval_decision=None,
            risk_level=GatewayRiskLevel.HIGH_RISK,
            can_execute=False,
            block_reason="internal_service_error",
            created_at=_now_iso(),
        )

    # ── Audit emission ────────────────────────────────────────────────────────

    def _emit_gateway_started(
        self, case: Any, topic: str, bundle_id: str, workflow_id: str, step_id: str
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_action_gateway_started(
                case=case, topic=topic, bundle_id=bundle_id,
                workflow_id=workflow_id, step_id=step_id,
            )
        except Exception:
            pass

    def _emit_gateway_completed(
        self, case: Any, topic: str, result: ActionGatewayResult, workflow_id: str, step_id: str
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_action_gateway_completed(
                case=case,
                topic=topic,
                result_id=result.result_id,
                status=result.status,
                risk_level=result.risk_level.value,
                can_execute=result.can_execute,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            pass

    def _emit_approval_requested(
        self, case: Any, topic: str, bundle_id: str,
        risk_level: GatewayRiskLevel, workflow_id: str, step_id: str
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_approval_requested(
                case=case, topic=topic, bundle_id=bundle_id,
                risk_level=risk_level.value, workflow_id=workflow_id, step_id=step_id,
            )
        except Exception:
            pass

    def _emit_approval_granted(
        self, case: Any, topic: str, bundle_id: str, approver: str,
        workflow_id: str, step_id: str
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_approval_granted(
                case=case, topic=topic, bundle_id=bundle_id,
                approver=approver, workflow_id=workflow_id, step_id=step_id,
            )
        except Exception:
            pass

    def _emit_approval_rejected(
        self, case: Any, topic: str, bundle_id: str, approver: str,
        workflow_id: str, step_id: str
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_approval_rejected(
                case=case, topic=topic, bundle_id=bundle_id,
                approver=approver, workflow_id=workflow_id, step_id=step_id,
            )
        except Exception:
            pass


# ── Factory ────────────────────────────────────────────────────────────────────

def build_action_gateway_service(audit_logger: Any = None) -> ActionGatewayService:
    """
    Factory function for constructing a fully-wired ActionGatewayService.

    audit_logger=None → no audit events emitted (offline/test mode).
    """
    return ActionGatewayService(
        proposal_gateway=ProposalGateway(),
        risk_engine=GatewayRiskEngine(),
        approval_engine=ApprovalEngine(),
        audit_logger=audit_logger,
    )
