"""
tests/test_sprint216_playbook_registry.py

Sprint 2.16: PlaybookRegistry unit tests.

Scenarios:
  1.  build_loads_all_five_playbooks        — 5 YAML files load with no errors
  2.  get_by_topic_returns_correct_defn     — registry.get(topic) finds definition
  3.  get_by_id_returns_correct_defn        — registry.get_by_id(workflow_id) finds definition
  4.  get_missing_topic_returns_none        — unknown topic returns None
  5.  get_missing_id_returns_none           — unknown workflow_id returns None
  6.  list_topics_returns_all_topics        — all 5 topics present
  7.  list_all_returns_all_definitions      — 5 definitions returned
  8.  len_matches_definitions               — __len__ correct
  9.  missing_workflow_id_raises            — validation error on missing field
  10. missing_topic_raises                  — validation error on missing field
  11. missing_name_raises                   — validation error on missing field
  12. empty_steps_raises                    — validation error on empty steps list
  13. bad_step_type_raises                  — unknown step type raises
  14. bad_navigation_pointer_raises         — on_success pointing to unknown step_id raises
  15. propose_action_missing_action_type_raises
  16. propose_action_missing_namespace_raises
  17. reversible_missing_rollback_raises
  18. bad_condition_operator_raises
  19. condition_missing_field_raises
  20. build_empty_directory_returns_empty_registry
  21. build_nonexistent_directory_raises_filenotfounderror
  22. vkyc_playbook_required_slots           — session_id + phone_number required
  23. vkyc_playbook_steps_count              — expected number of steps
  24. otp_playbook_loads_correctly
  25. api_callback_playbook_loads_correctly
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from case_engine.workflows.playbook_registry import PlaybookRegistry, PlaybookValidationError
from case_engine.workflows.models import WorkflowStepType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_yaml(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _minimal_yaml(
    workflow_id="test_v1",
    topic="Test_Topic",
    steps_yaml: str | None = None,
) -> str:
    if steps_yaml is None:
        steps_yaml = """
    - step_id: only_step
      type: RESOLVE_CASE
      name: Resolve
"""
    return f"""
workflow_id: {workflow_id}
topic: {topic}
version: "1.0"
name: Test Workflow
steps:{steps_yaml}
"""


# ── Test 1: build loads all five playbooks ────────────────────────────────────

class TestBuildLoadsAllPlaybooks:
    def test_five_playbooks_loaded(self):
        registry = PlaybookRegistry.build()
        assert len(registry) == 5

    def test_registry_has_vkyc_topic(self):
        registry = PlaybookRegistry.build()
        assert registry.get("VKYC_Session_Failure") is not None

    def test_registry_has_otp_topic(self):
        registry = PlaybookRegistry.build()
        assert registry.get("OTP_Delivery_Failure") is not None

    def test_registry_has_api_callback_topic(self):
        registry = PlaybookRegistry.build()
        assert registry.get("API_Callback_Failure") is not None

    def test_registry_has_document_ocr_topic(self):
        registry = PlaybookRegistry.build()
        assert registry.get("Document_OCR_Failure") is not None

    def test_registry_has_agent_portal_topic(self):
        registry = PlaybookRegistry.build()
        assert registry.get("Agent_Portal_Issue") is not None


# ── Test 2: get_by_topic ──────────────────────────────────────────────────────

class TestGetByTopic:
    def test_returns_correct_workflow_id(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("VKYC_Session_Failure")
        assert defn is not None
        assert defn.workflow_id == "vkyc_session_failure_v1"

    def test_defn_has_steps(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("OTP_Delivery_Failure")
        assert len(defn.steps) > 0

    def test_defn_has_required_slots(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("VKYC_Session_Failure")
        assert "session_id" in defn.required_slots
        assert "phone_number" in defn.required_slots


# ── Test 3: get_by_id ─────────────────────────────────────────────────────────

class TestGetById:
    def test_found_by_workflow_id(self):
        registry = PlaybookRegistry.build()
        defn = registry.get_by_id("vkyc_session_failure_v1")
        assert defn is not None
        assert defn.topic == "VKYC_Session_Failure"

    def test_otp_found_by_id(self):
        registry = PlaybookRegistry.build()
        defn = registry.get_by_id("otp_delivery_failure_v1")
        assert defn is not None

    def test_api_callback_found_by_id(self):
        registry = PlaybookRegistry.build()
        defn = registry.get_by_id("api_callback_failure_v1")
        assert defn is not None


# ── Test 4: get missing topic ─────────────────────────────────────────────────

class TestGetMissingTopic:
    def test_unknown_topic_returns_none(self):
        registry = PlaybookRegistry.build()
        assert registry.get("TOTALLY_UNKNOWN_TOPIC") is None

    def test_empty_string_returns_none(self):
        registry = PlaybookRegistry.build()
        assert registry.get("") is None


# ── Test 5: get_by_id missing ────────────────────────────────────────────────

class TestGetByIdMissing:
    def test_unknown_id_returns_none(self):
        registry = PlaybookRegistry.build()
        assert registry.get_by_id("totally_unknown_id") is None

    def test_empty_id_returns_none(self):
        registry = PlaybookRegistry.build()
        assert registry.get_by_id("") is None


# ── Test 6: list_topics ───────────────────────────────────────────────────────

class TestListTopics:
    def test_returns_five_topics(self):
        registry = PlaybookRegistry.build()
        assert len(registry.list_topics()) == 5

    def test_topics_are_sorted(self):
        registry = PlaybookRegistry.build()
        topics = registry.list_topics()
        assert topics == sorted(topics)

    def test_all_expected_topics_present(self):
        registry = PlaybookRegistry.build()
        topics = set(registry.list_topics())
        expected = {
            "VKYC_Session_Failure",
            "OTP_Delivery_Failure",
            "API_Callback_Failure",
            "Document_OCR_Failure",
            "Agent_Portal_Issue",
        }
        assert topics == expected


# ── Test 7: list_all ──────────────────────────────────────────────────────────

class TestListAll:
    def test_returns_five_definitions(self):
        registry = PlaybookRegistry.build()
        all_defns = registry.list_all()
        assert len(all_defns) == 5

    def test_all_have_workflow_id(self):
        registry = PlaybookRegistry.build()
        for defn in registry.list_all():
            assert defn.workflow_id


# ── Test 8: __len__ ───────────────────────────────────────────────────────────

class TestLen:
    def test_len_matches_definitions(self):
        registry = PlaybookRegistry.build()
        assert len(registry) == 5

    def test_empty_registry_len(self, tmp_path):
        (tmp_path / "empty_dir").mkdir()
        registry = PlaybookRegistry.build(tmp_path / "empty_dir")
        assert len(registry) == 0


# ── Test 9: missing workflow_id ───────────────────────────────────────────────

class TestValidationMissingWorkflowId:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            topic: Test
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: RESOLVE_CASE
                name: Done
        """)
        with pytest.raises(PlaybookValidationError, match="workflow_id"):
            PlaybookRegistry.build(tmp_path)


