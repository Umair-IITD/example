"""
tests/test_sprint225_playbooks.py

Sprint 2.25: Playbook YAML structure tests.

Coverage:
  - All 5 playbooks load without error
  - All 5 playbooks are version 2.0
  - All 5 playbooks have a CLARIFY step
  - All 5 playbooks have an INVESTIGATE step
  - All 5 playbooks have a KNOWLEDGE_LOOKUP step
  - All 5 playbooks have a REASON step
  - All 5 playbooks have a PROPOSE_ACTION step
  - All 5 playbooks have an ACTION_GATEWAY step
  - All 5 playbooks have an EXECUTE step
  - All 5 playbooks have a RESOLVE_CASE step
  - All 5 playbooks have at least one ESCALATE_CASE step
  - All 5 playbooks have required_slots defined
  - All 5 playbooks have investigation_steps metadata
  - All 5 playbooks: CLARIFY is first step
  - VKYC playbook: action_type=vkyc_session_reset in EXECUTE step
  - OTP playbook: action_type=otp_resend in EXECUTE step
  - API Callback: action_type=api_callback_retry in EXECUTE step
  - Document OCR: action_type=document_ocr_reprocess in EXECUTE step
  - Agent Portal: action_type=agent_session_refresh in EXECUTE step
"""
from __future__ import annotations

import pytest

from case_engine.workflows.models import WorkflowStepType
from case_engine.workflows.playbook_registry import PlaybookRegistry


# ── Registry fixture ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def registry() -> PlaybookRegistry:
    return PlaybookRegistry.build()


def _get_defn(registry: PlaybookRegistry, topic: str):
    defn = registry.get(topic)
    assert defn is not None, f"Playbook for topic '{topic}' not found"
    return defn


def _step_types(defn) -> set[str]:
    return {s.step_type.value for s in defn.steps}


TOPICS = [
    "VKYC_Session_Failure",
    "OTP_Delivery_Failure",
    "Document_OCR_Failure",
    "Agent_Portal_Issue",
    "API_Callback_Failure",
]

# OTP_Delivery_Failure was rewritten to v3.0 (Sprint 2.5.6) — no PROPOSE_ACTION/ACTION_GATEWAY/EXECUTE
V2_TOPICS = [t for t in TOPICS if t != "OTP_Delivery_Failure"]


# ── All playbooks load ────────────────────────────────────────────────────────

class TestPlaybooksLoad:
    @pytest.mark.parametrize("topic", TOPICS)
    def test_playbook_loads(self, registry, topic):
        defn = _get_defn(registry, topic)
        assert defn is not None

    @pytest.mark.parametrize("topic", TOPICS)
    def test_playbook_has_workflow_id(self, registry, topic):
        defn = _get_defn(registry, topic)
        assert defn.workflow_id

    @pytest.mark.parametrize("topic", V2_TOPICS)
    def test_playbook_version_is_2(self, registry, topic):
        defn = _get_defn(registry, topic)
        assert defn.version == "2.0"

    def test_otp_playbook_version_is_3(self, registry):
        defn = _get_defn(registry, "OTP_Delivery_Failure")
        assert defn.version == "3.0", f"OTP v3.0 rewrite expected; got {defn.version}"


# ── All playbooks have full pipeline steps ────────────────────────────────────

class TestPlaybooksFullPipeline:
    @pytest.mark.parametrize("topic", TOPICS)
    def test_has_clarify_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "CLARIFY" in types, f"{topic}: missing CLARIFY step"

    @pytest.mark.parametrize("topic", TOPICS)
    def test_has_investigate_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "INVESTIGATE" in types, f"{topic}: missing INVESTIGATE step"

    @pytest.mark.parametrize("topic", TOPICS)
    def test_has_knowledge_lookup_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "KNOWLEDGE_LOOKUP" in types, f"{topic}: missing KNOWLEDGE_LOOKUP step"

    @pytest.mark.parametrize("topic", TOPICS)
    def test_has_reason_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "REASON" in types, f"{topic}: missing REASON step"

    @pytest.mark.parametrize("topic", V2_TOPICS)
    def test_has_propose_action_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "PROPOSE_ACTION" in types, f"{topic}: missing PROPOSE_ACTION step"

    def test_otp_has_no_propose_action(self, registry):
        defn = _get_defn(registry, "OTP_Delivery_Failure")
        types = _step_types(defn)
        assert "PROPOSE_ACTION" not in types, "OTP v3.0 must NOT have PROPOSE_ACTION (L1 stops at RESOLVE_CASE)"

    @pytest.mark.parametrize("topic", V2_TOPICS)
    def test_has_action_gateway_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "ACTION_GATEWAY" in types, f"{topic}: missing ACTION_GATEWAY step"

    def test_otp_has_no_action_gateway(self, registry):
        defn = _get_defn(registry, "OTP_Delivery_Failure")
        types = _step_types(defn)
        assert "ACTION_GATEWAY" not in types, "OTP v3.0 must NOT have ACTION_GATEWAY"

    @pytest.mark.parametrize("topic", V2_TOPICS)
    def test_has_execute_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "EXECUTE" in types, f"{topic}: missing EXECUTE step"

    def test_otp_has_no_execute(self, registry):
        defn = _get_defn(registry, "OTP_Delivery_Failure")
        types = _step_types(defn)
        assert "EXECUTE" not in types, "OTP v3.0 must NOT have EXECUTE step"

    @pytest.mark.parametrize("topic", TOPICS)
    def test_has_resolve_case_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "RESOLVE_CASE" in types, f"{topic}: missing RESOLVE_CASE step"

    @pytest.mark.parametrize("topic", TOPICS)
    def test_has_at_least_one_escalate_case_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        types = _step_types(defn)
        assert "ESCALATE_CASE" in types, f"{topic}: missing ESCALATE_CASE step"


