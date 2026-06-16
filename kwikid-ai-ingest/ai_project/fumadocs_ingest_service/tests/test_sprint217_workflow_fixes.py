"""
tests/test_sprint217_workflow_fixes.py

Sprint 2.17 Part A — Workflow Reliability Tests

Covers:
  A1: ACTION_PENDING → RESOLVED state machine fix
  A2: New audit events (WORKFLOW_RESUMED, WORKFLOW_COMPLETED, WORKFLOW_FAILED)
  A3: Double-start protection (WorkflowAlreadyStartedError)
  A4: WorkflowConsistencyChecker (validate_start, validate_resume, check_state_alignment)
"""
from __future__ import annotations

import pytest

from case_engine.case_state import CaseState
from case_engine.models import AuditEventType, Case
from case_engine.service import (
    CaseService,
    WorkflowAlreadyStartedError,
    WorkflowStartResult,
)
from case_engine.state_machine import CaseStateMachine
from case_engine.workflows.consistency import (
    WorkflowConsistencyChecker,
    WorkflowConsistencyError,
)
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
from case_engine.slot_filling.models import SlotStatus, SlotValue
from case_engine.audit import AuditLogger


# ── Fixtures ──────────────────────────────────────────────────────────────────

class _NullRepo:
    def get_case(self, case_id): return None
    def get_case_by_ticket(self, ticket_id, client): return None
    def create_case(self, ticket_id, client): return Case(ticket_id=ticket_id, client=client)
    def update_case_state(self, case, state): pass
    def record_transition(self, t): pass
    def list_cases_by_workflow_state(self, states=None): return []


class _TrackingAudit(AuditLogger):
    def __init__(self):
        super().__init__(None)
        self.events = []

    def _write(self, entry):
        self.events.append(entry.action_type)

    def event_types(self):
        return [e.value for e in self.events]


def _make_slot(value: str = "test") -> SlotValue:
    sv = SlotValue(slot_name="session_id")
    sv.status = SlotStatus.FILLED
    sv.value = value
    return sv


def _make_resolve_step(step_id: str = "resolve") -> WorkflowStep:
    return WorkflowStep(
        step_index=0,
        step_id=step_id,
        step_type=WorkflowStepType.RESOLVE_CASE,
        name="Resolve",
    )


def _make_escalate_step(step_id: str = "escalate") -> WorkflowStep:
    return WorkflowStep(
        step_index=0,
        step_id=step_id,
        step_type=WorkflowStepType.ESCALATE_CASE,
        name="Escalate",
    )


def _simple_definition(first_step: WorkflowStep) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id="test_wf_v1",
        topic="VKYC_Session_Failure",
        version="1.0",
        name="Test WF",
        steps=(first_step,),
    )


def _service_with_registry(defn: WorkflowDefinition) -> tuple[CaseService, _TrackingAudit]:
    registry = PlaybookRegistry([defn])
    audit = _TrackingAudit()
    svc = CaseService(
        repository=_NullRepo(),
        audit_logger=audit,
        playbook_registry=registry,
    )
    return svc, audit


def _workflow_active_case() -> Case:
    case = Case(ticket_id="T-001", client="test")
    case.topic = "VKYC_Session_Failure"
    case.current_state = CaseState.WORKFLOW_ACTIVE
    return case


def _action_pending_case() -> Case:
    case = Case(ticket_id="T-002", client="test")
    case.topic = "VKYC_Session_Failure"
    case.current_state = CaseState.ACTION_PENDING
    case.workflow_state = WorkflowState.PAUSED.value
    return case


# ── A1: ACTION_PENDING → RESOLVED fix ─────────────────────────────────────────

