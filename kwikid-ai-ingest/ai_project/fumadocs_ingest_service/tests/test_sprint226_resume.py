"""
tests/test_sprint226_resume.py

Sprint 2.26 Part B: WorkflowEngine.resume_after_clarification() tests.

Coverage:
  - No-op when current step is not CLARIFY → FAILED result
  - READY path: slot context filled → navigates on_success
  - NEEDS_CLARIFICATION path: still missing slots → stays PAUSED
  - ESCALATE path: max attempts exceeded → on_failure
  - No clarification_service wired → SKIPPED_NO_CLARIFICATION_SERVICE
  - step_results preserved across pause/resume cycles
  - audit event WORKFLOW_CLARIFICATION_RESUMED emitted
  - Never raises
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from case_engine.workflows.models import WorkflowExecutionResult, WorkflowState
from case_engine.workflows.workflow_engine import WorkflowEngine
from case_engine.slot_filling.models import SlotStatus, SlotValue


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(
    case_id: str = "case-resume-1",
    workflow_context: dict | None = None,
    slot_state: dict | None = None,
):
    case = MagicMock()
    case.case_id  = case_id
    case.topic    = "VKYC_Session_Failure"
    case.workflow_context = workflow_context or {}
    case.slot_state       = slot_state or {}
    return case


def _slot_value(value: str | None, status: SlotStatus = SlotStatus.FILLED) -> SlotValue:
    return SlotValue(slot_name="s", status=status, value=value)


def _filled_slots() -> dict[str, SlotValue]:
    return {
        "session_id":   SlotValue("session_id",   SlotStatus.FILLED, "SES-001"),
        "phone_number": SlotValue("phone_number",  SlotStatus.FILLED, "9999999999"),
    }


def _missing_slots() -> dict[str, SlotValue]:
    return {
        "session_id":   SlotValue("session_id",   SlotStatus.PENDING, None),
        "phone_number": SlotValue("phone_number",  SlotStatus.FILLED,  "9999999999"),
    }


def _paused_vkyc_workflow_context(step_id: str = "clarify_slots") -> dict:
    """Return a WorkflowExecutionResult dict for a VKYC workflow PAUSED at CLARIFY."""
    from case_engine.workflows.playbook_registry import PlaybookRegistry
    registry = PlaybookRegistry.build()
    defn     = registry.get("VKYC_Session_Failure")
    assert defn is not None
    result = WorkflowExecutionResult(
        workflow_id=defn.workflow_id,
        workflow_state=WorkflowState.PAUSED,
        current_step_id=step_id,
    )
    result.record_step(
        step_id=step_id,
        outcome="CLARIFICATION_PENDING",
        detail={"status": "NEEDS_CLARIFICATION", "missing_slots": ["session_id"]},
    )
    return result.to_dict()


# ── resume_after_clarification(): basic plumbing ──────────────────────────────

class TestResumeAfterClarificationBasic:
    def test_returns_workflow_execution_result(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        engine   = WorkflowEngine()  # no clarification_service → SKIPPED path
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx)
        result   = engine.resume_after_clarification(case, registry, _filled_slots())
        assert isinstance(result, WorkflowExecutionResult)

    def test_no_registry_entry_returns_failed(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        engine   = WorkflowEngine()
        # Corrupt the workflow_id
        case = _make_case(workflow_context={"workflow_id": "nonexistent_wf"})
        result = engine.resume_after_clarification(case, registry, _filled_slots())
        assert result.workflow_state == WorkflowState.FAILED

    def test_empty_workflow_context_returns_failed(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        engine   = WorkflowEngine()
        case     = _make_case(workflow_context={})
        result   = engine.resume_after_clarification(case, registry, _filled_slots())
        assert result.workflow_state == WorkflowState.FAILED

    def test_wrong_step_type_returns_failed(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        engine   = WorkflowEngine()
        defn     = registry.get("VKYC_Session_Failure")
        # Find a non-CLARIFY step to set as current
        non_clarify = next(s for s in defn.steps if s.step_type.value != "CLARIFY")
        ctx  = _paused_vkyc_workflow_context(step_id=non_clarify.step_id)
        case = _make_case(workflow_context=ctx)
        result = engine.resume_after_clarification(case, registry, _filled_slots())
        assert result.workflow_state == WorkflowState.FAILED

    def test_never_raises_on_exception(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        engine   = WorkflowEngine()
        case     = MagicMock()
        case.case_id = "x"
        case.workflow_context = None  # will cause AttributeError internally
        # Should not raise
        result = engine.resume_after_clarification(case, registry, {})
        assert result.workflow_state in (WorkflowState.FAILED, WorkflowState.ESCALATED,
                                          WorkflowState.COMPLETED, WorkflowState.PAUSED,
                                          WorkflowState.RUNNING)


# ── resume_after_clarification(): SKIPPED (no service) ───────────────────────

class TestResumeAfterClarificationNoService:
    def test_no_service_skips_and_navigates_on_success(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        engine   = WorkflowEngine()  # no clarification_service
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx)
        result   = engine.resume_after_clarification(case, registry, _filled_slots())
        # No service → SKIPPED_NO_CLARIFICATION_SERVICE → on_success navigation
        # Depending on the next step, result state varies but should not be FAILED
        assert result.workflow_state != WorkflowState.FAILED


# ── resume_after_clarification(): with mock ClarificationService ──────────────

class TestResumeAfterClarificationWithService:
    def _make_clarification_service(self, status: str, ready: bool = False):
        svc = MagicMock()
        svc.clarify.return_value = {
            "status":            status,
            "ready_to_continue": ready,
            "missing_slots":     [] if ready else ["session_id"],
            "clarification_message": "" if ready else "Please provide session_id",
        }
        return svc

    def test_ready_navigates_forward(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        svc      = self._make_clarification_service("READY", ready=True)
        engine   = WorkflowEngine(clarification_service=svc)
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx)
        result   = engine.resume_after_clarification(case, registry, _filled_slots())
        # READY → navigates on_success → should not be PAUSED
        assert result.workflow_state != WorkflowState.PAUSED

    def test_needs_clarification_stays_paused(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        svc      = self._make_clarification_service("NEEDS_CLARIFICATION", ready=False)
        engine   = WorkflowEngine(clarification_service=svc)
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx, slot_state={})
        result   = engine.resume_after_clarification(case, registry, _missing_slots())
        assert result.workflow_state == WorkflowState.PAUSED

    def test_escalate_navigates_on_failure(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        svc      = self._make_clarification_service("ESCALATE", ready=False)
        engine   = WorkflowEngine(clarification_service=svc)
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx, slot_state={})
        result   = engine.resume_after_clarification(case, registry, _missing_slots())
        # ESCALATE → on_failure → should be ESCALATED
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_step_results_preserved_from_previous_pause(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        svc      = self._make_clarification_service("NEEDS_CLARIFICATION", ready=False)
        engine   = WorkflowEngine(clarification_service=svc)
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx, slot_state={})
        result   = engine.resume_after_clarification(case, registry, _missing_slots())
        # Should have at least one step recorded (the original CLARIFICATION_PENDING + the new one)
        assert len(result.step_results) >= 1

    def test_audit_event_emitted_on_resume(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        svc      = self._make_clarification_service("READY", ready=True)
        engine   = WorkflowEngine(clarification_service=svc)
        audit    = MagicMock()
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx)
        engine.resume_after_clarification(case, registry, _filled_slots(), audit=audit)
        audit.log_workflow_clarification_resumed.assert_called_once()

    def test_audit_none_does_not_raise(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        svc      = self._make_clarification_service("READY", ready=True)
        engine   = WorkflowEngine(clarification_service=svc)
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx)
        # Should not raise even with audit=None
        result = engine.resume_after_clarification(case, registry, _filled_slots(), audit=None)
        assert result is not None

    def test_slot_context_built_from_updated_values(self):
        """The clarification service receives the updated slot context."""
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry = PlaybookRegistry.build()
        svc      = self._make_clarification_service("READY", ready=True)
        engine   = WorkflowEngine(clarification_service=svc)
        ctx      = _paused_vkyc_workflow_context()
        case     = _make_case(workflow_context=ctx)
        slots    = _filled_slots()
        engine.resume_after_clarification(case, registry, slots, audit=None)
        # Verify clarify was called with a slot_context containing the filled values
        call_kwargs = svc.clarify.call_args[1] if svc.clarify.call_args else {}
        slot_ctx    = call_kwargs.get("slot_context", {})
        assert "session_id"   in slot_ctx
        assert "phone_number" in slot_ctx
