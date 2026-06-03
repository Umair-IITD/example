"""
tests/test_sprint1_transfer_context.py

TransferContextPayload tests.

Covers:
- to_dict() is JSON-serializable (no datetime objects, no unserializable types)
- to_json() produces valid JSON string
- to_html_note() contains expected HTML structure and key fields
- build_from_case() copies fields from Case correctly
- AttemptedRemediation.to_dict() excludes None error_code
- pii_masked defaults to True
- HTML escaping: special chars in trigger/reason are escaped
- Multiple remediations appear in HTML output
- Empty remediations list produces fallback text
"""
from __future__ import annotations

import json

import pytest

from case_engine.case_state import CaseState
from case_engine.models import Case
from case_engine.transfer_context import AttemptedRemediation, TransferContextPayload


def _case(**kwargs) -> Case:
    defaults: dict = dict(
        ticket_id="TKT-001",
        client="unity_bank",
        topic="OTP_DELIVERY_FAILURE",
        confidence=0.95,
        current_state=CaseState.ESCALATED,
        escalation_reason="below_threshold",
    )
    defaults.update(kwargs)
    return Case(**defaults)


def _payload(**kwargs) -> TransferContextPayload:
    return TransferContextPayload(
        case_id="CASE-XYZ",
        ticket_id="TKT-001",
        client="unity_bank",
        **kwargs,
    )


# ── Serialization ──────────────────────────────────────────────────────────────

class TestSerialization:
    def test_to_dict_is_json_serializable(self):
        p = _payload(
            topic="OTP_DELIVERY_FAILURE",
            confidence=0.95,
            escalation_trigger="below_threshold",
        )
        d = p.to_dict()
        # Should not raise
        json.dumps(d)

    def test_to_json_produces_valid_json(self):
        p = _payload(topic="OTP_DELIVERY_FAILURE")
        raw = p.to_json()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_to_json_contains_case_id(self):
        p = _payload()
        parsed = json.loads(p.to_json())
        assert parsed["case_id"] == "CASE-XYZ"

    def test_to_json_contains_ticket_id(self):
        p = _payload()
        parsed = json.loads(p.to_json())
        assert parsed["ticket_id"] == "TKT-001"

    def test_to_json_contains_payload_version(self):
        p = _payload()
        parsed = json.loads(p.to_json())
        assert "payload_version" in parsed

    def test_to_dict_diagnostic_summary_present(self):
        p = _payload(topic="OTP_DELIVERY_FAILURE", confidence=0.95)
        d = p.to_dict()
        assert "diagnostic_summary" in d
        assert d["diagnostic_summary"]["detected_topic"] == "OTP_DELIVERY_FAILURE"

    def test_attempted_remediations_in_dict(self):
        rem = AttemptedRemediation(action="resend_otp", outcome="failure")
        p   = _payload(attempted_remediations=[rem])
        d   = p.to_dict()
        rems = d["diagnostic_summary"]["attempted_remediations"]
        assert len(rems) == 1
        assert rems[0]["action"] == "resend_otp"


# ── AttemptedRemediation ───────────────────────────────────────────────────────

class TestAttemptedRemediation:
    def test_to_dict_excludes_none_error_code(self):
        rem = AttemptedRemediation(action="resend_otp", outcome="failure", error_code=None)
        d   = rem.to_dict()
        assert "error_code" not in d

    def test_to_dict_includes_error_code_when_present(self):
        rem = AttemptedRemediation(action="trigger_callback", outcome="failure", error_code="CBS_TIMEOUT")
        d   = rem.to_dict()
        assert d["error_code"] == "CBS_TIMEOUT"

    def test_timestamp_auto_populated(self):
        rem = AttemptedRemediation(action="resend_otp", outcome="success")
        assert rem.timestamp and len(rem.timestamp) > 0

    def test_custom_timestamp_preserved(self):
        rem = AttemptedRemediation(action="resend_otp", outcome="success", timestamp="2026-01-01T00:00:00+00:00")
        assert "2026-01-01" in rem.timestamp


# ── HTML note formatting ───────────────────────────────────────────────────────

