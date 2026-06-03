"""
tests/test_sprint1_audit.py

AuditLogger tests.

Covers:
- Log-only mode (no DB): all log methods complete without raising
- Correct AuditEventType used for each method
- DB write called with correct table name and non-empty row
- _write() swallows DB exceptions — primary request path is never blocked
- log_error populates error_code field
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from case_engine.audit import AuditLogger, _AUDIT_TABLE
from case_engine.case_state import CaseState
from case_engine.models import AuditEventType, Case, CaseTransition


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _case() -> Case:
    return Case(ticket_id="TKT-001", client="unity_bank")


def _transition(case: Case) -> CaseTransition:
    return CaseTransition(
        case_id=case.case_id,
        from_state=CaseState.NEW,
        to_state=CaseState.CLASSIFYING,
        reason="unit test",
        actor="system",
    )


def _mock_sb():
    """Return a Supabase client mock whose .table().insert().execute() chain succeeds."""
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock()
    return sb


# ── Log-only mode ─────────────────────────────────────────────────────────────

class TestLogOnlyMode:
    """When supabase_client=None, all log methods must complete without raising."""

    def test_log_transition_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_transition(case, _transition(case))   # must not raise

    def test_log_classification_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_classification(case, topic="OTP_DELIVERY_FAILURE", confidence=0.95, tier_used=1, meets_threshold=True)

    def test_log_rag_call_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_rag_call(case, match_type="exact_match", confidence="high", chunks_count=3, requires_human=False)

    def test_log_note_posted_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_note_posted(case, note_type="resolution", confidence="high")

    def test_log_escalation_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_escalation(case, trigger="below_threshold", reason="low confidence", priority="medium")

    def test_log_error_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_error(case, error_type="CLASSIFIER_CRASH", error_msg="unexpected exception")


# ── DB-mode: correct event types ──────────────────────────────────────────────

class TestEventTypes:
    """Verify each method sends the right AuditEventType to _write."""

    def _capture_entries(self) -> tuple[AuditLogger, list]:
        """Return an AuditLogger that captures every AuditEntry passed to _write."""
        captured = []
        al = AuditLogger()
        al._write = lambda entry: captured.append(entry)
        return al, captured

    def test_log_transition_type(self):
        al, entries = self._capture_entries()
        case = _case()
        al.log_transition(case, _transition(case))
        assert entries[0].action_type == AuditEventType.STATE_TRANSITION

    def test_log_classification_type(self):
        al, entries = self._capture_entries()
        case = _case()
        al.log_classification(case, topic="OTP_DELIVERY_FAILURE", confidence=0.95, tier_used=1, meets_threshold=True)
        assert entries[0].action_type == AuditEventType.CLASSIFICATION

    def test_log_rag_call_type(self):
        al, entries = self._capture_entries()
        case = _case()
        al.log_rag_call(case, match_type="exact_match", confidence="high", chunks_count=2, requires_human=False)
        assert entries[0].action_type == AuditEventType.RAG_CALLED

    def test_log_note_posted_type(self):
        al, entries = self._capture_entries()
        case = _case()
        al.log_note_posted(case, note_type="resolution", confidence="high")
        assert entries[0].action_type == AuditEventType.NOTE_POSTED

    def test_log_escalation_type(self):
        al, entries = self._capture_entries()
        case = _case()
        al.log_escalation(case, trigger="no_match", reason="no SOP", priority="high")
        assert entries[0].action_type == AuditEventType.ESCALATION_TRIGGERED

    def test_log_error_type(self):
        al, entries = self._capture_entries()
        case = _case()
        al.log_error(case, error_type="TIMEOUT", error_msg="classifier timed out")
        assert entries[0].action_type == AuditEventType.ERROR


# ── DB-mode: correct table and payload ────────────────────────────────────────

class TestDBWrite:
    """When supabase_client is provided, _write must insert into the correct table."""

    def test_writes_to_correct_table(self):
        sb   = _mock_sb()
        al   = AuditLogger(sb)
        case = _case()
        al.log_transition(case, _transition(case))
        sb.table.assert_called_once_with(_AUDIT_TABLE)

    def test_row_contains_case_id(self):
        sb   = _mock_sb()
        al   = AuditLogger(sb)
        case = _case()
        al.log_rag_call(case, match_type="exact_match", confidence="high", chunks_count=2, requires_human=False)
        insert_call_args = sb.table.return_value.insert.call_args[0][0]
        assert insert_call_args.get("case_id") == case.case_id

    def test_row_contains_ticket_id(self):
        sb   = _mock_sb()
        al   = AuditLogger(sb)
        case = _case()
        al.log_note_posted(case, note_type="resolution", confidence="high")
        row = sb.table.return_value.insert.call_args[0][0]
        assert row.get("ticket_id") == "TKT-001"

    def test_log_error_populates_error_code(self):
        captured = []
        al = AuditLogger()
        al._write = lambda entry: captured.append(entry)
        case = _case()
        al.log_error(case, error_type="DB_TIMEOUT", error_msg="connection refused")
        assert captured[0].error_code == "DB_TIMEOUT"


# ── _write() failure swallowing ───────────────────────────────────────────────

class TestWriteFailureSwallowing:
    """DB errors inside _write must not propagate — audit failure must not crash the request."""

    def test_db_error_does_not_raise(self):
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError("DB down")
        al   = AuditLogger(sb)
        case = _case()
        # Should complete without raising
        al.log_transition(case, _transition(case))
        assert True  # reached here = no raise

    def test_db_error_logged_at_error_level(self, caplog):
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.side_effect = Exception("network error")
        al   = AuditLogger(sb)
        case = _case()
        with caplog.at_level(logging.ERROR, logger="case_engine.audit"):
            al.log_transition(case, _transition(case))
        assert any("audit_write_failed" in r.message for r in caplog.records)

    def test_subsequent_calls_succeed_after_db_error(self):
        sb = MagicMock()
        # First call fails, second succeeds
        sb.table.return_value.insert.return_value.execute.side_effect = [
            Exception("first call fails"),
            MagicMock(),
        ]
        al   = AuditLogger(sb)
        case = _case()
        al.log_transition(case, _transition(case))   # fails internally
        al.log_note_posted(case, note_type="resolution", confidence="high")  # must still run
        assert sb.table.call_count == 2


# ── Transition-specific fields ─────────────────────────────────────────────────

class TestTransitionDetails:
    def test_log_transition_action_detail_contains_states(self):
        captured = []
        al = AuditLogger()
        al._write = lambda entry: captured.append(entry)
        case = _case()
        t    = _transition(case)
        al.log_transition(case, t)
        detail = captured[0].action_detail
        assert detail["from_state"] == CaseState.NEW.value
        assert detail["to_state"]   == CaseState.CLASSIFYING.value
        assert detail["reason"]     == "unit test"

    def test_log_classification_below_threshold_outcome(self):
        captured = []
        al = AuditLogger()
        al._write = lambda entry: captured.append(entry)
        case = _case()
        al.log_classification(case, topic="UNKNOWN", confidence=0.60, tier_used=0, meets_threshold=False)
        assert captured[0].outcome == "BELOW_THRESHOLD"

    def test_log_classification_above_threshold_outcome(self):
        captured = []
        al = AuditLogger()
        al._write = lambda entry: captured.append(entry)
        case = _case()
        al.log_classification(case, topic="OTP_DELIVERY_FAILURE", confidence=0.95, tier_used=1, meets_threshold=True)
        assert captured[0].outcome == "PASS"
