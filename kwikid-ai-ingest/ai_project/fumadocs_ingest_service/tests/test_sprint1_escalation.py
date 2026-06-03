"""
tests/test_sprint1_escalation.py

EscalationEngine deterministic rule tests.

Covers:
- No escalation for clean inputs
- All URGENT triggers fire before HIGH/MEDIUM/LOW triggers
- Every trigger type produces should_escalate=True with correct trigger enum
- Priority ordering: security flags take precedence over classifier gates
- consecutive_failures < 3 does NOT trigger escalation
- weak_match alone does NOT trigger escalation (per design note in escalation.py)
- No LLM call can occur (EscalationEngine is pure Python)
"""
from __future__ import annotations

import pytest

from case_engine.case_state import CaseState
from case_engine.escalation import (
    EscalationEngine,
    EscalationPriority,
    EscalationTrigger,
    TRIGGER_PRIORITY,
)
from case_engine.models import Case


def _case(**kwargs) -> Case:
    return Case(ticket_id="TKT-001", client="unity_bank", **kwargs)


def _engine() -> EscalationEngine:
    return EscalationEngine()


# ── No escalation baseline ─────────────────────────────────────────────────────

class TestNoEscalation:
    """All-clear inputs must not escalate."""

    def test_clean_inputs_do_not_escalate(self):
        eng = _engine()
        decision = eng.evaluate(
            _case(),
            classification_confidence=0.95,
            topic_known=True,
            match_type="exact_match",
            generation_confidence="high",
            requires_human=False,
        )
        assert decision.should_escalate is False
        assert decision.trigger is None

    def test_related_match_does_not_escalate(self):
        eng = _engine()
        decision = eng.evaluate(
            _case(), topic_known=True, match_type="related_match",
            classification_confidence=0.90,
        )
        assert decision.should_escalate is False

    def test_weak_match_alone_does_not_escalate(self):
        """Per design: weak_match defers to BranchCompletenessChecker in Level 2."""
        eng = _engine()
        decision = eng.evaluate(
            _case(), topic_known=True, match_type="weak_match",
            classification_confidence=0.90,
        )
        assert decision.should_escalate is False

    def test_two_consecutive_failures_does_not_escalate(self):
        eng = _engine()
        decision = eng.evaluate(_case(), consecutive_failures=2)
        assert decision.should_escalate is False


# ── URGENT triggers ────────────────────────────────────────────────────────────

class TestUrgentTriggers:
    def test_security_flag_deepfake(self):
        eng = _engine()
        d = eng.evaluate(_case(), security_flags=["DEEPFAKE_DETECTED"])
        assert d.should_escalate is True
        assert d.trigger   == EscalationTrigger.SECURITY_SIGNAL
        assert d.priority  == EscalationPriority.URGENT

    def test_security_flag_spoofing(self):
        eng = _engine()
        d = eng.evaluate(_case(), security_flags=["SPOOFING_ATTEMPT"])
        assert d.trigger == EscalationTrigger.SECURITY_SIGNAL

    def test_security_flag_foreign_ip(self):
        eng = _engine()
        d = eng.evaluate(_case(), security_flags=["FOREIGN_IP_CONNECTION"])
        assert d.trigger == EscalationTrigger.SECURITY_SIGNAL

    def test_security_flag_multi_device(self):
        eng = _engine()
        d = eng.evaluate(_case(), security_flags=["MULTI_DEVICE_SAME_SESSION"])
        assert d.trigger == EscalationTrigger.SECURITY_SIGNAL

    def test_unknown_security_flag_does_not_escalate(self):
        eng = _engine()
        d = eng.evaluate(_case(), security_flags=["SOME_UNKNOWN_FLAG"])
        assert d.should_escalate is False

    def test_security_freeze_active(self):
        eng = _engine()
        d = eng.evaluate(_case(), security_freeze_active=True)
        assert d.trigger  == EscalationTrigger.SECURITY_FREEZE_ACTIVE
        assert d.priority == EscalationPriority.URGENT

    def test_hard_lock_active(self):
        eng = _engine()
        d = eng.evaluate(_case(), hard_lock_active=True)
        assert d.trigger  == EscalationTrigger.COMPLIANCE_FLAG
        assert d.priority == EscalationPriority.URGENT

    def test_pii_detected(self):
        eng = _engine()
        d = eng.evaluate(_case(), pii_detected_in_output=True)
        assert d.trigger  == EscalationTrigger.PII_DETECTED
        assert d.priority == EscalationPriority.URGENT

    def test_session_ownership_mismatch(self):
        eng = _engine()
        d = eng.evaluate(_case(), session_ownership_mismatch=True)
        assert d.trigger  == EscalationTrigger.SESSION_MISMATCH
        assert d.priority == EscalationPriority.URGENT


# ── HIGH triggers ──────────────────────────────────────────────────────────────