# ── CLARIFY is first step ─────────────────────────────────────────────────────

class TestClarifyIsFirstStep:
    @pytest.mark.parametrize("topic", TOPICS)
    def test_first_step_is_clarify(self, registry, topic):
        defn = _get_defn(registry, topic)
        assert defn.steps, f"{topic}: has no steps"
        assert defn.steps[0].step_type is WorkflowStepType.CLARIFY, \
            f"{topic}: first step is {defn.steps[0].step_type}, expected CLARIFY"


# ── Required slots defined ────────────────────────────────────────────────────

class TestPlaybooksRequiredSlots:
    @pytest.mark.parametrize("topic", TOPICS)
    def test_required_slots_nonempty(self, registry, topic):
        defn = _get_defn(registry, topic)
        assert len(defn.required_slots) > 0, f"{topic}: required_slots is empty"

    def test_vkyc_required_slots(self, registry):
        defn = _get_defn(registry, "VKYC_Session_Failure")
        assert "session_id" in defn.required_slots
        assert "phone_number" in defn.required_slots

    def test_otp_required_slots(self, registry):
        defn = _get_defn(registry, "OTP_Delivery_Failure")
        assert "urn" in defn.required_slots
        assert "session_id" in defn.required_slots

    def test_ocr_required_slots(self, registry):
        defn = _get_defn(registry, "Document_OCR_Failure")
        assert "document_type" in defn.required_slots
        assert "application_id" in defn.required_slots

    def test_portal_required_slots(self, registry):
        defn = _get_defn(registry, "Agent_Portal_Issue")
        assert "agent_id" in defn.required_slots
        assert "portal_type" in defn.required_slots

    def test_callback_required_slots(self, registry):
        defn = _get_defn(registry, "API_Callback_Failure")
        assert "callback_type" in defn.required_slots
        assert "application_id" in defn.required_slots


# ── Investigation metadata present ────────────────────────────────────────────

class TestPlaybooksInvestigationMetadata:
    @pytest.mark.parametrize("topic", TOPICS)
    def test_investigation_steps_present(self, registry, topic):
        defn = _get_defn(registry, topic)
        assert len(defn.investigation_steps) > 0, f"{topic}: no investigation_steps"

    @pytest.mark.parametrize("topic", TOPICS)
    def test_tool_candidates_present(self, registry, topic):
        defn = _get_defn(registry, topic)
        assert len(defn.tool_candidates) > 0, f"{topic}: no tool_candidates"


# ── EXECUTE step action_type ──────────────────────────────────────────────────

class TestPlaybooksExecuteActionType:
    def _execute_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        for step in defn.steps:
            if step.step_type is WorkflowStepType.EXECUTE:
                return step
        pytest.fail(f"{topic}: no EXECUTE step found")

    def test_vkyc_execute_action_type(self, registry):
        step = self._execute_step(registry, "VKYC_Session_Failure")
        assert step.action_type == "vkyc_session_reset"

    def test_otp_execute_action_type(self, registry):
        # OTP v3.0: no EXECUTE step — L1 stops at RESOLVE_CASE (observation written to Freshdesk)
        defn = _get_defn(registry, "OTP_Delivery_Failure")
        types = _step_types(defn)
        assert "EXECUTE" not in types, "OTP v3.0 must not have EXECUTE — otp_resend is deferred to L2"
        assert "RESOLVE_CASE" in types, "OTP v3.0 must have RESOLVE_CASE (L1 observation output)"

    def test_ocr_execute_action_type(self, registry):
        step = self._execute_step(registry, "Document_OCR_Failure")
        assert step.action_type == "document_ocr_reprocess"

    def test_portal_execute_action_type(self, registry):
        step = self._execute_step(registry, "Agent_Portal_Issue")
        assert step.action_type == "agent_session_refresh"

    def test_callback_execute_action_type(self, registry):
        step = self._execute_step(registry, "API_Callback_Failure")
        assert step.action_type == "api_callback_retry"


# ── Risk levels ───────────────────────────────────────────────────────────────

class TestPlaybooksRiskLevels:
    def _propose_step(self, registry, topic):
        defn = _get_defn(registry, topic)
        for step in defn.steps:
            if step.step_type is WorkflowStepType.PROPOSE_ACTION:
                return step
        pytest.fail(f"{topic}: no PROPOSE_ACTION step found")

    def test_vkyc_is_reversible(self, registry):
        step = self._propose_step(registry, "VKYC_Session_Failure")
        assert step.risk_level == "REVERSIBLE"

    def test_otp_is_safe(self, registry):
        # OTP v3.0: no PROPOSE_ACTION step — risk level N/A; verify absence
        defn = _get_defn(registry, "OTP_Delivery_Failure")
        types = _step_types(defn)
        assert "PROPOSE_ACTION" not in types, "OTP v3.0 must not have PROPOSE_ACTION"

    def test_ocr_is_safe(self, registry):
        step = self._propose_step(registry, "Document_OCR_Failure")
        assert step.risk_level == "SAFE"

    def test_portal_is_safe(self, registry):
        step = self._propose_step(registry, "Agent_Portal_Issue")
        assert step.risk_level == "SAFE"

    def test_callback_is_reversible(self, registry):
        step = self._propose_step(registry, "API_Callback_Failure")
        assert step.risk_level == "REVERSIBLE"