class TestHtmlNote:
    def test_html_contains_case_id(self):
        p    = _payload()
        html = p.to_html_note()
        assert "CASE-XYZ" in html

    def test_html_contains_topic(self):
        p    = _payload(topic="OTP_DELIVERY_FAILURE")
        html = p.to_html_note()
        assert "OTP_DELIVERY_FAILURE" in html

    def test_html_contains_escalation_trigger(self):
        p    = _payload(escalation_trigger="below_threshold")
        html = p.to_html_note()
        assert "below_threshold" in html

    def test_html_contains_reason(self):
        p    = _payload(escalation_reason="Confidence too low")
        html = p.to_html_note()
        assert "Confidence too low" in html

    def test_html_escapes_special_chars_in_trigger(self):
        p    = _payload(escalation_trigger="<script>alert(1)</script>")
        html = p.to_html_note()
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_escapes_special_chars_in_reason(self):
        p    = _payload(escalation_reason='Reason with "quotes" & ampersand')
        html = p.to_html_note()
        assert '"quotes"' not in html    # raw quotes should be escaped
        assert "&amp;" in html

    def test_html_no_remediations_shows_fallback(self):
        p    = _payload(attempted_remediations=[])
        html = p.to_html_note()
        assert "No remediations attempted" in html

    def test_html_multiple_remediations(self):
        rems = [
            AttemptedRemediation(action="resend_otp", outcome="failure"),
            AttemptedRemediation(action="reset_session", outcome="success"),
        ]
        p    = _payload(attempted_remediations=rems)
        html = p.to_html_note()
        assert "resend_otp" in html
        assert "reset_session" in html

    def test_html_pii_masked_shown(self):
        p    = _payload(pii_masked=True)
        html = p.to_html_note()
        assert "PII masked: True" in html

    def test_html_confidence_formatted(self):
        p    = _payload(confidence=0.9512)
        html = p.to_html_note()
        # Confidence shown to 2 decimal places
        assert "0.95" in html

    def test_html_no_confidence_shows_na(self):
        p    = _payload(confidence=None)
        html = p.to_html_note()
        assert "N/A" in html

    def test_html_contains_hr_separator(self):
        p    = _payload()
        html = p.to_html_note()
        assert "<hr>" in html


# ── build_from_case() ─────────────────────────────────────────────────────────

class TestBuildFromCase:
    def test_copies_case_id(self):
        case = _case()
        p    = TransferContextPayload.build_from_case(case, escalation_trigger="below_threshold")
        assert p.case_id == case.case_id

    def test_copies_ticket_id(self):
        case = _case()
        p    = TransferContextPayload.build_from_case(case)
        assert p.ticket_id == "TKT-001"

    def test_copies_topic(self):
        case = _case()
        p    = TransferContextPayload.build_from_case(case)
        assert p.topic == "OTP_DELIVERY_FAILURE"

    def test_copies_confidence(self):
        case = _case()
        p    = TransferContextPayload.build_from_case(case)
        assert p.confidence == 0.95

    def test_copies_escalation_reason_from_case(self):
        case = _case(escalation_reason="no_match")
        p    = TransferContextPayload.build_from_case(case)
        assert p.escalation_reason == "no_match"

    def test_override_escalation_trigger(self):
        case = _case()
        p    = TransferContextPayload.build_from_case(case, escalation_trigger="pii_detected")
        assert p.escalation_trigger == "pii_detected"

    def test_override_root_cause_analysis(self):
        case = _case()
        p    = TransferContextPayload.build_from_case(
            case, root_cause_analysis="OTP DND block on mobile number"
        )
        assert p.root_cause_analysis == "OTP DND block on mobile number"

    def test_copies_attempted_remediations_from_case(self):
        case = _case()
        case.attempted_remediations = [
            {"action": "resend_otp", "outcome": "failure", "timestamp": "2026-01-01T00:00:00+00:00"}
        ]
        p = TransferContextPayload.build_from_case(case)
        assert len(p.attempted_remediations) == 1
        assert p.attempted_remediations[0].action == "resend_otp"

    def test_cited_sop_ids_passed_through(self):
        case = _case()
        p    = TransferContextPayload.build_from_case(case, cited_sop_ids=["SOP-001", "SOP-002"])
        assert p.cited_sop_ids == ["SOP-001", "SOP-002"]

    def test_pii_masked_defaults_true(self):
        p = _payload()
        assert p.pii_masked is True


# ── No datetime objects in output ─────────────────────────────────────────────

class TestNoDatetimeInOutput:
    def test_to_dict_has_no_datetime_objects(self):
        from datetime import datetime
        p = _payload(topic="OTP_DELIVERY_FAILURE")
        d = p.to_dict()
        raw_json = json.dumps(d)  # would raise if datetime present
        parsed   = json.loads(raw_json)
        # generated_at should be a string, not a datetime
        assert isinstance(parsed["generated_at"], str)
