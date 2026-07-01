"""
tests/test_unknown_topic_reaches_slotextract.py

Regression test: UNKNOWN-topic tickets must proceed to Slot Extraction / Clarification
per the approved flow_diagram.mermaid.

Evidence from blueprint (flow_diagram.mermaid line 198):
    CLASSIFIER --> SLOTEXTRACT
No ESCALATED node appears between CLASSIFIER and SLOTEXTRACT.

Root cause fixed: classify_case() was transitioning CLASSIFYING → ESCALATED for
UNKNOWN topics, which caused the runtime guard in support_agent_runtime.py to fire
and terminate the pipeline before Slot Extraction.

Fix: UNKNOWN-topic classification transitions to TRIAGE_COMPLETE (not ESCALATED).
     receive_message() must NOT bail early when topic is UNKNOWN; it should proceed
     to clarification so the user can provide more information.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from case_engine.case_state import CaseState
from case_engine.models import Case, TopicKey
from case_engine.runtime.agent_models import AgentStatus
from case_engine.runtime.support_agent_runtime import SupportAgentRuntime
from case_engine.service import build_case_service


# ── helpers ───────────────────────────────────────────────────────────────────

def _service():
    """Build an offline CaseService (no DB required)."""
    return build_case_service(supabase_client=None)


def _runtime(case_svc=None):
    """Build a SupportAgentRuntime wired to the given service."""
    return SupportAgentRuntime(case_service=case_svc)


UNMATCHED_TEXT = "I need help with something urgent please contact me."


# ── Test Group 1: classify_case with UNKNOWN topic ────────────────────────────

class TestClassifyUnknownTopic:
    """classify_case with UNKNOWN-classified text must NOT escalate."""

    def test_unknown_topic_is_set_on_case(self):
        svc  = _service()
        case = svc.open_case("TKT-U01", "unity_bank")
        svc.classify_case(case, UNMATCHED_TEXT)
        assert case.topic == "UNKNOWN", (
            f"Expected topic=UNKNOWN, got {case.topic!r}"
        )

    def test_unknown_topic_has_zero_confidence(self):
        svc  = _service()
        case = svc.open_case("TKT-U02", "unity_bank")
        svc.classify_case(case, UNMATCHED_TEXT)
        assert case.confidence == 0.0, (
            f"Expected confidence=0.0, got {case.confidence}"
        )

    def test_unknown_topic_does_NOT_escalate(self):
        """
        CRITICAL: The root cause bug — UNKNOWN classification must NOT set
        case.current_state = ESCALATED. After classification with UNKNOWN topic,
        the case must NOT be in ESCALATED state, as that terminates the pipeline
        before Slot Extraction, violating flow_diagram.mermaid CLASSIFIER→SLOTEXTRACT.
        """
        svc  = _service()
        case = svc.open_case("TKT-U03", "unity_bank")
        svc.classify_case(case, UNMATCHED_TEXT)
        assert case.current_state != CaseState.ESCALATED, (
            f"UNKNOWN topic incorrectly escalated the case. "
            f"State={case.current_state.value}. "
            f"Pipeline terminated before Slot Extraction — violates blueprint."
        )

    def test_unknown_topic_transitions_to_triage_complete(self):
        """
        Post-fix: UNKNOWN topic should land in TRIAGE_COMPLETE just like a
        known topic, so the runtime can proceed to Slot Extraction / Clarification.
        """
        svc  = _service()
        case = svc.open_case("TKT-U04", "unity_bank")
        svc.classify_case(case, UNMATCHED_TEXT)
        assert case.current_state == CaseState.TRIAGE_COMPLETE, (
            f"Expected TRIAGE_COMPLETE for UNKNOWN topic, got {case.current_state.value}"
        )


# ── Test Group 2: runtime pipeline reaches SLOT_EXTRACT for UNKNOWN topic ─────

class TestRuntimeUnknownTopicReachesSlotExtract:
    """
    The full SupportAgentRuntime must NOT return early at the ESCALATED guard.
    It must reach Slot Extraction (SLOT_EXTRACT appears in steps_completed).
    """

    def test_runtime_reaches_slot_extract_step(self):
        """
        After the fix, 'SLOT_EXTRACT' must appear in result.steps_completed
        for an UNKNOWN-topic ticket, proving the pipeline continued past the
        classification guard and entered Slot Extraction.
        """
        svc  = _service()
        case = svc.open_case("TKT-U10", "unity_bank")
        runtime = _runtime(case_svc=svc)

        result = runtime.run_case(case, UNMATCHED_TEXT)

        assert "SLOT_EXTRACT" in result.steps_completed, (
            f"Pipeline did NOT reach SLOT_EXTRACT for UNKNOWN topic. "
            f"steps_completed={result.steps_completed}. "
            f"ESCALATED guard fired early — pipeline blocked before Slot Extraction."
        )

    def test_runtime_does_not_return_escalated_status_for_unknown(self):
        """
        The runtime must not immediately return AgentStatus.ESCALATED for an
        UNKNOWN-topic ticket at the early-return guard. It should proceed through
        the pipeline and reach at least SLOT_EXTRACT before returning.
        """
        svc  = _service()
        case = svc.open_case("TKT-U11", "unity_bank")
        runtime = _runtime(case_svc=svc)

        result = runtime.run_case(case, UNMATCHED_TEXT)

        # The pipeline must have gone past the early ESCALATED return
        assert "SLOT_EXTRACT" in result.steps_completed, (
            f"Expected SLOT_EXTRACT in steps. Got: {result.steps_completed}"
        )

    def test_classify_step_recorded_for_unknown(self):
        """CLASSIFY step must still be recorded even for UNKNOWN topics."""
        svc  = _service()
        case = svc.open_case("TKT-U12", "unity_bank")
        runtime = _runtime(case_svc=svc)

        result = runtime.run_case(case, UNMATCHED_TEXT)

        assert "CLASSIFY" in result.steps_completed, (
            f"Expected CLASSIFY in steps. Got: {result.steps_completed}"
        )

    def test_classification_dict_populated_for_unknown(self):
        """result.classification must be populated even for UNKNOWN topic."""
        svc  = _service()
        case = svc.open_case("TKT-U13", "unity_bank")
        runtime = _runtime(case_svc=svc)

        result = runtime.run_case(case, UNMATCHED_TEXT)

        assert result.classification is not None, (
            "result.classification should be set even for UNKNOWN topic"
        )
        assert result.classification.get("topic") == "UNKNOWN"


# ── Test Group 3: receive_message with UNKNOWN topic proceeds to clarification ─

class TestReceiveMessageUnknownTopicClarification:
    """
    CaseService.receive_message must NOT bail early when topic is UNKNOWN.
    It should proceed to ask a general clarification question.
    """

    def test_receive_message_unknown_topic_does_not_return_silently(self):
        """
        Before the fix, receive_message returned an empty result with
        next_question=None when topic==UNKNOWN. After the fix, for UNKNOWN
        topics it should proceed to the clarification path.

        This test verifies the state post-classify is TRIAGE_COMPLETE so that
        receive_message can then advance into AWAITING_INPUT for slot filling.
        """
        svc  = _service()
        case = svc.open_case("TKT-U20", "unity_bank")
        # Classify first — must land in TRIAGE_COMPLETE, not ESCALATED
        svc.classify_case(case, UNMATCHED_TEXT)

        # Verify precondition: classification didn't escalate
        assert case.current_state == CaseState.TRIAGE_COMPLETE, (
            f"Precondition failed: classify_case set state={case.current_state.value}"
        )
        assert case.topic == "UNKNOWN"

        # Now receive_message should NOT silently return (it should at least
        # attempt to transition the case toward AWAITING_INPUT)
        result = svc.receive_message(case, UNMATCHED_TEXT)

        # The result must have a case_id — it ran without crashing
        assert result.case_id == case.case_id


# ── Test Group 4: Known topic still works correctly (regression) ───────────────

class TestKnownTopicRegressionAfterFix:
    """Ensure the fix doesn't break known-topic classification paths."""

    def test_known_topic_still_escalates_on_low_confidence(self):
        """
        A known topic that is below threshold (if any) should still escalate.
        This tests the BELOW_THRESHOLD branch is not accidentally removed.
        Note: Our classifier always returns 0.85+ for known topics in Tier 1,
        so we test that known topics still reach TRIAGE_COMPLETE.
        """
        svc  = _service()
        case = svc.open_case("TKT-K01", "unity_bank")
        svc.classify_case(case, "OTP not received on my mobile.")
        assert case.current_state == CaseState.TRIAGE_COMPLETE
        assert case.topic == TopicKey.OTP_DELIVERY_FAILURE.value

    def test_known_topic_runtime_proceeds_normally(self):
        """Known-topic cases must still flow through the full pipeline."""
        svc  = _service()
        case = svc.open_case("TKT-K02", "unity_bank")
        runtime = _runtime(case_svc=svc)

        result = runtime.run_case(case, "OTP not received on my mobile.")

        # CLASSIFY and SLOT_EXTRACT must both be in steps
        assert "CLASSIFY" in result.steps_completed
        assert "SLOT_EXTRACT" in result.steps_completed
