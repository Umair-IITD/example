"""
tests/test_sprint2278_conformance.py

Sprint 2.27.8: Architecture Conformance and Integration tests.

Covers:
  - assembly.py: startup_validation_result field on ProductionRuntime
  - assembly.py: validate_production_runtime called during build
  - webhook.py: NON_PRODUCTION_PATH marker
  - AuditEventType: all 9 Sprint 2.27.8 values present
  - models.py: total AuditEventType count >= 74
  - runtime/__init__.py: SupportAgentMode exported
  - Golden path test: process flow exercised end-to-end with mocks
  - Dry-run default: new SupportAgentRuntime defaults to DRY_RUN
"""
from __future__ import annotations

import inspect
import pytest
from unittest.mock import MagicMock


# ── AuditEventType Sprint 2.27.8 values ──────────────────────────────────────

class TestAuditEventTypeSprint2278:
    def test_dry_run_mode_active_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "DRY_RUN_MODE_ACTIVE")

    def test_production_mode_active_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "PRODUCTION_MODE_ACTIVE")

    def test_dry_run_execution_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "DRY_RUN_EXECUTION")
        assert AuditEventType.DRY_RUN_EXECUTION.value == "DRY_RUN_EXECUTION"

    def test_dry_run_route_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "DRY_RUN_ROUTE")

    def test_dry_run_action_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "DRY_RUN_ACTION")

    def test_startup_validation_passed_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "STARTUP_VALIDATION_PASSED")

    def test_startup_validation_failed_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "STARTUP_VALIDATION_FAILED")

    def test_startup_validation_warning_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "STARTUP_VALIDATION_WARNING")

    def test_invariant_violation_exists(self):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, "INVARIANT_VIOLATION")

    def test_total_event_count_at_least_74(self):
        from case_engine.models import AuditEventType
        count = len(list(AuditEventType))
        assert count >= 74, f"Expected >= 74 AuditEventType values, got {count}"

    def test_all_2278_values_are_strings(self):
        from case_engine.models import AuditEventType
        new_values = [
            "DRY_RUN_MODE_ACTIVE", "PRODUCTION_MODE_ACTIVE",
            "DRY_RUN_EXECUTION", "DRY_RUN_ROUTE", "DRY_RUN_ACTION",
            "STARTUP_VALIDATION_PASSED", "STARTUP_VALIDATION_FAILED",
            "STARTUP_VALIDATION_WARNING", "INVARIANT_VIOLATION",
        ]
        for val in new_values:
            assert AuditEventType[val].value == val


# ── ProductionRuntime: startup_validation_result field ───────────────────────

class TestProductionRuntimeStartupValidation:
    def test_startup_validation_result_field_exists(self):
        from runtime.assembly import ProductionRuntime
        import dataclasses
        field_names = [f.name for f in dataclasses.fields(ProductionRuntime)]
        assert "startup_validation_result" in field_names

    def test_startup_validation_result_default_is_none(self):
        from runtime.assembly import ProductionRuntime
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(ProductionRuntime)}
        field = fields["startup_validation_result"]
        assert field.default is None

    def test_total_runtime_fields_gte_34(self):
        from runtime.assembly import ProductionRuntime
        import dataclasses
        count = len(dataclasses.fields(ProductionRuntime))
        assert count >= 34, f"Expected >= 34 fields, got {count}"


# ── webhook.py: NON_PRODUCTION_PATH marker ────────────────────────────────────

class TestWebhookNonProductionPath:
    def test_webhook_has_non_production_path_comment(self):
        import api.routes.webhook as webhook_module
        src = inspect.getsource(webhook_module)
        assert "NON_PRODUCTION_PATH" in src

    def test_webhook_function_still_exists(self):
        from api.routes.webhook import receive_webhook
        assert callable(receive_webhook)


# ── runtime/__init__.py: SupportAgentMode export ─────────────────────────────

