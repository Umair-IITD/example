"""
tests/test_classify_unknown_contract.py

Contract test: classify_case() MUST set case.topic and case.confidence
even when the classifier returns UNKNOWN / below-threshold.

This test was originally written to verify the fields case.topic and
case.confidence are preserved after classification.

Updated (runtime-alignment fix): UNKNOWN-topic classification must transition
to TRIAGE_COMPLETE (not ESCALATED) so that the pipeline can proceed to
Slot Extraction / Clarification per the approved flow_diagram.mermaid.

See: tests/test_unknown_topic_reaches_slotextract.py for the full contract.
"""
from __future__ import annotations

import pytest

from case_engine.case_state import CaseState
from case_engine.models import Case, TopicKey
from case_engine.service import CaseService, build_case_service


def _service() -> CaseService:
    """Build an offline CaseService (no DB, no Supabase)."""
    return build_case_service(supabase_client=None)


class TestClassifyUnknownContract:
    """
    The classifier returns ClassificationResult(topic=UNKNOWN, confidence=0.0, tier_used=0)
    for text that matches no known topic.  The case MUST carry those values after
    classify_case() returns.

    Per flow_diagram.mermaid (CLASSIFIER --> SLOTEXTRACT), UNKNOWN-topic cases
    proceed to TRIAGE_COMPLETE (not ESCALATED) so the pipeline continues.
    """

    def test_classify_unmatched_preserves_unknown_topic(self):
        """case.topic must be 'UNKNOWN' (not None) after a no-match classification."""
        svc = _service()
        case = svc.open_case("TKT-CONTRACT-001", "unity_bank")

        # Sanity: topic starts as None
        assert case.topic is None

        # Text that cannot match any Tier-1 rule
        svc.classify_case(case, "Hello world")

        # Per blueprint fix: UNKNOWN topic goes to TRIAGE_COMPLETE so Slot
        # Extraction / Clarification can collect more context from the user.
        assert case.current_state == CaseState.TRIAGE_COMPLETE, (
            f"Expected TRIAGE_COMPLETE for UNKNOWN topic, got {case.current_state.value}. "
            "UNKNOWN-topic cases must proceed to Slot Extraction per flow_diagram.mermaid."
        )

        # CONTRACT: topic must be the string "UNKNOWN", not None
        assert case.topic == TopicKey.UNKNOWN.value, (
            f"Expected case.topic='UNKNOWN' but got case.topic={case.topic!r}. "
            "The else-branch of classify_case() is not writing result.topic.value onto the case."
        )

    def test_classify_unmatched_preserves_confidence_zero(self):
        """case.confidence must be 0.0 (not None) after a no-match classification."""
        svc = _service()
        case = svc.open_case("TKT-CONTRACT-002", "unity_bank")

        svc.classify_case(case, "Hello world")

        # Per blueprint fix: UNKNOWN → TRIAGE_COMPLETE
        assert case.current_state == CaseState.TRIAGE_COMPLETE

        # CONTRACT: confidence must be 0.0, not None
        assert case.confidence is not None, (
            "case.confidence is None after classify_case(). "
            "The else-branch is not writing result.confidence onto the case."
        )
        assert case.confidence == 0.0, (
            f"Expected case.confidence=0.0 but got {case.confidence!r}."
        )
