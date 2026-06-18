"""
tests/test_sprint2278_dry_run.py

Sprint 2.27.8: Dry Run Mode tests.

Covers:
  - SupportAgentMode enum (DRY_RUN / PRODUCTION)
  - _mode_from_env() reads SUPPORT_AGENT_MODE env var
  - SupportAgentRuntime defaults to DRY_RUN
  - In DRY_RUN: ASANACREATE is skipped (ASANACREATE_DRY_RUN in steps)
  - In PRODUCTION: ASANACREATE runs normally
  - DRY_RUN audit events emitted
  - build_support_agent_runtime() respects mode parameter
  - __init__.py exports SupportAgentMode
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch


from case_engine.runtime.agent_models import SupportAgentMode, _mode_from_env
from case_engine.runtime.support_agent_runtime import SupportAgentRuntime, build_support_agent_runtime
from case_engine.runtime import AgentStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(topic: str = "VKYC_Session_Failure"):
    from case_engine.models import Case
    from case_engine.case_state import CaseState
    return Case(
        case_id="case-dry-001",
        ticket_id="ticket-1",
        client="test_client",
        topic=topic,
        confidence=0.95,
        current_state=CaseState.NEW,
    )


def _make_mock_case_svc_with_l2_escalation():
    """Mock CaseService that triggers L2CHECK (workflow ESCALATED state)."""
    cs = MagicMock()
    cs.classify_case.side_effect = lambda c, t: c

    msg = MagicMock()
    msg.all_slots_filled = True
    msg.workflow_started = False
    msg.next_question = None
    cs.receive_message.return_value = msg

    wf = MagicMock()
    wf.workflow_id = "wf-escalated"
    wf.workflow_state = "ESCALATED"
    wf.step_results = []
    wf.resolved = False
    wf.escalated = True
    wf.escalation_reason = "Infrastructure failure"
    wf.resolution_note = None
    cs.start_workflow.return_value = wf
    return cs


# ── SupportAgentMode ──────────────────────────────────────────────────────────

class TestSupportAgentMode:
    def test_dry_run_value(self):
        assert SupportAgentMode.DRY_RUN == "DRY_RUN"
        assert SupportAgentMode.DRY_RUN.value == "DRY_RUN"

    def test_production_value(self):
        assert SupportAgentMode.PRODUCTION == "PRODUCTION"
        assert SupportAgentMode.PRODUCTION.value == "PRODUCTION"

    def test_is_str_enum(self):
        assert isinstance(SupportAgentMode.DRY_RUN, str)

    def test_str_comparison(self):
        assert SupportAgentMode.DRY_RUN == "DRY_RUN"
        assert SupportAgentMode.PRODUCTION != "DRY_RUN"

    def test_two_values_only(self):
        values = [m.value for m in SupportAgentMode]
        assert set(values) == {"DRY_RUN", "PRODUCTION"}


# ── _mode_from_env ────────────────────────────────────────────────────────────

class TestModeFromEnv:
    def test_defaults_to_dry_run_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPPORT_AGENT_MODE", None)
            mode = _mode_from_env()
        assert mode == SupportAgentMode.DRY_RUN

    def test_reads_production_from_env(self):
        with patch.dict(os.environ, {"SUPPORT_AGENT_MODE": "PRODUCTION"}):
            mode = _mode_from_env()
        assert mode == SupportAgentMode.PRODUCTION

    def test_reads_dry_run_from_env(self):
        with patch.dict(os.environ, {"SUPPORT_AGENT_MODE": "DRY_RUN"}):
            mode = _mode_from_env()
        assert mode == SupportAgentMode.DRY_RUN

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"SUPPORT_AGENT_MODE": "production"}):
            mode = _mode_from_env()
        assert mode == SupportAgentMode.PRODUCTION

    def test_invalid_value_defaults_to_dry_run(self):
        with patch.dict(os.environ, {"SUPPORT_AGENT_MODE": "INVALID"}):
            mode = _mode_from_env()
        assert mode == SupportAgentMode.DRY_RUN

    def test_empty_string_defaults_to_dry_run(self):
        with patch.dict(os.environ, {"SUPPORT_AGENT_MODE": ""}):
            mode = _mode_from_env()
        assert mode == SupportAgentMode.DRY_RUN


# ── SupportAgentRuntime mode default ─────────────────────────────────────────

class TestSupportAgentRuntimeDefault:
    def test_default_mode_is_dry_run(self):
        rt = SupportAgentRuntime()
        assert rt._mode == SupportAgentMode.DRY_RUN

    def test_explicit_dry_run(self):
        rt = SupportAgentRuntime(mode=SupportAgentMode.DRY_RUN)
        assert rt._mode == SupportAgentMode.DRY_RUN

    def test_explicit_production(self):
        rt = SupportAgentRuntime(mode=SupportAgentMode.PRODUCTION)
        assert rt._mode == SupportAgentMode.PRODUCTION


# ── DRY_RUN mode: ASANACREATE skipped ────────────────────────────────────────

class TestDryRunSkipsAsanaCreate:
    def test_asanacreate_skipped_in_dry_run(self):
        """In DRY_RUN mode, ASANACREATE_DRY_RUN appears instead of ASANACREATE."""
        cs = _make_mock_case_svc_with_l2_escalation()
        eng_svc = MagicMock()

        rt = SupportAgentRuntime(
            case_service=cs,
            engineering_escalation_service=eng_svc,
            mode=SupportAgentMode.DRY_RUN,
        )
        case = _make_case()
        result = rt.run_case(case, "VKYC session dropped")

        # ASANACREATE_DRY_RUN present, ASANACREATE absent
        assert "ASANACREATE_DRY_RUN" in result.steps_completed
        assert "ASANACREATE" not in result.steps_completed

    def test_engineering_create_ticket_not_called_in_dry_run(self):
        """create_ticket() must never be called in DRY_RUN mode."""
        cs = _make_mock_case_svc_with_l2_escalation()
        eng_svc = MagicMock()

        rt = SupportAgentRuntime(
            case_service=cs,
            engineering_escalation_service=eng_svc,
            mode=SupportAgentMode.DRY_RUN,
        )
        case = _make_case()
        rt.run_case(case, "VKYC session dropped")

        eng_svc.create_ticket.assert_not_called()

    def test_engineering_result_is_none_in_dry_run(self):
        """engineering_result must be None in DRY_RUN (no real ticket created)."""
        cs = _make_mock_case_svc_with_l2_escalation()
        eng_svc = MagicMock()

        rt = SupportAgentRuntime(
            case_service=cs,
            engineering_escalation_service=eng_svc,
            mode=SupportAgentMode.DRY_RUN,
        )
        case = _make_case()
        result = rt.run_case(case, "VKYC session dropped")

        assert result.engineering_result is None

    def test_dry_run_result_is_still_valid(self):
        """DRY_RUN result has valid agent_status and step tracking."""
        cs = _make_mock_case_svc_with_l2_escalation()
        rt = SupportAgentRuntime(
            case_service=cs,
            mode=SupportAgentMode.DRY_RUN,
        )
        case = _make_case()
        result = rt.run_case(case, "test")

        assert result.agent_status in (AgentStatus.ESCALATED, AgentStatus.SUCCESS)
        assert result.case_id == case.case_id
        assert len(result.steps_completed) > 0


# ── PRODUCTION mode: ASANACREATE runs ────────────────────────────────────────

class TestProductionModeAsanaCreate:
    def test_asanacreate_runs_in_production(self):
        """In PRODUCTION mode, ASANACREATE appears in steps."""
        cs = _make_mock_case_svc_with_l2_escalation()
        from case_engine.engineering.service import build_engineering_escalation_service
        eng_svc = build_engineering_escalation_service()

        rt = SupportAgentRuntime(
            case_service=cs,
            engineering_escalation_service=eng_svc,
            mode=SupportAgentMode.PRODUCTION,
        )
        case = _make_case()
        result = rt.run_case(case, "VKYC failed")

        assert "ASANACREATE" in result.steps_completed
        assert "ASANACREATE_DRY_RUN" not in result.steps_completed
        assert result.engineering_result is not None

    def test_production_mode_calls_create_ticket(self):
        cs = _make_mock_case_svc_with_l2_escalation()
        eng_svc = MagicMock()
        # Mock the create_ticket return value
        mock_eng_result = MagicMock()
        mock_ticket = MagicMock()
        mock_ticket.ticket_id = "eng-ticket-1"
        mock_eng_result.ticket = mock_ticket
        mock_eng_result.to_dict.return_value = {"ticket_id": "eng-ticket-1"}
        eng_svc.create_ticket.return_value = mock_eng_result

        rt = SupportAgentRuntime(
            case_service=cs,
            engineering_escalation_service=eng_svc,
            mode=SupportAgentMode.PRODUCTION,
        )
        case = _make_case()
        rt.run_case(case, "Infrastructure down")

        eng_svc.create_ticket.assert_called_once()


# ── DRY_RUN audit event emission ─────────────────────────────────────────────

class TestDryRunAuditEvents:
    def test_dry_run_action_audit_emitted_when_l2_needed(self):
        """log_dry_run_action must be called when needs_l2 is True in DRY_RUN."""
        cs = _make_mock_case_svc_with_l2_escalation()
        mock_audit = MagicMock()
        eng_svc = MagicMock()

        rt = SupportAgentRuntime(
            case_service=cs,
            engineering_escalation_service=eng_svc,
            audit_logger=mock_audit,
            mode=SupportAgentMode.DRY_RUN,
        )
        case = _make_case()
        rt.run_case(case, "API failure")

        mock_audit.log_dry_run_action.assert_called_once()
        call_kwargs = mock_audit.log_dry_run_action.call_args
        assert call_kwargs is not None

    def test_dry_run_execution_audit_emitted_when_workflow_ran(self):
        """log_dry_run_execution must be called after workflow completes."""
        cs = _make_mock_case_svc_with_l2_escalation()
        mock_audit = MagicMock()

        rt = SupportAgentRuntime(
            case_service=cs,
            audit_logger=mock_audit,
            mode=SupportAgentMode.DRY_RUN,
        )
        case = _make_case()
        rt.run_case(case, "API failure")

        mock_audit.log_dry_run_execution.assert_called_once()

    def test_no_dry_run_audit_in_production_mode(self):
        """DRY_RUN audit events must NOT be emitted in PRODUCTION mode."""
        cs = _make_mock_case_svc_with_l2_escalation()
        mock_audit = MagicMock()
        from case_engine.engineering.service import build_engineering_escalation_service
        eng_svc = build_engineering_escalation_service()

        rt = SupportAgentRuntime(
            case_service=cs,
            engineering_escalation_service=eng_svc,
            audit_logger=mock_audit,
            mode=SupportAgentMode.PRODUCTION,
        )
        case = _make_case()
        rt.run_case(case, "API failure")

        mock_audit.log_dry_run_action.assert_not_called()
        mock_audit.log_dry_run_execution.assert_not_called()

    def test_audit_failure_does_not_crash_run(self):
        """Even if audit emission fails, run_case must return a result."""
        cs = _make_mock_case_svc_with_l2_escalation()
        mock_audit = MagicMock()
        mock_audit.log_dry_run_action.side_effect = Exception("audit broken")
        mock_audit.log_dry_run_execution.side_effect = Exception("audit broken")
        eng_svc = MagicMock()

        rt = SupportAgentRuntime(
            case_service=cs,
            engineering_escalation_service=eng_svc,
            audit_logger=mock_audit,
            mode=SupportAgentMode.DRY_RUN,
        )
        case = _make_case()
        result = rt.run_case(case, "test")
        assert result is not None


# ── build_support_agent_runtime with mode ─────────────────────────────────────

class TestBuildWithMode:
    def test_default_mode_from_env_dry_run(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPPORT_AGENT_MODE", None)
            rt = build_support_agent_runtime()
        assert rt._mode == SupportAgentMode.DRY_RUN

    def test_explicit_production_mode(self):
        rt = build_support_agent_runtime(mode=SupportAgentMode.PRODUCTION)
        assert rt._mode == SupportAgentMode.PRODUCTION

    def test_env_production_propagates(self):
        with patch.dict(os.environ, {"SUPPORT_AGENT_MODE": "PRODUCTION"}):
            rt = build_support_agent_runtime()
        assert rt._mode == SupportAgentMode.PRODUCTION

    def test_explicit_mode_overrides_env(self):
        with patch.dict(os.environ, {"SUPPORT_AGENT_MODE": "PRODUCTION"}):
            rt = build_support_agent_runtime(mode=SupportAgentMode.DRY_RUN)
        assert rt._mode == SupportAgentMode.DRY_RUN


# ── __init__.py export ────────────────────────────────────────────────────────

class TestRuntimeInitExport:
    def test_support_agent_mode_exported(self):
        from case_engine.runtime import SupportAgentMode as exported
        assert exported is SupportAgentMode

    def test_mode_from_env_exported(self):
        from case_engine.runtime import _mode_from_env as exported
        assert callable(exported)