class TestHighTriggers:
    def test_irreversible_action(self):
        eng = _engine()
        d = eng.evaluate(_case(), irreversible_action_proposed=True)
        assert d.trigger  == EscalationTrigger.IRREVERSIBLE_ACTION
        assert d.priority == EscalationPriority.HIGH

    def test_unknown_topic(self):
        eng = _engine()
        d = eng.evaluate(_case(), topic_known=False)
        assert d.trigger  == EscalationTrigger.UNKNOWN_TOPIC
        assert d.priority == EscalationPriority.MEDIUM  # per TRIGGER_PRIORITY

    def test_below_threshold_confidence(self):
        eng = _engine()
        d = eng.evaluate(_case(), topic_known=True, classification_confidence=0.70)
        assert d.trigger  == EscalationTrigger.BELOW_THRESHOLD
        assert d.priority == EscalationPriority.MEDIUM

    def test_exactly_at_threshold_does_not_escalate(self):
        eng = _engine()
        d = eng.evaluate(_case(), topic_known=True, classification_confidence=0.85)
        assert d.should_escalate is False

    def test_no_match_retrieval(self):
        eng = _engine()
        d = eng.evaluate(_case(), topic_known=True, classification_confidence=0.90,
                         match_type="no_match")
        assert d.trigger == EscalationTrigger.NO_MATCH

    def test_requires_human_flag(self):
        eng = _engine()
        d = eng.evaluate(_case(), topic_known=True, classification_confidence=0.90,
                         requires_human=True)
        assert d.trigger  == EscalationTrigger.LOW_GENERATION_CONF
        assert d.priority == EscalationPriority.MEDIUM

    def test_consecutive_failures_at_threshold(self):
        eng = _engine()
        d = eng.evaluate(_case(), consecutive_failures=3)
        assert d.trigger  == EscalationTrigger.CONSECUTIVE_FAILURES
        assert d.priority == EscalationPriority.HIGH

    def test_slot_fill_timeout(self):
        eng = _engine()
        d = eng.evaluate(_case(), slot_fill_timed_out=True)
        assert d.trigger  == EscalationTrigger.SLOT_FILL_TIMEOUT
        assert d.priority == EscalationPriority.MEDIUM

    def test_workflow_error(self):
        eng = _engine()
        d = eng.evaluate(_case(), workflow_error=True)
        assert d.trigger  == EscalationTrigger.WORKFLOW_FAILURE
        assert d.priority == EscalationPriority.HIGH

    def test_customer_requested_escalation(self):
        eng = _engine()
        d = eng.evaluate(
            _case(),
            topic_known=True,
            classification_confidence=0.90,
            ticket_text="I want to speak to an agent immediately.",
        )
        assert d.trigger  == EscalationTrigger.CUSTOMER_REQUESTED
        assert d.priority == EscalationPriority.HIGH

    def test_customer_rbi_mention(self):
        eng = _engine()
        d = eng.evaluate(
            _case(),
            topic_known=True,
            classification_confidence=0.90,
            ticket_text="I will raise this with RBI if not resolved.",
        )
        assert d.trigger == EscalationTrigger.CUSTOMER_REQUESTED

    def test_customer_banking_ombudsman(self):
        eng = _engine()
        d = eng.evaluate(
            _case(),
            topic_known=True,
            classification_confidence=0.90,
            ticket_text="I want to contact the banking ombudsman.",
        )
        assert d.trigger == EscalationTrigger.CUSTOMER_REQUESTED


# ── Priority ordering: URGENT beats lower priorities ──────────────────────────

class TestPriorityOrdering:
    """Security triggers must fire even when lower-priority triggers also apply."""

    def test_security_freeze_beats_below_threshold(self):
        eng = _engine()
        d = eng.evaluate(
            _case(),
            security_freeze_active=True,
            classification_confidence=0.50,  # would trigger BELOW_THRESHOLD
            topic_known=False,               # would trigger UNKNOWN_TOPIC
        )
        assert d.trigger == EscalationTrigger.SECURITY_FREEZE_ACTIVE

    def test_pii_detected_beats_no_match(self):
        eng = _engine()
        d = eng.evaluate(
            _case(),
            pii_detected_in_output=True,
            match_type="no_match",
        )
        assert d.trigger == EscalationTrigger.PII_DETECTED

    def test_security_flags_beat_everything(self):
        eng = _engine()
        d = eng.evaluate(
            _case(),
            security_flags=["DEEPFAKE_DETECTED"],
            topic_known=False,
            match_type="no_match",
            workflow_error=True,
            pii_detected_in_output=True,
        )
        assert d.trigger == EscalationTrigger.SECURITY_SIGNAL


# ── EscalationDecision structure ──────────────────────────────────────────────

class TestDecisionStructure:
    def test_decision_has_reason_on_escalation(self):
        eng = _engine()
        d = eng.evaluate(_case(), topic_known=False)
        assert d.reason and len(d.reason) > 0

    def test_no_escalation_has_no_trigger(self):
        eng = _engine()
        d = eng.evaluate(_case(), topic_known=True, classification_confidence=0.95)
        assert d.trigger is None
        assert d.reason == ""

    def test_transfer_context_included_for_security_signal(self):
        eng = _engine()
        d = eng.evaluate(_case(), security_flags=["DEEPFAKE_DETECTED"])
        assert d.transfer_context is not None
        assert "security_flags" in d.transfer_context


# ── TRIGGER_PRIORITY table completeness ───────────────────────────────────────

class TestTriggerPriorityTable:
    def test_all_triggers_have_priority(self):
        for trigger in EscalationTrigger:
            assert trigger in TRIGGER_PRIORITY, f"{trigger} is missing from TRIGGER_PRIORITY"

    def test_urgent_triggers_are_correct(self):
        urgent = {t for t, p in TRIGGER_PRIORITY.items() if p == EscalationPriority.URGENT}
        assert EscalationTrigger.SECURITY_SIGNAL        in urgent
        assert EscalationTrigger.SECURITY_FREEZE_ACTIVE in urgent
        assert EscalationTrigger.PII_DETECTED           in urgent
        assert EscalationTrigger.SESSION_MISMATCH       in urgent