class TestActionPendingToResolvedFix:
    """A1: Verify that resume_workflow correctly transitions ACTION_PENDING → WORKFLOW_ACTIVE → RESOLVED."""

    def test_resume_from_action_pending_completes_to_resolved(self):
        """The P0 bug: resume from ACTION_PENDING must hop through WORKFLOW_ACTIVE."""
        # Build a workflow that resolves in one step
        resolve_step = _make_resolve_step("step_resolve")
        defn = _simple_definition(resolve_step)
        svc, _ = _service_with_registry(defn)

        # Start a case in the right state
        case = _workflow_active_case()
        slot_values = {"session_id": _make_slot("KID-123")}

        # Force workflow into PAUSED by writing context directly
        wer = WorkflowExecutionResult(
            workflow_id="test_wf_v1",
            workflow_state=WorkflowState.PAUSED,
            current_step_id="step_resolve",
        )
        case.workflow_context = wer.to_dict()
        case.workflow_state = WorkflowState.PAUSED.value
        case.current_state = CaseState.ACTION_PENDING

        # Create a fake action that succeeded
        class FakeAction:
            action_id = "act-001"
            current_state = __import__("case_engine.action_state", fromlist=["ActionState"]).ActionState.EXECUTED
            is_rolled_back = False
            execution_result = {"success": True}

        from case_engine.action_state import ActionState
        fake_action = FakeAction()

        result = svc.resume_workflow(case, fake_action, slot_values)

        # After fix: case state must be RESOLVED (not stuck in ACTION_PENDING)
        assert case.current_state == CaseState.RESOLVED, (
            f"Expected RESOLVED but got {case.current_state.value}. "
            "The ACTION_PENDING → WORKFLOW_ACTIVE → RESOLVED hop is missing."
        )

    def test_resume_from_action_pending_escalates_correctly(self):
        """Escalation from ACTION_PENDING must also hop through WORKFLOW_ACTIVE."""
        escalate_step = _make_escalate_step("step_esc")
        defn = _simple_definition(escalate_step)
        svc, _ = _service_with_registry(defn)

        case = _action_pending_case()
        wer = WorkflowExecutionResult(
            workflow_id="test_wf_v1",
            workflow_state=WorkflowState.PAUSED,
            current_step_id="step_esc",
        )
        case.workflow_context = wer.to_dict()

        class FakeAction:
            action_id = "act-002"
            current_state = __import__("case_engine.action_state", fromlist=["ActionState"]).ActionState.FAILED
            is_rolled_back = False
            execution_result = {}

        result = svc.resume_workflow(case, FakeAction(), {})
        assert case.current_state == CaseState.ESCALATED

    def test_state_machine_rejects_direct_action_pending_to_resolved(self):
        """Baseline: confirm direct ACTION_PENDING → RESOLVED is still blocked."""
        case = _action_pending_case()
        sm = CaseStateMachine()
        # The transition should be blocked
        result = sm.safe_transition(case, CaseState.RESOLVED, reason="test")
        assert result is None  # safe_transition returns None on block
        assert case.current_state == CaseState.ACTION_PENDING  # unchanged


# ── A2: New audit events ──────────────────────────────────────────────────────

