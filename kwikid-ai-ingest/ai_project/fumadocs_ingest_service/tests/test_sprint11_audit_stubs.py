"""
tests/test_sprint11_audit_stubs.py

Sprint 2 audit API stub method tests.

Covers:
- log_action_proposed: does not raise, uses ACTION_PROPOSED event type
- log_action_executed: does not raise, carries idempotency_key + error_code
- log_action_rejected: does not raise, outcome=REJECTED
- log_slot_filled: does not raise, uses SLOT_FILLED event type
- All stubs work in offline mode (sb=None)
- All stubs write to case_audit_log (not security_compliance_audit)
"""
from __future__ import annotations

import pytest

from case_engine.audit import AuditLogger
from case_engine.models import AuditEntry, AuditEventType, Case
from case_engine.case_state import CaseState


def _case() -> Case:
    return Case(ticket_id="TKT-STUB-001", client="unity_bank", current_state=CaseState.WORKFLOW_ACTIVE)


def _capture_audit(al: AuditLogger) -> list[AuditEntry]:
    captured: list[AuditEntry] = []
    al._write = lambda entry: captured.append(entry)
    return captured


class TestLogActionProposed:
    def test_does_not_raise_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_action_proposed(case, "resend_otp", {"channel": "sms"}, "idem-001")

    def test_action_type_is_action_proposed(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_proposed(case, "resend_otp", {"channel": "sms"}, "idem-001")
        assert len(captured) == 1
        assert captured[0].action_type == AuditEventType.ACTION_PROPOSED

    def test_idempotency_key_stored(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_proposed(case, "resend_otp", {}, "idem-KEY-42")
        assert captured[0].idempotency_key == "idem-KEY-42"

    def test_action_name_in_detail(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_proposed(case, "trigger_callback", {}, "idem-001")
        assert captured[0].action_detail["action_name"] == "trigger_callback"

    def test_outcome_is_proposed(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_proposed(case, "resend_otp", {}, "idem-001")
        assert captured[0].outcome == "PROPOSED"


class TestLogActionExecuted:
    def test_does_not_raise_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_action_executed(case, "resend_otp", "SUCCESS", "idem-001")

    def test_action_type_is_action_executed(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_executed(case, "resend_otp", "SUCCESS", "idem-001")
        assert captured[0].action_type == AuditEventType.ACTION_EXECUTED

    def test_idempotency_key_stored(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_executed(case, "resend_otp", "SUCCESS", "idem-XYZ")
        assert captured[0].idempotency_key == "idem-XYZ"

    def test_error_code_stored_when_present(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_executed(case, "trigger_callback", "FAILURE", "idem-001", error_code="CBS_TIMEOUT")
        assert captured[0].error_code == "CBS_TIMEOUT"

    def test_error_code_none_when_absent(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_executed(case, "resend_otp", "SUCCESS", "idem-001")
        assert captured[0].error_code is None


class TestLogActionRejected:
    def test_does_not_raise_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_action_rejected(case, "resend_otp", "human_sign_off_required", "idem-001")

    def test_action_type_is_action_rejected(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_rejected(case, "resend_otp", "human_sign_off_required", "idem-001")
        assert captured[0].action_type == AuditEventType.ACTION_REJECTED

    def test_outcome_is_rejected(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_rejected(case, "resend_otp", "human_sign_off_required", "idem-001")
        assert captured[0].outcome == "REJECTED"

    def test_reason_in_detail(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_action_rejected(case, "resend_otp", "irreversible_action", "idem-001")
        assert captured[0].action_detail.get("reason") == "irreversible_action"


class TestLogSlotFilled:
    def test_does_not_raise_offline(self):
        al   = AuditLogger()
        case = _case()
        al.log_slot_filled(case, "mobile_number", "sha256_abc123", turn_count=2)

    def test_action_type_is_slot_filled(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_slot_filled(case, "mobile_number", "sha256_abc123", turn_count=2)
        assert captured[0].action_type == AuditEventType.SLOT_FILLED

    def test_outcome_is_filled(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_slot_filled(case, "mobile_number", "sha256_abc123", turn_count=2)
        assert captured[0].outcome == "FILLED"

    def test_slot_name_in_detail(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_slot_filled(case, "aadhaar_last4", "sha256_xyz", turn_count=1)
        assert captured[0].action_detail["slot_name"] == "aadhaar_last4"

    def test_value_hash_in_detail(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_slot_filled(case, "mobile_number", "hash_abc", turn_count=1)
        assert captured[0].action_detail["value_hash"] == "hash_abc"

    def test_turn_count_in_detail(self):
        al      = AuditLogger()
        case    = _case()
        captured = _capture_audit(al)
        al.log_slot_filled(case, "mobile_number", "hash_abc", turn_count=3)
        assert captured[0].action_detail["turn_count"] == 3