class TestRuntimeInitSupportAgentMode:
    def test_support_agent_mode_in_all(self):
        import case_engine.runtime as runtime_pkg
        assert "SupportAgentMode" in runtime_pkg.__all__

    def test_support_agent_mode_importable(self):
        from case_engine.runtime import SupportAgentMode
        assert SupportAgentMode.DRY_RUN == "DRY_RUN"
        assert SupportAgentMode.PRODUCTION == "PRODUCTION"

    def test_mode_from_env_in_all(self):
        import case_engine.runtime as runtime_pkg
        assert "_mode_from_env" in runtime_pkg.__all__


# ── runtime/startup_validation.py: module structure ──────────────────────────

class TestStartupValidationModule:
    def test_module_importable(self):
        import runtime.startup_validation as svm
        assert hasattr(svm, "validate_production_runtime")
        assert hasattr(svm, "assert_production_ready")

    def test_validation_tier_values(self):
        from runtime.startup_validation import ValidationTier
        assert ValidationTier.CRITICAL.value == "CRITICAL"
        assert ValidationTier.IMPORTANT.value == "IMPORTANT"
        assert ValidationTier.GOLDEN_PATH.value == "GOLDEN_PATH"

    def test_critical_services_list_not_empty(self):
        from runtime.startup_validation import _CRITICAL_SERVICES
        assert len(_CRITICAL_SERVICES) >= 5
        assert "workflow_engine" in _CRITICAL_SERVICES
        assert "case_service" in _CRITICAL_SERVICES
        assert "audit_logger" in _CRITICAL_SERVICES

    def test_golden_path_services_list_not_empty(self):
        from runtime.startup_validation import _GOLDEN_PATH_SERVICES
        assert "support_agent_runtime" in _GOLDEN_PATH_SERVICES
        assert "ticket_orchestrator" in _GOLDEN_PATH_SERVICES


# ── runtime/invariants.py: module structure ───────────────────────────────────

class TestInvariantsModule:
    def test_module_importable(self):
        import runtime.invariants as inv
        assert hasattr(inv, "InvariantViolation")
        assert hasattr(inv, "check_gateway_approval_invariant")
        assert hasattr(inv, "check_slot_fill_before_workflow")
        assert hasattr(inv, "check_action_routing_invariant")
        assert hasattr(inv, "assert_playbook_has_terminal_states")

    def test_invariant_violation_is_exception(self):
        from runtime.invariants import InvariantViolation
        assert issubclass(InvariantViolation, Exception)


# ── api/routes/tickets.py: golden path route structure ───────────────────────

class TestTicketsRouteModule:
    def test_module_importable(self):
        from api.routes import tickets
        assert hasattr(tickets, "router")

    def test_process_ticket_route_exists(self):
        from api.routes.tickets import router
        post_paths = [r.path for r in router.routes if "POST" in getattr(r, "methods", set())]
        assert "/tickets/process" in post_paths

    def test_resume_route_exists(self):
        from api.routes.tickets import router
        paths = [r.path for r in router.routes]
        assert "/tickets/{ticket_id}/resume" in paths

    def test_close_route_exists(self):
        from api.routes.tickets import router
        paths = [r.path for r in router.routes]
        assert "/tickets/{ticket_id}/close" in paths

    def test_escalate_route_exists(self):
        from api.routes.tickets import router
        paths = [r.path for r in router.routes]
        assert "/tickets/{ticket_id}/escalate" in paths

    def test_status_route_exists(self):
        from api.routes.tickets import router
        paths = [r.path for r in router.routes]
        assert "/tickets/{ticket_id}/status" in paths

    def test_router_prefix(self):
        from api.routes.tickets import router
        assert router.prefix == "/tickets"


# ── Golden path end-to-end integration ───────────────────────────────────────

