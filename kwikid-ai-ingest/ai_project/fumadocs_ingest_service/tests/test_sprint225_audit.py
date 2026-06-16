"""
tests/test_sprint225_audit.py

Sprint 2.25: AuditLogger clarification method tests.

Coverage:
  - log_clarification_started: writes CLARIFICATION_STARTED event
  - log_clarification_started: includes topic, missing_slots, workflow_id, step_id
  - log_clarification_started: outcome == "STARTED"
  - log_clarification_started: never raises when supabase fails
  - log_clarification_completed: writes CLARIFICATION_COMPLETED event
  - log_clarification_completed: outcome == status param
  - log_clarification_completed: includes ready_to_continue, slot_count
  - log_clarification_completed: never raises
  - log_workflow_clarification_started: writes WORKFLOW_CLARIFICATION_STARTED event
  - log_workflow_clarification_started: includes workflow_id, step_id
  - log_workflow_clarification_started: outcome == "STARTED"
  - log_workflow_clarification_started: never raises
  - log_workflow_clarification_completed: writes WORKFLOW_CLARIFICATION_COMPLETED event
  - log_workflow_clarification_completed: outcome == status param
  - log_workflow_clarification_completed: includes ready_to_continue
  - log_workflow_clarification_completed: never raises
  - All 4 methods work in log-only mode (supabase_client=None)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from case_engine.audit import AuditLogger
from case_engine.models import AuditEventType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(case_id="case-test"):
    case = MagicMock()
    case.case_id = case_id
    case.ticket_id = "TKT-001"
    case.client = "test_client"
    return case


def _audit_log_only() -> AuditLogger:
    return AuditLogger(supabase_client=None)


def _audit_with_sb() -> tuple[AuditLogger, MagicMock]:
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock()
    return AuditLogger(supabase_client=sb), sb


# ── log_clarification_started ─────────────────────────────────────────────────

class TestLogClarificationStarted:
    def test_writes_clarification_started_event(self):
        audit, sb = _audit_with_sb()
        case = _make_case()
        audit.log_clarification_started(case, topic="VKYC_Session_Failure")
        sb.table.assert_called()

    def test_works_in_log_only_mode(self):
        audit = _audit_log_only()
        case = _make_case()
        audit.log_clarification_started(case, topic="VKYC_Session_Failure")

    def test_includes_topic_in_detail(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_clarification_started(
            case,
            topic="OTP_Delivery_Failure",
            workflow_id="wf-1",
            step_id="clarify_slots",
        )
        assert len(written) == 1
        assert written[0].action_type is AuditEventType.CLARIFICATION_STARTED
        assert written[0].action_detail["topic"] == "OTP_Delivery_Failure"

    def test_outcome_is_started(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_clarification_started(case, topic="T")
        assert written[0].outcome == "STARTED"

    def test_includes_missing_slots(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_clarification_started(
            case, topic="T", missing_slots=["session_id", "phone_number"]
        )
        assert "session_id" in written[0].action_detail["missing_slots"]

    def test_includes_workflow_and_step_id(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_clarification_started(
            case, topic="T", workflow_id="wf-xyz", step_id="clarify_1"
        )
        detail = written[0].action_detail
        assert detail.get("workflow_id") == "wf-xyz"
        assert detail.get("step_id") == "clarify_1"

    def test_never_raises_when_supabase_fails(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB offline")
        audit = AuditLogger(supabase_client=sb)
        case = _make_case()
        audit.log_clarification_started(case, topic="T")  # Must not raise


# ── log_clarification_completed ───────────────────────────────────────────────

class TestLogClarificationCompleted:
    def test_writes_clarification_completed_event(self):
        audit, sb = _audit_with_sb()
        case = _make_case()
        audit.log_clarification_completed(
            case, topic="T", status="READY", ready_to_continue=True
        )
        sb.table.assert_called()

    def test_works_in_log_only_mode(self):
        audit = _audit_log_only()
        case = _make_case()
        audit.log_clarification_completed(
            case, topic="T", status="NEEDS_CLARIFICATION", ready_to_continue=False
        )

    def test_outcome_equals_status(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_clarification_completed(
            case, topic="T", status="READY", ready_to_continue=True
        )
        assert written[0].outcome == "READY"
        assert written[0].action_type is AuditEventType.CLARIFICATION_COMPLETED

    def test_includes_ready_to_continue(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_clarification_completed(
            case, topic="T", status="NEEDS_CLARIFICATION", ready_to_continue=False, slot_count=2
        )
        detail = written[0].action_detail
        assert detail["ready_to_continue"] is False
        assert detail["missing_slot_count"] == 2

    def test_never_raises_when_supabase_fails(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB offline")
        audit = AuditLogger(supabase_client=sb)
        case = _make_case()
        audit.log_clarification_completed(
            case, topic="T", status="READY", ready_to_continue=True
        )  # Must not raise


# ── log_workflow_clarification_started ────────────────────────────────────────

class TestLogWorkflowClarificationStarted:
    def test_writes_workflow_clarification_started_event(self):
        audit, sb = _audit_with_sb()
        case = _make_case()
        audit.log_workflow_clarification_started(case, workflow_id="wf-1", step_id="s-1")
        sb.table.assert_called()

    def test_works_in_log_only_mode(self):
        audit = _audit_log_only()
        case = _make_case()
        audit.log_workflow_clarification_started(case, workflow_id="wf-1", step_id="s-1")

    def test_event_type_is_workflow_clarification_started(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_workflow_clarification_started(case, workflow_id="wf-1", step_id="s-1")
        assert written[0].action_type is AuditEventType.WORKFLOW_CLARIFICATION_STARTED

    def test_outcome_is_started(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_workflow_clarification_started(case, workflow_id="wf-1", step_id="s-1")
        assert written[0].outcome == "STARTED"

    def test_includes_workflow_id_and_step_id(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_workflow_clarification_started(
            case, workflow_id="vkyc_session_failure_v1", step_id="clarify_slots"
        )
        detail = written[0].action_detail
        assert detail["workflow_id"] == "vkyc_session_failure_v1"
        assert detail["step_id"] == "clarify_slots"

    def test_never_raises_when_supabase_fails(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB offline")
        audit = AuditLogger(supabase_client=sb)
        case = _make_case()
        audit.log_workflow_clarification_started(case, workflow_id="wf", step_id="s")


# ── log_workflow_clarification_completed ──────────────────────────────────────

class TestLogWorkflowClarificationCompleted:
    def test_writes_workflow_clarification_completed_event(self):
        audit, sb = _audit_with_sb()
        case = _make_case()
        audit.log_workflow_clarification_completed(
            case, workflow_id="wf-1", step_id="s-1", status="READY", ready_to_continue=True
        )
        sb.table.assert_called()

    def test_works_in_log_only_mode(self):
        audit = _audit_log_only()
        case = _make_case()
        audit.log_workflow_clarification_completed(
            case, workflow_id="wf-1", step_id="s-1",
            status="NEEDS_CLARIFICATION", ready_to_continue=False
        )

    def test_event_type_is_workflow_clarification_completed(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_workflow_clarification_completed(
            case, workflow_id="wf", step_id="s", status="READY", ready_to_continue=True
        )
        assert written[0].action_type is AuditEventType.WORKFLOW_CLARIFICATION_COMPLETED

    def test_outcome_equals_status(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_workflow_clarification_completed(
            case, workflow_id="wf", step_id="s",
            status="ESCALATE", ready_to_continue=False
        )
        assert written[0].outcome == "ESCALATE"

    def test_includes_ready_to_continue_in_detail(self, monkeypatch):
        written = []

        def _fake_write(entry):
            written.append(entry)

        audit = _audit_log_only()
        monkeypatch.setattr(audit, "_write", _fake_write)
        case = _make_case()
        audit.log_workflow_clarification_completed(
            case, workflow_id="wf", step_id="s",
            status="READY", ready_to_continue=True
        )
        assert written[0].action_detail["ready_to_continue"] is True

    def test_never_raises_when_supabase_fails(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB offline")
        audit = AuditLogger(supabase_client=sb)
        case = _make_case()
        audit.log_workflow_clarification_completed(
            case, workflow_id="wf", step_id="s",
            status="READY", ready_to_continue=True
        )  # Must not raise
