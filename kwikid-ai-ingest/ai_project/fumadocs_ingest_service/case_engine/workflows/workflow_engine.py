"""
case_engine/workflows/workflow_engine.py

Sprint 2.16: WorkflowEngine — deterministic step executor.

Responsibilities:
- Select the correct playbook for a case's topic.
- Validate that all required slots are filled before execution.
- Execute the current step (CHECK_CONDITION, PROPOSE_ACTION, RESOLVE, ESCALATE).
- Advance the workflow to the next step based on step outcome.
- Emit audit events at each step boundary.
- Integrate with ActionGateway for PROPOSE_ACTION steps (proposal only — no direct execution).

Design constraints:
- No LLM. No async. No global mutable state.
- WorkflowEngine is stateless: all state lives in WorkflowExecutionResult and Case.
- PROPOSE_ACTION steps stop execution and return PAUSED — the caller resumes
  via resume_after_action() when the action gateway completes.
- Never raises: all exceptions are caught, logged, and returned as FAILED results.

Integration contract with CaseService:
  result = engine.start(case, registry, slot_values, gateway, audit)
  # case is now WORKFLOW_ACTIVE (or ACTION_PENDING / ESCALATED / RESOLVED)

  result = engine.resume_after_action(case, registry, action_request, audit)
  # called when a PROPOSE_ACTION step's action gateway action reaches terminal state
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.trace import make_trace_id, trace_log

if TYPE_CHECKING:
    from case_engine.action_gateway import ActionGateway
    from case_engine.action_models import ActionProposal, ActionRequest
    from case_engine.action_state import ActionRiskLevel
    from case_engine.actions.service import ActionProposalService
    from case_engine.audit import AuditLogger
    from case_engine.clarification.service import ClarificationService
    from case_engine.execution.service import ExecutionService
    from case_engine.investigation.service import InvestigationService
    from case_engine.knowledge.service import KnowledgeService
    from case_engine.models import Case
    from case_engine.reasoning.service import ReasoningService
    from case_engine.slot_filling.models import SlotValue
    from case_engine.workflows.playbook_registry import PlaybookRegistry

LOGGER = logging.getLogger(__name__)


class WorkflowExecutionError(RuntimeError):
    """Raised for unrecoverable errors in workflow execution setup."""


class WorkflowEngine:
    """
    Deterministic workflow step executor.

    Optionally injectable with InvestigationService (INVESTIGATE steps) and
    KnowledgeService (KNOWLEDGE_LOOKUP steps).
    All mutable state is carried in WorkflowExecutionResult returned to the caller.

    WorkflowEngine()                                  — backwards-compatible.
    WorkflowEngine(investigation_service=svc)         — Sprint 2.19 full mode.
    WorkflowEngine(investigation_service=inv,
                   knowledge_service=kb)              — Sprint 2.20 full mode.
    WorkflowEngine(investigation_service=inv,
                   knowledge_service=kb,
                   action_proposal_service=aps)       — Sprint 2.21 full mode.
    WorkflowEngine(investigation_service=inv,
                   knowledge_service=kb,
                   action_proposal_service=aps,
                   action_gateway_service=ags)        — Sprint 2.22 full mode.
    WorkflowEngine(investigation_service=inv,
                   knowledge_service=kb,
                   action_proposal_service=aps,
                   action_gateway_service=ags,
                   execution_service=exs)             — Sprint 2.23 full mode.
    WorkflowEngine(investigation_service=inv,
                   knowledge_service=kb,
                   reasoning_service=rs,
                   action_proposal_service=aps,
                   action_gateway_service=ags,
                   execution_service=exs)             — Sprint 2.24 full mode.
    WorkflowEngine(investigation_service=inv,
                   knowledge_service=kb,
                   reasoning_service=rs,
                   action_proposal_service=aps,
                   action_gateway_service=ags,
                   execution_service=exs,
                   clarification_service=cs)          — Sprint 2.25 full mode.
    """

    def __init__(
        self,
        investigation_service:   "InvestigationService | None"   = None,
        knowledge_service:       "KnowledgeService | None"        = None,
        action_proposal_service: "ActionProposalService | None"   = None,
        action_gateway_service:  "ActionGatewayService | None"    = None,
        execution_service:       "ExecutionService | None"        = None,
        reasoning_service:       "ReasoningService | None"        = None,
        clarification_service:   "ClarificationService | None"    = None,
    ) -> None:
        self._investigation_service   = investigation_service
        self._knowledge_service       = knowledge_service
        self._action_proposal_service = action_proposal_service
        self._action_gateway_service  = action_gateway_service
        self._execution_service       = execution_service
        self._reasoning_service       = reasoning_service
        self._clarification_service   = clarification_service

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(
        self,
        case: "Case",
        registry: "PlaybookRegistry",
        slot_values: dict[str, "SlotValue"],
        gateway: "ActionGateway | None" = None,
        audit: "AuditLogger | None" = None,
    ) -> WorkflowExecutionResult:
        """
        Select and start the workflow for a case.

        1. Look up playbook for case.topic.
        2. Validate required slots are filled.
        3. Check entry_conditions.
        4. Execute the first step.
        5. Return the WorkflowExecutionResult.

        Never raises. Returns FAILED result on internal error.
        """
        try:
            defn = registry.get(case.topic or "")
            if defn is None:
                return self._failed(
                    "",
                    reason=f"No playbook for topic '{case.topic}'",
                    audit=audit,
                    case=case,
                )

            # Skip upfront slot validation when the workflow starts with CLARIFY —
            # the CLARIFY step itself handles missing slots by pausing (NEEDS_CLARIFICATION).
            # Upfront validation would fail-fast before the CLARIFY step gets a chance to run.
            has_clarify_first = (
                defn.first_step() is not None
                and defn.first_step().step_type == WorkflowStepType.CLARIFY
            )
            if not has_clarify_first:
                validation_error = self._validate_slots(defn, slot_values)
                if validation_error:
                    return self._failed(
                        defn.workflow_id,
                        reason=validation_error,
                        audit=audit,
                        case=case,
                    )

            slot_context = self._build_slot_context(slot_values)
            # Entry conditions are also deferred when the first step is CLARIFY —
            # the CLARIFY step is specifically designed to collect the slots those
            # conditions depend on, so evaluating them here would always escalate.
            if not has_clarify_first:
                entry_error = self._check_conditions(defn.entry_conditions, slot_context)
                if entry_error:
                    return self._escalated(
                        defn.workflow_id,
                        reason=f"Entry condition failed: {entry_error}",
                        audit=audit,
                        case=case,
                )

            self._validate_workflow_structure(defn)

            result = WorkflowExecutionResult(
                workflow_id=defn.workflow_id,
                workflow_state=WorkflowState.RUNNING,
            )

            self._emit_workflow_started(audit, case, defn, result)

            # Sprint 2.30.1 — TRACE_WORKFLOW (Phase 6 + Phase 7 verification)
            trace_id = make_trace_id(case.ticket_id or case.case_id)
            trace_log("TRACE_WORKFLOW", trace_id,
                      case_id=case.case_id,
                      ticket_id=case.ticket_id,
                      workflow_id=defn.workflow_id,
                      topic=case.topic or "",
                      steps=len(defn.steps),
                      knowledge_svc_type=type(self._knowledge_service).__name__ if self._knowledge_service else "None",
                      reasoning_svc_wired=self._reasoning_service is not None,
                      action_gw_wired=self._action_gateway_service is not None)
            LOGGER.info(
                "VERIFY workflow_selected case_id=%s workflow_id=%s topic=%s "
                "knowledge_svc=%s reasoning=%s gateway=%s",
                case.case_id, defn.workflow_id, case.topic,
                type(self._knowledge_service).__name__ if self._knowledge_service else "None",
                self._reasoning_service is not None,
                self._action_gateway_service is not None,
            )

            first_step = defn.first_step()
            if first_step is None:
                return self._failed(
                    defn.workflow_id,
                    reason="Playbook has no steps",
                    audit=audit,
                    case=case,
                )

            return self._execute_step(first_step, defn, result, slot_context, gateway, audit, case)

        except Exception as exc:
            LOGGER.exception("workflow_engine.start failed case_id=%s error=%s", case.case_id, exc)
            return WorkflowExecutionResult(
                workflow_state=WorkflowState.FAILED,
                escalation_reason=f"internal_error: {type(exc).__name__}",
            )

    def resume_after_action(
        self,
        case: "Case",
        registry: "PlaybookRegistry",
        action: "ActionRequest",
        slot_values: dict[str, "SlotValue"],
        audit: "AuditLogger | None" = None,
    ) -> WorkflowExecutionResult:
        """
        Resume a paused workflow after an action gateway action has completed.

        Called when an ACTION_PENDING case receives notification that its
        pending action has reached a terminal state (EXECUTED, FAILED, etc.).

        1. Load current WorkflowExecutionResult from case.workflow_context.
        2. Identify which step was paused.
        3. Determine the outcome (success/failure) from action state.
        4. Navigate to the next step and execute it.
        5. Return updated WorkflowExecutionResult.

        Never raises. Returns FAILED result on internal error.
        """
        try:
            result = WorkflowExecutionResult.from_dict(case.workflow_context or {})
            defn = registry.get_by_id(result.workflow_id)
            if defn is None:
                return self._failed(
                    result.workflow_id,
                    reason=f"Playbook not found: {result.workflow_id}",
                    audit=audit,
                    case=case,
                )

            paused_step = defn.step_by_id(result.current_step_id or "")
            if paused_step is None:
                return self._failed(
                    defn.workflow_id,
                    reason=f"Cannot find paused step: {result.current_step_id}",
                    audit=audit,
                    case=case,
                )

            from case_engine.action_state import ActionState, TERMINAL_ACTION_STATES
            action_succeeded = (
                action.current_state == ActionState.EXECUTED
                and not action.is_rolled_back
            )
            action_rejected = action.current_state == ActionState.REJECTED

            # Build context that includes action result for CHECK_CONDITION steps
            slot_context = self._build_slot_context(slot_values)
            slot_context["action_result.success"] = "true" if action_succeeded else "false"
            slot_context["action_result.state"] = action.current_state.value
            if action.execution_result:
                for k, v in action.execution_result.items():
                    slot_context[f"action_result.{k}"] = str(v)

            result.pending_action_id = None
            result.workflow_state = WorkflowState.RUNNING

            # Emit WORKFLOW_RESUMED audit event
            self._emit_workflow_resumed(audit, case, defn, result, action)

            # Record the action outcome in step results
            result.record_step(
                step_id=paused_step.step_id,
                outcome="ACTION_EXECUTED" if action_succeeded else "ACTION_FAILED",
                detail={
                    "action_id":    action.action_id,
                    "action_state": action.current_state.value,
                },
            )

            # Navigate based on outcome
            if action_rejected and paused_step.on_rejection:
                next_step_id = paused_step.on_rejection
            elif action_succeeded:
                next_step_id = paused_step.on_success if paused_step.on_approval is None else paused_step.on_approval
            else:
                next_step_id = paused_step.on_failure

            return self._navigate_and_execute(
                next_step_id, defn, result, slot_context, None, audit, case
            )

        except Exception as exc:
            LOGGER.exception(
                "workflow_engine.resume_after_action failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return WorkflowExecutionResult(
                workflow_state=WorkflowState.FAILED,
                escalation_reason=f"internal_error: {type(exc).__name__}",
            )

    def resume_after_clarification(
        self,
        case: "Case",
        registry: "PlaybookRegistry",
        slot_values: dict[str, "SlotValue"],
        audit: "AuditLogger | None" = None,
    ) -> WorkflowExecutionResult:
        """
        Resume a workflow that was PAUSED at a CLARIFY step.

        Called when the customer provides missing slot values. Re-runs the CLARIFY
        step with updated slot context. The result is one of:
          CLARIFICATION_READY     → workflow continues to on_success
          CLARIFICATION_PENDING   → workflow stays PAUSED (still missing slots)
          CLARIFICATION_ESCALATE  → workflow navigates to on_failure

        Never raises. Returns FAILED result on internal error.

        Sprint 2.26 — closes the P0 clarification resume loop.
        """
        try:
            result = WorkflowExecutionResult.from_dict(case.workflow_context or {})
            defn = registry.get_by_id(result.workflow_id)
            if defn is None:
                return self._failed(
                    result.workflow_id,
                    reason=f"Playbook not found: {result.workflow_id}",
                    audit=audit,
                    case=case,
                )

            clarify_step = defn.step_by_id(result.current_step_id or "")
            if clarify_step is None:
                return self._failed(
                    defn.workflow_id,
                    reason=f"Cannot find CLARIFY step: {result.current_step_id}",
                    audit=audit,
                    case=case,
                )

            if clarify_step.step_type != WorkflowStepType.CLARIFY:
                return self._failed(
                    defn.workflow_id,
                    reason=(
                        f"resume_after_clarification called but current step is "
                        f"{clarify_step.step_type.value}, not CLARIFY"
                    ),
                    audit=audit,
                    case=case,
                )

            # Rebuild slot_context from updated slot values
            slot_context = self._build_slot_context(slot_values)

            # Emit WORKFLOW_CLARIFICATION_RESUMED audit event
            if audit and case:
                try:
                    audit.log_workflow_clarification_resumed(
                        case,
                        workflow_id=defn.workflow_id,
                        step_id=clarify_step.step_id,
                        slots_updated=list(slot_context.keys()),
                    )
                except Exception:
                    pass

            # Resume workflow state so CLARIFY step can navigate if READY
            result.workflow_state = WorkflowState.RUNNING

            # Re-run the CLARIFY step with updated context
            return self._exec_clarify(
                clarify_step, defn, result, slot_context, None, audit, case
            )

        except Exception as exc:
            LOGGER.exception(
                "workflow_engine.resume_after_clarification failed case_id=%s error=%s",
                case.case_id, exc,
            )
            return WorkflowExecutionResult(
                workflow_state=WorkflowState.FAILED,
                escalation_reason=f"internal_error: {type(exc).__name__}",
            )

    # ── Step execution ─────────────────────────────────────────────────────────

    def _execute_step(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        """Dispatch to the appropriate handler for step.step_type."""
        result.current_step_id = step.step_id

        LOGGER.info(
            "workflow_engine.execute_step case_id=%s workflow=%s step=%s type=%s",
            case.case_id, defn.workflow_id, step.step_id, step.step_type.value,
        )

        if step.step_type == WorkflowStepType.CHECK_CONDITION:
            return self._exec_check_condition(step, defn, result, slot_context, gateway, audit, case)

        if step.step_type == WorkflowStepType.PROPOSE_ACTION:
            return self._exec_propose_action(step, defn, result, slot_context, gateway, audit, case)

        if step.step_type == WorkflowStepType.RESOLVE_CASE:
            return self._exec_resolve(step, defn, result, audit, case)

        if step.step_type == WorkflowStepType.ESCALATE_CASE:
            return self._exec_escalate(step, defn, result, audit, case)

        if step.step_type == WorkflowStepType.REQUEST_APPROVAL:
            return self._exec_request_approval(step, defn, result, audit, case)

        if step.step_type == WorkflowStepType.INVESTIGATE:
            return self._exec_investigate(step, defn, result, slot_context, gateway, audit, case)

        if step.step_type == WorkflowStepType.KNOWLEDGE_LOOKUP:
            return self._exec_knowledge_lookup(step, defn, result, slot_context, gateway, audit, case)

        if step.step_type == WorkflowStepType.ACTION_GATEWAY:
            return self._exec_action_gateway(step, defn, result, slot_context, gateway, audit, case)

        if step.step_type == WorkflowStepType.REASON:
            return self._exec_reason(step, defn, result, slot_context, gateway, audit, case)

        if step.step_type == WorkflowStepType.CLARIFY:
            return self._exec_clarify(step, defn, result, slot_context, gateway, audit, case)

        if step.step_type == WorkflowStepType.EXECUTE:
            return self._exec_execute(step, defn, result, slot_context, gateway, audit, case)

        if step.step_type == WorkflowStepType.COLLECT_INFORMATION:
            # At workflow level, COLLECT_INFORMATION means slots were missing
            # This is a design-time error — should not appear if slot filling is complete
            return self._escalated(
                defn.workflow_id,
                reason=f"COLLECT_INFORMATION step '{step.step_id}' reached after slot filling",
                audit=audit,
                case=case,
                existing=result,
            )

        # Unknown step type — treat as failure
        return self._failed(
            defn.workflow_id,
            reason=f"Unknown step type: {step.step_type}",
            audit=audit,
            case=case,
            existing=result,
        )

    def _exec_check_condition(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        all_pass = all(c.evaluate(slot_context) for c in step.conditions)
        outcome = "PASS" if all_pass else "FAIL"
        result.record_step(step_id=step.step_id, outcome=outcome)
        self._emit_step_completed(audit, case, defn, step, outcome)
        next_step_id = step.on_success if all_pass else step.on_failure
        return self._navigate_and_execute(next_step_id, defn, result, slot_context, gateway, audit, case)

    def _exec_propose_action(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        # Guard 1: "Investigation Before Action" (blueprint Section 29, Principle 1)
        # When workflow contains an INVESTIGATE step, investigation_result must be present.
        workflow_has_investigate = any(
            s.step_type == WorkflowStepType.INVESTIGATE for s in defn.steps
        )
        if workflow_has_investigate and result.investigation_result is None:
            LOGGER.warning(
                "workflow_engine: PROPOSE_ACTION step=%s reached without investigation_result"
                " — workflow=%s escalating (blueprint violation)",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="BLOCKED_NO_INVESTIGATION",
                detail={"reason": "PROPOSE_ACTION requires prior INVESTIGATE step result"},
            )
            self._emit_step_completed(audit, case, defn, step, "BLOCKED_NO_INVESTIGATION")
            return self._navigate_and_execute(
                step.on_failure, defn, result, slot_context, gateway, audit, case
            )

        # Guard 2: "Knowledge Before Action" (blueprint Section 29, Principle 3)
        # When workflow contains a KNOWLEDGE_LOOKUP step, knowledge_result must be present.
        workflow_has_knowledge_lookup = any(
            s.step_type == WorkflowStepType.KNOWLEDGE_LOOKUP for s in defn.steps
        )
        if workflow_has_knowledge_lookup and result.knowledge_result is None:
            LOGGER.warning(
                "workflow_engine: PROPOSE_ACTION step=%s reached without knowledge_result"
                " — workflow=%s escalating (blueprint violation)",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="BLOCKED_NO_KNOWLEDGE_LOOKUP",
                detail={"reason": "PROPOSE_ACTION requires prior KNOWLEDGE_LOOKUP step result"},
            )
            self._emit_step_completed(audit, case, defn, step, "BLOCKED_NO_KNOWLEDGE_LOOKUP")
            return self._navigate_and_execute(
                step.on_failure, defn, result, slot_context, gateway, audit, case
            )

        # Guard 3: "Reasoning Before Action" (blueprint Principle 3) — Sprint 2.24
        # When workflow contains a REASON step, reasoning_result must be present.
        workflow_has_reason = any(
            s.step_type == WorkflowStepType.REASON for s in defn.steps
        )
        if workflow_has_reason and result.reasoning_result is None:
            LOGGER.warning(
                "workflow_engine: PROPOSE_ACTION step=%s reached without reasoning_result"
                " — workflow=%s escalating (blueprint Principle 3 violation)",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="BLOCKED_NO_REASONING",
                detail={"reason": "PROPOSE_ACTION requires prior REASON step result (blueprint Principle 3)"},
            )
            self._emit_step_completed(audit, case, defn, step, "BLOCKED_NO_REASONING")
            return self._navigate_and_execute(
                step.on_failure, defn, result, slot_context, gateway, audit, case
            )

        # Sprint 2.21: Run Action Proposal Engine before gateway interaction
        # This populates result.action_proposal_result with the ACTIONPROPOSAL bundle.
        self._run_action_proposal(step, defn, result, audit, case)

        if gateway is None:
            # No gateway available (test / offline mode) — treat as success and navigate
            LOGGER.warning(
                "workflow_engine: no gateway available for PROPOSE_ACTION step=%s — skipping",
                step.step_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="SKIPPED_NO_GATEWAY",
                detail={"step_type": "PROPOSE_ACTION"},
            )
            self._emit_step_completed(audit, case, defn, step, "SKIPPED_NO_GATEWAY")
            next_step_id = step.on_success
            return self._navigate_and_execute(next_step_id, defn, result, slot_context, gateway, audit, case)

        # Guard 4: Action type allowlist (Sprint 2.30.1 Phase 5)
        # Blocks any action type not explicitly approved in the playbook registry.
        # Prevents workflow YAML corruption or injection from reaching the gateway.
        _ALLOWED_ACTION_TYPES: frozenset[str] = frozenset({
            "otp_resend",
            "vkyc_session_reset",
            "api_callback_retry",
            "document_ocr_reprocess",
            "agent_session_refresh",
            # Rollback / compensation counterparts
            "api_callback_cancel",
            "vkyc_session_restore",
        })
        _requested_action_type = step.action_type or ""
        if _requested_action_type and _requested_action_type not in _ALLOWED_ACTION_TYPES:
            LOGGER.error(
                "workflow_engine: PROPOSE_ACTION blocked — action_type=%r not in allowlist"
                " step=%s workflow=%s (Sprint 2.30.1 Guard 4)",
                _requested_action_type, step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="BLOCKED_DISALLOWED_ACTION_TYPE",
                detail={
                    "action_type": _requested_action_type,
                    "reason": "action_type not in approved allowlist",
                },
            )
            self._emit_step_completed(audit, case, defn, step, "BLOCKED_DISALLOWED_ACTION_TYPE")
            return self._navigate_and_execute(step.on_failure, defn, result, slot_context, gateway, audit, case)

        # Build action params by substituting slot values into template
        params = self._render_params(step.action_params_template, slot_context)

        try:
            from case_engine.action_models import ActionProposal
            from case_engine.action_state import ActionRiskLevel

            proposal = ActionProposal(
                action_type=step.action_type or "",
                action_namespace=step.action_namespace or "",
                risk_level=ActionRiskLevel(step.risk_level),
                action_params=params,
                rollback_action_type=step.rollback_action_type,
                proposed_by=f"workflow:{defn.workflow_id}",
            )
            action = gateway.propose(case, proposal)
            result.pending_action_id = action.action_id
            result.record_step(
                step_id=step.step_id,
                outcome="ACTION_PROPOSED",
                detail={"action_id": action.action_id, "action_state": action.current_state.value},
            )
            self._emit_step_completed(audit, case, defn, step, "ACTION_PROPOSED")
            # Sprint 2.30.1 — TRACE_ACTION_GATEWAY
            trace_id = make_trace_id(case.ticket_id or case.case_id)
            trace_log(
                "TRACE_ACTION_GATEWAY", trace_id,
                case_id=case.case_id,
                action_id=action.action_id,
                action_type=step.action_type or "",
                risk_level=step.risk_level,
                action_state=action.current_state.value,
            )
            LOGGER.info(
                "VERIFY action_proposal case_id=%s action_id=%s action_type=%s risk=%s state=%s",
                case.case_id, action.action_id, step.action_type, step.risk_level, action.current_state.value,
            )

            # SAFE actions are auto-approved and may complete synchronously;
            # REVERSIBLE/IRREVERSIBLE go to AWAITING_APPROVAL — pause the workflow.
            from case_engine.action_state import ActionState
            if action.current_state in (ActionState.EXECUTED,):
                # Synchronous completion (SAFE action with immediate execution)
                slot_context["action_result.success"] = "true"
                slot_context["action_result.state"] = action.current_state.value
                result.pending_action_id = None
                next_step_id = step.on_approval or step.on_success
                return self._navigate_and_execute(next_step_id, defn, result, slot_context, gateway, audit, case)

            # Action needs approval or is in-flight — pause
            result.workflow_state = WorkflowState.PAUSED
            return result

        except Exception as exc:
            LOGGER.exception(
                "workflow_engine: action proposal failed step=%s error=%s",
                step.step_id, exc,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="PROPOSAL_FAILED",
                detail={"error": str(exc)[:200]},
            )
            return self._navigate_and_execute(step.on_failure, defn, result, slot_context, gateway, audit, case)

    def _exec_resolve(
        self,
        step:   WorkflowStep,
        defn:   WorkflowDefinition,
        result: WorkflowExecutionResult,
        audit:  "AuditLogger | None",
        case:   "Case",
    ) -> WorkflowExecutionResult:
        result.record_step(step_id=step.step_id, outcome="RESOLVED")
        result.workflow_state = WorkflowState.COMPLETED
        result.completed_at = datetime.now(tz=timezone.utc)
        result.resolution_note = step.description or step.name
        self._emit_step_completed(audit, case, defn, step, "RESOLVED")
        self._emit_workflow_resolved(audit, case, defn, result)
        return result

    def _exec_escalate(
        self,
        step:   WorkflowStep,
        defn:   WorkflowDefinition,
        result: WorkflowExecutionResult,
        audit:  "AuditLogger | None",
        case:   "Case",
    ) -> WorkflowExecutionResult:
        result.record_step(step_id=step.step_id, outcome="ESCALATED")
        result.workflow_state = WorkflowState.ESCALATED
        result.completed_at = datetime.now(tz=timezone.utc)
        result.escalation_reason = step.description or step.name
        self._emit_step_completed(audit, case, defn, step, "ESCALATED")
        self._emit_workflow_escalated(audit, case, defn, result)
        return result

    def _exec_request_approval(
        self,
        step:   WorkflowStep,
        defn:   WorkflowDefinition,
        result: WorkflowExecutionResult,
        audit:  "AuditLogger | None",
        case:   "Case",
    ) -> WorkflowExecutionResult:
        result.record_step(step_id=step.step_id, outcome="APPROVAL_REQUESTED")
        result.workflow_state = WorkflowState.PAUSED
        self._emit_step_completed(audit, case, defn, step, "APPROVAL_REQUESTED")
        return result

    def _exec_investigate(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        """Execute an INVESTIGATE step via InvestigationStepExecutor."""
        self._emit_investigation_started(audit, case, defn, step)

        if self._investigation_service is None:
            # No investigation service wired — log warning, escalate for safety
            LOGGER.warning(
                "workflow_engine: INVESTIGATE step=%s reached but no investigation_service"
                " wired — workflow=%s escalating",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="NO_INVESTIGATION_SERVICE",
                detail={"reason": "InvestigationService not available"},
            )
            self._emit_step_completed(audit, case, defn, step, "NO_INVESTIGATION_SERVICE")
            return self._navigate_and_execute(
                step.on_failure, defn, result, slot_context, gateway, audit, case
            )

        from case_engine.workflows.investigation_step import InvestigationStepExecutor  # noqa: PLC0415
        executor = InvestigationStepExecutor(self._investigation_service)
        inv_result, success = executor.execute(step, defn, slot_context, case)

        # Persist serialized investigation result into workflow context
        result.investigation_result = inv_result.to_dict()

        outcome = "INVESTIGATION_COMPLETED" if success else "INVESTIGATION_ESCALATED"
        result.record_step(
            step_id=step.step_id,
            outcome=outcome,
            detail={
                "result_id":   inv_result.result_id,
                "category":    inv_result.root_cause.category.value,
                "confidence":  inv_result.root_cause.confidence,
                "escalate":    inv_result.root_cause.escalate,
            },
        )
        self._emit_step_completed(audit, case, defn, step, outcome)
        self._emit_investigation_completed(audit, case, defn, step, inv_result)

        next_step_id = step.on_success if success else step.on_failure
        return self._navigate_and_execute(next_step_id, defn, result, slot_context, gateway, audit, case)

    def _exec_knowledge_lookup(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        """Execute a KNOWLEDGE_LOOKUP step via KnowledgeLookupStepExecutor."""
        self._emit_knowledge_search_started(audit, case, defn, step)

        if self._knowledge_service is None:
            LOGGER.warning(
                "workflow_engine: KNOWLEDGE_LOOKUP step=%s reached but no knowledge_service"
                " wired — workflow=%s escalating",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="NO_KNOWLEDGE_SERVICE",
                detail={"reason": "KnowledgeService not available"},
            )
            self._emit_step_completed(audit, case, defn, step, "NO_KNOWLEDGE_SERVICE")
            return self._navigate_and_execute(
                step.on_failure, defn, result, slot_context, gateway, audit, case
            )

        from case_engine.workflows.knowledge_step import KnowledgeLookupStepExecutor  # noqa: PLC0415
        executor = KnowledgeLookupStepExecutor(self._knowledge_service)
        knowledge_dict, success = executor.execute(step, defn, result, case)

        result.knowledge_result = knowledge_dict

        sop_found   = bool(knowledge_dict.get("sop_match_found", False))
        result_id   = knowledge_dict.get("result_id", "")
        top_score   = 0.0
        if knowledge_dict.get("sop_match"):
            top_score = float(knowledge_dict["sop_match"].get("relevance_score", 0.0))

        # Count RAG chunks if KnowledgeOrchestrator returned them
        rag_chunks = len((knowledge_dict.get("rag_chunks") or []))

        outcome = "KNOWLEDGE_SEARCH_COMPLETED" if success else "KNOWLEDGE_SEARCH_FAILED"
        result.record_step(
            step_id=step.step_id,
            outcome=outcome,
            detail={
                "result_id":  result_id,
                "sop_found":  sop_found,
                "top_score":  top_score,
            },
        )
        self._emit_step_completed(audit, case, defn, step, outcome)
        self._emit_knowledge_search_completed(audit, case, defn, step, knowledge_dict)

        # Sprint 2.30.1 — TRACE_KNOWLEDGE (Phase 6) + Phase 7 verification log
        trace_id = make_trace_id(case.ticket_id or case.case_id)
        trace_log("TRACE_KNOWLEDGE", trace_id,
                  case_id=case.case_id,
                  outcome=outcome,
                  sop_found=sop_found,
                  top_score=round(top_score, 3),
                  rag_chunks=rag_chunks)
        LOGGER.info(
            "VERIFY knowledge_result case_id=%s outcome=%s sop_found=%s "
            "top_score=%.3f rag_chunks=%d",
            case.case_id, outcome, sop_found, top_score, rag_chunks,
        )

        next_step_id = step.on_success if success else step.on_failure
        return self._navigate_and_execute(next_step_id, defn, result, slot_context, gateway, audit, case)

    def _exec_reason(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        """
        Execute a REASON step via ReasoningService (Sprint 2.24).

        flow_diagram: ROOTCAUSE --> HYBRIDRAG --> REASONING --> GUARDRAILS --> ACTIONPROPOSAL

        If no service wired: SKIPPED_NO_REASONING_SERVICE, navigate on_success.
        COMPLETED result with should_escalate=False --> on_success.
        BLOCKED/ERROR or should_escalate=True       --> on_failure.
        """
        if audit and case:
            try:
                audit.log_workflow_reasoning_started(
                    case,
                    workflow_id=defn.workflow_id,
                    step_id=step.step_id,
                )
            except Exception:
                pass

        if self._reasoning_service is None:
            LOGGER.warning(
                "workflow_engine: REASON step=%s reached but no reasoning_service"
                " wired — workflow=%s (no-service path, skipping)",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="SKIPPED_NO_REASONING_SERVICE",
                detail={"reason": "ReasoningService not available"},
            )
            self._emit_step_completed(audit, case, defn, step, "SKIPPED_NO_REASONING_SERVICE")
            return self._navigate_and_execute(
                step.on_success, defn, result, slot_context, gateway, audit, case
            )

        reasoning_dict = self._reasoning_service.reason(
            investigation_result=result.investigation_result,
            knowledge_result=result.knowledge_result,
            case=case,
            workflow_id=defn.workflow_id,
            step_id=step.step_id,
        )

        result.reasoning_result = reasoning_dict

        status          = reasoning_dict.get("status", "ERROR")
        should_escalate = bool(reasoning_dict.get("should_escalate", False))
        recommended     = reasoning_dict.get("recommended_action", "")
        # Sprint 2.30.1 — TRACE_REASONING (Phase 6) + Phase 7 verification
        trace_id = make_trace_id(case.ticket_id or case.case_id)
        trace_log("TRACE_REASONING", trace_id,
                  case_id=case.case_id,
                  status=status,
                  should_escalate=should_escalate,
                  recommended=recommended)
        LOGGER.info(
            "VERIFY reasoning_result case_id=%s status=%s escalate=%s recommended=%s",
            case.case_id, status, should_escalate, recommended,
        )

        if status == "BLOCKED":
            outcome  = "REASONING_BLOCKED"
            success  = False
        elif status == "COMPLETED" and not should_escalate:
            outcome  = "REASONING_COMPLETED"
            success  = True
        elif status == "COMPLETED" and should_escalate:
            outcome  = "REASONING_ESCALATE"
            success  = False
        else:
            outcome  = "REASONING_ERROR"
            success  = False

        result.record_step(
            step_id=step.step_id,
            outcome=outcome,
            detail={
                "status":             status,
                "recommended_action": recommended,
                "should_escalate":    should_escalate,
            },
        )
        self._emit_step_completed(audit, case, defn, step, outcome)

        if audit and case:
            try:
                audit.log_workflow_reasoning_completed(
                    case,
                    workflow_id=defn.workflow_id,
                    step_id=step.step_id,
                    outcome=outcome,
                    recommended_action=recommended,
                )
            except Exception:
                pass

        next_step_id = step.on_success if success else step.on_failure
        return self._navigate_and_execute(next_step_id, defn, result, slot_context, gateway, audit, case)

    def _exec_clarify(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        """
        Execute a CLARIFY step via ClarificationService (Sprint 2.25).

        flow_diagram: Missing Slots --> CLARIFICATION_ENGINE --> CLARIFY --> Resume

        If no service wired: SKIPPED_NO_CLARIFICATION_SERVICE, navigate on_success.
        READY              --> CLARIFICATION_READY --> on_success (workflow continues)
        NEEDS_CLARIFICATION --> CLARIFICATION_PENDING --> PAUSED (wait for user input)
        ESCALATE           --> CLARIFICATION_ESCALATE --> on_failure
        ERROR              --> CLARIFICATION_ERROR --> on_failure
        """
        if audit and case:
            try:
                audit.log_workflow_clarification_started(
                    case,
                    workflow_id=defn.workflow_id,
                    step_id=step.step_id,
                )
            except Exception:
                pass

        if self._clarification_service is None:
            LOGGER.warning(
                "workflow_engine: CLARIFY step=%s reached but no clarification_service"
                " wired — workflow=%s (no-service path, skipping)",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="SKIPPED_NO_CLARIFICATION_SERVICE",
                detail={"reason": "ClarificationService not available"},
            )
            self._emit_step_completed(audit, case, defn, step, "SKIPPED_NO_CLARIFICATION_SERVICE")
            return self._navigate_and_execute(
                step.on_success, defn, result, slot_context, gateway, audit, case
            )

        # Extract slot_state from case for attempt tracking
        slot_state: dict[str, Any] = {}
        if case is not None and hasattr(case, "slot_state"):
            slot_state = case.slot_state or {}

        clarification_dict = self._clarification_service.clarify(
            topic=defn.topic,
            slot_context=slot_context,
            required_slots=defn.required_slots,
            slot_state=slot_state,
            case=case,
            workflow_id=defn.workflow_id,
            step_id=step.step_id,
        )

        result.clarification_result = clarification_dict

        status = clarification_dict.get("status", "ERROR")
        ready  = bool(clarification_dict.get("ready_to_continue", False))

        if status == "READY":
            outcome = "CLARIFICATION_READY"
            result.record_step(
                step_id=step.step_id,
                outcome=outcome,
                detail={
                    "status":            status,
                    "ready_to_continue": True,
                },
            )
            self._emit_step_completed(audit, case, defn, step, outcome)
            if audit and case:
                try:
                    audit.log_workflow_clarification_completed(
                        case,
                        workflow_id=defn.workflow_id,
                        step_id=step.step_id,
                        status=outcome,
                        ready_to_continue=True,
                    )
                except Exception:
                    pass
            return self._navigate_and_execute(
                step.on_success, defn, result, slot_context, gateway, audit, case
            )

        if status == "NEEDS_CLARIFICATION":
            outcome = "CLARIFICATION_PENDING"
            result.record_step(
                step_id=step.step_id,
                outcome=outcome,
                detail={
                    "status":                status,
                    "missing_slots":         clarification_dict.get("missing_slots", []),
                    "clarification_message": clarification_dict.get("clarification_message", ""),
                },
            )
            self._emit_step_completed(audit, case, defn, step, outcome)
            if audit and case:
                try:
                    audit.log_workflow_clarification_completed(
                        case,
                        workflow_id=defn.workflow_id,
                        step_id=step.step_id,
                        status=outcome,
                        ready_to_continue=False,
                    )
                except Exception:
                    pass
            # Pause workflow — waiting for customer to provide missing slot values
            result.workflow_state = WorkflowState.PAUSED
            return result

        if status == "ESCALATE":
            outcome = "CLARIFICATION_ESCALATE"
        else:
            outcome = "CLARIFICATION_ERROR"

        result.record_step(
            step_id=step.step_id,
            outcome=outcome,
            detail={
                "status":            status,
                "missing_slots":     clarification_dict.get("missing_slots", []),
                "ready_to_continue": False,
            },
        )
        self._emit_step_completed(audit, case, defn, step, outcome)
        if audit and case:
            try:
                audit.log_workflow_clarification_completed(
                    case,
                    workflow_id=defn.workflow_id,
                    step_id=step.step_id,
                    status=outcome,
                    ready_to_continue=False,
                )
            except Exception:
                pass
        return self._navigate_and_execute(
            step.on_failure, defn, result, slot_context, gateway, audit, case
        )

    def _exec_execute(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        """
        Execute an EXECUTE step via ExecutionService.

        EXECUTE --> VERIFY --> (RECOVERY) --> RESOLUTION (Sprint 2.23).
        If no service wired: SKIPPED_NO_EXECUTION_SERVICE, navigate on_success.
        SUCCESS/ROLLBACK_COMPLETED --> on_success (COMPLETED).
        FAILED/RETRY_SCHEDULED     --> on_failure (ESCALATED).
        """
        # Guard: ACTION_GATEWAY result must be present and can_execute=True
        if result.gateway_result is not None:
            can_execute = result.gateway_result.get("can_execute", False)
            if not can_execute:
                LOGGER.warning(
                    "workflow_engine: EXECUTE step=%s reached but gateway_result.can_execute=False"
                    " — workflow=%s escalating (blueprint violation)",
                    step.step_id, defn.workflow_id,
                )
                result.record_step(
                    step_id=step.step_id,
                    outcome="BLOCKED_GATEWAY_NOT_APPROVED",
                    detail={"reason": "EXECUTE step requires gateway_result.can_execute=True"},
                )
                self._emit_step_completed(audit, case, defn, step, "BLOCKED_GATEWAY_NOT_APPROVED")
                return self._navigate_and_execute(
                    step.on_failure, defn, result, slot_context, gateway, audit, case
                )

        if self._execution_service is None:
            LOGGER.warning(
                "workflow_engine: EXECUTE step=%s reached but no execution_service wired"
                " — workflow=%s skipping",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="SKIPPED_NO_EXECUTION_SERVICE",
                detail={"step_type": "EXECUTE"},
            )
            self._emit_step_completed(audit, case, defn, step, "SKIPPED_NO_EXECUTION_SERVICE")
            return self._navigate_and_execute(
                step.on_success, defn, result, slot_context, gateway, audit, case
            )

        try:
            action_type      = step.action_type or ""
            action_namespace = step.action_namespace or ""
            risk_level       = step.risk_level or "SAFE"
            action_params    = self._render_params(step.action_params_template, slot_context)

            bundle = self._execution_service.process(
                action_type=action_type,
                action_params=action_params,
                case_id=case.case_id,
                action_namespace=action_namespace,
                risk_level=risk_level,
                audit=audit,
                case=case,
                workflow_id=defn.workflow_id,
                step_id=step.step_id,
            )
            result.execution_result = bundle.to_dict()

            from case_engine.execution.models import ExecutionStatus  # noqa: PLC0415
            success_statuses = {ExecutionStatus.SUCCESS, ExecutionStatus.ROLLBACK_COMPLETED}
            if bundle.final_status in success_statuses:
                outcome = bundle.final_status.value
                result.record_step(
                    step_id=step.step_id,
                    outcome=outcome,
                    detail={"bundle_id": bundle.bundle_id, "status": bundle.final_status.value},
                )
                self._emit_step_completed(audit, case, defn, step, outcome)
                result.workflow_state = WorkflowState.COMPLETED
                result.completed_at = datetime.now(tz=timezone.utc)
                self._emit_workflow_resolved(audit, case, defn, result)
                return self._navigate_and_execute(
                    step.on_success, defn, result, slot_context, gateway, audit, case
                )
            else:
                outcome = bundle.final_status.value
                result.record_step(
                    step_id=step.step_id,
                    outcome=outcome,
                    detail={"bundle_id": bundle.bundle_id, "status": bundle.final_status.value},
                )
                self._emit_step_completed(audit, case, defn, step, outcome)
                return self._navigate_and_execute(
                    step.on_failure, defn, result, slot_context, gateway, audit, case
                )

        except Exception as exc:
            LOGGER.exception(
                "workflow_engine._exec_execute failed step=%s workflow=%s error=%s",
                step.step_id, defn.workflow_id, exc,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="EXECUTE_EXCEPTION",
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )
            self._emit_step_completed(audit, case, defn, step, "EXECUTE_EXCEPTION")
            return self._navigate_and_execute(
                step.on_failure, defn, result, slot_context, gateway, audit, case
            )

    # ── Navigation ────────────────────────────────────────────────────────────

    def _navigate_and_execute(
        self,
        next_step_id: str,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        if next_step_id == "RESOLVE":
            result.workflow_state = WorkflowState.COMPLETED
            result.completed_at = datetime.now(tz=timezone.utc)
            self._emit_workflow_resolved(audit, case, defn, result)
            return result

        if next_step_id == "ESCALATE":
            result.workflow_state = WorkflowState.ESCALATED
            result.completed_at = datetime.now(tz=timezone.utc)
            self._emit_workflow_escalated(audit, case, defn, result)
            return result

        next_step = defn.step_by_id(next_step_id)
        if next_step is None:
            LOGGER.error(
                "workflow_engine: unknown next_step_id=%s in workflow=%s case=%s",
                next_step_id, defn.workflow_id, case.case_id,
            )
            result.workflow_state = WorkflowState.FAILED
            result.escalation_reason = f"Unknown step_id: {next_step_id}"
            return result

        return self._execute_step(next_step, defn, result, slot_context, gateway, audit, case)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _validate_workflow_structure(self, defn: WorkflowDefinition) -> None:
        """
        Warn (not fail) about structural gaps in workflow design.

        Backwards compatible: existing workflows without the full pipeline are
        warned but not blocked. Hard enforcement happens at runtime in
        _exec_propose_action (Guards 1-3).

        Sprint 2.25 enhanced: checks all sequential pipeline pairs.
        """
        has_clarify       = any(s.step_type == WorkflowStepType.CLARIFY       for s in defn.steps)
        has_investigate   = any(s.step_type == WorkflowStepType.INVESTIGATE    for s in defn.steps)
        has_knowledge     = any(s.step_type == WorkflowStepType.KNOWLEDGE_LOOKUP for s in defn.steps)
        has_reason        = any(s.step_type == WorkflowStepType.REASON         for s in defn.steps)
        has_propose       = any(s.step_type == WorkflowStepType.PROPOSE_ACTION for s in defn.steps)
        has_action_gateway = any(s.step_type == WorkflowStepType.ACTION_GATEWAY for s in defn.steps)
        has_execute       = any(s.step_type == WorkflowStepType.EXECUTE        for s in defn.steps)

        # ── Existing backwards-compat checks (preserved) ──────────────────────
        if has_propose and not has_investigate:
            LOGGER.debug(
                "workflow_structure: workflow=%s has PROPOSE_ACTION but no INVESTIGATE step"
                " (pre-Sprint-2.19 playbook — backwards compat mode)",
                defn.workflow_id,
            )
        if has_propose and has_investigate and not has_knowledge:
            LOGGER.debug(
                "workflow_structure: workflow=%s has INVESTIGATE+PROPOSE_ACTION but no"
                " KNOWLEDGE_LOOKUP step (pre-Sprint-2.20 playbook — backwards compat mode)",
                defn.workflow_id,
            )
        if has_propose and has_investigate and has_knowledge and not has_reason:
            LOGGER.debug(
                "workflow_structure: workflow=%s has INVESTIGATE+KNOWLEDGE_LOOKUP+PROPOSE_ACTION"
                " but no REASON step (pre-Sprint-2.24 playbook — backwards compat mode)",
                defn.workflow_id,
            )

        # ── Sprint 2.25 enhanced pair checks ─────────────────────────────────
        # KNOWLEDGE_LOOKUP without REASON (unusual — SOP found but not reasoned about)
        if has_knowledge and not has_reason:
            LOGGER.debug(
                "workflow_structure: workflow=%s has KNOWLEDGE_LOOKUP but no REASON step"
                " (pre-Sprint-2.24 pattern — backwards compat mode)",
                defn.workflow_id,
            )
        # REASON without PROPOSE_ACTION (unusual — reasoned but no action)
        if has_reason and not has_propose:
            LOGGER.debug(
                "workflow_structure: workflow=%s has REASON but no PROPOSE_ACTION step"
                " (unusual — investigation reasoning result is unused)",
                defn.workflow_id,
            )
        # PROPOSE_ACTION without ACTION_GATEWAY (blueprint violation)
        if has_propose and not has_action_gateway:
            LOGGER.warning(
                "workflow_structure: workflow=%s has PROPOSE_ACTION but no ACTION_GATEWAY step"
                " (blueprint violation: no action may bypass the Action Gateway)",
                defn.workflow_id,
            )
        # ACTION_GATEWAY without EXECUTE (gateway approves but nothing executes)
        if has_action_gateway and not has_execute:
            LOGGER.debug(
                "workflow_structure: workflow=%s has ACTION_GATEWAY but no EXECUTE step"
                " (gateway approves but no execution step follows — check workflow design)",
                defn.workflow_id,
            )
        # EXECUTE without ACTION_GATEWAY (blueprint violation — critical)
        if has_execute and not has_action_gateway:
            LOGGER.warning(
                "workflow_structure: workflow=%s has EXECUTE but no ACTION_GATEWAY step"
                " (blueprint violation: Section 26 — no bypass of Action Gateway)",
                defn.workflow_id,
            )

    def _validate_slots(
        self,
        defn:        WorkflowDefinition,
        slot_values: dict[str, "SlotValue"],
    ) -> str | None:
        """Return an error string if any required slot is not FILLED, else None."""
        from case_engine.slot_filling.models import SlotStatus
        for slot_name in defn.required_slots:
            sv = slot_values.get(slot_name)
            if sv is None or sv.status != SlotStatus.FILLED:
                return f"Required slot '{slot_name}' is not filled"
        return None

    def _check_conditions(
        self,
        conditions:   tuple,
        slot_context: dict[str, str],
    ) -> str | None:
        """Return description of first failing condition, or None if all pass."""
        for cond in conditions:
            if not cond.evaluate(slot_context):
                return cond.description or str(cond.field)
        return None

    def _build_slot_context(
        self,
        slot_values: dict[str, "SlotValue"],
    ) -> dict[str, str]:
        """Build a flat string dict from slot values for condition evaluation."""
        ctx: dict[str, str] = {}
        for name, sv in slot_values.items():
            if sv.value is not None:
                ctx[name] = sv.value
        return ctx

    def _render_params(
        self,
        template:     dict[str, Any],
        slot_context: dict[str, str],
    ) -> dict[str, Any]:
        """
        Substitute {slot_name} placeholders in action param template values.

        Template values that reference missing slots are rendered as empty string.
        Non-string template values (bools, ints) are passed through unchanged.
        """
        result: dict[str, Any] = {}
        for k, v in template.items():
            if isinstance(v, str):
                rendered = re.sub(
                    r"\{(\w+)\}",
                    lambda m: slot_context.get(m.group(1), ""),
                    v,
                )
                result[k] = rendered
            else:
                result[k] = v
        return result

    # ── Terminal result constructors ──────────────────────────────────────────

    def _failed(
        self,
        workflow_id: str,
        reason:      str,
        audit:       "AuditLogger | None",
        case:        "Case",
        existing:    WorkflowExecutionResult | None = None,
    ) -> WorkflowExecutionResult:
        result = existing or WorkflowExecutionResult(workflow_id=workflow_id)
        result.workflow_state = WorkflowState.FAILED
        result.escalation_reason = reason
        result.completed_at = datetime.now(tz=timezone.utc)
        LOGGER.error(
            "workflow_engine.failed case_id=%s workflow=%s reason=%s",
            case.case_id, workflow_id, reason,
        )
        self._emit_workflow_failed(audit, case, workflow_id, reason)
        return result

    def _escalated(
        self,
        workflow_id: str,
        reason:      str,
        audit:       "AuditLogger | None",
        case:        "Case",
        existing:    WorkflowExecutionResult | None = None,
    ) -> WorkflowExecutionResult:
        result = existing or WorkflowExecutionResult(workflow_id=workflow_id)
        result.workflow_state = WorkflowState.ESCALATED
        result.escalation_reason = reason
        result.completed_at = datetime.now(tz=timezone.utc)
        return result

    # ── Audit emission ────────────────────────────────────────────────────────

    def _emit_workflow_started(
        self,
        audit:  "AuditLogger | None",
        case:   "Case",
        defn:   WorkflowDefinition,
        result: WorkflowExecutionResult,
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_workflow_started(case, workflow_id=defn.workflow_id, run_id=result.run_id)
        except Exception:
            pass

    def _emit_step_completed(
        self,
        audit:   "AuditLogger | None",
        case:    "Case",
        defn:    WorkflowDefinition,
        step:    WorkflowStep,
        outcome: str,
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_workflow_step_completed(
                case,
                workflow_id=defn.workflow_id,
                step_id=step.step_id,
                step_type=step.step_type.value,
                outcome=outcome,
            )
        except Exception:
            pass

    def _emit_workflow_resumed(
        self,
        audit:  "AuditLogger | None",
        case:   "Case",
        defn:   WorkflowDefinition,
        result: WorkflowExecutionResult,
        action: "ActionRequest",
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_workflow_resumed(
                case,
                workflow_id=defn.workflow_id,
                run_id=result.run_id,
                action_id=action.action_id,
            )
        except Exception:
            pass

    def _emit_workflow_resolved(
        self,
        audit:  "AuditLogger | None",
        case:   "Case",
        defn:   WorkflowDefinition | None,
        result: WorkflowExecutionResult,
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_workflow_resolved(
                case,
                workflow_id=result.workflow_id,
                resolution_note=result.resolution_note,
            )
            audit.log_workflow_completed(
                case,
                workflow_id=result.workflow_id,
                resolution_note=result.resolution_note,
            )
        except Exception:
            pass

    def _emit_workflow_failed(
        self,
        audit:  "AuditLogger | None",
        case:   "Case",
        workflow_id: str,
        reason: str,
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_workflow_failed(case, workflow_id=workflow_id, failure_reason=reason)
        except Exception:
            pass

    def _emit_workflow_escalated(
        self,
        audit:  "AuditLogger | None",
        case:   "Case",
        defn:   WorkflowDefinition | None,
        result: WorkflowExecutionResult,
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_workflow_escalated(
                case,
                workflow_id=result.workflow_id,
                escalation_reason=result.escalation_reason,
            )
        except Exception:
            pass

    def _emit_investigation_started(
        self,
        audit: "AuditLogger | None",
        case:  "Case",
        defn:  WorkflowDefinition,
        step:  WorkflowStep,
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_workflow_investigation_started(
                case,
                workflow_id=defn.workflow_id,
                step_id=step.step_id,
                topic=defn.topic,
            )
        except Exception:
            pass

    def _emit_investigation_completed(
        self,
        audit:      "AuditLogger | None",
        case:       "Case",
        defn:       WorkflowDefinition,
        step:       WorkflowStep,
        inv_result: "object",  # InvestigationResult — avoid circular import at runtime
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_workflow_investigation_completed(
                case,
                workflow_id=defn.workflow_id,
                step_id=step.step_id,
                category=inv_result.root_cause.category.value,  # type: ignore[attr-defined]
                confidence=inv_result.root_cause.confidence,  # type: ignore[attr-defined]
                escalate=inv_result.root_cause.escalate,  # type: ignore[attr-defined]
                result_id=inv_result.result_id,  # type: ignore[attr-defined]
            )
        except Exception:
            pass

    def _exec_action_gateway(
        self,
        step:         WorkflowStep,
        defn:         WorkflowDefinition,
        result:       WorkflowExecutionResult,
        slot_context: dict[str, str],
        gateway:      "ActionGateway | None",
        audit:        "AuditLogger | None",
        case:         "Case",
    ) -> WorkflowExecutionResult:
        """
        Execute an ACTION_GATEWAY step.

        Reads result.action_proposal_result (from a preceding PROPOSE_ACTION step)
        and runs it through ActionGatewayService: ACTIONGW → RISKCHECK → APPROVAL.
        Stores the gateway_result in WorkflowExecutionResult.

        Status routing:
          APPROVED         → on_success (can proceed to executor)
          PENDING_APPROVAL → PAUSED (await human decision)
          BLOCKED          → on_failure (validation failed — escalate)
          ERROR            → on_failure (internal error — escalate)

        If no ActionGatewayService is wired, logs a warning and navigates on_success
        for backwards compatibility.

        Never raises.
        """
        if self._action_gateway_service is None:
            LOGGER.warning(
                "workflow_engine: no action_gateway_service wired for ACTION_GATEWAY step=%s"
                " workflow=%s — skipping gateway (backwards compat)",
                step.step_id, defn.workflow_id,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="SKIPPED_NO_GATEWAY_SERVICE",
                detail={"step_type": "ACTION_GATEWAY"},
            )
            self._emit_step_completed(audit, case, defn, step, "SKIPPED_NO_GATEWAY_SERVICE")
            return self._navigate_and_execute(step.on_success, defn, result, slot_context, gateway, audit, case)

        try:
            gateway_dict = self._action_gateway_service.process(
                topic=defn.topic,
                action_proposal_result=result.action_proposal_result,
                investigation_result=result.investigation_result,
                knowledge_result=result.knowledge_result,
                case=case,
                workflow_id=defn.workflow_id,
                step_id=step.step_id,
            )
            result.gateway_result = gateway_dict
            gw_status = gateway_dict.get("status", "ERROR")

            LOGGER.info(
                "workflow_engine: ACTION_GATEWAY step=%s workflow=%s status=%s can_execute=%s",
                step.step_id, defn.workflow_id, gw_status, gateway_dict.get("can_execute"),
            )

            if gw_status == "APPROVED":
                result.record_step(
                    step_id=step.step_id,
                    outcome="GATEWAY_APPROVED",
                    detail={"status": gw_status, "risk_level": gateway_dict.get("risk_level")},
                )
                self._emit_step_completed(audit, case, defn, step, "GATEWAY_APPROVED")
                return self._navigate_and_execute(step.on_success, defn, result, slot_context, gateway, audit, case)

            if gw_status == "PENDING_APPROVAL":
                result.record_step(
                    step_id=step.step_id,
                    outcome="GATEWAY_PENDING_APPROVAL",
                    detail={"status": gw_status, "risk_level": gateway_dict.get("risk_level")},
                )
                self._emit_step_completed(audit, case, defn, step, "GATEWAY_PENDING_APPROVAL")
                result.workflow_state = WorkflowState.PAUSED
                return result

            # BLOCKED or ERROR → on_failure
            LOGGER.warning(
                "workflow_engine: ACTION_GATEWAY step=%s workflow=%s gw_status=%s — escalating",
                step.step_id, defn.workflow_id, gw_status,
            )
            result.record_step(
                step_id=step.step_id,
                outcome=f"GATEWAY_{gw_status}",
                detail={"status": gw_status, "block_reason": gateway_dict.get("block_reason")},
            )
            self._emit_step_completed(audit, case, defn, step, f"GATEWAY_{gw_status}")
            return self._navigate_and_execute(step.on_failure, defn, result, slot_context, gateway, audit, case)

        except Exception as exc:
            LOGGER.exception(
                "workflow_engine: ACTION_GATEWAY step=%s failed error=%s",
                step.step_id, exc,
            )
            result.record_step(
                step_id=step.step_id,
                outcome="GATEWAY_EXCEPTION",
                detail={"error": str(exc)[:200]},
            )
            self._emit_step_completed(audit, case, defn, step, "GATEWAY_EXCEPTION")
            return self._navigate_and_execute(step.on_failure, defn, result, slot_context, gateway, audit, case)

    def _run_action_proposal(
        self,
        step:   WorkflowStep,
        defn:   WorkflowDefinition,
        result: WorkflowExecutionResult,
        audit:  "AuditLogger | None",
        case:   "Case",
    ) -> None:
        """
        Run ActionProposalService if wired, store result in WorkflowExecutionResult.

        Called at the start of every PROPOSE_ACTION step (before gateway interaction).
        If no service is wired, logs a debug message and returns silently — the gateway
        interaction can still proceed so backwards compatibility is maintained.
        Never raises.
        """
        if self._action_proposal_service is None:
            LOGGER.debug(
                "workflow_engine: no action_proposal_service wired for PROPOSE_ACTION step=%s"
                " workflow=%s — skipping proposal engine (backwards compat)",
                step.step_id, defn.workflow_id,
            )
            return
        try:
            proposal_dict = self._action_proposal_service.propose(
                topic=defn.topic,
                investigation_result=result.investigation_result,
                knowledge_result=result.knowledge_result,
                reasoning_result=result.reasoning_result,
                case=case,
                workflow_id=defn.workflow_id,
                step_id=step.step_id,
            )
            result.action_proposal_result = proposal_dict
            LOGGER.info(
                "workflow_engine: action_proposal_result stored step=%s status=%s",
                step.step_id, proposal_dict.get("status"),
            )
        except Exception:
            LOGGER.exception(
                "workflow_engine: _run_action_proposal failed step=%s workflow=%s",
                step.step_id, defn.workflow_id,
            )

    def _emit_knowledge_search_started(
        self,
        audit: "AuditLogger | None",
        case:  "Case",
        defn:  WorkflowDefinition,
        step:  WorkflowStep,
    ) -> None:
        if audit is None:
            return
        try:
            audit.log_knowledge_search_started(
                case,
                workflow_id=defn.workflow_id,
                step_id=step.step_id,
                topic=defn.topic,
            )
        except Exception:
            pass

    def _emit_knowledge_search_completed(
        self,
        audit:          "AuditLogger | None",
        case:           "Case",
        defn:           WorkflowDefinition,
        step:           WorkflowStep,
        knowledge_dict: dict[str, Any],
    ) -> None:
        if audit is None:
            return
        try:
            result_id   = knowledge_dict.get("result_id", "")
            sop_found   = bool(knowledge_dict.get("sop_match_found", False))
            top_score   = 0.0
            sop_match   = knowledge_dict.get("sop_match") or {}
            if isinstance(sop_match, dict):
                top_score = float(sop_match.get("relevance_score", 0.0))

            if sop_found:
                entry_id    = (sop_match.get("entry") or {}).get("entry_id", "")
                entry_title = (sop_match.get("entry") or {}).get("title", "")
                audit.log_sop_match_found(
                    case,
                    workflow_id=defn.workflow_id,
                    step_id=step.step_id,
                    entry_id=entry_id,
                    entry_title=entry_title,
                    relevance_score=top_score,
                )
            else:
                audit.log_sop_match_not_found(
                    case,
                    workflow_id=defn.workflow_id,
                    step_id=step.step_id,
                    topic=defn.topic,
                )
            audit.log_knowledge_search_completed(
                case,
                workflow_id=defn.workflow_id,
                step_id=step.step_id,
                result_id=result_id,
                sop_match_found=sop_found,
                top_score=top_score,
            )
        except Exception:
            pass
