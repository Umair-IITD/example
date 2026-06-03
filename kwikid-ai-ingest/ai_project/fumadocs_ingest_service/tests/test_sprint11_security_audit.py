"""
tests/test_sprint11_security_audit.py

AuditLogger.log_security_event() unit tests.

Covers:
- log_security_event does not raise in offline mode (sb=None)
- log_security_event routes to security_compliance_audit, not case_audit_log
- security_event_type, event_details, severity are captured in the row
- reported_to_soc defaults to False
- detected_at is populated
- Failure in _write_security does not raise (never raises contract)
- case_id and ticket_id are populated from Case
"""
from __future__ import annotations

import pytest

from case_engine.audit import AuditLogger
from case_engine.case_state import CaseState
from case_engine.models import Case


def _case() -> Case:
    return Case(ticket_id="TKT-SEC-001", client="unity_bank", current_state=CaseState.ESCALATED)


def _capture_security(al: AuditLogger) -> list[dict]:
    """Intercept _write_security calls and collect rows."""
    captured: list[dict] = []
    al._write_security = lambda row: captured.append(row)
    return captured


class TestLogSecurityEvent:
    def test_does_not_raise_offline(self):
        al   = AuditLogger(supabase_client=None)
        case = _case()
        al.log_security_event(
            case,
            security_event_type="DEEPFAKE_SUSPECTED",
            event_details={"score": 0.92},
            severity="CRITICAL",
        )
        # No exception raised — offline mode logs only

    def test_routes_to_security_table_not_audit_table(self):
        al   = AuditLogger(supabase_client=None)
        case = _case()

        # Intercept which table is written to
        written_to: list[str] = []
        al._write_security = lambda row: written_to.append("security")
        al._write         = lambda entry: written_to.append("audit")

        # Offline mode: _sb is None so _write_security returns immediately.
        # Override to simulate a call being made.
        original_write_security = AuditLogger._write_security

        def fake_write_security(self_inner, row):
            written_to.append("security")

        AuditLogger._write_security = fake_write_security  # type: ignore[method-assign]
        try:
            al.log_security_event(
                case,
                security_event_type="FOREIGN_IP",
                event_details={"ip": "1.2.3.4"},
            )
        finally:
            AuditLogger._write_security = original_write_security  # type: ignore[method-assign]

        # _write (case_audit_log) must NOT have been called
        assert "audit" not in written_to

    def test_row_contains_security_event_type(self):
        al      = AuditLogger(supabase_client=object())  # non-None sb to trigger write path
        case    = _case()
        captured = _capture_security(al)

        al.log_security_event(
            case,
            security_event_type="AADHAAR_MISMATCH",
            event_details={"field": "aadhaar_number"},
            severity="HIGH",
        )
        assert len(captured) == 1
        assert captured[0]["security_event_type"] == "AADHAAR_MISMATCH"

    def test_row_severity_recorded(self):
        al      = AuditLogger(supabase_client=object())
        case    = _case()
        captured = _capture_security(al)

        al.log_security_event(
            case,
            security_event_type="FOREIGN_IP",
            event_details={},
            severity="CRITICAL",
        )
        assert captured[0]["severity"] == "CRITICAL"

    def test_row_default_severity_is_high(self):
        al      = AuditLogger(supabase_client=object())
        case    = _case()
        captured = _capture_security(al)

        al.log_security_event(
            case,
            security_event_type="FOREIGN_IP",
            event_details={},
        )
        assert captured[0]["severity"] == "HIGH"

    def test_row_reported_to_soc_false_by_default(self):
        al      = AuditLogger(supabase_client=object())
        case    = _case()
        captured = _capture_security(al)

        al.log_security_event(
            case,
            security_event_type="DEEPFAKE_SUSPECTED",
            event_details={},
        )
        assert captured[0]["reported_to_soc"] is False

    def test_row_contains_case_id(self):
        al      = AuditLogger(supabase_client=object())
        case    = _case()
        captured = _capture_security(al)

        al.log_security_event(
            case,
            security_event_type="DEEPFAKE_SUSPECTED",
            event_details={},
        )
        assert captured[0]["case_id"] == case.case_id

    def test_row_contains_ticket_id(self):
        al      = AuditLogger(supabase_client=object())
        case    = _case()
        captured = _capture_security(al)

        al.log_security_event(
            case,
            security_event_type="DEEPFAKE_SUSPECTED",
            event_details={},
        )
        assert captured[0]["ticket_id"] == "TKT-SEC-001"

    def test_row_detected_at_populated(self):
        al      = AuditLogger(supabase_client=object())
        case    = _case()
        captured = _capture_security(al)

        al.log_security_event(
            case,
            security_event_type="DEEPFAKE_SUSPECTED",
            event_details={},
        )
        assert captured[0]["detected_at"]
        assert len(captured[0]["detected_at"]) > 10  # non-empty timestamp string

    def test_write_failure_does_not_raise(self):
        # supabase_client=object() causes AttributeError when _write_security
        # calls self._sb.table(...). That AttributeError is caught by
        # _write_security's own try/except and logged — no exception propagates.
        al   = AuditLogger(supabase_client=object())
        case = _case()
        al.log_security_event(
            case,
            security_event_type="FOREIGN_IP",
            event_details={"ip": "1.2.3.4"},
        )
        # Absence of exception is the assertion