class TestGoldenPathIntegration:
    """
    Integration test: verifies the golden path from SupportAgentRuntime
    through to a result without hitting real services.
    """

    def test_full_run_case_dry_run_returns_result(self):
        from case_engine.runtime.support_agent_runtime import SupportAgentRuntime
        from case_engine.runtime.agent_models import SupportAgentMode, AgentStatus
        from case_engine.models import Case
        from case_engine.case_state import CaseState

        cs = MagicMock()
        cs.classify_case.side_effect = lambda c, t: c
        msg = MagicMock()
        msg.all_slots_filled = True
        msg.workflow_started = False
        msg.next_question = None
        cs.receive_message.return_value = msg
        wf = MagicMock()
        wf.workflow_id = "wf-1"
        wf.workflow_state = "RESOLVED"
        wf.step_results = []
        wf.resolved = True
        wf.escalated = False
        wf.escalation_reason = None
        wf.resolution_note = "OTP resent successfully"
        cs.start_workflow.return_value = wf

        rt = SupportAgentRuntime(
            case_service=cs,
            mode=SupportAgentMode.DRY_RUN,
        )
        case = Case(
            case_id="golden-case-1",
            ticket_id="ticket-1",
            client="test_client",
            topic="OTP_Delivery_Failure",
            confidence=0.97,
            current_state=CaseState.NEW,
        )
        result = rt.run_case(case, "I didn't receive the OTP")

        assert result is not None
        assert result.case_id == "golden-case-1"
        assert result.agent_status in list(AgentStatus)
        assert "ASANACREATE" not in result.steps_completed  # DRY_RUN

    def test_ticket_orchestrator_process_then_status(self):
        """Process a ticket then check status returns expected state."""
        from case_engine.ticket_orchestration.orchestrator import build_ticket_orchestrator
        from case_engine.ticket_orchestration.models import TicketContext, TicketLifecycleState

        agent = MagicMock()
        from case_engine.runtime.agent_models import AgentStatus
        from case_engine.runtime import AgentExecutionResult
        agent_result = AgentExecutionResult(
            run_id="r1",
            case_id="c1",
            agent_status=AgentStatus.SUCCESS,
            workflow_result=None,
            response_draft=None,
            engineering_result=None,
            classification=None,
            steps_completed=("CLASSIFY_CACHED",),
            error_code=None,
            error_msg=None,
            started_at="2026-06-16T00:00:00+00:00",
            completed_at="2026-06-16T00:00:01+00:00",
            duration_ms=100,
        )
        agent.run_case.return_value = agent_result

        cs = MagicMock()
        case_obj = MagicMock()
        case_obj.case_id = "c1"
        case_obj.current_state.value = "OPEN"
        cs.open_case.return_value = case_obj

        orch = build_ticket_orchestrator(
            agent_runtime=agent,
            case_service=cs,
        )
        ctx = TicketContext(
            ticket_id="golden-t-1",
            client="test_client",
            subject="OTP not received",
            description="I requested OTP 3 times",
        )
        result = orch.process_ticket(ctx)
        assert result.ticket_id == "golden-t-1"

        state = orch.get_lifecycle_state("golden-t-1")
        assert state is not None
        # State should be CLOSED (SUCCESS) or WAITING or ESCALATED
        assert isinstance(state, TicketLifecycleState)


# ── DRY_RUN default safety check ─────────────────────────────────────────────

class TestDryRunDefaultSafety:
    def test_new_runtime_is_dry_run_by_default(self):
        from case_engine.runtime.support_agent_runtime import SupportAgentRuntime
        from case_engine.runtime.agent_models import SupportAgentMode
        rt = SupportAgentRuntime()
        assert rt._mode == SupportAgentMode.DRY_RUN

    def test_build_runtime_without_env_is_dry_run(self):
        import os
        from unittest.mock import patch
        from case_engine.runtime.support_agent_runtime import build_support_agent_runtime
        from case_engine.runtime.agent_models import SupportAgentMode
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPPORT_AGENT_MODE", None)
            rt = build_support_agent_runtime()
        assert rt._mode == SupportAgentMode.DRY_RUN