# ── Test 10: missing topic ────────────────────────────────────────────────────

class TestValidationMissingTopic:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: RESOLVE_CASE
                name: Done
        """)
        with pytest.raises(PlaybookValidationError, match="topic"):
            PlaybookRegistry.build(tmp_path)


# ── Test 11: missing name ─────────────────────────────────────────────────────

class TestValidationMissingName:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            steps:
              - step_id: s1
                type: RESOLVE_CASE
                name: Done
        """)
        with pytest.raises(PlaybookValidationError, match="name"):
            PlaybookRegistry.build(tmp_path)


# ── Test 12: empty steps ──────────────────────────────────────────────────────

class TestValidationEmptySteps:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            name: Test
            steps: []
        """)
        with pytest.raises(PlaybookValidationError, match="steps"):
            PlaybookRegistry.build(tmp_path)


# ── Test 13: bad step type ────────────────────────────────────────────────────

class TestValidationBadStepType:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: DO_MAGIC
                name: Magic
        """)
        with pytest.raises(PlaybookValidationError):
            PlaybookRegistry.build(tmp_path)


# ── Test 14: bad navigation pointer ──────────────────────────────────────────

class TestValidationBadNavigationPointer:
    def test_on_success_unknown_step_id_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: CHECK_CONDITION
                name: Check
                on_success: nonexistent_step
                on_failure: ESCALATE
        """)
        with pytest.raises(PlaybookValidationError, match="nonexistent_step"):
            PlaybookRegistry.build(tmp_path)


# ── Test 15: PROPOSE_ACTION missing action_type ───────────────────────────────

class TestValidationProposeActionMissingType:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: PROPOSE_ACTION
                name: Propose
                action_namespace: kwikid.vkyc
        """)
        with pytest.raises(PlaybookValidationError, match="action_type"):
            PlaybookRegistry.build(tmp_path)


# ── Test 16: PROPOSE_ACTION missing namespace ─────────────────────────────────