class TestNewAuditEventTypes:
    """A2: Verify WORKFLOW_RESUMED, WORKFLOW_COMPLETED, WORKFLOW_FAILED exist as AuditEventType."""

    def test_workflow_resumed_event_type_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_RESUMED")
        assert AuditEventType.WORKFLOW_RESUMED.value == "WORKFLOW_RESUMED"

    def test_workflow_completed_event_type_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_COMPLETED")
        assert AuditEventType.WORKFLOW_COMPLETED.value == "WORKFLOW_COMPLETED"

    def test_workflow_failed_event_type_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_FAILED")
        assert AuditEventType.WORKFLOW_FAILED.value == "WORKFLOW_FAILED"

    def test_tool_executed_event_type_exists(self):
        assert hasattr(AuditEventType, "TOOL_EXECUTED")
        assert AuditEventType.TOOL_EXECUTED.value == "TOOL_EXECUTED"

    def test_tool_failed_event_type_exists(self):
        assert hasattr(AuditEventType, "TOOL_FAILED")

    def test_audit_logger_has_log_workflow_resumed(self):
        import inspect
        audit = AuditLogger(None)
        assert hasattr(audit, "log_workflow_resumed")
        assert callable(audit.log_workflow_resumed)

    def test_audit_logger_has_log_workflow_completed(self):
        audit = AuditLogger(None)
        assert hasattr(audit, "log_workflow_completed")

    def test_audit_logger_has_log_workflow_failed(self):
        audit = AuditLogger(None)
        assert hasattr(audit, "log_workflow_failed")

    def test_audit_logger_has_log_tool_executed(self):
        audit = AuditLogger(None)
        assert hasattr(audit, "log_tool_executed")

    def test_log_workflow_resumed_does_not_raise(self):
        audit = AuditLogger(None)
        case = Case(ticket_id="T-001", client="test")
        audit.log_workflow_resumed(case, "wf_id", "run_id", "act_id")

    def test_log_workflow_completed_does_not_raise(self):
        audit = AuditLogger(None)
        case = Case(ticket_id="T-001", client="test")
        audit.log_workflow_completed(case, "wf_id", "resolved")

    def test_log_workflow_failed_does_not_raise(self):
        audit = AuditLogger(None)
        case = Case(ticket_id="T-001", client="test")
        audit.log_workflow_failed(case, "wf_id", "internal_error")

    def test_log_tool_executed_does_not_raise(self):
        audit = AuditLogger(None)
        case = Case(ticket_id="T-001", client="test")
        audit.log_tool_executed(case, "GetSessionDetailsTool", True, {"status": "FAILED"})

    def test_workflow_failed_emitted_when_no_playbook(self):
        """WORKFLOW_FAILED audit should be emitted when no playbook is found."""
        audit = _TrackingAudit()
        engine = WorkflowEngine()
        registry = PlaybookRegistry([])  # empty registry
        case = _workflow_active_case()

        result = engine.start(case, registry, {}, audit=audit)
        assert result.workflow_state == WorkflowState.FAILED
        assert "WORKFLOW_FAILED" in audit.event_types()

    def test_workflow_completed_emitted_on_resolve(self):
        """WORKFLOW_COMPLETED audit should be emitted when workflow resolves."""
        audit = _TrackingAudit()
        engine = WorkflowEngine()
        defn = _simple_definition(_make_resolve_step("r"))
        registry = PlaybookRegistry([defn])
        case = _workflow_active_case()

        result = engine.start(case, registry, {}, audit=audit)
        assert result.workflow_state == WorkflowState.COMPLETED
        assert "WORKFLOW_COMPLETED" in audit.event_types()


# ── A3: Double-start protection ────────────────────────────────────────────────

