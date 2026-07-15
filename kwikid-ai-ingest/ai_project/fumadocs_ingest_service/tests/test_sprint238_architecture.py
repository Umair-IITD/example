"""
tests/test_sprint238_architecture.py

Sprint 2.38: Architecture regression suite.

Validates all new types introduced in Sprint 2.38:
  - KnowledgeBase: KnowledgeQuery, KnowledgeEntry, KnowledgeProviderResult, KnowledgeProvider ABC
  - SOP: SOPDocument, SOPTriggerCondition, SOPStep, SOPRegistry, SOPResolver
  - Workflow contracts: WorkflowRequirement, WorkflowOutput, WorkflowTransition
  - Workflow repository: WorkflowRepository ABC, WorkflowResolver
  - Evidence domain: EvidencePriority, EvidenceConfidence, EvidenceMetadata, EvidenceReference
  - Vision: VisionReference, VisionAnalysis, VisionEvidence hierarchy, NullVisionProvider
  - InvestigationContext: state machine, slot ops, audit trail, serialization
  - Tools: ToolProvider, ToolCapability, ToolDefinition update

100% pure unit tests — no network, no database, no filesystem.
All tests run offline.
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass
from typing import Any

# ── Knowledge base ─────────────────────────────────────────────────────────────
from case_engine.knowledge.base import (
    KnowledgeEntry,
    KnowledgeProvider,
    KnowledgeProviderResult,
    KnowledgeQuery,
    KnowledgeRepository,
)

# ── SOP ────────────────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.models import (
    SOPActionType,
    SOPDocument,
    SOPStatus,
    SOPStep,
    SOPTriggerCondition,
    SOPTriggerOperator,
    SOPVersion,
)
from case_engine.knowledge.sop.repository import SOPRegistry, SOPRepository
from case_engine.knowledge.sop.provider import SOPResolver, SOPProvider

# ── Workflow contracts ─────────────────────────────────────────────────────────
from case_engine.workflows.contracts import (
    OutputKind,
    RequirementKind,
    TransitionTrigger,
    WorkflowOutput,
    WorkflowRequirement,
    WorkflowTransition,
)
from case_engine.workflows.repository import WorkflowRepository, WorkflowResolver
from case_engine.workflows.models import WorkflowDefinition

# ── Evidence domain ────────────────────────────────────────────────────────────
from case_engine.investigation.evidence.models import (
    EvidenceConfidence,
    EvidenceMetadata,
    EvidencePriority,
    EvidenceReference,
    EvidenceStatus,
)

# ── Vision ─────────────────────────────────────────────────────────────────────
from case_engine.vision.models import (
    DocumentEvidence,
    ImageEvidence,
    VideoEvidence,
    VisionAnalysis,
    VisionAnalysisStatus,
    VisionEvidence,
    VisionFinding,
    VisionModality,
    VisionReference,
)
from case_engine.vision.provider import (
    NullVisionProvider,
    VisionCapability,
    VisionProvider,
)

# ── InvestigationContext ───────────────────────────────────────────────────────
from case_engine.investigation.context import (
    InvestigationContext,
    InvestigationState,
    InvestigationTiming,
)

# ── Tenant ─────────────────────────────────────────────────────────────────────
from case_engine.tenant.models import (
    TenantContext,
    TenantEnvironment,
    TenantType,
)

# ── Tools ──────────────────────────────────────────────────────────────────────
from case_engine.tools.tool_models import (
    ToolCapability,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures & helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_tenant() -> TenantContext:
    return TenantContext(
        client_id="unity_bank",
        client_name="Unity Bank",
        domain="unitybank.co.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetSessionDetailsTool", "GetUserDetailsTool"),
        credentials_ref="unity_bank_api_key_ref",
    )


def _make_sop(
    sop_id: str = "sop_vkyc_failure",
    topic: str = "VKYC_SESSION_FAILURE",
    status: SOPStatus = SOPStatus.ACTIVE,
    applicable_to: tuple[str, ...] = (),
    trigger_conditions: tuple[SOPTriggerCondition, ...] = (),
    step_count: int = 2,
) -> SOPDocument:
    steps = tuple(
        SOPStep(
            step_number=i + 1,
            step_id=f"step_{i}",
            action_type=SOPActionType.INVESTIGATE,
            title=f"Step {i + 1}",
            instruction=f"Do step {i + 1}",
        )
        for i in range(step_count)
    )
    return SOPDocument(
        sop_id=sop_id,
        title="VKYC Failure SOP",
        topic=topic,
        status=status,
        version="1.0",
        trigger_conditions=trigger_conditions,
        steps=steps,
        applicable_to=applicable_to,
    )


def _make_vision_reference(modality: VisionModality = VisionModality.IMAGE) -> VisionReference:
    return VisionReference(
        reference_id="ref-001",
        modality=modality,
        uri="s3://kwikid-evidence/session-screenshot.jpg",
        content_type="image/jpeg",
        captured_at="2024-01-15T10:30:00Z",
    )


def _make_vision_analysis(
    reference: VisionReference,
    status: VisionAnalysisStatus = VisionAnalysisStatus.COMPLETED,
    findings: list[VisionFinding] | None = None,
) -> VisionAnalysis:
    return VisionAnalysis(
        analysis_id="analysis-001",
        reference_id=reference.reference_id,
        modality=reference.modality,
        status=status,
        findings=findings if findings is not None else [VisionFinding.BLANK_SCREEN],
        confidence=0.92,
        raw_output={"score": 0.92},
        analysed_at="2024-01-15T10:30:05Z",
        provider_name="test_provider",
    )


def _make_context() -> InvestigationContext:
    return InvestigationContext.create(
        case_id="case-001",
        ticket_id="ticket-001",
        ticket_subject="VKYC session failed",
        ticket_description="Customer cannot complete VKYC session.",
        customer_id="cust-001",
        customer_email="rahul@unitybank.co.in",
        channel="email",
        tenant_context=_make_tenant(),
        topic="VKYC_SESSION_FAILURE",
        classification_confidence=0.87,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Knowledge base
# ═══════════════════════════════════════════════════════════════════════════════

class TestKnowledgeQuery:
    def test_defaults_populated(self):
        q = KnowledgeQuery(topic="OTP_DELIVERY_FAILURE", query_text="otp not received")
        assert q.query_id  # auto-populated UUID
        assert q.created_at  # auto-populated ISO timestamp
        assert q.max_results == 5
        assert q.context == {}
        assert q.client_id is None

    def test_custom_fields(self):
        q = KnowledgeQuery(
            topic="VKYC_SESSION_FAILURE",
            query_text="camera not working",
            context={"session_id": "sess-123"},
            client_id="unity_bank",
            max_results=3,
        )
        assert q.client_id == "unity_bank"
        assert q.max_results == 3
        assert q.context["session_id"] == "sess-123"

    def test_unique_query_ids(self):
        q1 = KnowledgeQuery(topic="T", query_text="x")
        q2 = KnowledgeQuery(topic="T", query_text="x")
        assert q1.query_id != q2.query_id


class TestKnowledgeEntry:
    def test_construction(self):
        entry = KnowledgeEntry(
            entry_id="e-1",
            source="sop_repository",
            title="Reset VKYC Session",
            content="Step 1: Reset. Step 2: Verify.",
            topic="VKYC_SESSION_FAILURE",
            relevance=0.9,
        )
        assert entry.relevance == 0.9
        assert entry.metadata == {}

    def test_default_metadata(self):
        entry = KnowledgeEntry(
            entry_id="e-2", source="s", title="T", content="C", topic="T"
        )
        assert isinstance(entry.metadata, dict)


class TestKnowledgeProviderResult:
    def test_empty_factory(self):
        result = KnowledgeProviderResult.empty("sop_repository", "q-1")
        assert result.provider_name == "sop_repository"
        assert result.query_id == "q-1"
        assert result.entries == []
        assert result.total_found == 0

    def test_with_entries(self):
        entry = KnowledgeEntry(
            entry_id="e-1", source="s", title="T", content="C", topic="T"
        )
        result = KnowledgeProviderResult(
            provider_name="sop_repository",
            query_id="q-1",
            entries=[entry],
            total_found=5,
        )
        assert len(result.entries) == 1
        assert result.total_found == 5


class TestKnowledgeProviderABC:
    def test_cannot_instantiate_without_implementation(self):
        with pytest.raises(TypeError):
            KnowledgeProvider()  # type: ignore[abstract]

    def test_cannot_instantiate_repository_without_implementation(self):
        with pytest.raises(TypeError):
            KnowledgeRepository()  # type: ignore[abstract]

    def test_concrete_provider_works(self):
        class _TestProvider(KnowledgeProvider):
            @property
            def provider_name(self) -> str:
                return "test"

            def retrieve(self, query):
                return KnowledgeProviderResult.empty("test", query.query_id)

        provider = _TestProvider()
        assert provider.is_available() is True
        assert provider.provider_name == "test"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SOP Models
# ═══════════════════════════════════════════════════════════════════════════════

class TestSOPTriggerCondition:
    def test_equals_operator(self):
        cond = SOPTriggerCondition(
            field="failure_code", operator=SOPTriggerOperator.EQUALS, value="LIVENESS_FAIL"
        )
        assert cond.matches({"failure_code": "LIVENESS_FAIL"}) is True
        assert cond.matches({"failure_code": "OTHER"}) is False
        assert cond.matches({}) is False

    def test_equals_case_insensitive(self):
        cond = SOPTriggerCondition(
            field="status", operator=SOPTriggerOperator.EQUALS, value="failed"
        )
        assert cond.matches({"status": "FAILED"}) is True
        assert cond.matches({"status": "Failed"}) is True

    def test_contains_operator(self):
        cond = SOPTriggerCondition(
            field="error_msg", operator=SOPTriggerOperator.CONTAINS, value="network"
        )
        assert cond.matches({"error_msg": "Network timeout occurred"}) is True
        assert cond.matches({"error_msg": "Camera not found"}) is False

    def test_in_operator(self):
        cond = SOPTriggerCondition(
            field="category",
            operator=SOPTriggerOperator.IN,
            value=["LIVENESS_FAILURE", "DOCUMENT_FAILURE"],
        )
        assert cond.matches({"category": "LIVENESS_FAILURE"}) is True
        assert cond.matches({"category": "NETWORK_FAILURE"}) is False

    def test_exists_operator(self):
        cond = SOPTriggerCondition(
            field="session_id", operator=SOPTriggerOperator.EXISTS, value=None
        )
        assert cond.matches({"session_id": "sess-123"}) is True
        assert cond.matches({}) is False
        assert cond.matches({"session_id": None}) is False

    def test_never_raises_on_bad_input(self):
        cond = SOPTriggerCondition(
            field="x", operator=SOPTriggerOperator.EQUALS, value="y"
        )
        assert cond.matches(None) is False  # type: ignore[arg-type]
        assert cond.matches("not a dict") is False  # type: ignore[arg-type]

    def test_to_dict(self):
        cond = SOPTriggerCondition(
            field="f", operator=SOPTriggerOperator.EQUALS, value="v", description="test"
        )
        d = cond.to_dict()
        assert d["field"] == "f"
        assert d["operator"] == "eq"
        assert d["value"] == "v"


class TestSOPDocument:
    def test_is_active(self):
        sop_active = _make_sop(status=SOPStatus.ACTIVE)
        sop_draft = _make_sop(status=SOPStatus.DRAFT)
        assert sop_active.is_active() is True
        assert sop_draft.is_active() is False

    def test_applies_to_client_global(self):
        sop = _make_sop(applicable_to=())
        assert sop.applies_to_client("any_client") is True
        assert sop.applies_to_client("unity_bank") is True

    def test_applies_to_client_restricted(self):
        sop = _make_sop(applicable_to=("unity_bank", "kotak"))
        assert sop.applies_to_client("unity_bank") is True
        assert sop.applies_to_client("hdfc") is False

    def test_matches_context_all_conditions(self):
        cond1 = SOPTriggerCondition(
            field="failure_code", operator=SOPTriggerOperator.EQUALS, value="LIVENESS"
        )
        cond2 = SOPTriggerCondition(
            field="attempt_count", operator=SOPTriggerOperator.EXISTS, value=None
        )
        sop = _make_sop(trigger_conditions=(cond1, cond2))
        assert sop.matches_context({"failure_code": "LIVENESS", "attempt_count": 3}) is True
        assert sop.matches_context({"failure_code": "LIVENESS"}) is False
        assert sop.matches_context({}) is False

    def test_matches_context_no_conditions(self):
        sop = _make_sop(trigger_conditions=())
        assert sop.matches_context({}) is True
        assert sop.matches_context({"any": "thing"}) is True

    def test_to_dict_structure(self):
        sop = _make_sop()
        d = sop.to_dict()
        assert d["sop_id"] == "sop_vkyc_failure"
        assert d["topic"] == "VKYC_SESSION_FAILURE"
        assert d["status"] == "ACTIVE"
        assert d["step_count"] == 2
        assert isinstance(d["steps"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SOP Repository
# ═══════════════════════════════════════════════════════════════════════════════

class TestSOPRepository:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            SOPRepository()  # type: ignore[abstract]

    def test_register_and_get(self):
        registry = SOPRegistry()
        sop = _make_sop()
        registry.register(sop)
        assert registry.get_by_id("sop_vkyc_failure") is sop
        assert registry.count() == 1

    def test_get_by_id_missing(self):
        registry = SOPRegistry()
        assert registry.get_by_id("nonexistent") is None

    def test_get_by_topic_active_only(self):
        registry = SOPRegistry()
        active = _make_sop(sop_id="s1", topic="VKYC_SESSION_FAILURE", status=SOPStatus.ACTIVE)
        draft = _make_sop(sop_id="s2", topic="VKYC_SESSION_FAILURE", status=SOPStatus.DRAFT)
        registry.register(active)
        registry.register(draft)
        results = registry.get_by_topic("VKYC_SESSION_FAILURE")
        assert len(results) == 1
        assert results[0].sop_id == "s1"

    def test_get_by_topic_wrong_topic(self):
        registry = SOPRegistry()
        registry.register(_make_sop(topic="VKYC_SESSION_FAILURE"))
        assert registry.get_by_topic("OTP_DELIVERY_FAILURE") == []

    def test_get_all_active(self):
        registry = SOPRegistry()
        registry.register(_make_sop(sop_id="s1", topic="VKYC_SESSION_FAILURE"))
        registry.register(_make_sop(sop_id="s2", topic="OTP_DELIVERY_FAILURE"))
        registry.register(_make_sop(sop_id="s3", topic="VKYC_SESSION_FAILURE", status=SOPStatus.DEPRECATED))
        active = registry.get_all_active()
        assert len(active) == 2
        assert all(s.is_active() for s in active)

    def test_register_many(self):
        registry = SOPRegistry()
        sops = [_make_sop(sop_id=f"s{i}", topic=f"TOPIC_{i}") for i in range(5)]
        registry.register_many(sops)
        assert registry.count() == 5

    def test_overwrite_same_id(self):
        registry = SOPRegistry()
        v1 = _make_sop(sop_id="sop_1")
        v2 = SOPDocument(
            sop_id="sop_1", title="Updated", topic="VKYC_SESSION_FAILURE",
            status=SOPStatus.ACTIVE, version="2.0",
            trigger_conditions=(), steps=(),
        )
        registry.register(v1)
        registry.register(v2)
        assert registry.count() == 1
        assert registry.get_by_id("sop_1").version == "2.0"  # type: ignore

    def test_all_topics(self):
        registry = SOPRegistry()
        registry.register(_make_sop(sop_id="s1", topic="VKYC_SESSION_FAILURE"))
        registry.register(_make_sop(sop_id="s2", topic="OTP_DELIVERY_FAILURE"))
        registry.register(_make_sop(sop_id="s3", topic="VKYC_SESSION_FAILURE", status=SOPStatus.DEPRECATED))
        topics = registry.all_topics()
        assert "VKYC_SESSION_FAILURE" in topics
        assert "OTP_DELIVERY_FAILURE" in topics

    def test_summary(self):
        registry = SOPRegistry()
        registry.register(_make_sop(sop_id="s1", status=SOPStatus.ACTIVE))
        registry.register(_make_sop(sop_id="s2", status=SOPStatus.DRAFT))
        registry.register(_make_sop(sop_id="s3", status=SOPStatus.DEPRECATED))
        summary = registry.summary()
        assert summary["ACTIVE"] == 1
        assert summary["DRAFT"] == 1
        assert summary["DEPRECATED"] == 1
        assert summary["ARCHIVED"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SOP Provider / Resolver
# ═══════════════════════════════════════════════════════════════════════════════

class TestSOPResolver:
    def _registry_with_sops(self) -> SOPRegistry:
        registry = SOPRegistry()
        # Global SOP (applies to all)
        registry.register(_make_sop(
            sop_id="sop_global",
            topic="VKYC_SESSION_FAILURE",
            applicable_to=(),
            step_count=2,
        ))
        # Client-specific SOP
        registry.register(_make_sop(
            sop_id="sop_unity",
            topic="VKYC_SESSION_FAILURE",
            applicable_to=("unity_bank",),
            step_count=3,
        ))
        return registry

    def test_find_matching_sops_all_active(self):
        resolver = SOPResolver(self._registry_with_sops())
        sops = resolver.find_matching_sops(
            topic="VKYC_SESSION_FAILURE", context={}, client_id=None
        )
        assert len(sops) == 2

    def test_client_specific_filter(self):
        resolver = SOPResolver(self._registry_with_sops())
        sops = resolver.find_matching_sops(
            topic="VKYC_SESSION_FAILURE", context={}, client_id="hdfc"
        )
        assert len(sops) == 1
        assert sops[0].sop_id == "sop_global"

    def test_client_specific_first_in_results(self):
        resolver = SOPResolver(self._registry_with_sops())
        sops = resolver.find_matching_sops(
            topic="VKYC_SESSION_FAILURE", context={}, client_id="unity_bank"
        )
        assert len(sops) == 2
        assert sops[0].sop_id == "sop_unity"  # client-specific first

    def test_trigger_condition_filtering(self):
        registry = SOPRegistry()
        cond = SOPTriggerCondition(
            field="failure_code", operator=SOPTriggerOperator.EQUALS, value="LIVENESS"
        )
        registry.register(_make_sop(sop_id="s1", trigger_conditions=(cond,)))
        registry.register(_make_sop(sop_id="s2", trigger_conditions=()))
        resolver = SOPResolver(registry)
        matching = resolver.find_matching_sops(
            topic="VKYC_SESSION_FAILURE",
            context={"failure_code": "NETWORK"},
        )
        assert len(matching) == 1
        assert matching[0].sop_id == "s2"

    def test_retrieve_converts_to_entries(self):
        resolver = SOPResolver(self._registry_with_sops())
        query = KnowledgeQuery(topic="VKYC_SESSION_FAILURE", query_text="camera issue")
        result = resolver.retrieve(query)
        assert result.provider_name == "sop_repository"
        assert len(result.entries) > 0
        assert all(e.topic == "VKYC_SESSION_FAILURE" for e in result.entries)

    def test_retrieve_never_raises(self):
        resolver = SOPResolver(SOPRegistry())
        query = KnowledgeQuery(topic="NONEXISTENT_TOPIC", query_text="whatever")
        result = resolver.retrieve(query)
        assert result.entries == []
        assert result.total_found == 0

    def test_retrieve_respects_max_results(self):
        registry = SOPRegistry()
        for i in range(5):
            registry.register(_make_sop(sop_id=f"s{i}"))
        resolver = SOPResolver(registry)
        query = KnowledgeQuery(
            topic="VKYC_SESSION_FAILURE", query_text="x", max_results=2
        )
        result = resolver.retrieve(query)
        assert len(result.entries) == 2
        assert result.total_found == 5

    def test_is_available(self):
        resolver = SOPResolver(SOPRegistry())
        assert resolver.is_available() is True


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Workflow contracts
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkflowRequirement:
    def test_required_by_default(self):
        req = WorkflowRequirement(kind=RequirementKind.SLOT, key="session_id")
        assert req.required is True
        assert req.description == ""

    def test_optional_requirement(self):
        req = WorkflowRequirement(
            kind=RequirementKind.EVIDENCE, key="user_kyc_status", required=False
        )
        assert req.required is False

    def test_to_dict(self):
        req = WorkflowRequirement(
            kind=RequirementKind.KNOWLEDGE,
            key="sop_matched",
            required=True,
            description="SOP must be found before proceeding",
        )
        d = req.to_dict()
        assert d["kind"] == "KNOWLEDGE"
        assert d["key"] == "sop_matched"
        assert d["required"] is True
        assert "description" in d

    def test_all_requirement_kinds(self):
        kinds = [
            RequirementKind.SLOT,
            RequirementKind.EVIDENCE,
            RequirementKind.INVESTIGATION,
            RequirementKind.KNOWLEDGE,
            RequirementKind.APPROVAL,
            RequirementKind.TOOL_RESULT,
        ]
        for kind in kinds:
            req = WorkflowRequirement(kind=kind, key="x")
            assert req.to_dict()["kind"] == kind.value


class TestWorkflowOutput:
    def test_defaults(self):
        out = WorkflowOutput(kind=OutputKind.EVIDENCE, key="user_evidence")
        assert out.description == ""
        assert out.schema_hint == ""

    def test_to_dict(self):
        out = WorkflowOutput(
            kind=OutputKind.ROOT_CAUSE,
            key="root_cause_analysis",
            description="RCA result",
            schema_hint="RootCauseAnalysis.to_dict()",
        )
        d = out.to_dict()
        assert d["kind"] == "ROOT_CAUSE"
        assert d["key"] == "root_cause_analysis"

    def test_all_output_kinds(self):
        kinds = [
            OutputKind.EVIDENCE, OutputKind.ROOT_CAUSE, OutputKind.RECOMMENDATION,
            OutputKind.ACTION_PROPOSAL, OutputKind.KNOWLEDGE_ENTRY,
            OutputKind.OBSERVATION, OutputKind.CLARIFICATION,
        ]
        for kind in kinds:
            out = WorkflowOutput(kind=kind, key="x")
            assert out.to_dict()["kind"] == kind.value


class TestWorkflowTransition:
    def test_is_terminal_resolve(self):
        t = WorkflowTransition(trigger=TransitionTrigger.SUCCESS, target_step="RESOLVE")
        assert t.is_terminal() is True

    def test_is_terminal_escalate(self):
        t = WorkflowTransition(trigger=TransitionTrigger.ESCALATE, target_step="ESCALATE")
        assert t.is_terminal() is True

    def test_is_terminal_fail(self):
        t = WorkflowTransition(trigger=TransitionTrigger.FAILURE, target_step="FAIL")
        assert t.is_terminal() is True

    def test_is_not_terminal(self):
        t = WorkflowTransition(trigger=TransitionTrigger.SUCCESS, target_step="step_propose")
        assert t.is_terminal() is False

    def test_to_dict(self):
        t = WorkflowTransition(
            trigger=TransitionTrigger.LOW_CONFIDENCE,
            target_step="ESCALATE",
            condition="confidence < 0.4",
            description="Escalate if confidence too low",
        )
        d = t.to_dict()
        assert d["trigger"] == "LOW_CONFIDENCE"
        assert d["target_step"] == "ESCALATE"
        assert d["is_terminal"] is True

    def test_all_transition_triggers(self):
        triggers = [
            TransitionTrigger.SUCCESS, TransitionTrigger.FAILURE, TransitionTrigger.ESCALATE,
            TransitionTrigger.APPROVED, TransitionTrigger.REJECTED,
            TransitionTrigger.SLOT_MISSING, TransitionTrigger.LOW_CONFIDENCE,
            TransitionTrigger.HIGH_RISK,
        ]
        for trigger in triggers:
            t = WorkflowTransition(trigger=trigger, target_step="step_next")
            assert t.to_dict()["trigger"] == trigger.value


# ═══════════════════════════════════════════════════════════════════════════════
# 6. WorkflowRepository & WorkflowResolver
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _MockWorkflow:
    """Minimal workflow-like object for resolver tests."""
    workflow_id: str
    topic: str
    version: str = "1.0"


class _MinimalWorkflowRepo(WorkflowRepository):
    def __init__(self, workflows: list[_MockWorkflow]) -> None:
        self._workflows = {w.workflow_id: w for w in workflows}
        self._by_topic = {w.topic: w for w in workflows}

    def get(self, topic: str):  # type: ignore[override]
        return self._by_topic.get(topic)

    def get_by_id(self, workflow_id: str):  # type: ignore[override]
        return self._workflows.get(workflow_id)

    def list_all(self):  # type: ignore[override]
        return list(self._workflows.values())


class TestWorkflowRepository:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            WorkflowRepository()  # type: ignore[abstract]

    def test_get_by_topic_delegates_to_get(self):
        wf = _MockWorkflow(workflow_id="wf-1", topic="VKYC_SESSION_FAILURE")
        repo = _MinimalWorkflowRepo([wf])
        assert repo.get_by_topic("VKYC_SESSION_FAILURE") is wf
        assert repo.get_by_topic("OTHER") is None

    def test_count_delegates_to_list_all(self):
        workflows = [
            _MockWorkflow(workflow_id=f"wf-{i}", topic=f"TOPIC_{i}")
            for i in range(3)
        ]
        repo = _MinimalWorkflowRepo(workflows)
        assert repo.count() == 3

    def test_available_topics(self):
        repo = _MinimalWorkflowRepo([
            _MockWorkflow(workflow_id="wf-1", topic="VKYC_SESSION_FAILURE"),
            _MockWorkflow(workflow_id="wf-2", topic="OTP_DELIVERY_FAILURE"),
        ])
        topics = repo.available_topics()
        assert "VKYC_SESSION_FAILURE" in topics
        assert "OTP_DELIVERY_FAILURE" in topics


class TestWorkflowResolver:
    def _make_repo(self) -> _MinimalWorkflowRepo:
        return _MinimalWorkflowRepo([
            _MockWorkflow(workflow_id="wf-vkyc", topic="VKYC_SESSION_FAILURE"),
            _MockWorkflow(workflow_id="wf-otp", topic="OTP_DELIVERY_FAILURE"),
        ])

    def test_resolve_by_topic(self):
        resolver = WorkflowResolver(self._make_repo())
        wf = resolver.resolve("VKYC_SESSION_FAILURE")
        assert wf is not None
        assert wf.workflow_id == "wf-vkyc"  # type: ignore

    def test_resolve_unknown_topic_returns_none(self):
        resolver = WorkflowResolver(self._make_repo())
        assert resolver.resolve("UNKNOWN_TOPIC") is None

    def test_resolve_with_workflow_id_override(self):
        resolver = WorkflowResolver(self._make_repo())
        wf = resolver.resolve(
            "VKYC_SESSION_FAILURE",
            workflow_overrides={"workflow_id": "wf-otp"},
        )
        assert wf is not None
        assert wf.workflow_id == "wf-otp"  # type: ignore

    def test_override_falls_back_to_topic_on_unknown_id(self):
        resolver = WorkflowResolver(self._make_repo())
        wf = resolver.resolve(
            "VKYC_SESSION_FAILURE",
            workflow_overrides={"workflow_id": "nonexistent-id"},
        )
        assert wf is not None
        assert wf.workflow_id == "wf-vkyc"  # type: ignore

    def test_resolve_never_raises(self):
        class _BrokenRepo(_MinimalWorkflowRepo):
            def get(self, topic):
                raise RuntimeError("database down")

        resolver = WorkflowResolver(_BrokenRepo([]))
        result = resolver.resolve("ANY_TOPIC")
        assert result is None

    def test_available_topics(self):
        resolver = WorkflowResolver(self._make_repo())
        topics = resolver.available_topics()
        assert "VKYC_SESSION_FAILURE" in topics
        assert "OTP_DELIVERY_FAILURE" in topics


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Evidence domain
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidencePriority:
    def test_ordering_critical_lt_high(self):
        assert EvidencePriority.CRITICAL < EvidencePriority.HIGH

    def test_ordering_high_lt_normal(self):
        assert EvidencePriority.HIGH < EvidencePriority.NORMAL

    def test_ordering_normal_lt_low(self):
        assert EvidencePriority.NORMAL < EvidencePriority.LOW

    def test_ordering_critical_lt_low(self):
        assert EvidencePriority.CRITICAL < EvidencePriority.LOW

    def test_gt_ordering(self):
        assert EvidencePriority.LOW > EvidencePriority.HIGH

    def test_le_and_ge(self):
        assert EvidencePriority.CRITICAL <= EvidencePriority.CRITICAL
        assert EvidencePriority.LOW >= EvidencePriority.LOW
        assert EvidencePriority.HIGH <= EvidencePriority.NORMAL

    def test_sortable(self):
        priorities = [
            EvidencePriority.LOW,
            EvidencePriority.CRITICAL,
            EvidencePriority.NORMAL,
            EvidencePriority.HIGH,
        ]
        sorted_priorities = sorted(priorities)
        assert sorted_priorities == [
            EvidencePriority.CRITICAL,
            EvidencePriority.HIGH,
            EvidencePriority.NORMAL,
            EvidencePriority.LOW,
        ]

    def test_values(self):
        assert EvidencePriority.CRITICAL.value == "CRITICAL"
        assert EvidencePriority.LOW.value == "LOW"


class TestEvidenceStatus:
    def test_all_values(self):
        expected = {"PENDING", "COLLECTED", "FAILED", "INVALID", "SUPERSEDED"}
        actual = {s.value for s in EvidenceStatus}
        assert actual == expected


class TestEvidenceConfidence:
    def test_score_clamped_high(self):
        ec = EvidenceConfidence(score=1.5)
        assert ec.score == 1.0

    def test_score_clamped_low(self):
        ec = EvidenceConfidence(score=-0.3)
        assert ec.score == 0.0

    def test_score_valid(self):
        ec = EvidenceConfidence(score=0.75, rationale="Tool succeeded", basis="tool_success")
        assert ec.score == 0.75

    def test_score_boundary_zero(self):
        ec = EvidenceConfidence(score=0.0)
        assert ec.score == 0.0

    def test_score_boundary_one(self):
        ec = EvidenceConfidence(score=1.0)
        assert ec.score == 1.0

    def test_immutable(self):
        ec = EvidenceConfidence(score=0.8)
        with pytest.raises((AttributeError, TypeError)):
            ec.score = 0.5  # type: ignore[misc]

    def test_to_dict(self):
        ec = EvidenceConfidence(score=0.9, rationale="High confidence", basis="primary")
        d = ec.to_dict()
        assert d["score"] == 0.9
        assert d["rationale"] == "High confidence"
        assert d["basis"] == "primary"


class TestEvidenceMetadata:
    def test_construction(self):
        meta = EvidenceMetadata(
            source_system="freshdesk",
            collection_method="api_call",
            reliability_tier="primary",
            tags=("kyc", "session"),
            provenance_url="https://freshdesk.example.com/tickets/123",
        )
        assert meta.source_system == "freshdesk"
        assert "kyc" in meta.tags

    def test_defaults(self):
        meta = EvidenceMetadata(source_system="vkyc", collection_method="webhook")
        assert meta.reliability_tier == "primary"
        assert meta.tags == ()
        assert meta.provenance_url == ""

    def test_to_dict(self):
        meta = EvidenceMetadata(
            source_system="unity_id",
            collection_method="api_call",
            tags=("identity", "vkyc"),
        )
        d = meta.to_dict()
        assert d["source_system"] == "unity_id"
        assert isinstance(d["tags"], list)
        assert "identity" in d["tags"]


class TestEvidenceReference:
    def test_default_priority(self):
        ref = EvidenceReference(
            evidence_id="ev-1",
            evidence_type="SESSION",
            source_system="vkyc_platform",
            collected_at="2024-01-15T10:00:00Z",
        )
        assert ref.priority == EvidencePriority.NORMAL

    def test_custom_priority(self):
        ref = EvidenceReference(
            evidence_id="ev-2",
            evidence_type="LOG",
            source_system="vkyc_platform",
            collected_at="2024-01-15T10:00:00Z",
            priority=EvidencePriority.CRITICAL,
        )
        assert ref.priority == EvidencePriority.CRITICAL

    def test_to_dict(self):
        ref = EvidenceReference(
            evidence_id="ev-3",
            evidence_type="USER",
            source_system="freshdesk",
            collected_at="2024-01-15T10:00:00Z",
            priority=EvidencePriority.HIGH,
        )
        d = ref.to_dict()
        assert d["evidence_id"] == "ev-3"
        assert d["priority"] == "HIGH"
        assert d["evidence_type"] == "USER"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Vision models
# ═══════════════════════════════════════════════════════════════════════════════

class TestVisionReference:
    def test_construction(self):
        ref = _make_vision_reference()
        assert ref.reference_id == "ref-001"
        assert ref.modality == VisionModality.IMAGE
        assert ref.content_type == "image/jpeg"

    def test_to_dict(self):
        ref = _make_vision_reference()
        d = ref.to_dict()
        assert d["modality"] == "IMAGE"
        assert d["reference_id"] == "ref-001"

    def test_immutable(self):
        ref = _make_vision_reference()
        with pytest.raises((AttributeError, TypeError)):
            ref.uri = "other://path"  # type: ignore[misc]


class TestVisionAnalysis:
    def test_has_finding(self):
        ref = _make_vision_reference()
        analysis = _make_vision_analysis(ref, findings=[VisionFinding.BLANK_SCREEN])
        assert analysis.has_finding(VisionFinding.BLANK_SCREEN) is True
        assert analysis.has_finding(VisionFinding.CAMERA_ISSUE) is False

    def test_is_normal_explicit(self):
        ref = _make_vision_reference()
        analysis = _make_vision_analysis(ref, findings=[VisionFinding.NORMAL])
        assert analysis.is_normal() is True

    def test_is_normal_no_findings(self):
        ref = _make_vision_reference()
        analysis = _make_vision_analysis(ref, findings=[])
        assert analysis.is_normal() is True

    def test_is_not_normal(self):
        ref = _make_vision_reference()
        analysis = _make_vision_analysis(ref, findings=[VisionFinding.LIVENESS_FAILURE])
        assert analysis.is_normal() is False

    def test_to_dict(self):
        ref = _make_vision_reference()
        analysis = _make_vision_analysis(ref, findings=[VisionFinding.BLANK_SCREEN])
        d = analysis.to_dict()
        assert d["status"] == "COMPLETED"
        assert "BLANK_SCREEN" in d["findings"]
        assert d["confidence"] == 0.92


class TestVisionEvidenceHierarchy:
    def test_base_vision_evidence(self):
        ref = _make_vision_reference()
        analysis = _make_vision_analysis(ref)
        ev = VisionEvidence(
            evidence_id="ve-1",
            reference=ref,
            analysis=analysis,
            collected_at="2024-01-15T10:30:05Z",
            case_id="case-001",
        )
        assert ev.modality == VisionModality.IMAGE
        assert ev.status == VisionAnalysisStatus.COMPLETED

    def test_image_evidence(self):
        ref = _make_vision_reference(VisionModality.IMAGE)
        analysis = _make_vision_analysis(ref)
        img = ImageEvidence(
            evidence_id="ie-1",
            reference=ref,
            analysis=analysis,
            collected_at="2024-01-15T10:30:05Z",
            case_id="case-001",
            ocr_text="IDENTITY VERIFICATION FAILED",
            contains_error_dialog=True,
        )
        assert isinstance(img, VisionEvidence)
        assert img.ocr_text == "IDENTITY VERIFICATION FAILED"
        assert img.contains_error_dialog is True

    def test_image_evidence_to_dict(self):
        ref = _make_vision_reference(VisionModality.IMAGE)
        analysis = _make_vision_analysis(ref)
        img = ImageEvidence(
            evidence_id="ie-2",
            reference=ref, analysis=analysis,
            collected_at="2024-01-15T10:30:05Z", case_id="c-1",
            ocr_text="Error: timeout",
            contains_error_dialog=False,
        )
        d = img.to_dict()
        assert d["ocr_text"] == "Error: timeout"
        assert d["contains_error_dialog"] is False
        assert d["modality"] == "IMAGE"

    def test_video_evidence(self):
        ref = _make_vision_reference(VisionModality.VIDEO)
        analysis = _make_vision_analysis(ref)
        vid = VideoEvidence(
            evidence_id="vid-1",
            reference=ref, analysis=analysis,
            collected_at="2024-01-15T10:30:05Z", case_id="c-1",
            duration_seconds=45.2,
            key_timestamps=["00:00:10", "00:00:35"],
            audio_present=True,
        )
        assert isinstance(vid, VisionEvidence)
        assert vid.duration_seconds == 45.2
        d = vid.to_dict()
        assert d["duration_seconds"] == 45.2
        assert d["audio_present"] is True

    def test_document_evidence(self):
        ref = _make_vision_reference(VisionModality.DOCUMENT)
        analysis = _make_vision_analysis(ref)
        doc = DocumentEvidence(
            evidence_id="doc-1",
            reference=ref, analysis=analysis,
            collected_at="2024-01-15T10:30:05Z", case_id="c-1",
            page_count=2,
            ocr_text="Aadhaar Card\nName: Rahul Kumar",
            document_type="AADHAAR",
            is_legible=True,
        )
        assert isinstance(doc, VisionEvidence)
        assert doc.page_count == 2
        assert doc.document_type == "AADHAAR"
        d = doc.to_dict()
        assert d["is_legible"] is True

    def test_polymorphism(self):
        ref = _make_vision_reference(VisionModality.IMAGE)
        analysis = _make_vision_analysis(ref)
        items: list[VisionEvidence] = [
            ImageEvidence(evidence_id="a", reference=ref, analysis=analysis,
                          collected_at="t", case_id="c"),
            VideoEvidence(evidence_id="b", reference=_make_vision_reference(VisionModality.VIDEO),
                          analysis=analysis, collected_at="t", case_id="c"),
        ]
        for item in items:
            d = item.to_dict()
            assert "evidence_id" in d
            assert "modality" in d


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Vision provider
# ═══════════════════════════════════════════════════════════════════════════════

class TestVisionProvider:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            VisionProvider()  # type: ignore[abstract]

    def test_null_provider_returns_skipped(self):
        provider = NullVisionProvider()
        ref = _make_vision_reference()
        result = provider.analyse(ref)
        assert result.status == VisionAnalysisStatus.SKIPPED
        assert isinstance(result, VisionEvidence)

    def test_null_provider_never_raises(self):
        provider = NullVisionProvider()
        ref = _make_vision_reference()
        result = provider.analyse(ref)
        assert result is not None

    def test_null_provider_not_available(self):
        provider = NullVisionProvider()
        assert provider.is_available() is False

    def test_null_provider_no_capabilities(self):
        provider = NullVisionProvider()
        assert provider.capabilities == set()
        assert provider.supports(VisionCapability.IMAGE_ANALYSIS) is False
        assert provider.supports(VisionCapability.IMAGE_OCR) is False

    def test_null_provider_name(self):
        provider = NullVisionProvider()
        assert provider.provider_name == "null_vision"

    def test_null_provider_returns_matched_reference(self):
        provider = NullVisionProvider()
        ref = _make_vision_reference(VisionModality.VIDEO)
        result = provider.analyse(ref)
        assert result.reference.reference_id == ref.reference_id

    def test_vision_capability_values(self):
        expected = {
            "IMAGE_OCR", "IMAGE_ANALYSIS", "VIDEO_ANALYSIS",
            "DOCUMENT_EXTRACTION", "LIVENESS_CHECK", "FACE_MATCH",
        }
        actual = {c.value for c in VisionCapability}
        assert actual == expected


# ═══════════════════════════════════════════════════════════════════════════════
# 10. InvestigationContext
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvestigationContextCreate:
    def test_create_factory(self):
        ctx = _make_context()
        assert ctx.context_id  # UUID auto-generated
        assert ctx.case_id == "case-001"
        assert ctx.topic == "VKYC_SESSION_FAILURE"
        assert ctx.state == InvestigationState.CREATED

    def test_unique_context_ids(self):
        ctx1 = _make_context()
        ctx2 = _make_context()
        assert ctx1.context_id != ctx2.context_id

    def test_initial_defaults(self):
        ctx = _make_context()
        assert ctx.slots == {}
        assert ctx.missing_slots == []
        assert ctx.clarification_rounds == 0
        assert ctx.workflow_definition is None
        assert ctx.sop_document is None
        assert ctx.evidence_bundle is None
        assert ctx.root_cause is None
        assert ctx.observation_text is None
        assert ctx.audit_events == []
        assert ctx.overall_confidence == 0.0

    def test_timing_initialized(self):
        ctx = _make_context()
        assert ctx.timing.started_at
        assert ctx.timing.classified_at is None


class TestInvestigationContextStateMachine:
    def test_initial_state_created(self):
        ctx = _make_context()
        assert ctx.state == InvestigationState.CREATED
        assert not ctx.is_terminal()

    def test_transition_to_investigating(self):
        ctx = _make_context()
        ctx.transition_to(InvestigationState.INVESTIGATING)
        assert ctx.state == InvestigationState.INVESTIGATING

    def test_transition_through_pipeline(self):
        ctx = _make_context()
        stages = [
            InvestigationState.CLASSIFYING,
            InvestigationState.COLLECTING_SLOTS,
            InvestigationState.INVESTIGATING,
            InvestigationState.REASONING,
            InvestigationState.PROPOSING,
            InvestigationState.VALIDATING,
            InvestigationState.EXECUTING,
            InvestigationState.VERIFYING,
            InvestigationState.RESOLVED,
        ]
        for state in stages:
            ctx.transition_to(state)
        assert ctx.state == InvestigationState.RESOLVED
        assert ctx.is_terminal()

    def test_cannot_transition_from_resolved(self):
        ctx = _make_context()
        ctx.transition_to(InvestigationState.RESOLVED)
        with pytest.raises(ValueError):
            ctx.transition_to(InvestigationState.INVESTIGATING)

    def test_cannot_transition_from_escalated(self):
        ctx = _make_context()
        ctx.transition_to(InvestigationState.ESCALATED)
        with pytest.raises(ValueError):
            ctx.transition_to(InvestigationState.REASONING)

    def test_cannot_transition_from_failed(self):
        ctx = _make_context()
        ctx.transition_to(InvestigationState.FAILED)
        with pytest.raises(ValueError):
            ctx.transition_to(InvestigationState.CREATED)

    def test_terminal_states(self):
        for terminal in [InvestigationState.RESOLVED, InvestigationState.ESCALATED, InvestigationState.FAILED]:
            ctx = _make_context()
            ctx.transition_to(terminal)
            assert ctx.is_terminal()

    def test_non_terminal_states(self):
        non_terminal = [
            InvestigationState.CREATED, InvestigationState.CLASSIFYING,
            InvestigationState.COLLECTING_SLOTS, InvestigationState.INVESTIGATING,
            InvestigationState.REASONING, InvestigationState.PROPOSING,
            InvestigationState.VALIDATING, InvestigationState.EXECUTING,
            InvestigationState.VERIFYING,
        ]
        for state in non_terminal:
            ctx = _make_context()
            ctx.state = state
            assert not ctx.is_terminal()

    def test_idempotent_same_state(self):
        ctx = _make_context()
        ctx.transition_to(InvestigationState.INVESTIGATING)
        ctx.transition_to(InvestigationState.INVESTIGATING)
        assert ctx.state == InvestigationState.INVESTIGATING


class TestInvestigationContextSlots:
    def test_set_and_get_slot(self):
        ctx = _make_context()
        ctx.set_slot("session_id", "sess-123")
        assert ctx.get_slot("session_id") == "sess-123"

    def test_get_missing_slot_returns_none(self):
        ctx = _make_context()
        assert ctx.get_slot("nonexistent") is None

    def test_set_slot_removes_from_missing(self):
        ctx = _make_context()
        ctx.missing_slots = ["session_id", "user_urn"]
        ctx.set_slot("session_id", "sess-456")
        assert "session_id" not in ctx.missing_slots
        assert "user_urn" in ctx.missing_slots

    def test_set_slot_not_in_missing(self):
        ctx = _make_context()
        ctx.missing_slots = ["other_slot"]
        ctx.set_slot("session_id", "sess-789")
        assert ctx.missing_slots == ["other_slot"]

    def test_slots_complete(self):
        ctx = _make_context()
        ctx.missing_slots = []
        assert ctx.slots_complete() is True
        ctx.missing_slots = ["session_id"]
        assert ctx.slots_complete() is False


class TestInvestigationContextMutations:
    def test_add_audit_event(self):
        ctx = _make_context()
        ctx.add_audit_event("INVESTIGATION_STARTED", {"tool": "GetSessionDetailsTool"})
        assert len(ctx.audit_events) == 1
        event = ctx.audit_events[0]
        assert event["event_type"] == "INVESTIGATION_STARTED"
        assert event["details"]["tool"] == "GetSessionDetailsTool"
        assert "timestamp" in event
        assert "state" in event

    def test_add_multiple_audit_events(self):
        ctx = _make_context()
        ctx.add_audit_event("EVENT_A")
        ctx.add_audit_event("EVENT_B")
        ctx.add_audit_event("EVENT_C")
        assert len(ctx.audit_events) == 3

    def test_add_tool_result(self):
        ctx = _make_context()
        result = ToolResult.ok(
            tool_name="GetSessionDetailsTool",
            payload={"session_status": "FAILED"},
        )
        ctx.add_tool_result(result)
        assert len(ctx.tool_results) == 1
        assert ctx.tool_results[0].tool_name == "GetSessionDetailsTool"

    def test_add_vision_analysis(self):
        ctx = _make_context()
        ref = _make_vision_reference()
        analysis = _make_vision_analysis(ref)
        ev = VisionEvidence(
            evidence_id="ve-1", reference=ref, analysis=analysis,
            collected_at="t", case_id=ctx.case_id,
        )
        ctx.add_vision_analysis(ev)
        assert len(ctx.vision_analyses) == 1


class TestInvestigationContextPresenceChecks:
    def test_has_tenant(self):
        ctx = _make_context()
        assert ctx.has_tenant() is True

    def test_has_topic(self):
        ctx = _make_context()
        assert ctx.has_topic() is True

    def test_has_no_evidence(self):
        ctx = _make_context()
        assert ctx.has_evidence() is False

    def test_has_no_root_cause(self):
        ctx = _make_context()
        assert ctx.has_root_cause() is False

    def test_has_no_sop(self):
        ctx = _make_context()
        assert ctx.has_sop() is False

    def test_has_no_workflow(self):
        ctx = _make_context()
        assert ctx.has_workflow() is False


class TestInvestigationContextToDict:
    def test_to_dict_structure(self):
        ctx = _make_context()
        d = ctx.to_dict()
        required_keys = [
            "context_id", "case_id", "ticket_id", "topic", "state",
            "classification_confidence", "overall_confidence",
            "slots", "missing_slots", "clarification_rounds",
            "sop_match_found", "knowledge_entry_count",
            "tool_result_count", "vision_analysis_count",
            "audit_event_count", "timing", "resolution_status",
            "escalation_reason", "completed_at",
        ]
        for key in required_keys:
            assert key in d, f"Missing key: {key}"

    def test_to_dict_state_value(self):
        ctx = _make_context()
        assert ctx.to_dict()["state"] == "CREATED"

    def test_to_dict_timing_nested(self):
        ctx = _make_context()
        d = ctx.to_dict()
        timing = d["timing"]
        assert "started_at" in timing
        assert "classified_at" in timing


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Tool models update (Part H)
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolProvider:
    def test_all_providers_exist(self):
        # Sprint 2.50 added METRICS_PLATFORM (Uptime Kuma read-only integration).
        # Additive extension of the frozen enum; expected set updated in the
        # PR-review sprint to reflect the current inventory.
        expected = {
            "FRESHDESK", "UNITY", "ADMIN_PORTAL", "S3",
            "DATABASE", "FLAGSMITH", "MINIO", "VISION", "MCP",
            "METRICS_PLATFORM",
        }
        actual = {p.value for p in ToolProvider}
        assert actual == expected


class TestToolCapability:
    def test_all_capabilities_exist(self):
        expected = {"READ", "WRITE", "EXECUTE", "QUERY", "NOTIFY", "ANALYZE"}
        actual = {c.value for c in ToolCapability}
        assert actual == expected


class TestToolDefinitionUpdate:
    def test_backwards_compatible_no_provider(self):
        td = ToolDefinition(
            tool_name="GetSessionDetailsTool",
            description="Fetch VKYC session details",
            required_inputs=("session_id",),
            output_schema={"session_status": "str"},
        )
        assert td.provider is None
        assert td.capability is None

    def test_with_provider_and_capability(self):
        td = ToolDefinition(
            tool_name="ResetSessionTool",
            description="Reset a VKYC session",
            required_inputs=("session_id",),
            output_schema={"reset_success": "bool"},
            provider=ToolProvider.UNITY,
            capability=ToolCapability.EXECUTE,
        )
        assert td.provider == ToolProvider.UNITY
        assert td.capability == ToolCapability.EXECUTE

    def test_to_dict_includes_provider(self):
        td = ToolDefinition(
            tool_name="GetTicketTool",
            description="Fetch Freshdesk ticket",
            required_inputs=("ticket_id",),
            output_schema={"ticket_status": "str"},
            provider=ToolProvider.FRESHDESK,
            capability=ToolCapability.READ,
        )
        d = td.to_dict()
        assert d["provider"] == "FRESHDESK"
        assert d["capability"] == "READ"

    def test_to_dict_null_provider(self):
        td = ToolDefinition(
            tool_name="OldTool",
            description="Legacy tool",
            required_inputs=(),
            output_schema={},
        )
        d = td.to_dict()
        assert d["provider"] is None
        assert d["capability"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Dependency isolation
# ═══════════════════════════════════════════════════════════════════════════════

def _is_import_line(line: str) -> bool:
    """Return True only for actual Python import statements (not comments or docstrings)."""
    stripped = line.strip()
    return stripped.startswith("from ") or stripped.startswith("import ")


class TestDependencyIsolation:
    def test_evidence_models_no_case_engine_imports(self):
        import case_engine.investigation.evidence.models as m
        source = m.__file__
        with open(source, encoding="utf-8") as f:
            content = f.read()
        for line in content.splitlines():
            if not _is_import_line(line):
                continue
            for forbidden in ["from case_engine", "import case_engine"]:
                assert forbidden not in line, (
                    f"evidence/models.py must not import from case_engine: {line!r}"
                )

    def test_vision_models_no_case_engine_imports(self):
        import case_engine.vision.models as m
        source = m.__file__
        with open(source, encoding="utf-8") as f:
            content = f.read()
        for line in content.splitlines():
            if not _is_import_line(line):
                continue
            for forbidden in ["from case_engine", "import case_engine"]:
                assert forbidden not in line, (
                    f"vision/models.py must not import from case_engine: {line!r}"
                )

    def test_sop_models_no_case_engine_imports(self):
        import case_engine.knowledge.sop.models as m
        source = m.__file__
        with open(source, encoding="utf-8") as f:
            content = f.read()
        for line in content.splitlines():
            if not _is_import_line(line):
                continue
            for forbidden in ["from case_engine", "import case_engine"]:
                assert forbidden not in line, (
                    f"sop/models.py must not import from case_engine: {line!r}"
                )

    def test_workflow_contracts_no_case_engine_imports(self):
        import case_engine.workflows.contracts as m
        source = m.__file__
        with open(source, encoding="utf-8") as f:
            content = f.read()
        for line in content.splitlines():
            if not _is_import_line(line):
                continue
            for forbidden in ["from case_engine", "import case_engine"]:
                assert forbidden not in line, (
                    f"workflows/contracts.py must not import from case_engine: {line!r}"
                )

    def test_knowledge_base_no_case_engine_imports(self):
        import case_engine.knowledge.base as m
        source = m.__file__
        with open(source, encoding="utf-8") as f:
            content = f.read()
        for line in content.splitlines():
            if not _is_import_line(line):
                continue
            for forbidden in ["from case_engine", "import case_engine"]:
                assert forbidden not in line, (
                    f"knowledge/base.py must not import from case_engine: {line!r}"
                )
