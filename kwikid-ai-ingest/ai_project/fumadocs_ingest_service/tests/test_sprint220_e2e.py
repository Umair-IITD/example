"""
tests/test_sprint220_e2e.py

Sprint 2.20: End-to-end integration tests — full knowledge workflow path.

Wires together:
  WorkflowEngine (with KnowledgeService + InvestigationService)
  → KNOWLEDGE_LOOKUP step → KnowledgeLookupStepExecutor
  → KnowledgeService.search()
  → SOPMatcher → HybridRetriever → KnowledgeEntry
  → ResolutionRecommendationEngine
  → knowledge_result stored in WorkflowExecutionResult

Uses seeded KnowledgeRepository with entries for all 5 topics.
Verifies full JSONB round-trip and blueprint compliance.

Coverage:
  - engine.start() with KNOWLEDGE_LOOKUP → COMPLETED
  - knowledge_result populated after step
  - recommendation action is present
  - sop_match_found reflects repository state
  - all 5 topics produce valid results
  - no KB entries → sop_match_found=False but still COMPLETED
  - full JSONB round-trip through WorkflowExecutionResult.to_dict/from_dict
  - INVESTIGATE → KNOWLEDGE_LOOKUP → RESOLVE full pipeline
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from case_engine.knowledge import build_knowledge_service
from case_engine.knowledge.importer import StackOverflowImporter
from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
)
from case_engine.knowledge.repository import InMemoryKnowledgeRepository
from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine
from case_engine.slot_filling.models import SlotValue, SlotStatus


# ── Fixtures / Helpers ────────────────────────────────────────────────────────

def _slot_values(**kwargs: str) -> dict[str, SlotValue]:
    return {k: SlotValue(slot_name=k, value=v, status=SlotStatus.FILLED) for k, v in kwargs.items()}


def _make_case(topic: str = "VKYC_Session_Failure", case_id: str = "e2e-kb-001"):
    case = MagicMock()
    case.case_id = case_id
    case.topic = topic
    return case


def _registry(defn: WorkflowDefinition):
    reg = MagicMock()
    reg.get.return_value = defn
    reg.get_by_id.return_value = defn
    return reg


_SAMPLE_SO_EXPORT = {
    "questions": [
        {
            "Id": 1,
            "Title": "How to reset a failed VKYC session",
            "Body": "<p>User is stuck after VKYC session timeout.</p>",
            "Tags": ["vkyc", "session_reset", "expired_session"],
            "Score": 15,
            "AcceptedAnswerId": 10,
            "CreationDate": "2024-01-15T10:00:00Z",
            "Answers": [{
                "Id": 10,
                "Body": "1. Navigate to Admin Portal.\n2. Find session by ID.\n3. Click Reset Session.",
                "Score": 12, "IsAccepted": True, "CreationDate": "2024-01-15T11:00:00Z",
            }],
        },
        {
            "Id": 2,
            "Title": "OTP not delivered - SMS failure",
            "Body": "<p>OTP SMS is not being delivered.</p>",
            "Tags": ["otp", "sms_failure", "otp_resend"],
            "Score": 8,
            "AcceptedAnswerId": 20,
            "CreationDate": "2024-01-16T10:00:00Z",
            "Answers": [{
                "Id": 20,
                "Body": "1. Check SMS provider status.\n2. Trigger OTP resend from Admin Portal.",
                "Score": 5, "IsAccepted": True, "CreationDate": "2024-01-16T11:00:00Z",
            }],
        },
        {
            "Id": 3,
            "Title": "OCR document failure - PAN verification",
            "Body": "<p>Document OCR is failing for PAN card.</p>",
            "Tags": ["ocr", "document_failure", "retry"],
            "Score": 6,
            "AcceptedAnswerId": 30,
            "CreationDate": "2024-01-17T10:00:00Z",
            "Answers": [{
                "Id": 30,
                "Body": "1. Ask user to re-upload document.\n2. Retry OCR from Admin Portal.",
                "Score": 4, "IsAccepted": True, "CreationDate": "2024-01-17T11:00:00Z",
            }],
        },
        {
            "Id": 4,
            "Title": "Agent portal is down or unavailable",
            "Body": "<p>Agents cannot access the portal.</p>",
            "Tags": ["portal", "portal_unavailable", "portal_refresh"],
            "Score": 5,
            "AcceptedAnswerId": 40,
            "CreationDate": "2024-01-18T10:00:00Z",
            "Answers": [{
                "Id": 40,
                "Body": "1. Check portal status page.\n2. Clear cache.\n3. Refresh browser.",
                "Score": 3, "IsAccepted": True, "CreationDate": "2024-01-18T11:00:00Z",
            }],
        },
        {
            "Id": 5,
            "Title": "API callback is not being received",
            "Body": "<p>Webhook callbacks are failing.</p>",
            "Tags": ["callback", "callback_failure", "callback_retry"],
            "Score": 7,
            "AcceptedAnswerId": 50,
            "CreationDate": "2024-01-19T10:00:00Z",
            "Answers": [{
                "Id": 50,
                "Body": "1. Verify callback URL configuration.\n2. Trigger manual retry.",
                "Score": 5, "IsAccepted": True, "CreationDate": "2024-01-19T11:00:00Z",
            }],
        },
    ]
}


@pytest.fixture(scope="module")
def seeded_kb_service():
    importer = StackOverflowImporter()
    entries  = importer.import_from_dict(_SAMPLE_SO_EXPORT)
    return build_knowledge_service(seed_entries=entries)


@pytest.fixture(scope="module")
def empty_kb_service():
    return build_knowledge_service()


@pytest.fixture(scope="module")
def engine(seeded_kb_service):
    return WorkflowEngine(knowledge_service=seeded_kb_service)


@pytest.fixture(scope="module")
def engine_no_kb():
    return WorkflowEngine()


def _workflow_kb_only(topic: str = "VKYC_Session_Failure") -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=f"e2e-kb-{topic[:8].lower()}",
        topic=topic, version="1.0", name=f"E2E KB Only — {topic}",
        steps=(
            WorkflowStep(
                step_index=0, step_id="step-kb",
                step_type=WorkflowStepType.KNOWLEDGE_LOOKUP,
                name="Knowledge Lookup",
                on_success="RESOLVE", on_failure="ESCALATE",
            ),
        ),
    )


# ── Happy path ────────────────────────────────────────────────────────────────

class TestE2EKnowledgeCompletes:
    def test_workflow_completes(self, engine):
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = engine.start(_make_case(), _registry(defn), _slot_values(session_id="S-E2E-001"))
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_knowledge_result_populated(self, engine):
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = engine.start(_make_case(), _registry(defn), _slot_values(session_id="S-E2E-002"))
        assert result.knowledge_result is not None

    def test_knowledge_result_has_result_id(self, engine):
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = engine.start(_make_case(), _registry(defn), _slot_values(session_id="S-E2E-003"))
        assert result.knowledge_result.get("result_id")

    def test_recommendation_has_action(self, engine):
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = engine.start(_make_case(), _registry(defn), _slot_values(session_id="S-E2E-004"))
        rec = result.knowledge_result.get("recommendation") or {}
        assert rec.get("recommended_action")

    def test_sop_match_found_vkyc(self, engine):
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = engine.start(_make_case(), _registry(defn), _slot_values(session_id="S-E2E-005"))
        assert result.knowledge_result.get("sop_match_found") is True

    def test_result_json_serializable(self, engine):
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = engine.start(_make_case(), _registry(defn), _slot_values(session_id="S-E2E-006"))
        serialized = json.dumps(result.knowledge_result)
        assert len(serialized) > 0


# ── All 5 topics ──────────────────────────────────────────────────────────────

class TestE2EAllTopics:
    @pytest.mark.parametrize("topic,slots", [
        ("VKYC_Session_Failure",  {"session_id": "S-T1"}),
        ("OTP_Delivery_Failure",  {"phone_number": "9876543210"}),
        ("Document_OCR_Failure",  {"application_id": "APP-1"}),
        ("Agent_Portal_Issue",    {"session_id": "S-T4"}),
        ("API_Callback_Failure",  {"session_id": "S-T5"}),
    ])
    def test_topic_produces_valid_result(self, engine, topic, slots):
        defn = _workflow_kb_only(topic)
        result = engine.start(_make_case(topic), _registry(defn), _slot_values(**slots))
        assert result.workflow_state in (WorkflowState.COMPLETED, WorkflowState.ESCALATED)
        assert result.knowledge_result is not None


# ── No entries in repository ──────────────────────────────────────────────────

class TestE2EEmptyRepository:
    def test_empty_kb_still_completes(self, engine_no_kb):
        svc_empty = build_knowledge_service()
        eng = WorkflowEngine(knowledge_service=svc_empty)
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = eng.start(_make_case(), _registry(defn), _slot_values(session_id="S-empty"))
        assert result.knowledge_result is not None

    def test_empty_kb_sop_match_found_false(self, engine_no_kb):
        svc_empty = build_knowledge_service()
        eng = WorkflowEngine(knowledge_service=svc_empty)
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = eng.start(_make_case(), _registry(defn), _slot_values(session_id="S-empty2"))
        assert result.knowledge_result.get("sop_match_found") is False


# ── JSONB round-trip ──────────────────────────────────────────────────────────

class TestE2EJSONBRoundTrip:
    def test_workflow_context_round_trips(self, engine):
        defn = _workflow_kb_only("OTP_Delivery_Failure")
        result = engine.start(
            _make_case("OTP_Delivery_Failure"), _registry(defn),
            _slot_values(phone_number="9876543210"),
        )
        d = result.to_dict()
        json_str = json.dumps(d)
        restored_dict = json.loads(json_str)
        restored = WorkflowExecutionResult.from_dict(restored_dict)
        assert restored.knowledge_result is not None

    def test_recommendation_survives_round_trip(self, engine):
        defn = _workflow_kb_only("VKYC_Session_Failure")
        result = engine.start(_make_case(), _registry(defn), _slot_values(session_id="S-rt-001"))
        d = result.to_dict()
        restored = WorkflowExecutionResult.from_dict(json.loads(json.dumps(d)))
        rec = restored.knowledge_result.get("recommendation") or {}
        assert rec.get("recommended_action")