class TestDoubleStartProtection:
    """A3: start_workflow raises WorkflowAlreadyStartedError if workflow already active."""

    def test_raises_when_workflow_running(self):
        resolve_step = _make_resolve_step("r")
        defn = _simple_definition(resolve_step)
        svc, _ = _service_with_registry(defn)

        case = _workflow_active_case()
        case.workflow_state = WorkflowState.RUNNING.value

        with pytest.raises(WorkflowAlreadyStartedError):
            svc.start_workflow(case, {})

    def test_raises_when_workflow_paused(self):
        resolve_step = _make_resolve_step("r")
        defn = _simple_definition(resolve_step)
        svc, _ = _service_with_registry(defn)

        case = _workflow_active_case()
        case.workflow_state = WorkflowState.PAUSED.value

        with pytest.raises(WorkflowAlreadyStartedError):
            svc.start_workflow(case, {})

    def test_does_not_raise_when_workflow_none(self):
        resolve_step = _make_resolve_step("r")
        defn = _simple_definition(resolve_step)
        svc, _ = _service_with_registry(defn)

        case = _workflow_active_case()
        case.workflow_state = None  # no prior workflow

        # Should not raise — first start is valid
        result = svc.start_workflow(case, {})
        assert isinstance(result, WorkflowStartResult)

    def test_does_not_raise_when_workflow_completed(self):
        """COMPLETED workflows are terminal — new start is allowed (re-run scenario)."""
        resolve_step = _make_resolve_step("r")
        defn = _simple_definition(resolve_step)
        svc, _ = _service_with_registry(defn)

        case = _workflow_active_case()
        case.workflow_state = WorkflowState.COMPLETED.value

        # Should not raise — COMPLETED is not an active state
        result = svc.start_workflow(case, {})
        assert isinstance(result, WorkflowStartResult)

    def test_does_not_raise_when_workflow_failed(self):
        """FAILED workflows can be retried."""
        resolve_step = _make_resolve_step("r")
        defn = _simple_definition(resolve_step)
        svc, _ = _service_with_registry(defn)

        case = _workflow_active_case()
        case.workflow_state = WorkflowState.FAILED.value

        result = svc.start_workflow(case, {})
        assert isinstance(result, WorkflowStartResult)

    def test_receive_message_handles_already_started_idempotently(self):
        """Auto-start in receive_message treats AlreadyStartedError as success (wf_started=True)."""
        resolve_step = _make_resolve_step("r")
        defn = _simple_definition(resolve_step)
        svc, _ = _service_with_registry(defn)

        case = Case(ticket_id="T-003", client="test")
        case.topic = "VKYC_Session_Failure"
        case.current_state = CaseState.WORKFLOW_ACTIVE
        case.workflow_state = WorkflowState.RUNNING.value  # already running

        # Pre-fill slots so all_required_filled triggers
        case.slot_state = {
            "session_id":   {"status": "FILLED", "value": "KID-123", "attempt_count": 1},
            "phone_number": {"status": "FILLED", "value": "+91-9999", "attempt_count": 1},
        }

        result = svc.receive_message(case, "hello")
        assert result.workflow_started is True  # idempotent — treated as started


# ── A4: WorkflowConsistencyChecker ────────────────────────────────────────────

