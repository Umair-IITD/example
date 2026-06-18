"""
tests/test_sprint2278_invariants.py

Sprint 2.27.8: Runtime Invariant System tests.

Covers:
  - InvariantViolation exception
  - check_gateway_approval_invariant()
  - check_slot_fill_before_workflow()
  - check_action_routing_invariant()
  - assert_playbook_has_terminal_states()
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


from runtime.invariants import (
    InvariantViolation,
    check_gateway_approval_invariant,
    check_slot_fill_before_workflow,
    check_action_routing_invariant,
    assert_playbook_has_terminal_states,
    _APPROVAL_REQUIRED_RISK_LEVELS,
    _NEVER_APPROVE_RISK_LEVELS,
    _TERMINAL_STEP_TYPES,
)


# ── InvariantViolation ────────────────────────────────────────────────────────

class TestInvariantViolation:
    def test_is_exception(self):
        exc = InvariantViolation("TEST", "test detail")
        assert isinstance(exc, Exception)

    def test_has_invariant_name(self):
        exc = InvariantViolation("MY_INVARIANT", "detail")
        assert exc.invariant_name == "MY_INVARIANT"

    def test_has_detail(self):
        exc = InvariantViolation("X", "some detail")
        assert exc.detail == "some detail"

    def test_str_representation(self):
        exc = InvariantViolation("GATEWAY_APPROVAL", "risk mismatch")
        msg = str(exc)
        assert "GATEWAY_APPROVAL" in msg
        assert "risk mismatch" in msg

    def test_can_be_raised_and_caught(self):
        with pytest.raises(InvariantViolation) as exc_info:
            raise InvariantViolation("TEST", "detail")
        assert exc_info.value.invariant_name == "TEST"


# ── check_gateway_approval_invariant ─────────────────────────────────────────

class TestGatewayApprovalInvariant:
    # ── REVERSIBLE rules ──────────────────────────────────────────────────────

    def test_reversible_with_approval_required_passes(self):
        check_gateway_approval_invariant("REVERSIBLE", True)  # no exception

    def test_reversible_without_approval_raises(self):
        with pytest.raises(InvariantViolation) as exc_info:
            check_gateway_approval_invariant("REVERSIBLE", False)
        assert exc_info.value.invariant_name == "GATEWAY_APPROVAL"
        assert "REVERSIBLE" in exc_info.value.detail

    # ── IRREVERSIBLE rules ────────────────────────────────────────────────────

    def test_irreversible_with_approval_required_passes(self):
        check_gateway_approval_invariant("IRREVERSIBLE", True)

    def test_irreversible_without_approval_raises(self):
        with pytest.raises(InvariantViolation):
            check_gateway_approval_invariant("IRREVERSIBLE", False)

    # ── SAFE rules ────────────────────────────────────────────────────────────

    def test_safe_without_approval_passes(self):
        check_gateway_approval_invariant("SAFE", False)

    def test_safe_with_approval_raises(self):
        with pytest.raises(InvariantViolation) as exc_info:
            check_gateway_approval_invariant("SAFE", True)
        assert "SAFE" in exc_info.value.detail

    # ── Case insensitivity ────────────────────────────────────────────────────

    def test_lowercase_reversible_raises_without_approval(self):
        with pytest.raises(InvariantViolation):
            check_gateway_approval_invariant("reversible", False)

    def test_lowercase_safe_with_approval_raises(self):
        with pytest.raises(InvariantViolation):
            check_gateway_approval_invariant("safe", True)

    # ── Unknown risk levels ───────────────────────────────────────────────────

    def test_unknown_risk_level_no_raise(self):
        # Unknown risk levels don't match any invariant → no exception
        check_gateway_approval_invariant("UNKNOWN_LEVEL", True)
        check_gateway_approval_invariant("UNKNOWN_LEVEL", False)

    def test_empty_risk_level_no_raise(self):
        check_gateway_approval_invariant("", True)
        check_gateway_approval_invariant("", False)

    # ── Tier constants ────────────────────────────────────────────────────────

    def test_approval_required_tiers_defined(self):
        assert "REVERSIBLE" in _APPROVAL_REQUIRED_RISK_LEVELS
        assert "IRREVERSIBLE" in _APPROVAL_REQUIRED_RISK_LEVELS

    def test_never_approve_tiers_defined(self):
        assert "SAFE" in _NEVER_APPROVE_RISK_LEVELS


# ── check_slot_fill_before_workflow ──────────────────────────────────────────

class TestSlotFillBeforeWorkflow:
    def test_all_filled_workflow_started_passes(self):
        msg = MagicMock()
        msg.all_slots_filled = True
        msg.workflow_started = True
        check_slot_fill_before_workflow(msg)  # no exception

    def test_not_filled_workflow_not_started_passes(self):
        msg = MagicMock()
        msg.all_slots_filled = False
        msg.workflow_started = False
        check_slot_fill_before_workflow(msg)

    def test_all_filled_workflow_not_started_passes(self):
        msg = MagicMock()
        msg.all_slots_filled = True
        msg.workflow_started = False
        check_slot_fill_before_workflow(msg)

    def test_workflow_started_without_all_slots_raises(self):
        msg = MagicMock()
        msg.all_slots_filled = False
        msg.workflow_started = True
        with pytest.raises(InvariantViolation) as exc_info:
            check_slot_fill_before_workflow(msg)
        assert exc_info.value.invariant_name == "SLOT_FILL_BEFORE_WORKFLOW"

    def test_missing_attributes_default_safe(self):
        class MinimalMsg:
            pass
        check_slot_fill_before_workflow(MinimalMsg())  # defaults: filled=True, started=False


# ── check_action_routing_invariant ────────────────────────────────────────────

class TestActionRoutingInvariant:
    def test_none_adapter_result_passes(self):
        check_action_routing_invariant("otp_resend", None)

    def test_successful_routing_passes(self):
        result = MagicMock()
        result.success = True
        result.error_code = None
        check_action_routing_invariant("otp_resend", result)

    def test_not_routable_error_raises(self):
        result = MagicMock()
        result.success = False
        result.error_code = "NOT_ROUTABLE"
        with pytest.raises(InvariantViolation) as exc_info:
            check_action_routing_invariant("unknown_action", result)
        assert exc_info.value.invariant_name == "ACTION_ROUTING"
        assert "unknown_action" in exc_info.value.detail

    def test_other_error_code_does_not_raise(self):
        result = MagicMock()
        result.success = False
        result.error_code = "ADAPTER_TIMEOUT"
        check_action_routing_invariant("otp_resend", result)

    def test_not_routable_case_insensitive(self):
        result = MagicMock()
        result.success = False
        result.error_code = "not_routable"
        with pytest.raises(InvariantViolation):
            check_action_routing_invariant("otp_resend", result)


# ── assert_playbook_has_terminal_states ───────────────────────────────────────

class TestAssertPlaybookHasTerminalStates:
    def _make_playbook(self, step_types: list[str], playbook_id: str = "pb1"):
        playbook = MagicMock()
        playbook.playbook_id = playbook_id
        steps = []
        for st in step_types:
            step = MagicMock()
            step.step_type.value = st
            steps.append(step)
        playbook.steps = steps
        return playbook

    def test_playbook_with_resolve_passes(self):
        pb = self._make_playbook(["CLARIFY", "INVESTIGATE", "RESOLVE_CASE"])
        assert_playbook_has_terminal_states(pb)  # no exception

    def test_playbook_with_escalate_passes(self):
        pb = self._make_playbook(["INVESTIGATE", "ESCALATE_CASE"])
        assert_playbook_has_terminal_states(pb)

    def test_playbook_with_both_terminals_passes(self):
        pb = self._make_playbook(["RESOLVE_CASE", "ESCALATE_CASE"])
        assert_playbook_has_terminal_states(pb)

    def test_playbook_without_terminal_raises(self):
        pb = self._make_playbook(["CLARIFY", "INVESTIGATE", "KNOWLEDGE_LOOKUP"])
        with pytest.raises(InvariantViolation) as exc_info:
            assert_playbook_has_terminal_states(pb)
        assert exc_info.value.invariant_name == "PLAYBOOK_TERMINAL_STATES"
        assert "pb1" in exc_info.value.detail

    def test_empty_steps_raises(self):
        pb = self._make_playbook([])
        with pytest.raises(InvariantViolation):
            assert_playbook_has_terminal_states(pb)

    def test_violation_lists_step_types_found(self):
        pb = self._make_playbook(["CLARIFY", "INVESTIGATE"])
        with pytest.raises(InvariantViolation) as exc_info:
            assert_playbook_has_terminal_states(pb)
        assert "CLARIFY" in exc_info.value.detail or "INVESTIGATE" in exc_info.value.detail

    def test_terminal_step_types_constants(self):
        assert "RESOLVE_CASE" in _TERMINAL_STEP_TYPES
        assert "ESCALATE_CASE" in _TERMINAL_STEP_TYPES

    def test_step_type_as_string_handled(self):
        playbook = MagicMock()
        playbook.playbook_id = "pb_string"
        step = MagicMock()
        # step.step_type is a string, not an enum
        del step.step_type.value  # remove .value attribute
        step.step_type = "RESOLVE_CASE"
        playbook.steps = [step]
        assert_playbook_has_terminal_states(playbook)  # should pass


# ── AuditLogger dry-run methods ───────────────────────────────────────────────

class TestAuditDryRunMethods:
    def _make_case(self):
        from case_engine.models import Case
        return Case(case_id="c1", ticket_id="t1", client="cli")

    def test_log_dry_run_execution(self):
        from case_engine.audit import AuditLogger
        logger = AuditLogger(supabase_client=None)
        case = self._make_case()
        logger.log_dry_run_execution(case, action_type="workflow", workflow_id="wf-1")

    def test_log_dry_run_route(self):
        from case_engine.audit import AuditLogger
        logger = AuditLogger(supabase_client=None)
        logger.log_dry_run_route(action_type="otp_resend", adapter_type="FRESHDESK", case_id="c1")

    def test_log_dry_run_action(self):
        from case_engine.audit import AuditLogger
        logger = AuditLogger(supabase_client=None)
        case = self._make_case()
        logger.log_dry_run_action(case, action_type="engineering_escalation", step="ASANACREATE")

    def test_log_startup_validation_passed(self):
        from case_engine.audit import AuditLogger
        logger = AuditLogger(supabase_client=None)
        logger.log_startup_validation_passed("workflow_engine", "CRITICAL")

    def test_log_startup_validation_failed(self):
        from case_engine.audit import AuditLogger
        logger = AuditLogger(supabase_client=None)
        logger.log_startup_validation_failed("case_service", "CRITICAL", "service is None")

    def test_log_startup_validation_warning(self):
        from case_engine.audit import AuditLogger
        logger = AuditLogger(supabase_client=None)
        logger.log_startup_validation_warning("adapter_router", "IMPORTANT", "adapter not reachable")

    def test_log_invariant_violation(self):
        from case_engine.audit import AuditLogger
        logger = AuditLogger(supabase_client=None)
        logger.log_invariant_violation(
            invariant_name="GATEWAY_APPROVAL",
            violation_detail="REVERSIBLE without approval_required=True",
            severity="ERROR",
            case_id="c1",
        )

    def test_audit_event_types_exist(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "DRY_RUN_EXECUTION")
        assert hasattr(AuditEventType, "DRY_RUN_ROUTE")
        assert hasattr(AuditEventType, "DRY_RUN_ACTION")
        assert hasattr(AuditEventType, "STARTUP_VALIDATION_PASSED")
        assert hasattr(AuditEventType, "STARTUP_VALIDATION_FAILED")
        assert hasattr(AuditEventType, "STARTUP_VALIDATION_WARNING")
        assert hasattr(AuditEventType, "INVARIANT_VIOLATION")
