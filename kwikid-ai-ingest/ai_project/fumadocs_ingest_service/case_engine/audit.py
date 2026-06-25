"""
case_engine/audit.py

AuditLogger: write-through audit log for all case events.

Design:
- Every call attempts a synchronous DB write.
- On failure: error is logged, exception is NOT re-raised (no silent data loss
  but also no crash of the primary request path).
- If supabase_client is None, falls back to log-only mode (useful for tests
  and offline environments).
- Case events → case_audit_log (append-only, no UPDATE/DELETE).
- Security events → security_compliance_audit (RLS-protected, SOC-only access).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.case_state import CaseState
from case_engine.models import AuditEntry, AuditEventType, Case, CaseTransition, MatchType

LOGGER = logging.getLogger(__name__)

_AUDIT_TABLE    = "case_audit_log"
_SECURITY_TABLE = "security_compliance_audit"


class AuditLogger:
    """
    Writes case events to case_audit_log.

    supabase_client: a supabase-py Client instance, or None for log-only mode.
    """

    def __init__(self, supabase_client: Any = None) -> None:
        self._sb = supabase_client

    # ── Public logging methods ─────────────────────────────────────────────────

    def log_transition(
        self,
        case: Case,
        transition: CaseTransition,
    ) -> None:
        """Log a state transition event."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            actor=transition.actor,
            action_type=AuditEventType.STATE_TRANSITION,
            action_detail={
                "from_state": transition.from_state.value,
                "to_state":   transition.to_state.value,
                "reason":     transition.reason,
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_classification(
        self,
        case: Case,
        topic: str,
        confidence: float,
        tier_used: int,
        meets_threshold: bool,
    ) -> None:
        """Log a topic classification result."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.CLASSIFICATION,
            action_detail={
                "topic":          topic,
                "confidence":     confidence,
                "tier_used":      tier_used,
                "meets_threshold": meets_threshold,
            },
            outcome="PASS" if meets_threshold else "BELOW_THRESHOLD",
        )
        self._write(entry)

    def log_rag_call(
        self,
        case: Case,
        match_type: str,
        confidence: str,
        chunks_count: int,
        requires_human: bool,
        cited_sop_ids: list[str] | None = None,
    ) -> None:
        """Log a RAG retrieval event."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.RAG_CALLED,
            action_detail={
                "match_type":     match_type,
                "confidence":     confidence,
                "chunks_count":   chunks_count,
                "requires_human": requires_human,
                "cited_sop_ids":  cited_sop_ids or [],
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_note_posted(
        self,
        case: Case,
        note_type: str,
        confidence: str,
    ) -> None:
        """Log a Freshdesk note posting event."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.NOTE_POSTED,
            action_detail={
                "note_type":  note_type,
                "confidence": confidence,
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_escalation(
        self,
        case: Case,
        trigger: str,
        reason: str,
        priority: str,
    ) -> None:
        """Log an escalation event."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ESCALATION_TRIGGERED,
            action_detail={
                "trigger":  trigger,
                "reason":   reason,
                "priority": priority,
            },
            outcome="ESCALATED",
        )
        self._write(entry)

    def log_error(
        self,
        case: Case,
        error_type: str,
        error_msg: str,
        *,
        actor: str = "system",
    ) -> None:
        """Log a system error event. Never raises."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            actor=actor,
            action_type=AuditEventType.ERROR,
            action_detail={"error_type": error_type, "message": error_msg},
            outcome="FAILURE",
            error_code=error_type,
        )
        self._write(entry)

    # ── Security compliance events ─────────────────────────────────────────────

    def log_security_event(
        self,
        case: Case,
        *,
        security_event_type: str,
        event_details: dict[str, Any],
        severity: str = "HIGH",
    ) -> None:
        """Log a security event to security_compliance_audit (not case_audit_log).

        Security events (DEEPFAKE_SUSPECTED, FOREIGN_IP, AADHAAR_MISMATCH, etc.)
        are SOC-routed and must NOT appear in case_audit_log.
        """
        LOGGER.warning(
            "security_event type=%s case=%s ticket=%s severity=%s",
            security_event_type, case.case_id, case.ticket_id, severity,
        )
        if self._sb is None:
            return

        row: dict[str, Any] = {
            "audit_id":            str(uuid.uuid4()),
            "case_id":             case.case_id,
            "ticket_id":           case.ticket_id,
            "client":              case.client,
            "security_event_type": security_event_type,
            "event_details":       event_details,
            "severity":            severity,
            "detected_at":         datetime.now(tz=timezone.utc).isoformat(),
            "reported_to_soc":     False,
        }
        self._write_security(row)

    # ── Sprint 2 audit API stubs ───────────────────────────────────────────────
    # These methods record Level 2 Action Gateway events. They write to
    # case_audit_log so the event trail is complete when Level 2 is enabled.
    # They are placeholders only — not wired to any runtime logic in Sprint 1.

    def log_action_proposed(
        self,
        case: Case,
        action_name: str,
        action_detail: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        """Sprint 2 stub: log an action proposal from the Action Gateway."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_PROPOSED,
            action_detail={"action_name": action_name, **action_detail},
            outcome="PROPOSED",
            idempotency_key=idempotency_key,
        )
        self._write(entry)

    def log_action_executed(
        self,
        case: Case,
        action_name: str,
        outcome: str,
        idempotency_key: str,
        error_code: str | None = None,
    ) -> None:
        """Sprint 2 stub: log a successfully executed action."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_EXECUTED,
            action_detail={"action_name": action_name, "outcome": outcome},
            outcome=outcome,
            error_code=error_code,
            idempotency_key=idempotency_key,
        )
        self._write(entry)

    def log_action_rejected(
        self,
        case: Case,
        action_name: str,
        reason: str,
        idempotency_key: str,
    ) -> None:
        """Sprint 2 stub: log a rejected action (human sign-off required)."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_REJECTED,
            action_detail={"action_name": action_name, "reason": reason},
            outcome="REJECTED",
            idempotency_key=idempotency_key,
        )
        self._write(entry)

    # ── Sprint 2.16: Workflow orchestration audit ──────────────────────────────

    def log_workflow_started(
        self,
        case: Case,
        workflow_id: str,
        run_id: str,
    ) -> None:
        """Log that a workflow has started executing for this case."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_STARTED,
            action_detail={"workflow_id": workflow_id, "run_id": run_id},
            outcome="STARTED",
        )
        self._write(entry)

    def log_workflow_step_completed(
        self,
        case: Case,
        workflow_id: str,
        step_id: str,
        step_type: str,
        outcome: str,
    ) -> None:
        """Log that a single workflow step has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_STEP_COMPLETED,
            action_detail={
                "workflow_id": workflow_id,
                "step_id":     step_id,
                "step_type":   step_type,
                "outcome":     outcome,
            },
            outcome=outcome,
        )
        self._write(entry)

    def log_workflow_escalated(
        self,
        case: Case,
        workflow_id: str,
        escalation_reason: str | None,
    ) -> None:
        """Log that a workflow has escalated to human handling."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_ESCALATED,
            action_detail={
                "workflow_id":       workflow_id,
                "escalation_reason": escalation_reason or "unknown",
            },
            outcome="ESCALATED",
        )
        self._write(entry)

    def log_workflow_resolved(
        self,
        case: Case,
        workflow_id: str,
        resolution_note: str | None,
    ) -> None:
        """Log that a workflow has resolved the case successfully."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_RESOLVED,
            action_detail={
                "workflow_id":     workflow_id,
                "resolution_note": resolution_note or "",
            },
            outcome="RESOLVED",
        )
        self._write(entry)

    # ── Sprint 2.17: Additional workflow lifecycle audit ───────────────────────

    def log_workflow_resumed(
        self,
        case: Case,
        workflow_id: str,
        run_id: str,
        action_id: str,
    ) -> None:
        """Log that a paused workflow has been resumed after action completion."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_RESUMED,
            action_detail={
                "workflow_id": workflow_id,
                "run_id":      run_id,
                "action_id":   action_id,
            },
            outcome="RESUMED",
        )
        self._write(entry)

    def log_workflow_completed(
        self,
        case: Case,
        workflow_id: str,
        resolution_note: str | None,
    ) -> None:
        """Log explicit workflow completion (mirrors log_workflow_resolved with COMPLETED event type)."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_COMPLETED,
            action_detail={
                "workflow_id":     workflow_id,
                "resolution_note": resolution_note or "",
            },
            outcome="COMPLETED",
        )
        self._write(entry)

    def log_workflow_failed(
        self,
        case: Case,
        workflow_id: str,
        failure_reason: str | None,
    ) -> None:
        """Log that a workflow reached a FAILED terminal state."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_FAILED,
            action_detail={
                "workflow_id":    workflow_id,
                "failure_reason": failure_reason or "unknown",
            },
            outcome="FAILED",
        )
        self._write(entry)

    # ── Sprint 2.18: Investigation layer audit ────────────────────────────────

    def log_investigation_started(
        self,
        case: Case,
        topic: str,
        plan_id: str,
        step_count: int,
    ) -> None:
        """Log that investigation has started for this case."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.INVESTIGATION_STARTED,
            action_detail={
                "topic":      topic,
                "plan_id":    plan_id,
                "step_count": step_count,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_investigation_completed(
        self,
        case: Case,
        plan_id: str,
        bundle_id: str,
        analysis_id: str,
        category: str,
        escalate: bool,
    ) -> None:
        """Log that investigation has completed for this case."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.INVESTIGATION_COMPLETED,
            action_detail={
                "plan_id":     plan_id,
                "bundle_id":   bundle_id,
                "analysis_id": analysis_id,
                "category":    category,
                "escalate":    escalate,
            },
            outcome="ESCALATED" if escalate else "COMPLETED",
        )
        self._write(entry)

    # ── Sprint 2.19: Workflow investigation step audit ────────────────────────

    def log_workflow_investigation_started(
        self,
        case: Case,
        workflow_id: str,
        step_id: str,
        topic: str,
    ) -> None:
        """Log that an INVESTIGATE workflow step has begun executing."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_INVESTIGATION_STARTED,
            action_detail={
                "workflow_id": workflow_id,
                "step_id":     step_id,
                "topic":       topic,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_workflow_investigation_completed(
        self,
        case: Case,
        workflow_id: str,
        step_id: str,
        category: str,
        confidence: float,
        escalate: bool,
        result_id: str,
    ) -> None:
        """Log that an INVESTIGATE workflow step has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_INVESTIGATION_COMPLETED,
            action_detail={
                "workflow_id": workflow_id,
                "step_id":     step_id,
                "category":    category,
                "confidence":  confidence,
                "escalate":    escalate,
                "result_id":   result_id,
            },
            outcome="ESCALATED" if escalate else "COMPLETED",
        )
        self._write(entry)

    def log_tool_executed(
        self,
        case: Case,
        tool_name: str,
        success: bool,
        payload: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        """Log a tool invocation from the investigation layer."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.TOOL_EXECUTED if success else AuditEventType.TOOL_FAILED,
            action_detail={
                "tool_name":  tool_name,
                "success":    success,
                "payload_keys": list((payload or {}).keys()),
            },
            outcome="SUCCESS" if success else "FAILURE",
            error_code=error_code,
        )
        self._write(entry)

    def log_slot_filled(
        self,
        case: Case,
        slot_name: str,
        value_hash: str,
        turn_count: int,
    ) -> None:
        """Sprint 2 stub: log a slot fill from the conversational state machine."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.SLOT_FILLED,
            action_detail={
                "slot_name":  slot_name,
                "value_hash": value_hash,
                "turn_count": turn_count,
            },
            outcome="FILLED",
        )
        self._write(entry)

    # ── Sprint 2.20: Knowledge Layer audit ───────────────────────────────────

    def log_knowledge_search_started(
        self,
        case: Case,
        topic: str,
        root_cause_category: str | None = None,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that a KNOWLEDGE_LOOKUP workflow step has begun searching."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.KNOWLEDGE_SEARCH_STARTED,
            action_detail={
                "topic":               topic,
                "root_cause_category": root_cause_category,
                "workflow_id":         workflow_id,
                "step_id":             step_id,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_knowledge_search_completed(
        self,
        case: Case,
        topic: str,
        result_id: str,
        sop_match_found: bool,
        top_score: float,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that a KNOWLEDGE_LOOKUP workflow step has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.KNOWLEDGE_SEARCH_COMPLETED,
            action_detail={
                "topic":           topic,
                "result_id":       result_id,
                "sop_match_found": sop_match_found,
                "top_score":       top_score,
                "workflow_id":     workflow_id,
                "step_id":         step_id,
            },
            outcome="MATCH_FOUND" if sop_match_found else "NO_MATCH",
        )
        self._write(entry)

    def log_sop_match_found(
        self,
        case: Case,
        topic: str,
        entry_id: str,
        entry_title: str,
        relevance_score: float,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that an SOP match was found during knowledge search."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.SOP_MATCH_FOUND,
            action_detail={
                "topic":            topic,
                "entry_id":         entry_id,
                "entry_title":      entry_title,
                "relevance_score":  relevance_score,
                "workflow_id":      workflow_id,
                "step_id":          step_id,
            },
            outcome="FOUND",
        )
        self._write(entry)

    def log_sop_match_not_found(
        self,
        case: Case,
        topic: str,
        root_cause_category: str | None = None,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that no SOP match was found during knowledge search."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.SOP_MATCH_NOT_FOUND,
            action_detail={
                "topic":               topic,
                "root_cause_category": root_cause_category,
                "workflow_id":         workflow_id,
                "step_id":             step_id,
            },
            outcome="NOT_FOUND",
        )
        self._write(entry)

    # ── Sprint 2.21: Action Proposal Engine audit ─────────────────────────────

    def log_action_proposal_started(
        self,
        case: Case,
        topic: str,
        root_cause_category: str,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that the Action Proposal Engine has begun generating proposals."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_PROPOSAL_STARTED,
            action_detail={
                "topic":               topic,
                "root_cause_category": root_cause_category,
                "workflow_id":         workflow_id,
                "step_id":             step_id,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_action_proposal_completed(
        self,
        case: Case,
        topic: str,
        bundle_id: str,
        proposal_count: int,
        top_action: str | None,
        risk_level: str | None,
        requires_approval: bool,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that the Action Proposal Engine has completed generating proposals."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_PROPOSAL_COMPLETED,
            action_detail={
                "topic":             topic,
                "bundle_id":         bundle_id,
                "proposal_count":    proposal_count,
                "top_action":        top_action,
                "risk_level":        risk_level,
                "requires_approval": requires_approval,
                "workflow_id":       workflow_id,
                "step_id":           step_id,
            },
            outcome="COMPLETED",
        )
        self._write(entry)

    def log_action_proposal_blocked(
        self,
        case: Case,
        topic: str,
        block_reason: str,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that action proposal was blocked (e.g. missing investigation_result)."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_PROPOSAL_BLOCKED,
            action_detail={
                "topic":        topic,
                "block_reason": block_reason,
                "workflow_id":  workflow_id,
                "step_id":      step_id,
            },
            outcome="BLOCKED",
        )
        self._write(entry)

    def log_risk_assessment_completed(
        self,
        case: Case,
        topic: str,
        bundle_id: str,
        action_type: str,
        risk_level: str,
        requires_approval: bool,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that a risk assessment has completed for a proposed action."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.RISK_ASSESSMENT_COMPLETED,
            action_detail={
                "topic":             topic,
                "bundle_id":         bundle_id,
                "action_type":       action_type,
                "risk_level":        risk_level,
                "requires_approval": requires_approval,
                "workflow_id":       workflow_id,
                "step_id":           step_id,
            },
            outcome=risk_level,
        )
        self._write(entry)

    # ── Sprint 2.22: Action Gateway + Approval audit ─────────────────────────

    def log_action_gateway_started(
        self,
        case: Case,
        topic: str,
        bundle_id: str,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that the Action Gateway pipeline has begun processing a proposal."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_GATEWAY_STARTED,
            action_detail={
                "topic":       topic,
                "bundle_id":   bundle_id,
                "workflow_id": workflow_id,
                "step_id":     step_id,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_action_gateway_completed(
        self,
        case: Case,
        topic: str,
        result_id: str,
        status: str,
        risk_level: str,
        can_execute: bool,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that the Action Gateway pipeline has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ACTION_GATEWAY_COMPLETED,
            action_detail={
                "topic":       topic,
                "result_id":   result_id,
                "status":      status,
                "risk_level":  risk_level,
                "can_execute": can_execute,
                "workflow_id": workflow_id,
                "step_id":     step_id,
            },
            outcome=status,
        )
        self._write(entry)

    def log_approval_requested(
        self,
        case: Case,
        topic: str,
        bundle_id: str,
        risk_level: str,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that human approval has been requested for an action."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.APPROVAL_REQUESTED,
            action_detail={
                "topic":       topic,
                "bundle_id":   bundle_id,
                "risk_level":  risk_level,
                "workflow_id": workflow_id,
                "step_id":     step_id,
            },
            outcome="PENDING",
        )
        self._write(entry)

    def log_approval_granted(
        self,
        case: Case,
        topic: str,
        bundle_id: str,
        approver: str,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that an action approval has been granted."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.APPROVAL_GRANTED,
            action_detail={
                "topic":       topic,
                "bundle_id":   bundle_id,
                "approver":    approver,
                "workflow_id": workflow_id,
                "step_id":     step_id,
            },
            outcome="APPROVED",
        )
        self._write(entry)

    def log_approval_rejected(
        self,
        case: Case,
        topic: str,
        bundle_id: str,
        approver: str,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that an action approval has been rejected by a human."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.APPROVAL_REJECTED,
            action_detail={
                "topic":       topic,
                "bundle_id":   bundle_id,
                "approver":    approver,
                "workflow_id": workflow_id,
                "step_id":     step_id,
            },
            outcome="REJECTED",
        )
        self._write(entry)

    # ── Sprint 2.23: Execution layer audit ───────────────────────────────────

    def log_execution_started(
        self,
        case: Case,
        action_type: str,
        bundle_id: str,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Log that an EXECUTE workflow step has begun."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.EXECUTION_STARTED,
            action_detail={
                "action_type": action_type,
                "bundle_id":   bundle_id,
                "workflow_id": workflow_id or "",
                "step_id":     step_id or "",
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_execution_completed(
        self,
        case: Case,
        action_type: str,
        bundle_id: str,
        status: str,
        success: bool,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Log that an EXECUTE workflow step has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.EXECUTION_COMPLETED,
            action_detail={
                "action_type": action_type,
                "bundle_id":   bundle_id,
                "status":      status,
                "success":     success,
                "workflow_id": workflow_id or "",
                "step_id":     step_id or "",
            },
            outcome="SUCCESS" if success else "FAILED",
        )
        self._write(entry)

    def log_verification_started(
        self,
        case: Case,
        action_type: str,
        bundle_id: str,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Log that the VERIFY node has begun."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.VERIFICATION_STARTED,
            action_detail={
                "action_type": action_type,
                "bundle_id":   bundle_id,
                "workflow_id": workflow_id or "",
                "step_id":     step_id or "",
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_verification_completed(
        self,
        case: Case,
        action_type: str,
        bundle_id: str,
        status: str,
        success: bool,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Log that the VERIFY node has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.VERIFICATION_COMPLETED,
            action_detail={
                "action_type": action_type,
                "bundle_id":   bundle_id,
                "status":      status,
                "success":     success,
                "workflow_id": workflow_id or "",
                "step_id":     step_id or "",
            },
            outcome="VERIFIED" if success else "UNVERIFIED",
        )
        self._write(entry)

    def log_recovery_started(
        self,
        case: Case,
        action_type: str,
        bundle_id: str,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Log that the RECOVERY node has begun."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.RECOVERY_STARTED,
            action_detail={
                "action_type": action_type,
                "bundle_id":   bundle_id,
                "workflow_id": workflow_id or "",
                "step_id":     step_id or "",
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_recovery_completed(
        self,
        case: Case,
        action_type: str,
        bundle_id: str,
        strategy: str,
        status: str,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Log that the RECOVERY node has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.RECOVERY_COMPLETED,
            action_detail={
                "action_type": action_type,
                "bundle_id":   bundle_id,
                "strategy":    strategy,
                "status":      status,
                "workflow_id": workflow_id or "",
                "step_id":     step_id or "",
            },
            outcome=status,
        )
        self._write(entry)

    def log_resolution_started(
        self,
        case: Case,
        action_type: str,
        bundle_id: str,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Log that the RESOLUTION node has begun."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.RESOLUTION_STARTED,
            action_detail={
                "action_type": action_type,
                "bundle_id":   bundle_id,
                "workflow_id": workflow_id or "",
                "step_id":     step_id or "",
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_resolution_completed(
        self,
        case: Case,
        action_type: str,
        bundle_id: str,
        status: str,
        resolved: bool,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Log that the RESOLUTION node has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.RESOLUTION_COMPLETED,
            action_detail={
                "action_type": action_type,
                "bundle_id":   bundle_id,
                "status":      status,
                "resolved":    resolved,
                "workflow_id": workflow_id or "",
                "step_id":     step_id or "",
            },
            outcome="RESOLVED" if resolved else status,
        )
        self._write(entry)

    # ── Sprint 2.24: Investigation Reasoning Engine audit ─────────────────────

    def log_reasoning_started(
        self,
        case: Case,
        topic: str,
        root_cause_category: str,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that the Investigation Reasoning Engine has begun."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.REASONING_STARTED,
            action_detail={
                "topic":               topic,
                "root_cause_category": root_cause_category,
                "workflow_id":         workflow_id,
                "step_id":             step_id,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_reasoning_completed(
        self,
        case: Case,
        topic: str,
        result_id: str,
        outcome: str,
        recommended_action: str,
        should_escalate: bool,
        confidence: float,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that the Investigation Reasoning Engine has produced a result."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.REASONING_COMPLETED,
            action_detail={
                "topic":              topic,
                "result_id":          result_id,
                "outcome":            outcome,
                "recommended_action": recommended_action,
                "should_escalate":    should_escalate,
                "confidence":         confidence,
                "workflow_id":        workflow_id,
                "step_id":            step_id,
            },
            outcome=outcome,
        )
        self._write(entry)

    def log_workflow_reasoning_started(
        self,
        case: Case,
        workflow_id: str,
        step_id: str,
    ) -> None:
        """Log that a REASON workflow step has begun."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_REASONING_STARTED,
            action_detail={
                "workflow_id": workflow_id,
                "step_id":     step_id,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_workflow_reasoning_completed(
        self,
        case: Case,
        workflow_id: str,
        step_id: str,
        outcome: str,
        recommended_action: str = "",
    ) -> None:
        """Log that a REASON workflow step has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_REASONING_COMPLETED,
            action_detail={
                "workflow_id":        workflow_id,
                "step_id":            step_id,
                "recommended_action": recommended_action,
            },
            outcome=outcome,
        )
        self._write(entry)

    # ── Sprint 2.25: Clarification Layer audit ───────────────────────────────

    def log_clarification_started(
        self,
        case: Case,
        topic: str,
        missing_slots: list[str] | None = None,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that a CLARIFY workflow step has begun checking slot completeness."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.CLARIFICATION_STARTED,
            action_detail={
                "topic":         topic,
                "missing_slots": missing_slots or [],
                "workflow_id":   workflow_id,
                "step_id":       step_id,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_clarification_completed(
        self,
        case: Case,
        topic: str,
        status: str,
        ready_to_continue: bool,
        slot_count: int = 0,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that a CLARIFY workflow step has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.CLARIFICATION_COMPLETED,
            action_detail={
                "topic":             topic,
                "status":            status,
                "ready_to_continue": ready_to_continue,
                "missing_slot_count": slot_count,
                "workflow_id":       workflow_id,
                "step_id":           step_id,
            },
            outcome=status,
        )
        self._write(entry)

    def log_workflow_clarification_started(
        self,
        case: Case,
        workflow_id: str,
        step_id: str,
    ) -> None:
        """Log that a CLARIFY workflow step has begun executing."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_CLARIFICATION_STARTED,
            action_detail={
                "workflow_id": workflow_id,
                "step_id":     step_id,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_workflow_clarification_completed(
        self,
        case: Case,
        workflow_id: str,
        step_id: str,
        status: str,
        ready_to_continue: bool,
    ) -> None:
        """Log that a CLARIFY workflow step has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_CLARIFICATION_COMPLETED,
            action_detail={
                "workflow_id":       workflow_id,
                "step_id":           step_id,
                "status":            status,
                "ready_to_continue": ready_to_continue,
            },
            outcome=status,
        )
        self._write(entry)

    def log_workflow_clarification_resumed(
        self,
        case: Case,
        workflow_id: str,
        step_id: str,
        slots_updated: list[str],
    ) -> None:
        """Log that a PAUSED-at-CLARIFY workflow is being resumed with updated slots."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.WORKFLOW_CLARIFICATION_RESUMED,
            action_detail={
                "workflow_id":   workflow_id,
                "step_id":       step_id,
                "slots_updated": slots_updated,
            },
            outcome="RESUMED",
        )
        self._write(entry)

    def log_clarification_attempt_incremented(
        self,
        case: Case,
        slot_name: str,
        attempt_count: int,
        max_attempts: int,
        workflow_id: str = "",
        step_id: str = "",
    ) -> None:
        """Log that attempt_count was incremented for a specific missing slot."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.CLARIFICATION_ATTEMPT_INCREMENTED,
            action_detail={
                "slot_name":     slot_name,
                "attempt_count": attempt_count,
                "max_attempts":  max_attempts,
                "workflow_id":   workflow_id,
                "step_id":       step_id,
            },
            outcome=f"attempt_{attempt_count}_of_{max_attempts}",
        )
        self._write(entry)

    # ── Sprint 2.27: Adapter Framework audit ─────────────────────────────────

    def log_adapter_request_started(
        self,
        adapter_type: str,
        operation: str,
        request_id: str,
        case_id: str,
        action_type: str,
    ) -> None:
        """Log that an AdapterRouter has dispatched a request to an adapter."""
        entry = AuditEntry(
            case_id=case_id,
            action_type=AuditEventType.ADAPTER_REQUEST_STARTED,
            action_detail={
                "adapter_type": adapter_type,
                "operation":    operation,
                "request_id":   request_id,
                "action_type":  action_type,
            },
            outcome="STARTED",
        )
        self._write(entry)

    def log_adapter_request_completed(
        self,
        adapter_type: str,
        operation: str,
        request_id: str,
        case_id: str,
        status: str,
        success: bool,
        duration_ms: int,
        adapter_name: str,
    ) -> None:
        """Log that an adapter has returned a response to the AdapterRouter."""
        entry = AuditEntry(
            case_id=case_id,
            action_type=AuditEventType.ADAPTER_REQUEST_COMPLETED,
            action_detail={
                "adapter_type": adapter_type,
                "operation":    operation,
                "request_id":   request_id,
                "status":       status,
                "success":      success,
                "duration_ms":  duration_ms,
                "adapter_name": adapter_name,
            },
            outcome="SUCCESS" if success else "FAILED",
        )
        self._write(entry)

    def log_adapter_health_check(
        self,
        adapter_type: str,
        adapter_name: str,
        healthy: bool,
    ) -> None:
        """Log an adapter health check result."""
        entry = AuditEntry(
            action_type=AuditEventType.ADAPTER_HEALTH_CHECK,
            action_detail={
                "adapter_type": adapter_type,
                "adapter_name": adapter_name,
                "healthy":      healthy,
            },
            outcome="HEALTHY" if healthy else "UNHEALTHY",
        )
        self._write(entry)

    def log_adapter_routing_failed(
        self,
        adapter_type: str,
        operation: str,
        request_id: str,
        case_id: str,
        reason: str,
    ) -> None:
        """Log that AdapterRouter could not route a request (no adapter registered)."""
        entry = AuditEntry(
            case_id=case_id,
            action_type=AuditEventType.ADAPTER_ROUTING_FAILED,
            action_detail={
                "adapter_type": adapter_type,
                "operation":    operation,
                "request_id":   request_id,
                "reason":       reason,
            },
            outcome="BLOCKED",
        )
        self._write(entry)

    # ── Sprint 2.27.5: Architecture convergence audit ────────────────────────

    def log_knowledge_orchestration_completed(
        self,
        case: Case,
        bundle_id: str,
        topic: str,
        sop_found: bool,
        rag_placeholder: bool,
        confidence: float,
        escalation_required: bool,
    ) -> None:
        """Log completion of the KnowledgeOrchestrator unified pipeline."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.KNOWLEDGE_ORCHESTRATION_COMPLETED,
            action_detail={
                "bundle_id":           bundle_id,
                "topic":               topic,
                "sop_found":           sop_found,
                "rag_placeholder":     rag_placeholder,
                "confidence":          confidence,
                "escalation_required": escalation_required,
            },
            outcome="ESCALATE" if escalation_required else "PROCEED",
        )
        self._write(entry)

    def log_response_generated(
        self,
        case: Case,
        draft_id: str,
        response_type: str,
        confidence: float,
        escalation_required: bool,
        generator_type: str,
    ) -> None:
        """Log completion of the ResponseGenerationService (USERRESPONSE node)."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.RESPONSE_GENERATED,
            action_detail={
                "draft_id":            draft_id,
                "response_type":       response_type,
                "confidence":          confidence,
                "escalation_required": escalation_required,
                "generator_type":      generator_type,
            },
            outcome="ESCALATE" if escalation_required else "SEND",
        )
        self._write(entry)

    def log_engineering_escalation_created(
        self,
        case: Case,
        ticket_id: str,
        priority: str,
        external_id: str | None = None,
    ) -> None:
        """Log creation of an engineering escalation ticket (ASANACREATE node)."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ENGINEERING_ESCALATION_CREATED,
            action_detail={
                "engineering_ticket_id": ticket_id,
                "priority":              priority,
                "external_id":           external_id,
            },
            outcome="CREATED",
        )
        self._write(entry)

    def log_engineering_escalation_resolved(
        self,
        case: Case,
        ticket_id: str,
        resolved_at: str | None = None,
    ) -> None:
        """Log resolution of an engineering escalation ticket."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.ENGINEERING_ESCALATION_RESOLVED,
            action_detail={
                "engineering_ticket_id": ticket_id,
                "resolved_at":           resolved_at,
            },
            outcome="RESOLVED",
        )
        self._write(entry)

    def log_agent_run_started(self, case: Case) -> None:
        """Log that SupportAgentRuntime.run_case() has started."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.AGENT_RUN_STARTED,
            action_detail={"topic": case.topic or "unknown"},
            outcome="STARTED",
        )
        self._write(entry)

    def log_agent_run_completed(
        self,
        case: Case,
        run_id: str,
        agent_status: str,
        steps_completed: list[str],
        duration_ms: int,
    ) -> None:
        """Log that SupportAgentRuntime.run_case() has completed."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.AGENT_RUN_COMPLETED,
            action_detail={
                "run_id":          run_id,
                "agent_status":    agent_status,
                "steps_completed": steps_completed,
                "duration_ms":     duration_ms,
            },
            outcome=agent_status,
        )
        self._write(entry)

    def log_action_routed(
        self,
        action_type: str,
        adapter_type: str,
        operation: str,
        success: bool,
        case_id: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Log a RouterService.route() call (universal adapter dispatch)."""
        entry = AuditEntry(
            case_id=case_id,
            action_type=AuditEventType.ADAPTER_REQUEST_COMPLETED,
            action_detail={
                "action_type":  action_type,
                "adapter_type": adapter_type,
                "operation":    operation,
                "success":      success,
                "duration_ms":  duration_ms,
            },
            outcome="SUCCESS" if success else "FAILED",
        )
        self._write(entry)

    # ── Sprint 2.27.8: Dry-run mode and startup validation audit ─────────────

    def log_dry_run_execution(
        self,
        case: Case,
        action_type: str,
        workflow_id: str = "",
        reason: str = "DRY_RUN mode active — execution simulated",
    ) -> None:
        """Log that an EXECUTE step was simulated (DRY_RUN mode)."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.DRY_RUN_EXECUTION,
            action_detail={
                "action_type": action_type,
                "workflow_id": workflow_id,
                "reason":      reason,
                "simulated":   True,
            },
            outcome="DRY_RUN_SIMULATED",
        )
        self._write(entry)

    def log_dry_run_route(
        self,
        action_type: str,
        adapter_type: str,
        case_id: str = "",
        reason: str = "DRY_RUN mode active — routing simulated",
    ) -> None:
        """Log that an adapter route call was simulated (DRY_RUN mode)."""
        entry = AuditEntry(
            case_id=case_id,
            action_type=AuditEventType.DRY_RUN_ROUTE,
            action_detail={
                "action_type":  action_type,
                "adapter_type": adapter_type,
                "reason":       reason,
                "simulated":    True,
            },
            outcome="DRY_RUN_SIMULATED",
        )
        self._write(entry)

    def log_dry_run_action(
        self,
        case: Case,
        action_type: str,
        step: str = "",
        reason: str = "DRY_RUN mode active — external action skipped",
    ) -> None:
        """Log that an external action was skipped (DRY_RUN mode)."""
        entry = AuditEntry(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=AuditEventType.DRY_RUN_ACTION,
            action_detail={
                "action_type": action_type,
                "step":        step,
                "reason":      reason,
                "simulated":   True,
            },
            outcome="DRY_RUN_SKIPPED",
        )
        self._write(entry)

    def log_startup_validation_passed(
        self,
        service_name: str,
        tier: str,
        details: str = "",
    ) -> None:
        """Log that a startup validation check passed."""
        entry = AuditEntry(
            action_type=AuditEventType.STARTUP_VALIDATION_PASSED,
            action_detail={
                "service_name": service_name,
                "tier":         tier,
                "details":      details,
            },
            outcome="PASSED",
        )
        self._write(entry)

    def log_startup_validation_failed(
        self,
        service_name: str,
        tier: str,
        error_msg: str,
    ) -> None:
        """Log that a startup validation check failed (CRITICAL or IMPORTANT tier)."""
        entry = AuditEntry(
            action_type=AuditEventType.STARTUP_VALIDATION_FAILED,
            action_detail={
                "service_name": service_name,
                "tier":         tier,
                "error_msg":    error_msg,
            },
            outcome="FAILED",
            error_code=f"STARTUP_{tier}_FAILED",
        )
        self._write(entry)

    def log_startup_validation_warning(
        self,
        service_name: str,
        tier: str,
        warning_msg: str,
    ) -> None:
        """Log a startup validation warning (non-critical service unavailable)."""
        entry = AuditEntry(
            action_type=AuditEventType.STARTUP_VALIDATION_WARNING,
            action_detail={
                "service_name": service_name,
                "tier":         tier,
                "warning_msg":  warning_msg,
            },
            outcome="WARNING",
        )
        self._write(entry)

    def log_invariant_violation(
        self,
        invariant_name: str,
        violation_detail: str,
        severity: str = "ERROR",
        case_id: str = "",
    ) -> None:
        """Log a runtime invariant violation."""
        entry = AuditEntry(
            case_id=case_id,
            action_type=AuditEventType.INVARIANT_VIOLATION,
            action_detail={
                "invariant_name":   invariant_name,
                "violation_detail": violation_detail,
                "severity":         severity,
            },
            outcome="VIOLATION",
            error_code=f"INVARIANT_{invariant_name.upper()}",
        )
        self._write(entry)

    # ── Sprint 2.27.9: Multi-Tenant Client Resolution audit ───────────────────

    def log_client_resolved(
        self,
        ticket_id:   str,
        client_id:   str,
        client_name: str,
        domain:      str,
    ) -> None:
        """Log a successful client resolution (email domain → tenant)."""
        entry = AuditEntry(
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.CLIENT_RESOLVED,
            action_detail={
                "client_id":   client_id,
                "client_name": client_name,
                "domain":      domain,
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_unknown_client(
        self,
        ticket_id: str,
        domain:    str,
    ) -> None:
        """
        Log a failed client resolution — unknown domain.

        Per SUPPORT_OPERATIONS_BLUEPRINT Layer 1.5:
        "Stop automation, create audit event, route to human review."
        """
        entry = AuditEntry(
            ticket_id=ticket_id,
            client="UNKNOWN",
            action_type=AuditEventType.CLIENT_RESOLUTION_FAILED,
            action_detail={
                "domain": domain,
                "reason": "Domain not registered in TenantRegistry",
            },
            outcome="UNKNOWN_CLIENT",
            error_code="CLIENT_RESOLUTION_FAILED",
        )
        self._write(entry)

    def log_tenant_context_attached(
        self,
        case_id:      str,
        ticket_id:    str,
        client_id:    str,
        client_name:  str,
        environment:  str,
        tool_count:   int,
    ) -> None:
        """Log that TenantContext was successfully attached to a Case."""
        entry = AuditEntry(
            case_id=case_id,
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.TENANT_CONTEXT_ATTACHED,
            action_detail={
                "client_id":   client_id,
                "client_name": client_name,
                "environment": environment,
                "tool_count":  tool_count,
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_unknown_client_escalated(
        self,
        ticket_id: str,
        domain:    str,
        reason:    str = "Unknown client domain — routed to human review",
    ) -> None:
        """Log that a ticket was escalated due to an unresolvable client."""
        entry = AuditEntry(
            ticket_id=ticket_id,
            client="UNKNOWN",
            action_type=AuditEventType.UNKNOWN_CLIENT_ESCALATED,
            action_detail={
                "domain": domain,
                "reason": reason,
            },
            outcome="ESCALATED",
            error_code="UNKNOWN_CLIENT",
        )
        self._write(entry)

    # ── Internal write ─────────────────────────────────────────────────────────

    def _write(self, entry: AuditEntry) -> None:
        """Write an audit entry to case_audit_log. Never raises."""
        LOGGER.debug(
            "audit event=%s case=%s ticket=%s outcome=%s",
            entry.action_type.value, entry.case_id, entry.ticket_id, entry.outcome,
        )

        if self._sb is None:
            return  # log-only mode

        try:
            self._sb.table(_AUDIT_TABLE).insert(entry.to_db_row()).execute()
        except Exception as exc:
            # Log but do not re-raise — audit failure must not crash the request path
            LOGGER.error(
                "audit_write_failed event=%s case_id=%s error=%s",
                entry.action_type.value, entry.case_id, exc,
            )

    def _write_security(self, row: dict[str, Any]) -> None:
        """Write a row to security_compliance_audit. Never raises."""
        if self._sb is None:
            return

        try:
            self._sb.table(_SECURITY_TABLE).insert(row).execute()
        except Exception as exc:
            LOGGER.error(
                "security_audit_write_failed type=%s case_id=%s error=%s",
                row.get("security_event_type"), row.get("case_id"), exc,
            )

    # ── Sprint 2.28.1: Freshdesk Foundation audit methods ────────────────────

    def log_webhook_received(
        self,
        ticket_id: str,
        event_type: str,
        *,
        client_id: str = "",
        idempotency_key: str | None = None,
    ) -> None:
        """Log a Freshdesk webhook event received by the receiver."""
        entry = AuditEntry(
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.WEBHOOK_RECEIVED,
            action_detail={"event_type": event_type},
            outcome="RECEIVED",
            idempotency_key=idempotency_key,
        )
        self._write(entry)

    def log_webhook_rejected(
        self,
        ticket_id: str,
        event_type: str,
        reason: str,
        *,
        client_id: str = "",
    ) -> None:
        """Log a rejected webhook (signature failure, replay, size limit)."""
        entry = AuditEntry(
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.WEBHOOK_REJECTED,
            action_detail={"event_type": event_type, "reason": reason},
            outcome="REJECTED",
            error_code=reason,
        )
        self._write(entry)

    def log_webhook_duplicate(
        self,
        ticket_id: str,
        event_type: str,
        idempotency_key: str,
        *,
        client_id: str = "",
    ) -> None:
        """Log a duplicate webhook event (idempotency hit)."""
        entry = AuditEntry(
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.WEBHOOK_DUPLICATE,
            action_detail={"event_type": event_type},
            outcome="SKIPPED",
            idempotency_key=idempotency_key,
        )
        self._write(entry)

    def log_ticket_ingested(
        self,
        ticket_id: str,
        client_id: str,
        *,
        case_id: str = "",
        subject: str = "",
        cf_clients: str = "",
    ) -> None:
        """Log successful ticket ingestion (webhook → case created)."""
        entry = AuditEntry(
            case_id=case_id,
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.TICKET_INGESTED,
            action_detail={
                "case_id":   case_id,
                "cf_clients": cf_clients,
            },
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_customer_reply_received(
        self,
        ticket_id: str,
        client_id: str,
        *,
        case_id: str = "",
        clarification_resolved: bool = False,
    ) -> None:
        """Log a customer reply received on a ticket."""
        entry = AuditEntry(
            case_id=case_id,
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.CUSTOMER_REPLY_RECEIVED,
            action_detail={"clarification_resolved": clarification_resolved},
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_private_note_added(
        self,
        ticket_id: str,
        client_id: str,
        note_id: str,
        *,
        case_id: str = "",
    ) -> None:
        """Log a private note written to Freshdesk."""
        entry = AuditEntry(
            case_id=case_id,
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.PRIVATE_NOTE_ADDED,
            action_detail={"note_id": note_id},
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_public_reply_sent(
        self,
        ticket_id: str,
        client_id: str,
        note_id: str,
        *,
        case_id: str = "",
    ) -> None:
        """Log a public reply sent to a customer via Freshdesk."""
        entry = AuditEntry(
            case_id=case_id,
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.PUBLIC_REPLY_SENT,
            action_detail={"note_id": note_id},
            outcome="SUCCESS",
        )
        self._write(entry)

    def log_signature_failure(
        self,
        ticket_id: str,
        event_type: str,
        reason: str,
    ) -> None:
        """Log a webhook signature verification failure."""
        entry = AuditEntry(
            ticket_id=ticket_id,
            action_type=AuditEventType.SIGNATURE_FAILURE,
            action_detail={"event_type": event_type, "reason": reason},
            outcome="REJECTED",
            error_code="SIGNATURE_FAILURE",
        )
        self._write(entry)

    def log_freshdesk_api_error(
        self,
        ticket_id: str,
        operation: str,
        error_msg: str,
        *,
        client_id: str = "",
        case_id: str = "",
    ) -> None:
        """Log a Freshdesk API call error."""
        entry = AuditEntry(
            case_id=case_id,
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.FRESHDESK_API_ERROR,
            action_detail={"operation": operation, "error": error_msg},
            outcome="FAILURE",
            error_code="FRESHDESK_API_ERROR",
        )
        self._write(entry)

    def log_conversation_state_updated(
        self,
        ticket_id: str,
        client_id: str,
        new_state: str,
        *,
        case_id: str = "",
        reason: str = "",
    ) -> None:
        """Log a conversation state transition."""
        entry = AuditEntry(
            case_id=case_id,
            ticket_id=ticket_id,
            client=client_id,
            action_type=AuditEventType.CONVERSATION_STATE_UPDATED,
            action_detail={"new_state": new_state, "reason": reason},
            outcome="SUCCESS",
        )
        self._write(entry)
