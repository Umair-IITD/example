"""
tests/test_sprint216_workflow_engine.py

Sprint 2.16: WorkflowEngine deterministic execution tests.

Scenarios:
  1.  start_no_playbook_for_topic → FAILED
  2.  start_missing_required_slot → FAILED
  3.  start_check_condition_pass → navigates to on_success
  4.  start_check_condition_fail → navigates to on_failure
  5.  start_resolves_case        → COMPLETED
  6.  start_escalates_case       → ESCALATED
  7.  start_no_gateway_skips_propose_action → SKIPPED_NO_GATEWAY, continues
  8.  start_with_gateway_pauses_on_reversible_action → PAUSED
  9.  start_with_gateway_auto_executes_safe_action → COMPLETED (sync)
  10. resume_after_action_success → continues to next step → COMPLETED
  11. resume_after_action_failure → navigates to on_failure
  12. resume_after_action_rejected → navigates to on_rejection
  13. render_params_substitutes_slot_values
  14. render_params_missing_slot_becomes_empty_string
  15. render_params_non_string_values_passthrough
  16. entry_conditions_fail → ESCALATED before first step
  17. request_approval_step_pauses_workflow
  18. collect_information_step_escalates
  19. audit_emissions_do_not_raise_on_none_audit
  20. audit_emissions_do_not_raise_on_broken_audit
  21. step_results_recorded_per_step
  22. navigate_to_resolve_sentinel → COMPLETED
  23. navigate_to_escalate_sentinel → ESCALATED
  24. navigate_to_unknown_step_id → FAILED
  25. nested_check_condition_chain
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from case_engine.models import Case
from case_engine.slot_filling.models import SlotStatus, SlotValue
from case_engine.workflows.models import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.playbook_registry import PlaybookRegistry
from case_engine.workflows.workflow_engine import WorkflowEngine


# ── Fake helpers ──────────────────────────────────────────────────────────────

def _case(topic: str = "VKYC_Session_Failure") -> Case:
    c = Case(ticket_id="TKT-001", client="unity_bank")
    c.topic = topic
    # Populate workflow_context so resume_after_action has something to read from
    c.workflow_context = {}
    return c


def _slot(name: str, value: str | None, status: SlotStatus = SlotStatus.FILLED) -> SlotValue:
    return SlotValue(slot_name=name, status=status, value=value)


def _filled_slots(**kwargs: str) -> dict[str, SlotValue]:
    return {name: _slot(name, value) for name, value in kwargs.items()}


def _empty_slot(name: str) -> SlotValue:
    return SlotValue(slot_name=name, status=SlotStatus.EMPTY, value=None)


def _make_resolve_step(step_id: str = "resolve") -> WorkflowStep:
    return WorkflowStep(
        step_index=0, step_id=step_id,
        step_type=WorkflowStepType.RESOLVE_CASE,
        name="Resolve", on_success="RESOLVE", on_failure="ESCALATE",
    )


def _make_escalate_step(step_id: str = "esc") -> WorkflowStep:
    return WorkflowStep(
        step_index=0, step_id=step_id,
        step_type=WorkflowStepType.ESCALATE_CASE,
        name="Escalate", on_success="ESCALATE", on_failure="ESCALATE",
    )


def _make_check_step(
    step_id: str,
    conditions: tuple,
    on_success: str = "RESOLVE",
    on_failure: str = "ESCALATE",
    index: int = 0,
) -> WorkflowStep:
    return WorkflowStep(
        step_index=index, step_id=step_id,
        step_type=WorkflowStepType.CHECK_CONDITION,
        name="Check", conditions=conditions,
        on_success=on_success, on_failure=on_failure,
    )


def _make_propose_step(
    step_id: str,
    risk_level: str = "REVERSIBLE",
    rollback: str | None = "restore_action",
    on_success: str = "RESOLVE",
    on_failure: str = "ESCALATE",
    on_approval: str | None = None,
    on_rejection: str | None = None,
    index: int = 0,
) -> WorkflowStep:
    return WorkflowStep(
        step_index=index, step_id=step_id,
        step_type=WorkflowStepType.PROPOSE_ACTION,
        name="Propose",
        action_type="reset_session",
        action_namespace="kwikid.vkyc",
        risk_level=risk_level,
        rollback_action_type=rollback if risk_level == "REVERSIBLE" else None,
        action_params_template={"session_id": "{session_id}"},
        on_success=on_success, on_failure=on_failure,
        on_approval=on_approval, on_rejection=on_rejection,
    )


def _make_registry_single(topic: str, steps: tuple) -> PlaybookRegistry:
    defn = WorkflowDefinition(
        workflow_id=f"test_{topic.lower()}_v1",
        topic=topic,
        version="1.0",
        name=f"Test {topic}",
        required_slots=(),
        steps=steps,
    )
    return PlaybookRegistry([defn])


def _engine() -> WorkflowEngine:
    return WorkflowEngine()


# ── Fake gateway ──────────────────────────────────────────────────────────────

from case_engine.action_models import ActionProposal, ActionRequest
from case_engine.action_state import ActionRiskLevel, ActionState


class FakeGateway:
    """Minimal gateway that returns an action in the specified state."""

    def __init__(self, return_state: ActionState = ActionState.AWAITING_APPROVAL) -> None:
        self._state = return_state
        self.proposed: list[ActionProposal] = []

    def propose(self, case: Case, proposal: ActionProposal) -> ActionRequest:
        self.proposed.append(proposal)
        action = ActionRequest(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=proposal.action_type,
            action_namespace=proposal.action_namespace,
            risk_level=ActionRiskLevel(proposal.risk_level.value),
            idempotency_key=uuid.uuid4().hex,
            current_state=self._state,
        )
        return action


class BrokenGateway:
    """Gateway that always raises."""

    def propose(self, case: Case, proposal: ActionProposal) -> ActionRequest:
        raise RuntimeError("gateway_exploded")


# ── Test 1: no playbook for topic ─────────────────────────────────────────────

class TestStartNoPlaybook:
    def test_returns_failed_state(self):
        engine = _engine()
        registry = PlaybookRegistry([])
        case = _case(topic="UNKNOWN_TOPIC")
        result = engine.start(case, registry, {})
        assert result.workflow_state == WorkflowState.FAILED
        assert "UNKNOWN_TOPIC" in result.escalation_reason

    def test_failed_result_is_terminal(self):
        engine = _engine()
        registry = PlaybookRegistry([])
        case = _case("MISSING")
        result = engine.start(case, registry, {})
        assert result.is_terminal()


# ── Test 2: missing required slot ─────────────────────────────────────────────

class TestStartMissingRequiredSlot:
    def test_required_slot_not_filled_returns_failed(self):
        resolve = _make_resolve_step()
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T",
            required_slots=("session_id",),
            steps=(resolve,),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        slots = {"session_id": _empty_slot("session_id")}
        result = _engine().start(case, registry, slots)
        assert result.workflow_state == WorkflowState.FAILED
        assert "session_id" in result.escalation_reason

    def test_slot_missing_entirely_returns_failed(self):
        resolve = _make_resolve_step()
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T",
            required_slots=("session_id",),
            steps=(resolve,),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        result = _engine().start(case, registry, {})  # empty slots
        assert result.workflow_state == WorkflowState.FAILED


# ── Test 3: CHECK_CONDITION pass → on_success ─────────────────────────────────

class TestStartCheckConditionPass:
    def test_pass_navigates_to_on_success_resolve(self):
        resolve = _make_resolve_step("do_resolve")
        cond = WorkflowCondition(field="session_id", operator="exists")
        check = _make_check_step("check", (cond,), on_success="do_resolve", on_failure="ESCALATE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(check, resolve),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        slots = _filled_slots(session_id="KID-001")
        result = _engine().start(case, registry, slots)
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_step_result_recorded_as_pass(self):
        resolve = _make_resolve_step("do_resolve")
        cond = WorkflowCondition(field="session_id", operator="exists")
        check = _make_check_step("check", (cond,), on_success="do_resolve")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(check, resolve),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        result = _engine().start(case, registry, _filled_slots(session_id="KID-001"))
        outcomes = [r["outcome"] for r in result.step_results]
        assert "PASS" in outcomes


# ── Test 4: CHECK_CONDITION fail → on_failure ─────────────────────────────────

class TestStartCheckConditionFail:
    def test_fail_navigates_to_escalate(self):
        cond = WorkflowCondition(field="session_id", operator="eq", value="SPECIFIC")
        check = _make_check_step("check", (cond,), on_success="RESOLVE", on_failure="ESCALATE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(check,),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        result = _engine().start(case, registry, _filled_slots(session_id="WRONG"))
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_step_result_recorded_as_fail(self):
        cond = WorkflowCondition(field="session_id", operator="eq", value="SPECIFIC")
        check = _make_check_step("check", (cond,), on_failure="ESCALATE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(check,),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        result = _engine().start(case, registry, _filled_slots(session_id="WRONG"))
        assert result.step_results[0]["outcome"] == "FAIL"


# ── Test 5: RESOLVE_CASE → COMPLETED ─────────────────────────────────────────

class TestStartResolvesCase:
    def test_completed_state(self):
        resolve = _make_resolve_step()
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(resolve,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_completed_at_set(self):
        resolve = _make_resolve_step()
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(resolve,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.completed_at is not None

    def test_resolution_note_set(self):
        resolve = WorkflowStep(
            step_index=0, step_id="resolve",
            step_type=WorkflowStepType.RESOLVE_CASE,
            name="Resolve", description="Session reset succeeded",
            on_success="RESOLVE", on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(resolve,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.resolution_note == "Session reset succeeded"


# ── Test 6: ESCALATE_CASE → ESCALATED ────────────────────────────────────────

class TestStartEscalatesCase:
    def test_escalated_state(self):
        esc = _make_escalate_step()
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(esc,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_escalation_reason_set(self):
        esc = WorkflowStep(
            step_index=0, step_id="esc",
            step_type=WorkflowStepType.ESCALATE_CASE,
            name="Escalate", description="Cannot auto-resolve",
            on_success="ESCALATE", on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(esc,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.escalation_reason == "Cannot auto-resolve"


# ── Test 7: no gateway → SKIPPED_NO_GATEWAY, continues ───────────────────────

class TestStartNoGatewaySkipsPropose:
    def test_skipped_and_continues_to_resolve(self):
        propose = _make_propose_step("prop", on_success="RESOLVE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {}, gateway=None)
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_skipped_outcome_recorded(self):
        propose = _make_propose_step("prop", on_success="RESOLVE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {}, gateway=None)
        outcomes = [r["outcome"] for r in result.step_results]
        assert "SKIPPED_NO_GATEWAY" in outcomes


# ── Test 8: gateway reversible action → PAUSED ───────────────────────────────

class TestStartGatewayPausesOnReversible:
    def test_workflow_pauses(self):
        gateway = FakeGateway(ActionState.AWAITING_APPROVAL)
        propose = _make_propose_step("prop", risk_level="REVERSIBLE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {}, gateway=gateway)
        assert result.workflow_state == WorkflowState.PAUSED

    def test_pending_action_id_set(self):
        gateway = FakeGateway(ActionState.AWAITING_APPROVAL)
        propose = _make_propose_step("prop", risk_level="REVERSIBLE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {}, gateway=gateway)
        assert result.pending_action_id is not None

    def test_gateway_proposal_called_with_correct_type(self):
        gateway = FakeGateway(ActionState.AWAITING_APPROVAL)
        propose = _make_propose_step("prop", risk_level="REVERSIBLE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose,),
        )
        _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {}, gateway=gateway)
        assert len(gateway.proposed) == 1
        assert gateway.proposed[0].action_type == "reset_session"


# ── Test 9: gateway safe action executes synchronously → COMPLETED ────────────

class TestStartGatewaySafeAutoExecutes:
    def test_safe_action_completes_workflow(self):
        gateway = FakeGateway(ActionState.EXECUTED)
        resolve = _make_resolve_step("do_resolve")
        propose = _make_propose_step("prop", risk_level="SAFE", rollback=None, on_success="do_resolve")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose, resolve),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {}, gateway=gateway)
        assert result.workflow_state == WorkflowState.COMPLETED


# ── Test 10: resume after action success ──────────────────────────────────────

class TestResumeAfterActionSuccess:
    def _setup(self):
        """Start a workflow that pauses, return case + registry + result."""
        gateway = FakeGateway(ActionState.AWAITING_APPROVAL)
        resolve = _make_resolve_step("do_resolve")
        propose = _make_propose_step("prop", risk_level="REVERSIBLE",
                                     on_success="do_resolve", on_approval="do_resolve")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose, resolve),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        start_result = _engine().start(case, registry, {}, gateway=gateway)
        assert start_result.workflow_state == WorkflowState.PAUSED
        case.workflow_context = start_result.to_dict()
        return case, registry, gateway.proposed[0].action_type

    def test_resumed_workflow_completes(self):
        case, registry, _ = self._setup()
        # Simulate the action completing successfully
        action = ActionRequest(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type="reset_session",
            action_namespace="kwikid.vkyc",
            risk_level=ActionRiskLevel.REVERSIBLE,
            idempotency_key=uuid.uuid4().hex,
            current_state=ActionState.EXECUTED,
        )
        result = _engine().resume_after_action(case, registry, action, {})
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_resumed_result_has_action_executed_step(self):
        case, registry, _ = self._setup()
        action = ActionRequest(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type="reset_session",
            action_namespace="kwikid.vkyc",
            risk_level=ActionRiskLevel.REVERSIBLE,
            idempotency_key=uuid.uuid4().hex,
            current_state=ActionState.EXECUTED,
        )
        result = _engine().resume_after_action(case, registry, action, {})
        outcomes = [r["outcome"] for r in result.step_results]
        assert "ACTION_EXECUTED" in outcomes


# ── Test 11: resume after action failure ──────────────────────────────────────

class TestResumeAfterActionFailure:
    def test_failed_action_navigates_to_on_failure(self):
        gateway = FakeGateway(ActionState.AWAITING_APPROVAL)
        esc = _make_escalate_step("do_escalate")
        propose = _make_propose_step("prop", risk_level="REVERSIBLE", on_failure="do_escalate")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose, esc),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        start_result = _engine().start(case, registry, {}, gateway=gateway)
        case.workflow_context = start_result.to_dict()

        action = ActionRequest(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type="reset_session",
            action_namespace="kwikid.vkyc",
            risk_level=ActionRiskLevel.REVERSIBLE,
            idempotency_key=uuid.uuid4().hex,
            current_state=ActionState.FAILED,
        )
        result = _engine().resume_after_action(case, registry, action, {})
        assert result.workflow_state == WorkflowState.ESCALATED


# ── Test 12: resume after action rejection ────────────────────────────────────

class TestResumeAfterActionRejection:
    def test_rejected_action_navigates_to_on_rejection(self):
        gateway = FakeGateway(ActionState.AWAITING_APPROVAL)
        esc = _make_escalate_step("do_reject_esc")
        propose = _make_propose_step("prop", risk_level="REVERSIBLE",
                                     on_rejection="do_reject_esc")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(propose, esc),
        )
        registry = PlaybookRegistry([defn])
        case = _case("Topic_A")
        start_result = _engine().start(case, registry, {}, gateway=gateway)
        case.workflow_context = start_result.to_dict()

        action = ActionRequest(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type="reset_session",
            action_namespace="kwikid.vkyc",
            risk_level=ActionRiskLevel.REVERSIBLE,
            idempotency_key=uuid.uuid4().hex,
            current_state=ActionState.REJECTED,
        )
        result = _engine().resume_after_action(case, registry, action, {})
        assert result.workflow_state == WorkflowState.ESCALATED


# ── Test 13: render_params substitutes slot values ───────────────────────────

class TestRenderParams:
    def test_substitutes_single_placeholder(self):
        engine = _engine()
        template = {"session_id": "{session_id}"}
        context = {"session_id": "KID-001"}
        result = engine._render_params(template, context)
        assert result["session_id"] == "KID-001"

    def test_substitutes_multiple_placeholders(self):
        engine = _engine()
        template = {"session_id": "{session_id}", "phone": "{phone_number}"}
        context = {"session_id": "KID-001", "phone_number": "+91-9999"}
        result = engine._render_params(template, context)
        assert result["session_id"] == "KID-001"
        assert result["phone"] == "+91-9999"

    def test_composite_template_substitution(self):
        engine = _engine()
        template = {"message": "Reset {session_id} for {phone_number}"}
        context = {"session_id": "KID-001", "phone_number": "+91-9999"}
        result = engine._render_params(template, context)
        assert result["message"] == "Reset KID-001 for +91-9999"


# ── Test 14: render_params missing slot → empty string ───────────────────────

class TestRenderParamsMissingSlot:
    def test_missing_slot_renders_as_empty_string(self):
        engine = _engine()
        template = {"session_id": "{session_id}"}
        result = engine._render_params(template, {})
        assert result["session_id"] == ""


# ── Test 15: render_params non-string values passthrough ─────────────────────

class TestRenderParamsNonString:
    def test_bool_passthrough(self):
        engine = _engine()
        template = {"reset_immediately": True}
        result = engine._render_params(template, {})
        assert result["reset_immediately"] is True

    def test_int_passthrough(self):
        engine = _engine()
        template = {"max_retries": 3}
        result = engine._render_params(template, {})
        assert result["max_retries"] == 3


# ── Test 16: entry conditions fail → ESCALATED before first step ──────────────

class TestEntryConditionsFail:
    def test_failed_entry_condition_escalates(self):
        cond = WorkflowCondition(field="session_id", operator="exists")
        resolve = _make_resolve_step()
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T",
            entry_conditions=(cond,),
            steps=(resolve,),
        )
        # session_id not in slot context
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.workflow_state == WorkflowState.ESCALATED
        assert "Entry condition failed" in result.escalation_reason


# ── Test 17: REQUEST_APPROVAL pauses ─────────────────────────────────────────

class TestRequestApprovalPauses:
    def test_approval_step_pauses(self):
        approval_step = WorkflowStep(
            step_index=0, step_id="request_approval",
            step_type=WorkflowStepType.REQUEST_APPROVAL,
            name="Approval", on_success="RESOLVE", on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(approval_step,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.workflow_state == WorkflowState.PAUSED

    def test_approval_step_records_approval_requested(self):
        approval_step = WorkflowStep(
            step_index=0, step_id="request_approval",
            step_type=WorkflowStepType.REQUEST_APPROVAL,
            name="Approval", on_success="RESOLVE", on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(approval_step,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.step_results[0]["outcome"] == "APPROVAL_REQUESTED"


# ── Test 18: COLLECT_INFORMATION escalates ───────────────────────────────────

class TestCollectInformationEscalates:
    def test_collect_info_step_escalates(self):
        collect = WorkflowStep(
            step_index=0, step_id="collect_info",
            step_type=WorkflowStepType.COLLECT_INFORMATION,
            name="Collect", on_success="RESOLVE", on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(collect,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {})
        assert result.workflow_state == WorkflowState.ESCALATED


# ── Test 19: audit emissions do not raise on None ────────────────────────────

class TestAuditNone:
    def test_no_error_with_none_audit(self):
        resolve = _make_resolve_step()
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(resolve,),
        )
        # Must not raise even with audit=None
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {}, audit=None)
        assert result.workflow_state == WorkflowState.COMPLETED


# ── Test 20: audit emissions do not raise on broken audit ────────────────────

class TestAuditBroken:
    def test_broken_audit_does_not_propagate(self):
        class BrokenAudit:
            def log_workflow_started(self, *a, **kw): raise RuntimeError("audit boom")
            def log_workflow_step_completed(self, *a, **kw): raise RuntimeError("audit boom")
            def log_workflow_resolved(self, *a, **kw): raise RuntimeError("audit boom")
            def log_workflow_escalated(self, *a, **kw): raise RuntimeError("audit boom")

        resolve = _make_resolve_step()
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(resolve,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]), {}, audit=BrokenAudit())
        assert result.workflow_state == WorkflowState.COMPLETED


# ── Test 21: step results recorded per step ───────────────────────────────────

class TestStepResultsRecorded:
    def test_multi_step_all_recorded(self):
        cond = WorkflowCondition(field="session_id", operator="exists")
        resolve = _make_resolve_step("do_resolve")
        check = _make_check_step("check", (cond,), on_success="do_resolve")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(check, resolve),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]),
                                 _filled_slots(session_id="KID-001"))
        assert len(result.step_results) == 2
        assert result.step_results[0]["step_id"] == "check"
        assert result.step_results[1]["step_id"] == "do_resolve"


# ── Test 22: navigate to RESOLVE sentinel ────────────────────────────────────

class TestNavigateToResolveSentinel:
    def test_resolve_sentinel_completes(self):
        cond = WorkflowCondition(field="session_id", operator="exists")
        check = _make_check_step("check", (cond,), on_success="RESOLVE", on_failure="ESCALATE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(check,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]),
                                 _filled_slots(session_id="KID-001"))
        assert result.workflow_state == WorkflowState.COMPLETED


# ── Test 23: navigate to ESCALATE sentinel ────────────────────────────────────

class TestNavigateToEscalateSentinel:
    def test_escalate_sentinel_escalates(self):
        cond = WorkflowCondition(field="session_id", operator="eq", value="SPECIFIC")
        check = _make_check_step("check", (cond,), on_success="RESOLVE", on_failure="ESCALATE")
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(check,),
        )
        result = _engine().start(_case("Topic_A"), PlaybookRegistry([defn]),
                                 _filled_slots(session_id="WRONG"))
        assert result.workflow_state == WorkflowState.ESCALATED


# ── Test 24: navigate to unknown step_id → FAILED ────────────────────────────

class TestNavigateUnknownStepId:
    def test_unknown_step_id_returns_failed(self):
        # Manually craft a defn with a step that navigates to a non-existent step
        # We bypass registry validation by constructing directly
        check = WorkflowStep(
            step_index=0, step_id="check",
            step_type=WorkflowStepType.CHECK_CONDITION,
            name="Check",
            conditions=(WorkflowCondition(field="x", operator="exists"),),
            on_success="nonexistent_step_id",
            on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T", steps=(check,),
        )
        registry = PlaybookRegistry([defn])
        result = _engine().start(_case("Topic_A"), registry, _filled_slots(x="val"))
        assert result.workflow_state == WorkflowState.FAILED


# ── Test 25: nested check condition chain ─────────────────────────────────────

class TestNestedCheckConditionChain:
    def test_two_check_steps_then_resolve(self):
        resolve = _make_resolve_step("do_resolve")
        esc = _make_escalate_step("do_escalate")
        cond2 = WorkflowCondition(field="phone_number", operator="exists")
        check2 = _make_check_step("check2", (cond2,),
                                   on_success="do_resolve", on_failure="do_escalate", index=1)
        cond1 = WorkflowCondition(field="session_id", operator="exists")
        check1 = _make_check_step("check1", (cond1,),
                                   on_success="check2", on_failure="do_escalate", index=0)
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T",
            steps=(check1, check2, resolve, esc),
        )
        result = _engine().start(
            _case("Topic_A"), PlaybookRegistry([defn]),
            _filled_slots(session_id="KID-001", phone_number="+91-9999"),
        )
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_second_check_fails_escalates(self):
        resolve = _make_resolve_step("do_resolve")
        esc = _make_escalate_step("do_escalate")
        cond2 = WorkflowCondition(field="phone_number", operator="eq", value="EXPECTED")
        check2 = _make_check_step("check2", (cond2,),
                                   on_success="do_resolve", on_failure="do_escalate", index=1)
        cond1 = WorkflowCondition(field="session_id", operator="exists")
        check1 = _make_check_step("check1", (cond1,),
                                   on_success="check2", on_failure="do_escalate", index=0)
        defn = WorkflowDefinition(
            workflow_id="t_v1", topic="Topic_A",
            version="1.0", name="T",
            steps=(check1, check2, resolve, esc),
        )
        result = _engine().start(
            _case("Topic_A"), PlaybookRegistry([defn]),
            _filled_slots(session_id="KID-001", phone_number="WRONG"),
        )
        assert result.workflow_state == WorkflowState.ESCALATED