class TestWorkflowConsistencyChecker:
    """A4: WorkflowConsistencyChecker validate_start, validate_resume, check_state_alignment."""

    def setup_method(self):
        self.checker = WorkflowConsistencyChecker()

    # validate_start

    def test_validate_start_ok_when_no_prior_workflow(self):
        case = Case()
        case.workflow_state = None
        self.checker.validate_start(case)  # should not raise

    def test_validate_start_raises_when_completed(self):
        case = Case()
        case.workflow_state = WorkflowState.COMPLETED.value
        with pytest.raises(WorkflowConsistencyError, match="COMPLETED"):
            self.checker.validate_start(case)

    def test_validate_start_raises_when_running(self):
        case = Case()
        case.workflow_state = WorkflowState.RUNNING.value
        with pytest.raises(WorkflowConsistencyError, match="active"):
            self.checker.validate_start(case)

    def test_validate_start_raises_when_paused(self):
        case = Case()
        case.workflow_state = WorkflowState.PAUSED.value
        with pytest.raises(WorkflowConsistencyError, match="active"):
            self.checker.validate_start(case)

    def test_validate_start_ok_when_escalated(self):
        """ESCALATED terminal state: new start is valid (different workflow)."""
        case = Case()
        case.workflow_state = WorkflowState.ESCALATED.value
        self.checker.validate_start(case)  # should not raise

    def test_validate_start_ok_when_failed(self):
        case = Case()
        case.workflow_state = WorkflowState.FAILED.value
        self.checker.validate_start(case)  # should not raise

    # validate_resume

    def test_validate_resume_ok_when_paused_and_action_pending(self):
        case = Case()
        case.current_state = CaseState.ACTION_PENDING
        case.workflow_state = WorkflowState.PAUSED.value
        self.checker.validate_resume(case)  # should not raise

    def test_validate_resume_raises_when_no_workflow(self):
        case = Case()
        case.workflow_state = None
        case.current_state = CaseState.ACTION_PENDING
        with pytest.raises(WorkflowConsistencyError, match="no active paused"):
            self.checker.validate_resume(case)

    def test_validate_resume_raises_when_terminal_completed(self):
        case = Case()
        case.current_state = CaseState.WORKFLOW_ACTIVE
        case.workflow_state = WorkflowState.COMPLETED.value
        with pytest.raises(WorkflowConsistencyError, match="terminal"):
            self.checker.validate_resume(case)

    def test_validate_resume_raises_when_terminal_failed(self):
        case = Case()
        case.current_state = CaseState.WORKFLOW_ACTIVE
        case.workflow_state = WorkflowState.FAILED.value
        with pytest.raises(WorkflowConsistencyError, match="terminal"):
            self.checker.validate_resume(case)

    def test_validate_resume_raises_when_running_not_paused(self):
        case = Case()
        case.current_state = CaseState.WORKFLOW_ACTIVE
        case.workflow_state = WorkflowState.RUNNING.value
        with pytest.raises(WorkflowConsistencyError, match="PAUSED"):
            self.checker.validate_resume(case)

    def test_validate_resume_raises_when_paused_but_not_action_pending(self):
        case = Case()
        case.current_state = CaseState.WORKFLOW_ACTIVE  # should be ACTION_PENDING
        case.workflow_state = WorkflowState.PAUSED.value
        with pytest.raises(WorkflowConsistencyError, match="ACTION_PENDING"):
            self.checker.validate_resume(case)

    # check_state_alignment

    def test_alignment_ok_when_resolved_and_completed(self):
        case = Case()
        case.current_state = CaseState.RESOLVED
        case.workflow_state = WorkflowState.COMPLETED.value
        case.workflow_context = {"completed_at": "2026-06-08T00:00:00+00:00"}
        warnings = self.checker.check_state_alignment(case)
        assert warnings == []

    def test_alignment_warning_when_resolved_but_paused(self):
        case = Case()
        case.current_state = CaseState.RESOLVED
        case.workflow_state = WorkflowState.PAUSED.value
        case.workflow_context = {}
        warnings = self.checker.check_state_alignment(case)
        assert len(warnings) > 0
        assert any("PAUSED" in w or "RESOLVED" in w for w in warnings)

    def test_alignment_warning_when_paused_no_pending_action_id(self):
        case = Case()
        case.current_state = CaseState.ACTION_PENDING
        case.workflow_state = WorkflowState.PAUSED.value
        case.workflow_context = {}  # no pending_action_id
        warnings = self.checker.check_state_alignment(case)
        assert any("pending_action_id" in w for w in warnings)

    def test_alignment_no_warnings_when_no_workflow(self):
        case = Case()
        case.workflow_state = None
        warnings = self.checker.check_state_alignment(case)
        assert warnings == []

    # validate_no_duplicate_active_workflow

    def test_no_duplicates_with_single_active_case(self):
        case = Case()
        case.workflow_state = WorkflowState.RUNNING.value
        violations = self.checker.validate_no_duplicate_active_workflow([case])
        assert violations == []

    def test_resume_workflow_blocked_by_consistency_checker_when_not_paused(self):
        """A4: resume_workflow returns FAILED if consistency check fails."""
        resolve_step = _make_resolve_step("r")
        defn = _simple_definition(resolve_step)
        svc, _ = _service_with_registry(defn)

        case = Case(ticket_id="T-099", client="test")
        case.current_state = CaseState.WORKFLOW_ACTIVE
        case.workflow_state = WorkflowState.RUNNING.value  # not PAUSED — resume invalid

        class FakeAction:
            action_id = "act-099"
            current_state = __import__("case_engine.action_state", fromlist=["ActionState"]).ActionState.EXECUTED
            is_rolled_back = False
            execution_result = {}

        result = svc.resume_workflow(case, FakeAction(), {})
        assert "consistency_error" in (result.escalation_reason or "")