class TestValidationProposeActionMissingNamespace:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: PROPOSE_ACTION
                name: Propose
                action_type: some_action
        """)
        with pytest.raises(PlaybookValidationError, match="action_namespace"):
            PlaybookRegistry.build(tmp_path)


# ── Test 17: REVERSIBLE missing rollback ──────────────────────────────────────

class TestValidationReversibleMissingRollback:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: PROPOSE_ACTION
                name: Propose
                action_type: some_action
                action_namespace: ns.test
                risk_level: REVERSIBLE
        """)
        with pytest.raises(PlaybookValidationError, match="rollback"):
            PlaybookRegistry.build(tmp_path)


# ── Test 18: bad condition operator ──────────────────────────────────────────

class TestValidationBadConditionOperator:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: CHECK_CONDITION
                name: Check
                conditions:
                  - field: session_id
                    operator: LIKE
                    value: KID-%
        """)
        with pytest.raises(PlaybookValidationError):
            PlaybookRegistry.build(tmp_path)


# ── Test 19: condition missing field ─────────────────────────────────────────

class TestValidationConditionMissingField:
    def test_raises(self, tmp_path):
        _write_yaml(tmp_path, "bad.yml", """
            workflow_id: test_v1
            topic: Test_Topic
            version: "1.0"
            name: Test
            steps:
              - step_id: s1
                type: CHECK_CONDITION
                name: Check
                conditions:
                  - operator: exists
        """)
        with pytest.raises(PlaybookValidationError, match="field"):
            PlaybookRegistry.build(tmp_path)


# ── Test 20: empty directory ─────────────────────────────────────────────────

class TestBuildEmptyDirectory:
    def test_empty_dir_returns_zero_len_registry(self, tmp_path):
        (tmp_path / "playbooks").mkdir()
        registry = PlaybookRegistry.build(tmp_path / "playbooks")
        assert len(registry) == 0
        assert registry.list_topics() == []


# ── Test 21: nonexistent directory ───────────────────────────────────────────

class TestBuildNonexistentDirectory:
    def test_raises_filenotfounderror(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            PlaybookRegistry.build(tmp_path / "does_not_exist")


# ── Test 22: vkyc required slots ─────────────────────────────────────────────

class TestVkycPlaybookRequiredSlots:
    def test_session_id_required(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("VKYC_Session_Failure")
        assert "session_id" in defn.required_slots

    def test_phone_number_required(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("VKYC_Session_Failure")
        assert "phone_number" in defn.required_slots


# ── Test 23: vkyc steps count ────────────────────────────────────────────────

class TestVkycPlaybookStepsCount:
    def test_has_expected_steps(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("VKYC_Session_Failure")
        # check_session_status, propose_session_reset, verify_reset_outcome,
        # resolve_session_reset, escalate_missing_slots, escalate_reset_failed,
        # escalate_reset_rejected = 7 steps
        assert len(defn.steps) >= 4

    def test_first_step_is_clarify(self):
        # Sprint 2.25: all playbooks now start with CLARIFY (slot completeness check)
        registry = PlaybookRegistry.build()
        defn = registry.get("VKYC_Session_Failure")
        assert defn.first_step().step_type == WorkflowStepType.CLARIFY

    def test_has_propose_action_step(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("VKYC_Session_Failure")
        step_types = {s.step_type for s in defn.steps}
        assert WorkflowStepType.PROPOSE_ACTION in step_types

    def test_has_resolve_step(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("VKYC_Session_Failure")
        step_types = {s.step_type for s in defn.steps}
        assert WorkflowStepType.RESOLVE_CASE in step_types


# ── Test 24: otp playbook ─────────────────────────────────────────────────────

class TestOtpPlaybookLoads:
    def test_required_slots_present(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("OTP_Delivery_Failure")
        assert "phone_number" in defn.required_slots
        assert "channel" in defn.required_slots

    def test_version_set(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("OTP_Delivery_Failure")
        assert defn.version

    def test_workflow_id_correct(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("OTP_Delivery_Failure")
        assert defn.workflow_id == "otp_delivery_failure_v1"


# ── Test 25: api_callback playbook ────────────────────────────────────────────

class TestApiCallbackPlaybookLoads:
    def test_required_slots_present(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("API_Callback_Failure")
        assert "callback_type" in defn.required_slots
        assert "application_id" in defn.required_slots

    def test_has_reversible_action(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("API_Callback_Failure")
        reversible = [s for s in defn.steps
                      if s.step_type == WorkflowStepType.PROPOSE_ACTION
                      and s.risk_level == "REVERSIBLE"]
        assert len(reversible) >= 1

    def test_reversible_action_has_rollback_type(self):
        registry = PlaybookRegistry.build()
        defn = registry.get("API_Callback_Failure")
        for step in defn.steps:
            if step.step_type == WorkflowStepType.PROPOSE_ACTION and step.risk_level == "REVERSIBLE":
                assert step.rollback_action_type is not None
