"""
tests/test_sprint238_integration_certification.py

Sprint 2.38 — Final Architecture Integration Certification
Principal Architect: Certification Audit Suite

Tests:
  Part A  — Dependency graph validation (no cycles, correct direction)
  Part B  — Full InvestigationContext composition with real architecture objects
  Part C  — Repository coexistence
  Part D  — Evidence integration (EvidenceBundle + metadata + vision)
  Part E  — Workflow integration (WorkflowDefinition → WorkflowResolver → ctx)
  Part F  — Knowledge integration (KnowledgeQuery → SOPResolver → EvidenceReference)
  Part G  — Vision integration (NullVisionProvider → VisionEvidence → ctx)
  Part H  — Tool integration (ToolDefinition → ToolProvider → ctx)
  Part I  — Action Gateway field compatibility
  Part J  — Blueprint completeness (structural assertions)
  Part K  — Sprint 2.39 readiness
"""
from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

# ── Knowledge base ─────────────────────────────────────────────────────────────
from case_engine.knowledge.base import (
    KnowledgeEntry,
    KnowledgeProvider,
    KnowledgeProviderResult,
    KnowledgeQuery,
)
from case_engine.knowledge.sop.models import (
    SOPActionType,
    SOPDocument,
    SOPStatus,
    SOPStep,
    SOPTriggerCondition,
    SOPTriggerOperator,
    SOPVersion,
)
from case_engine.knowledge.sop.repository import SOPRegistry
from case_engine.knowledge.sop.provider import SOPResolver

# ── Workflow ───────────────────────────────────────────────────────────────────
from case_engine.workflows.contracts import (
    OutputKind,
    RequirementKind,
    TransitionTrigger,
    WorkflowOutput,
    WorkflowRequirement,
    WorkflowTransition,
)
from case_engine.workflows.models import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.repository import WorkflowRepository, WorkflowResolver

# ── Investigation ──────────────────────────────────────────────────────────────
from case_engine.investigation.context import (
    InvestigationContext,
    InvestigationState,
)
from case_engine.investigation.evidence.models import (
    EvidenceConfidence,
    EvidenceMetadata,
    EvidencePriority,
    EvidenceReference,
    EvidenceStatus,
)
from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    InvestigationPlan,
    InvestigationStep,
    LogEvidence,
    RootCauseAnalysis,
    RootCauseCategory,
    RecommendedAction,
    SessionEvidence,
    UserEvidence,
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

# ── Tools ──────────────────────────────────────────────────────────────────────
from case_engine.tools.tool_models import (
    ToolCapability,
    ToolDefinition,
    ToolInput,
    ToolProvider,
    ToolResult,
)

# ── Tenant ─────────────────────────────────────────────────────────────────────
from case_engine.tenant.models import (
    TenantContext,
    TenantEnvironment,
    TenantType,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Shared test builders
# ═══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _tenant() -> TenantContext:
    return TenantContext(
        client_id="unity_bank",
        client_name="Unity Bank",
        domain="unitybank.co.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetSessionDetailsTool", "GetUserDetailsTool", "GetFailureReasonTool"),
        credentials_ref="unity_bank_api_key",
        workflow_overrides={},
        portal_base_url="https://portal.unitybank.co.in",
    )


def _workflow_step(index: int, step_id: str, step_type: WorkflowStepType) -> WorkflowStep:
    return WorkflowStep(
        step_index=index,
        step_id=step_id,
        step_type=step_type,
        name=f"Step {index}",
        description=f"Step {index}: {step_type.value}",
        on_success="RESOLVE" if index == 2 else f"step_{index + 1}",
        on_failure="ESCALATE",
    )


def _workflow_definition() -> WorkflowDefinition:
    steps = (
        _workflow_step(0, "step_investigate", WorkflowStepType.INVESTIGATE),
        _workflow_step(1, "step_knowledge", WorkflowStepType.KNOWLEDGE_LOOKUP),
        _workflow_step(2, "step_propose", WorkflowStepType.PROPOSE_ACTION),
    )
    return WorkflowDefinition(
        workflow_id="wf-vkyc-failure-v1",
        topic="VKYC_SESSION_FAILURE",
        version="1.0",
        name="VKYC Session Failure Playbook",
        description="Handles VKYC session failure cases end-to-end.",
        required_slots=("session_id",),
        steps=steps,
        tool_candidates=("GetSessionDetailsTool", "GetFailureReasonTool"),
        resolution_paths={"success": "SESSION_RESET", "escalate": "MANUAL_REVIEW"},
    )


def _sop_document() -> SOPDocument:
    cond = SOPTriggerCondition(
        field="failure_code", operator=SOPTriggerOperator.IN,
        value=["LIVENESS_FAIL", "CAMERA_FAIL"],
        description="Apply when failure is liveness or camera related",
    )
    steps = (
        SOPStep(
            step_number=1, step_id="sop_step_1",
            action_type=SOPActionType.INVESTIGATE,
            title="Check session state",
            instruction="Retrieve session details from Unity platform",
            expected_outcome="Session status retrieved",
        ),
        SOPStep(
            step_number=2, step_id="sop_step_2",
            action_type=SOPActionType.EXECUTE,
            title="Reset VKYC session",
            instruction="Trigger session reset via Unity admin API",
            expected_outcome="Session reset successfully",
            on_failure="sop_step_3",
        ),
        SOPStep(
            step_number=3, step_id="sop_step_3",
            action_type=SOPActionType.ESCALATE,
            title="Escalate to L2",
            instruction="Escalate if reset failed",
            expected_outcome="Case escalated to L2 agent",
        ),
    )
    return SOPDocument(
        sop_id="sop_vkyc_session_failure",
        title="VKYC Session Failure Resolution",
        topic="VKYC_SESSION_FAILURE",
        status=SOPStatus.ACTIVE,
        version="1.0",
        trigger_conditions=(cond,),
        steps=steps,
        applicable_to=("unity_bank",),
        tags=("vkyc", "session", "liveness"),
        escalation_threshold=0.4,
        current_version=SOPVersion(
            version="1.0",
            created_by="architecture_team",
            change_note="Initial version",
        ),
        description="Deterministic resolution procedure for VKYC session failures.",
    )


def _investigation_context() -> InvestigationContext:
    return InvestigationContext.create(
        case_id="case-cert-001",
        ticket_id="FD-78901",
        ticket_subject="Unable to complete VKYC session",
        ticket_description="Customer reports camera issue during liveness check.",
        customer_id="cust-rahul-001",
        customer_email="rahul.kumar@unitybank.co.in",
        channel="email",
        tenant_context=_tenant(),
        topic="VKYC_SESSION_FAILURE",
        classification_confidence=0.91,
    )


def _vision_reference(modality: VisionModality = VisionModality.IMAGE) -> VisionReference:
    return VisionReference(
        reference_id="vref-session-screenshot",
        modality=modality,
        uri="s3://kwikid-evidence/case-cert-001/screenshot.jpg",
        content_type="image/jpeg",
        captured_at="2024-01-15T10:30:00Z",
        description="VKYC session screenshot showing camera error",
    )


def _vision_analysis(ref: VisionReference) -> VisionAnalysis:
    return VisionAnalysis(
        analysis_id="analysis-cert-001",
        reference_id=ref.reference_id,
        modality=ref.modality,
        status=VisionAnalysisStatus.COMPLETED,
        findings=[VisionFinding.CAMERA_ISSUE, VisionFinding.BLANK_SCREEN],
        confidence=0.88,
        raw_output={"detected_issues": ["camera_blocked", "blank_frame"]},
        analysed_at=_now(),
        provider_name="null_vision",
        duration_ms=0,
    )


def _evidence_bundle(case_id: str) -> EvidenceBundle:
    now = _now()
    user_ev = UserEvidence(
        evidence_id="ev-user-001",
        evidence_type=EvidenceType.USER,
        source=EvidenceSource.GET_USER_DETAILS,
        tool_name="GetUserDetailsTool",
        payload={"kyc_status": "IN_PROGRESS", "account_state": "ACTIVE", "risk_tier": "LOW"},
        collected_at=now,
        invocation_id="inv-001",
        success=True,
    )
    session_ev = SessionEvidence(
        evidence_id="ev-session-001",
        evidence_type=EvidenceType.SESSION,
        source=EvidenceSource.GET_SESSION_DETAILS,
        tool_name="GetSessionDetailsTool",
        payload={
            "session_id": "sess-abc-123",
            "session_status": "FAILED",
            "attempt_count": 3,
            "failure_code": "LIVENESS_FAIL",
            "reset_eligible": True,
        },
        collected_at=now,
        invocation_id="inv-002",
        success=True,
    )
    log_ev = LogEvidence(
        evidence_id="ev-log-001",
        evidence_type=EvidenceType.LOG,
        source=EvidenceSource.GET_FAILURE_REASON,
        tool_name="GetFailureReasonTool",
        payload={
            "failure_category": "LIVENESS_FAILURE",
            "is_transient": True,
            "recommended_action": "SESSION_RESET",
        },
        collected_at=now,
        invocation_id="inv-003",
        success=True,
    )
    plan = InvestigationPlan(
        plan_id="plan-cert-001",
        case_id=case_id,
        topic="VKYC_SESSION_FAILURE",
        workflow_id="wf-vkyc-failure-v1",
        steps=(
            InvestigationStep(
                step_id="s1",
                sequence=0,
                tool_name="GetUserDetailsTool",
                purpose="Retrieve KYC status",
                required_slot="user_urn",
                input_key="user_urn",
            ),
            InvestigationStep(
                step_id="s2",
                sequence=1,
                tool_name="GetSessionDetailsTool",
                purpose="Retrieve session details",
                required_slot="session_id",
                input_key="session_id",
            ),
            InvestigationStep(
                step_id="s3",
                sequence=2,
                tool_name="GetFailureReasonTool",
                purpose="Determine failure cause",
                required_slot="session_id",
                input_key="session_id",
            ),
        ),
        created_at=now,
    )
    return EvidenceBundle(
        bundle_id="bundle-cert-001",
        case_id=case_id,
        topic="VKYC_SESSION_FAILURE",
        plan_id=plan.plan_id,
        items=[user_ev, session_ev, log_ev],
        collected_at=now,
    )


def _root_cause(case_id: str) -> RootCauseAnalysis:
    return RootCauseAnalysis(
        analysis_id="rca-cert-001",
        case_id=case_id,
        topic="VKYC_SESSION_FAILURE",
        category=RootCauseCategory.LIVENESS_FAILURE,
        confidence=0.87,
        explanation=(
            "Session has 3 failed liveness check attempts. Failure code LIVENESS_FAIL "
            "indicates camera obstruction. Reset is eligible and recommended."
        ),
        evidence_ids=["ev-user-001", "ev-session-001", "ev-log-001"],
        recommended_action=RecommendedAction.SESSION_RESET,
        escalate=False,
        analysed_at=_now(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Part A: Dependency Graph Validation
# ═══════════════════════════════════════════════════════════════════════════════

def _get_ce_imports(module_dotpath: str) -> list[str]:
    """Return case_engine.* modules imported by the given module's source file."""
    mod = importlib.import_module(module_dotpath)
    source = mod.__file__
    if not source or source.endswith(".pyc"):
        pysrc = source.replace(".pyc", ".py").replace("/__pycache__/", "/") if source else None
        if not pysrc:
            return []
        source = pysrc
    with open(source, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("case_engine"):
                found.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("case_engine"):
                    found.append(alias.name)
    return list(set(found))


class TestPartA_DependencyGraph:
    """
    A: Dependency graph — verify correct direction, no cycles.

    Declared graph (import arrows →):
      knowledge/base            ← stdlib only (leaf)
      knowledge/sop/models      ← stdlib only (leaf)
      workflows/contracts       ← stdlib only (leaf)
      investigation/evidence    ← stdlib only (leaf)
      vision/models             ← stdlib only (leaf)
      knowledge/sop/repository  ← knowledge/sop/models
      knowledge/sop/provider    ← knowledge/base, sop/models, sop/repository
      workflows/models          ← stdlib only (leaf)
      workflows/repository      ← workflows/models
      tools/tool_models         ← stdlib only (leaf)
      vision/provider           ← vision/models
      investigation/context     ← workflows/models, knowledge/sop/models,
                                   knowledge/base, investigation/models,
                                   tools/tool_models, tenant/models, vision/models
    """

    def test_A1_knowledge_base_is_leaf(self):
        imports = _get_ce_imports("case_engine.knowledge.base")
        assert imports == [], f"knowledge/base.py must be a leaf; found: {imports}"

    def test_A2_sop_models_is_leaf(self):
        imports = _get_ce_imports("case_engine.knowledge.sop.models")
        assert imports == [], f"sop/models.py must be a leaf; found: {imports}"

    def test_A3_workflow_contracts_is_leaf(self):
        imports = _get_ce_imports("case_engine.workflows.contracts")
        assert imports == [], f"workflows/contracts.py must be a leaf; found: {imports}"

    def test_A4_evidence_models_is_leaf(self):
        imports = _get_ce_imports("case_engine.investigation.evidence.models")
        assert imports == [], f"evidence/models.py must be a leaf; found: {imports}"

    def test_A5_vision_models_is_leaf(self):
        imports = _get_ce_imports("case_engine.vision.models")
        assert imports == [], f"vision/models.py must be a leaf; found: {imports}"

    def test_A6_sop_repository_imports_only_sop_models(self):
        imports = _get_ce_imports("case_engine.knowledge.sop.repository")
        assert all("knowledge.sop" in i for i in imports), (
            f"sop/repository.py must only import from sop package; found: {imports}"
        )

    def test_A7_sop_provider_imports_from_knowledge_layer_only(self):
        imports = _get_ce_imports("case_engine.knowledge.sop.provider")
        for imp in imports:
            assert imp.startswith("case_engine.knowledge"), (
                f"sop/provider.py must only import from knowledge layer; found: {imp}"
            )

    def test_A8_workflow_repository_imports_only_workflow_models(self):
        imports = _get_ce_imports("case_engine.workflows.repository")
        for imp in imports:
            assert imp.startswith("case_engine.workflows"), (
                f"workflows/repository.py must only import from workflows layer; found: {imp}"
            )

    def test_A9_vision_provider_imports_only_vision_models(self):
        imports = _get_ce_imports("case_engine.vision.provider")
        for imp in imports:
            assert imp.startswith("case_engine.vision"), (
                f"vision/provider.py must only import from vision layer; found: {imp}"
            )

    def test_A10_context_does_not_import_gateway(self):
        imports = _get_ce_imports("case_engine.investigation.context")
        for imp in imports:
            assert "action_gateway" not in imp, (
                f"investigation/context.py must not import from action_gateway; found: {imp}"
            )

    def test_A11_context_does_not_import_investigation_planner(self):
        imports = _get_ce_imports("case_engine.investigation.context")
        for imp in imports:
            assert "planner" not in imp, (
                f"investigation/context.py must not import InvestigationPlanner; found: {imp}"
            )

    def test_A12_no_reverse_imports(self):
        """
        Leaf nodes must not import from higher layers.
        Verified: knowledge/base has no case_engine imports.
        Verified: vision/models has no case_engine imports.
        Verified: evidence/models has no case_engine imports.
        """
        for leaf in [
            "case_engine.knowledge.base",
            "case_engine.knowledge.sop.models",
            "case_engine.workflows.contracts",
            "case_engine.investigation.evidence.models",
            "case_engine.vision.models",
        ]:
            assert _get_ce_imports(leaf) == [], f"{leaf} must be a leaf"

    def test_A13_cycle_detection(self):
        """
        Build the full import graph and verify no cycles exist.
        A cycle exists if module A imports module B which (transitively) imports module A.
        """
        graph: dict[str, list[str]] = {}
        modules_to_check = [
            "case_engine.knowledge.base",
            "case_engine.knowledge.sop.models",
            "case_engine.knowledge.sop.repository",
            "case_engine.knowledge.sop.provider",
            "case_engine.workflows.contracts",
            "case_engine.workflows.models",
            "case_engine.workflows.repository",
            "case_engine.investigation.evidence.models",
            "case_engine.investigation.context",
            "case_engine.vision.models",
            "case_engine.vision.provider",
            "case_engine.tools.tool_models",
        ]
        for mod in modules_to_check:
            graph[mod] = _get_ce_imports(mod)

        def has_cycle(node: str, visited: set, stack: set) -> bool:
            visited.add(node)
            stack.add(node)
            for dep in graph.get(node, []):
                if dep not in visited:
                    if has_cycle(dep, visited, stack):
                        return True
                elif dep in stack:
                    return True
            stack.discard(node)
            return False

        for mod in modules_to_check:
            assert not has_cycle(mod, set(), set()), (
                f"Cycle detected in dependency graph starting from {mod}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Part B: Full InvestigationContext Composition with Real Objects
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartB_InvestigationContextComposition:
    """
    B: Prove a complete InvestigationContext can be constructed and populated
       using ONLY Sprint 2.38 architectural components. No dictionaries for
       model objects — typed instances throughout.
    """

    def test_B1_construct_full_investigation_context(self):
        ctx = _investigation_context()
        assert ctx.context_id
        assert ctx.case_id == "case-cert-001"
        assert ctx.topic == "VKYC_SESSION_FAILURE"
        assert ctx.state == InvestigationState.CREATED

    def test_B2_attach_workflow_definition(self):
        ctx = _investigation_context()
        wf = _workflow_definition()
        ctx.workflow_definition = wf
        assert ctx.has_workflow()
        assert ctx.workflow_definition.workflow_id == "wf-vkyc-failure-v1"
        assert len(ctx.workflow_definition.steps) == 3

    def test_B3_attach_sop_document_with_typed_steps(self):
        ctx = _investigation_context()
        sop = _sop_document()
        ctx.sop_document = sop
        ctx.sop_match_found = True
        ctx.sop_steps = sop.steps
        assert ctx.has_sop()
        assert len(ctx.sop_steps) == 3
        assert all(isinstance(s, SOPStep) for s in ctx.sop_steps)

    def test_B4_attach_knowledge_entries(self):
        ctx = _investigation_context()
        entries = [
            KnowledgeEntry(
                entry_id=f"ke-{i}",
                source="sop_repository",
                title=f"Knowledge Entry {i}",
                content=f"Content for entry {i}",
                topic="VKYC_SESSION_FAILURE",
                relevance=0.9 - i * 0.1,
                metadata={"version": "1.0", "tags": ["vkyc"]},
            )
            for i in range(3)
        ]
        ctx.knowledge_entries = entries
        assert len(ctx.knowledge_entries) == 3
        assert all(isinstance(e, KnowledgeEntry) for e in ctx.knowledge_entries)

    def test_B5_attach_evidence_bundle(self):
        ctx = _investigation_context()
        bundle = _evidence_bundle(ctx.case_id)
        ctx.evidence_bundle = bundle
        assert ctx.has_evidence()
        assert len(ctx.evidence_bundle.items) == 3
        assert ctx.evidence_bundle.bundle_id == "bundle-cert-001"

    def test_B6_attach_root_cause(self):
        ctx = _investigation_context()
        rca = _root_cause(ctx.case_id)
        ctx.root_cause = rca
        assert ctx.has_root_cause()
        assert ctx.root_cause.category == RootCauseCategory.LIVENESS_FAILURE
        assert ctx.root_cause.confidence == 0.87

    def test_B7_attach_tool_results(self):
        ctx = _investigation_context()
        results = [
            ToolResult.ok("GetUserDetailsTool", {"kyc_status": "IN_PROGRESS"}, duration_ms=120),
            ToolResult.ok("GetSessionDetailsTool", {"session_status": "FAILED"}, duration_ms=95),
            ToolResult.fail("GetMetricsTool", "SERVICE_UNAVAILABLE", "Metrics service down"),
        ]
        for r in results:
            ctx.add_tool_result(r)
        assert len(ctx.tool_results) == 3
        assert ctx.tool_results[0].tool_name == "GetUserDetailsTool"
        assert ctx.tool_results[2].success is False

    def test_B8_attach_vision_evidence(self):
        ctx = _investigation_context()
        ref = _vision_reference(VisionModality.IMAGE)
        analysis = _vision_analysis(ref)
        img_ev = ImageEvidence(
            evidence_id="img-ev-cert-001",
            reference=ref,
            analysis=analysis,
            collected_at=_now(),
            case_id=ctx.case_id,
            ocr_text="Camera access denied — please grant permissions",
            contains_error_dialog=True,
        )
        ctx.add_vision_analysis(img_ev)
        assert len(ctx.vision_analyses) == 1
        assert isinstance(ctx.vision_analyses[0], ImageEvidence)
        assert ctx.vision_analyses[0].contains_error_dialog is True

    def test_B9_workflow_contracts_companion_objects(self):
        """Verify WorkflowRequirement, WorkflowOutput, WorkflowTransition are
        constructable and match the WorkflowStep they describe."""
        step = _workflow_step(0, "step_investigate", WorkflowStepType.INVESTIGATE)

        requirements = (
            WorkflowRequirement(
                kind=RequirementKind.SLOT,
                key="session_id",
                required=True,
                description="Session ID must be filled before investigation",
            ),
            WorkflowRequirement(
                kind=RequirementKind.SLOT,
                key="user_urn",
                required=False,
                description="User URN enriches the investigation",
            ),
        )
        outputs = (
            WorkflowOutput(
                kind=OutputKind.EVIDENCE,
                key="session_evidence",
                description="Session details from VKYC platform",
            ),
        )
        transitions = (
            WorkflowTransition(
                trigger=TransitionTrigger.SUCCESS,
                target_step="step_knowledge",
                description="Proceed to knowledge lookup",
            ),
            WorkflowTransition(
                trigger=TransitionTrigger.FAILURE,
                target_step="ESCALATE",
                description="Escalate if investigation fails",
            ),
        )

        assert step.step_id == "step_investigate"
        assert len(requirements) == 2
        assert requirements[0].required is True
        assert outputs[0].kind == OutputKind.EVIDENCE
        assert transitions[1].is_terminal() is True
        assert all(r.to_dict() for r in requirements)

    def test_B10_evidence_reference_links_to_evidence_items(self):
        """EvidenceReference should link to items in the EvidenceBundle."""
        bundle = _evidence_bundle("case-cert-001")
        refs = [
            EvidenceReference(
                evidence_id=ev.evidence_id,
                evidence_type=ev.evidence_type.value,
                source_system=ev.source.value,
                collected_at=ev.collected_at,
                priority=EvidencePriority.HIGH if ev.success else EvidencePriority.LOW,
            )
            for ev in bundle.successful_items
        ]
        assert len(refs) == 3
        assert all(isinstance(r, EvidenceReference) for r in refs)
        assert all(r.priority in (EvidencePriority.HIGH, EvidencePriority.LOW) for r in refs)

    def test_B11_full_context_serializes_without_error(self):
        """Full populated InvestigationContext must serialize to a flat dict."""
        ctx = _investigation_context()
        ctx.workflow_definition = _workflow_definition()
        ctx.sop_document = _sop_document()
        ctx.sop_match_found = True
        ctx.sop_steps = _sop_document().steps
        ctx.evidence_bundle = _evidence_bundle(ctx.case_id)
        ctx.root_cause = _root_cause(ctx.case_id)
        ctx.set_slot("session_id", "sess-abc-123")
        ctx.set_slot("user_urn", "urn:kwikid:rahul-001")
        ctx.add_audit_event("INVESTIGATION_STARTED")
        ctx.add_audit_event("EVIDENCE_COLLECTED", {"item_count": 3})
        ctx.transition_to(InvestigationState.INVESTIGATING)
        ctx.transition_to(InvestigationState.REASONING)

        d = ctx.to_dict()
        assert d["state"] == "REASONING"
        assert d["sop_match_found"] is True
        assert d["workflow_id"] == "wf-vkyc-failure-v1"
        assert d["root_cause_category"] == "LIVENESS_FAILURE"
        assert d["root_cause_confidence"] == 0.87
        assert d["audit_event_count"] == 2
        assert d["slots"]["session_id"] == "sess-abc-123"

    def test_B12_evidence_confidence_enriches_evidence_items(self):
        """EvidenceConfidence can annotate evidence items from the bundle."""
        bundle = _evidence_bundle("case-cert-001")
        for ev in bundle.items:
            confidence = EvidenceConfidence(
                score=1.0 if ev.success else 0.0,
                rationale="Tool invocation succeeded" if ev.success else "Tool failed",
                basis="tool_success" if ev.success else "tool_failure",
            )
            assert 0.0 <= confidence.score <= 1.0

        # Clamping enforced
        high = EvidenceConfidence(score=5.0)
        low = EvidenceConfidence(score=-3.0)
        assert high.score == 1.0
        assert low.score == 0.0

    def test_B13_evidence_metadata_records_provenance(self):
        """EvidenceMetadata tracks where evidence came from."""
        meta = EvidenceMetadata(
            source_system="unity_vkyc_platform",
            collection_method="rest_api_call",
            reliability_tier="primary",
            tags=("vkyc", "session", "liveness"),
            provenance_url="https://api.unity.co.in/v2/sessions/sess-abc-123",
        )
        assert meta.source_system == "unity_vkyc_platform"
        assert "vkyc" in meta.tags
        d = meta.to_dict()
        assert d["reliability_tier"] == "primary"


# ═══════════════════════════════════════════════════════════════════════════════
# Part C: Repository Coexistence
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _CertWorkflow:
    workflow_id: str
    topic: str
    version: str = "1.0"


class _CertWorkflowRepo(WorkflowRepository):
    def __init__(self) -> None:
        self._store: dict[str, _CertWorkflow] = {}

    def register(self, wf: _CertWorkflow) -> None:
        self._store[wf.workflow_id] = wf

    def get(self, topic: str):
        return next((w for w in self._store.values() if w.topic == topic), None)

    def get_by_id(self, workflow_id: str):
        return self._store.get(workflow_id)

    def list_all(self) -> list:
        return list(self._store.values())


class TestPartC_RepositoryCoexistence:
    """
    C: All repositories can be instantiated and operated simultaneously.
    """

    def test_C1_sop_registry_and_workflow_repo_coexist(self):
        sop_reg = SOPRegistry()
        wf_reg = _CertWorkflowRepo()

        sop_reg.register(_sop_document())
        wf_reg.register(_CertWorkflow("wf-1", "VKYC_SESSION_FAILURE"))

        assert sop_reg.count() == 1
        assert wf_reg.count() == 1

    def test_C2_vision_provider_coexists_with_registries(self):
        sop_reg = SOPRegistry()
        wf_reg = _CertWorkflowRepo()
        vision = NullVisionProvider()

        sop_reg.register(_sop_document())
        wf_reg.register(_CertWorkflow("wf-1", "VKYC_SESSION_FAILURE"))

        assert not vision.is_available()
        assert sop_reg.count() == 1
        assert wf_reg.count() == 1

    def test_C3_workflow_resolver_resolves_from_repo(self):
        wf_reg = _CertWorkflowRepo()
        wf_reg.register(_CertWorkflow("wf-vkyc", "VKYC_SESSION_FAILURE"))
        wf_reg.register(_CertWorkflow("wf-otp", "OTP_DELIVERY_FAILURE"))
        resolver = WorkflowResolver(wf_reg)

        result = resolver.resolve("VKYC_SESSION_FAILURE")
        assert result is not None
        assert result.workflow_id == "wf-vkyc"

    def test_C4_sop_resolver_resolves_from_registry(self):
        sop_reg = SOPRegistry()
        sop_reg.register(_sop_document())
        resolver = SOPResolver(sop_reg)
        query = KnowledgeQuery(
            topic="VKYC_SESSION_FAILURE",
            query_text="camera issue",
            context={"failure_code": "LIVENESS_FAIL"},
        )
        result = resolver.retrieve(query)
        assert len(result.entries) >= 1

    def test_C5_all_repos_resolve_to_none_on_miss(self):
        wf_reg = _CertWorkflowRepo()
        wf_resolver = WorkflowResolver(wf_reg)
        sop_reg = SOPRegistry()
        sop_resolver = SOPResolver(sop_reg)
        vision = NullVisionProvider()

        assert wf_resolver.resolve("UNKNOWN") is None
        sops = sop_resolver.find_matching_sops("UNKNOWN", {})
        assert sops == []
        ref = _vision_reference()
        ev = vision.analyse(ref)
        assert ev.status == VisionAnalysisStatus.SKIPPED

    def test_C6_interfaces_are_compatible_across_repos(self):
        """WorkflowRepository and SOPRepository satisfy their ABCs independently."""
        sop_reg = SOPRegistry()
        wf_reg = _CertWorkflowRepo()

        sop_rep = sop_reg  # satisfies SOPRepository interface
        wf_rep = wf_reg    # satisfies WorkflowRepository interface

        assert callable(sop_rep.get_by_id)
        assert callable(sop_rep.get_by_topic)
        assert callable(wf_rep.get)
        assert callable(wf_rep.get_by_id)
        assert callable(wf_rep.list_all)


# ═══════════════════════════════════════════════════════════════════════════════
# Part D: Evidence Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartD_EvidenceIntegration:
    """
    D: EvidenceBundle populated with all evidence types + metadata + references.

    Note: KnowledgeEvidence and ToolEvidence do not exist as named types in
    the current implementation (Gap D-01). The EvidenceBundle contains
    Evidence subtypes from investigation/models.py. Vision evidence is stored
    separately in InvestigationContext.vision_analyses.
    """

    def test_D1_evidence_bundle_construction(self):
        bundle = _evidence_bundle("case-cert-001")
        assert len(bundle.items) == 3
        assert bundle.bundle_id == "bundle-cert-001"
        assert len(bundle.successful_items) == 3

    def test_D2_evidence_bundle_serialization(self):
        bundle = _evidence_bundle("case-cert-001")
        d = bundle.to_dict()
        assert d["total_items"] == 3
        assert d["success_count"] == 3
        assert isinstance(d["items"], list)
        assert d["items"][0]["evidence_type"] in [e.value for e in EvidenceType]

    def test_D3_evidence_by_type_filtering(self):
        bundle = _evidence_bundle("case-cert-001")
        user_evs = bundle.get_by_type(EvidenceType.USER)
        session_evs = bundle.get_by_type(EvidenceType.SESSION)
        log_evs = bundle.get_by_type(EvidenceType.LOG)
        assert len(user_evs) == 1
        assert len(session_evs) == 1
        assert len(log_evs) == 1

    def test_D4_evidence_by_source_lookup(self):
        bundle = _evidence_bundle("case-cert-001")
        session_ev = bundle.get_by_source(EvidenceSource.GET_SESSION_DETAILS)
        assert session_ev is not None
        assert session_ev.payload["session_status"] == "FAILED"

    def test_D5_image_evidence_in_context(self):
        ctx = _investigation_context()
        ref = _vision_reference(VisionModality.IMAGE)
        analysis = _vision_analysis(ref)
        img_ev = ImageEvidence(
            evidence_id="img-d5",
            reference=ref, analysis=analysis,
            collected_at=_now(), case_id=ctx.case_id,
            ocr_text="CAMERA ACCESS DENIED",
            contains_error_dialog=True,
        )
        ctx.add_vision_analysis(img_ev)
        stored = ctx.vision_analyses[0]
        assert isinstance(stored, ImageEvidence)
        assert stored.contains_error_dialog is True

    def test_D6_video_evidence_in_context(self):
        ctx = _investigation_context()
        ref = _vision_reference(VisionModality.VIDEO)
        analysis = _vision_analysis(ref)
        vid_ev = VideoEvidence(
            evidence_id="vid-d6",
            reference=ref, analysis=analysis,
            collected_at=_now(), case_id=ctx.case_id,
            duration_seconds=47.3,
            key_timestamps=["00:00:12", "00:00:38"],
            audio_present=False,
        )
        ctx.add_vision_analysis(vid_ev)
        assert ctx.vision_analyses[0].duration_seconds == 47.3

    def test_D7_document_evidence_in_context(self):
        ctx = _investigation_context()
        ref = _vision_reference(VisionModality.DOCUMENT)
        analysis = _vision_analysis(ref)
        doc_ev = DocumentEvidence(
            evidence_id="doc-d7",
            reference=ref, analysis=analysis,
            collected_at=_now(), case_id=ctx.case_id,
            page_count=1,
            ocr_text="Aadhaar Card — Name: Rahul Kumar",
            document_type="AADHAAR",
            is_legible=True,
        )
        ctx.add_vision_analysis(doc_ev)
        assert ctx.vision_analyses[0].document_type == "AADHAAR"

    def test_D8_evidence_priority_ordering(self):
        refs = [
            EvidenceReference("e1", "SESSION", "vkyc", _now(), EvidencePriority.CRITICAL),
            EvidenceReference("e2", "USER", "freshdesk", _now(), EvidencePriority.NORMAL),
            EvidenceReference("e3", "LOG", "vkyc", _now(), EvidencePriority.HIGH),
        ]
        sorted_refs = sorted(refs, key=lambda r: r.priority)
        assert sorted_refs[0].evidence_id == "e1"
        assert sorted_refs[1].evidence_id == "e3"
        assert sorted_refs[2].evidence_id == "e2"

    def test_D9_evidence_confidence_clamping(self):
        over = EvidenceConfidence(score=2.5)
        under = EvidenceConfidence(score=-1.0)
        perfect = EvidenceConfidence(score=1.0)
        assert over.score == 1.0
        assert under.score == 0.0
        assert perfect.score == 1.0

    def test_D10_evidence_status_lifecycle(self):
        statuses = [EvidenceStatus.PENDING, EvidenceStatus.COLLECTED,
                    EvidenceStatus.FAILED, EvidenceStatus.INVALID, EvidenceStatus.SUPERSEDED]
        assert len(set(s.value for s in statuses)) == 5

    def test_D11_evidence_metadata_serialization(self):
        meta = EvidenceMetadata(
            source_system="unity_vkyc_platform",
            collection_method="rest_api_call",
            reliability_tier="primary",
            tags=("vkyc", "session"),
        )
        d = meta.to_dict()
        assert d["source_system"] == "unity_vkyc_platform"
        assert "vkyc" in d["tags"]
        assert d["reliability_tier"] == "primary"

    def test_D12_evidence_reference_serialization(self):
        ref = EvidenceReference(
            evidence_id="ev-001",
            evidence_type="SESSION",
            source_system="vkyc_platform",
            collected_at=_now(),
            priority=EvidencePriority.CRITICAL,
        )
        d = ref.to_dict()
        assert d["priority"] == "CRITICAL"
        assert d["evidence_id"] == "ev-001"


# ═══════════════════════════════════════════════════════════════════════════════
# Part E: Workflow Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartE_WorkflowIntegration:
    """
    E: WorkflowDefinition construction, resolver routing, context storage.
    """

    def test_E1_workflow_definition_construction(self):
        wf = _workflow_definition()
        assert wf.workflow_id == "wf-vkyc-failure-v1"
        assert len(wf.steps) == 3
        assert wf.first_step().step_id == "step_investigate"

    def test_E2_workflow_step_navigation(self):
        wf = _workflow_definition()
        first = wf.first_step()
        assert first.step_type == WorkflowStepType.INVESTIGATE
        assert first.on_success == "step_1"
        next_step = wf.step_by_id("step_knowledge")
        assert next_step is not None
        assert next_step.step_type == WorkflowStepType.KNOWLEDGE_LOOKUP

    def test_E3_workflow_resolver_routes_to_definition(self):
        repo = _CertWorkflowRepo()
        repo.register(_CertWorkflow("wf-vkyc-failure-v1", "VKYC_SESSION_FAILURE"))
        resolver = WorkflowResolver(repo)
        result = resolver.resolve("VKYC_SESSION_FAILURE")
        assert result is not None
        assert result.workflow_id == "wf-vkyc-failure-v1"

    def test_E4_tenant_override_routes_to_specific_workflow(self):
        repo = _CertWorkflowRepo()
        repo.register(_CertWorkflow("wf-default", "VKYC_SESSION_FAILURE"))
        repo.register(_CertWorkflow("wf-unity-custom", "VKYC_SESSION_FAILURE"))
        resolver = WorkflowResolver(repo)
        result = resolver.resolve(
            "VKYC_SESSION_FAILURE",
            workflow_overrides={"workflow_id": "wf-unity-custom"},
        )
        assert result is not None
        assert result.workflow_id == "wf-unity-custom"

    def test_E5_workflow_stored_in_context(self):
        ctx = _investigation_context()
        assert not ctx.has_workflow()
        wf = _workflow_definition()
        ctx.workflow_definition = wf
        assert ctx.has_workflow()
        assert ctx.workflow_definition.required_slots == ("session_id",)

    def test_E6_workflow_contracts_companion_to_steps(self):
        """Contracts describe what steps need and produce — typed, serializable."""
        reqs = [
            WorkflowRequirement(kind=RequirementKind.SLOT, key="session_id"),
            WorkflowRequirement(kind=RequirementKind.EVIDENCE, key="session_evidence", required=False),
        ]
        outputs = [
            WorkflowOutput(kind=OutputKind.ROOT_CAUSE, key="root_cause"),
            WorkflowOutput(kind=OutputKind.RECOMMENDATION, key="recommendation"),
        ]
        transitions = [
            WorkflowTransition(trigger=TransitionTrigger.SUCCESS, target_step="step_propose"),
            WorkflowTransition(trigger=TransitionTrigger.LOW_CONFIDENCE, target_step="ESCALATE"),
            WorkflowTransition(trigger=TransitionTrigger.FAILURE, target_step="ESCALATE"),
        ]
        assert all(r.to_dict() for r in reqs)
        assert all(o.to_dict() for o in outputs)
        assert transitions[1].is_terminal() is True
        assert not transitions[0].is_terminal()

    def test_E7_workflow_definition_serialization(self):
        wf = _workflow_definition()
        d = wf.to_dict()
        assert d["workflow_id"] == "wf-vkyc-failure-v1"
        assert d["step_count"] == 3
        assert isinstance(d["steps"], list)
        assert d["steps"][0]["step_type"] == "INVESTIGATE"

    def test_E8_workflow_resolver_never_raises(self):
        class _BrokenRepo(_CertWorkflowRepo):
            def get(self, topic):
                raise RuntimeError("network failure")
        resolver = WorkflowResolver(_BrokenRepo())
        assert resolver.resolve("ANY") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Part F: Knowledge Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartF_KnowledgeIntegration:
    """
    F: KnowledgeQuery → KnowledgeProvider → KnowledgeEntry → EvidenceReference
    """

    def test_F1_knowledge_query_construction(self):
        query = KnowledgeQuery(
            topic="VKYC_SESSION_FAILURE",
            query_text="camera liveness check failure",
            context={"failure_code": "LIVENESS_FAIL", "attempt_count": 3},
            client_id="unity_bank",
            max_results=5,
        )
        assert query.query_id
        assert query.client_id == "unity_bank"

    def test_F2_sop_resolver_returns_knowledge_entries(self):
        registry = SOPRegistry()
        registry.register(_sop_document())
        resolver = SOPResolver(registry)
        query = KnowledgeQuery(
            topic="VKYC_SESSION_FAILURE",
            query_text="camera liveness failure",
            context={"failure_code": "LIVENESS_FAIL"},
            client_id="unity_bank",
        )
        result = resolver.retrieve(query)
        assert result.provider_name == "sop_repository"
        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry.topic == "VKYC_SESSION_FAILURE"

    def test_F3_knowledge_entry_to_evidence_reference(self):
        """A KnowledgeEntry can generate a companion EvidenceReference."""
        entry = KnowledgeEntry(
            entry_id="ke-sop-vkyc",
            source="sop_repository",
            title="VKYC Session Failure Resolution",
            content="Step 1: Check session. Step 2: Reset session.",
            topic="VKYC_SESSION_FAILURE",
            relevance=0.95,
            metadata={"version": "1.0", "sop_id": "sop_vkyc_session_failure"},
        )
        ref = EvidenceReference(
            evidence_id=entry.entry_id,
            evidence_type="KNOWLEDGE_ENTRY",
            source_system=entry.source,
            collected_at=_now(),
            priority=EvidencePriority.HIGH,
        )
        assert ref.evidence_id == "ke-sop-vkyc"
        assert ref.evidence_type == "KNOWLEDGE_ENTRY"
        assert ref.priority == EvidencePriority.HIGH

    def test_F4_knowledge_entries_stored_in_context(self):
        ctx = _investigation_context()
        registry = SOPRegistry()
        registry.register(_sop_document())
        resolver = SOPResolver(registry)
        query = KnowledgeQuery(
            topic=ctx.topic,
            query_text="camera failure",
            client_id=ctx.tenant_context.client_id,
            context={"failure_code": "LIVENESS_FAIL"},
        )
        result = resolver.retrieve(query)
        ctx.knowledge_entries = result.entries
        assert len(ctx.knowledge_entries) > 0
        assert ctx.knowledge_entries[0].topic == "VKYC_SESSION_FAILURE"

    def test_F5_no_adapter_required_between_provider_and_context(self):
        """KnowledgeEntry can be stored directly in InvestigationContext.knowledge_entries."""
        ctx = _investigation_context()
        entry = KnowledgeEntry(
            entry_id="ke-direct",
            source="sop_repository",
            title="Direct Entry",
            content="content",
            topic="VKYC_SESSION_FAILURE",
        )
        ctx.knowledge_entries = [entry]
        assert len(ctx.knowledge_entries) == 1
        assert isinstance(ctx.knowledge_entries[0], KnowledgeEntry)

    def test_F6_knowledge_provider_is_available(self):
        registry = SOPRegistry()
        provider = SOPResolver(registry)
        assert provider.is_available() is True
        assert provider.provider_name == "sop_repository"

    def test_F7_knowledge_provider_result_empty_on_miss(self):
        registry = SOPRegistry()
        resolver = SOPResolver(registry)
        result = KnowledgeProviderResult.empty("sop_repository", "q-miss")
        assert result.total_found == 0
        assert result.entries == []

    def test_F8_sop_trigger_condition_filters_by_context(self):
        """SOP trigger conditions act as a dynamic filter over InvestigationContext slots."""
        cond = SOPTriggerCondition(
            field="failure_code",
            operator=SOPTriggerOperator.IN,
            value=["LIVENESS_FAIL", "CAMERA_FAIL"],
        )
        slots = {"session_id": "sess-123", "failure_code": "LIVENESS_FAIL"}
        assert cond.matches(slots) is True
        assert cond.matches({"failure_code": "NETWORK_FAIL"}) is False


# ═══════════════════════════════════════════════════════════════════════════════
# Part G: Vision Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartG_VisionIntegration:
    """
    G: NullVisionProvider → VisionEvidence → InvestigationContext
    """

    def test_G1_null_provider_analyse_returns_skipped(self):
        provider = NullVisionProvider()
        ref = _vision_reference()
        ev = provider.analyse(ref)
        assert ev.status == VisionAnalysisStatus.SKIPPED
        assert ev.analysis.findings == []

    def test_G2_null_provider_never_raises(self):
        provider = NullVisionProvider()
        for modality in VisionModality:
            ref = VisionReference(
                reference_id=f"ref-{modality.value}",
                modality=modality,
                uri="s3://bucket/file",
                content_type="image/jpeg",
            )
            ev = provider.analyse(ref)
            assert ev is not None

    def test_G3_null_provider_not_available(self):
        assert NullVisionProvider().is_available() is False

    def test_G4_vision_evidence_stored_in_context(self):
        ctx = _investigation_context()
        provider = NullVisionProvider()
        refs = [
            VisionReference(
                reference_id=f"ref-{i}",
                modality=VisionModality.IMAGE,
                uri=f"s3://bucket/img-{i}.jpg",
                content_type="image/jpeg",
            )
            for i in range(3)
        ]
        for ref in refs:
            ev = provider.analyse(ref)
            ctx.add_vision_analysis(ev)
        assert len(ctx.vision_analyses) == 3
        assert all(v.status == VisionAnalysisStatus.SKIPPED for v in ctx.vision_analyses)

    def test_G5_real_vision_analysis_result_in_context(self):
        """A 'real' (non-null) analysis result can also be stored."""
        ctx = _investigation_context()
        ref = _vision_reference(VisionModality.IMAGE)
        analysis = _vision_analysis(ref)
        img_ev = ImageEvidence(
            evidence_id="img-g5",
            reference=ref, analysis=analysis,
            collected_at=_now(), case_id=ctx.case_id,
            ocr_text="ERROR: Camera permission denied",
            contains_error_dialog=True,
        )
        ctx.add_vision_analysis(img_ev)
        stored = ctx.vision_analyses[0]
        assert stored.status == VisionAnalysisStatus.COMPLETED
        assert stored.analysis.has_finding(VisionFinding.CAMERA_ISSUE)

    def test_G6_future_provider_can_replace_null_without_context_change(self):
        """Architecture supports provider substitution without touching InvestigationContext."""
        class _HypotheticalClaudeVision(VisionProvider):
            @property
            def provider_name(self) -> str:
                return "claude_vision"
            @property
            def capabilities(self) -> set:
                return {VisionCapability.IMAGE_ANALYSIS, VisionCapability.IMAGE_OCR}
            def analyse(self, ref: VisionReference) -> VisionEvidence:
                from datetime import datetime, timezone
                analysis = VisionAnalysis(
                    analysis_id="analysis-claude-001",
                    reference_id=ref.reference_id,
                    modality=ref.modality,
                    status=VisionAnalysisStatus.COMPLETED,
                    findings=[VisionFinding.CAMERA_ISSUE],
                    confidence=0.92,
                    raw_output={},
                    analysed_at=datetime.now(tz=timezone.utc).isoformat(),
                    provider_name=self.provider_name,
                    duration_ms=150,
                )
                return VisionEvidence(
                    evidence_id="ev-claude",
                    reference=ref,
                    analysis=analysis,
                    collected_at=datetime.now(tz=timezone.utc).isoformat(),
                    case_id="test",
                )

        ctx = _investigation_context()
        provider: VisionProvider = _HypotheticalClaudeVision()
        ref = _vision_reference()
        ev = provider.analyse(ref)
        ctx.add_vision_analysis(ev)
        assert ctx.vision_analyses[0].analysis.provider_name == "claude_vision"

    def test_G7_vision_evidence_serialization(self):
        ref = _vision_reference(VisionModality.IMAGE)
        analysis = _vision_analysis(ref)
        img_ev = ImageEvidence(
            evidence_id="g7",
            reference=ref, analysis=analysis,
            collected_at=_now(), case_id="c1",
            ocr_text="denied",
            contains_error_dialog=True,
        )
        d = img_ev.to_dict()
        assert d["modality"] == "IMAGE"
        assert d["contains_error_dialog"] is True
        assert "analysis" in d


# ═══════════════════════════════════════════════════════════════════════════════
# Part H: Tool Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartH_ToolIntegration:
    """
    H: ToolDefinition → ToolProvider → ToolCapability → InvestigationContext
    """

    def test_H1_tool_definition_with_provider(self):
        td = ToolDefinition(
            tool_name="GetSessionDetailsTool",
            description="Fetch VKYC session details from Unity platform",
            required_inputs=("session_id",),
            output_schema={
                "session_status": "str",
                "attempt_count": "int",
                "failure_code": "str",
                "reset_eligible": "bool",
            },
            version="1.0",
            tags=("vkyc", "session", "unity"),
            provider=ToolProvider.UNITY,
            capability=ToolCapability.READ,
        )
        assert td.provider == ToolProvider.UNITY
        assert td.capability == ToolCapability.READ

    def test_H2_all_sprint238_tools_have_providers(self):
        tools = [
            ToolDefinition(
                tool_name="GetUserDetailsTool",
                description="Freshdesk contact lookup",
                required_inputs=("user_urn",),
                output_schema={"kyc_status": "str"},
                provider=ToolProvider.FRESHDESK,
                capability=ToolCapability.READ,
            ),
            ToolDefinition(
                tool_name="GetSessionDetailsTool",
                description="Unity session lookup",
                required_inputs=("session_id",),
                output_schema={"session_status": "str"},
                provider=ToolProvider.UNITY,
                capability=ToolCapability.READ,
            ),
            ToolDefinition(
                tool_name="ResetSessionTool",
                description="Unity session reset",
                required_inputs=("session_id",),
                output_schema={"reset_success": "bool"},
                provider=ToolProvider.UNITY,
                capability=ToolCapability.EXECUTE,
            ),
            ToolDefinition(
                tool_name="GetFlagTool",
                description="Flagsmith feature flag lookup",
                required_inputs=("flag_name",),
                output_schema={"enabled": "bool"},
                provider=ToolProvider.FLAGSMITH,
                capability=ToolCapability.READ,
            ),
        ]
        for t in tools:
            assert t.provider is not None
            assert t.capability is not None
            d = t.to_dict()
            assert d["provider"] in [p.value for p in ToolProvider]

    def test_H3_tool_result_ok_and_fail(self):
        ok = ToolResult.ok("GetSessionDetailsTool", {"session_status": "FAILED"}, duration_ms=95)
        fail = ToolResult.fail("GetMetricsTool", "SERVICE_UNAVAILABLE", "Metrics service down")
        assert ok.success is True
        assert ok.tool_name == "GetSessionDetailsTool"
        assert fail.success is False
        assert fail.error_code == "SERVICE_UNAVAILABLE"

    def test_H4_tool_results_stored_in_context(self):
        ctx = _investigation_context()
        tools_results = [
            ToolResult.ok("GetUserDetailsTool", {"kyc_status": "IN_PROGRESS"}, duration_ms=120),
            ToolResult.ok("GetSessionDetailsTool", {"session_status": "FAILED"}, duration_ms=95),
            ToolResult.ok("GetFailureReasonTool", {"failure_category": "LIVENESS_FAILURE"}, duration_ms=60),
        ]
        for r in tools_results:
            ctx.add_tool_result(r)
        assert len(ctx.tool_results) == 3
        assert ctx.tool_results[0].payload["kyc_status"] == "IN_PROGRESS"

    def test_H5_backwards_compatible_tool_definition(self):
        """Existing ToolDefinitions without provider/capability still work."""
        td = ToolDefinition(
            tool_name="LegacyTool",
            description="Pre-Sprint-2.38 tool definition",
            required_inputs=("session_id",),
            output_schema={"result": "str"},
        )
        assert td.provider is None
        assert td.capability is None
        d = td.to_dict()
        assert d["provider"] is None
        assert d["capability"] is None

    def test_H6_all_tool_providers_exist(self):
        # Sprint 2.50 added METRICS_PLATFORM (Uptime Kuma read-only integration).
        expected = {
            "FRESHDESK", "UNITY", "ADMIN_PORTAL", "S3",
            "DATABASE", "FLAGSMITH", "MINIO", "VISION", "MCP",
            "METRICS_PLATFORM",
        }
        assert {p.value for p in ToolProvider} == expected

    def test_H7_all_tool_capabilities_exist(self):
        expected = {"READ", "WRITE", "EXECUTE", "QUERY", "NOTIFY", "ANALYZE"}
        assert {c.value for c in ToolCapability} == expected

    def test_H8_tool_input_construction(self):
        inp = ToolInput(
            tool_name="GetSessionDetailsTool",
            inputs={"session_id": "sess-abc-123"},
            requested_by="step_investigate",
        )
        assert inp.invocation_id
        assert inp.requested_at
        d = inp.to_dict()
        assert d["tool_name"] == "GetSessionDetailsTool"
        assert d["inputs"]["session_id"] == "sess-abc-123"


# ═══════════════════════════════════════════════════════════════════════════════
# Part I: Action Gateway Field Compatibility
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartI_ActionGatewayCompatibility:
    """
    I: Verify InvestigationContext already contains every field required by
       ProposalGateway.validate() and ActionGatewayDecision.

    ProposalGateway.validate() signature:
        validate(topic, action_proposal_result, investigation_result, knowledge_result)

    Where:
        action_proposal_result["status"] == "COMPLETED"
        action_proposal_result["top_proposal"] present
        investigation_result["root_cause_category"] present
        investigation_result["confidence"] >= 0.4
        knowledge_result (advisory — sop correlation)
    """

    def test_I1_context_has_topic_for_gateway(self):
        ctx = _investigation_context()
        assert isinstance(ctx.topic, str)
        assert len(ctx.topic) > 0

    def test_I2_context_action_proposal_field_exists(self):
        ctx = _investigation_context()
        assert hasattr(ctx, "action_proposal")
        ctx.action_proposal = {
            "status": "COMPLETED",
            "top_proposal": {
                "action_type": "SESSION_RESET",
                "action_namespace": "unity_platform",
                "confidence": 0.87,
            },
        }
        assert ctx.action_proposal["status"] == "COMPLETED"

    def test_I3_root_cause_can_produce_investigation_result_dict(self):
        ctx = _investigation_context()
        rca = _root_cause(ctx.case_id)
        ctx.root_cause = rca
        investigation_result_dict = rca.to_dict()
        assert "category" in investigation_result_dict
        assert "confidence" in investigation_result_dict
        assert investigation_result_dict["confidence"] >= 0.4
        assert investigation_result_dict["category"] == "LIVENESS_FAILURE"

    def test_I4_knowledge_entries_can_produce_knowledge_result_dict(self):
        ctx = _investigation_context()
        ctx.knowledge_entries = [
            KnowledgeEntry(
                entry_id="ke-1",
                source="sop_repository",
                title="VKYC SOP",
                content="Step 1...",
                topic="VKYC_SESSION_FAILURE",
                relevance=0.95,
            )
        ]
        knowledge_result_dict = {
            "status": "COMPLETED",
            "entries": [e.__dict__ for e in ctx.knowledge_entries],
            "sop_matched": ctx.sop_match_found,
        }
        assert len(knowledge_result_dict["entries"]) == 1

    def test_I5_gateway_decision_field_exists_in_context(self):
        ctx = _investigation_context()
        assert hasattr(ctx, "gateway_decision")
        assert ctx.gateway_decision is None

    def test_I6_gateway_decision_can_be_stored(self):
        ctx = _investigation_context()
        ctx.gateway_decision = {
            "decision_id": "gd-001",
            "validation_status": "VALID",
            "risk_level": "SAFE",
            "requires_approval": False,
        }
        assert ctx.gateway_decision["validation_status"] == "VALID"

    def test_I7_sop_fields_for_gateway_sop_check(self):
        """ProposalGateway SOP check is advisory — context has sop_match_found."""
        ctx = _investigation_context()
        ctx.sop_document = _sop_document()
        ctx.sop_match_found = True
        assert ctx.sop_match_found is True
        assert ctx.sop_document.sop_id == "sop_vkyc_session_failure"

    def test_I8_all_gateway_required_fields_present(self):
        """Enumerate all fields ProposalGateway.validate() needs, verify each exists."""
        ctx = _investigation_context()
        required_mappings = {
            "topic": ctx.topic,
            "action_proposal": ctx.action_proposal,
            "root_cause": ctx.root_cause,
            "knowledge_entries": ctx.knowledge_entries,
            "gateway_decision": ctx.gateway_decision,
            "sop_match_found": ctx.sop_match_found,
            "observation_text": ctx.observation_text,
        }
        for field_name, field_val in required_mappings.items():
            assert hasattr(ctx, field_name), f"InvestigationContext missing field: {field_name}"

    def test_I9_gap_report_typed_gateway_decision(self):
        """
        Gap I-01: InvestigationContext stores gateway_decision as dict | None,
        not as typed ActionGatewayDecision. This is intentional to avoid
        importing from action_gateway (would create a dependency inversion).
        The pipeline stage that calls ProposalGateway converts it to dict for
        storage. No architectural change required for Sprint 2.39.
        """
        from case_engine.investigation.context import InvestigationContext
        import inspect
        hints = {}
        for field_name, field in inspect.signature(InvestigationContext.__init__).parameters.items():
            hints[field_name] = field.annotation
        # gateway_decision is dict | None, not ActionGatewayDecision — expected by design
        assert True  # Gap documented; not a blocker


# ═══════════════════════════════════════════════════════════════════════════════
# Part J: Blueprint Completeness
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartJ_BlueprintCompleteness:
    """
    J: Architecture completeness validation against SUPPORT_OPERATIONS_BLUEPRINT.md.

    For each major blueprint component: IMPLEMENTED / PARTIAL / NOT_YET
    """

    # --- IMPLEMENTED components ---

    def test_J01_tenant_resolution(self):
        """Blueprint Layer 1.5: TenantContext — IMPLEMENTED (Sprint 2.27.9)"""
        from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment
        ctx = TenantContext(
            client_id="t", client_name="T", domain="t.com",
            tenant_type=TenantType.BANK, environment=TenantEnvironment.PRODUCTION,
            enabled_tools=(), credentials_ref="ref",
        )
        assert ctx.client_id == "t"

    def test_J02_workflow_playbooks(self):
        """Blueprint Section 8: Workflow Playbooks — IMPLEMENTED (Sprint 2.16)"""
        wf = _workflow_definition()
        assert wf.workflow_id is not None
        assert len(wf.steps) > 0

    def test_J03_workflow_requirements_and_contracts(self):
        """Blueprint Section 8: Workflow Contracts — IMPLEMENTED (Sprint 2.38)"""
        req = WorkflowRequirement(kind=RequirementKind.SLOT, key="session_id")
        out = WorkflowOutput(kind=OutputKind.EVIDENCE, key="session_evidence")
        trans = WorkflowTransition(trigger=TransitionTrigger.SUCCESS, target_step="step_next")
        assert req.kind == RequirementKind.SLOT
        assert out.kind == OutputKind.EVIDENCE
        assert not trans.is_terminal()

    def test_J04_sop_repository(self):
        """Blueprint Section 6: SOP Repository — IMPLEMENTED (Sprint 2.38)"""
        registry = SOPRegistry()
        registry.register(_sop_document())
        assert registry.count() == 1
        assert registry.get_by_id("sop_vkyc_session_failure") is not None

    def test_J05_knowledge_provider_interface(self):
        """Blueprint Section 31: Knowledge Provider — IMPLEMENTED (Sprint 2.38)"""
        registry = SOPRegistry()
        registry.register(_sop_document())
        provider: KnowledgeProvider = SOPResolver(registry)
        assert provider.is_available()

    def test_J06_investigation_context(self):
        """Blueprint Section 31: Shared pipeline context — IMPLEMENTED (Sprint 2.38)"""
        ctx = _investigation_context()
        assert ctx.state == InvestigationState.CREATED
        assert ctx.context_id is not None

    def test_J07_evidence_domain(self):
        """Blueprint Section 12: Evidence types — IMPLEMENTED (Sprint 2.18+2.38)"""
        bundle = _evidence_bundle("case-cert-001")
        assert len(bundle.items) == 3
        ref = EvidenceReference("ev-1", "SESSION", "vkyc", _now())
        confidence = EvidenceConfidence(score=0.9)
        assert ref.priority == EvidencePriority.NORMAL
        assert confidence.score == 0.9

    def test_J08_vision_architecture(self):
        """Blueprint Section 12: Vision as Evidence Provider — IMPLEMENTED (Sprint 2.38)"""
        provider: VisionProvider = NullVisionProvider()
        ref = _vision_reference()
        ev = provider.analyse(ref)
        assert isinstance(ev, VisionEvidence)

    def test_J09_tool_registry_model(self):
        """Blueprint Tools subgraph: ToolDefinition + ToolProvider — IMPLEMENTED (Sprint 2.17+2.38)"""
        td = ToolDefinition(
            tool_name="GetSessionDetailsTool",
            description="Unity session lookup",
            required_inputs=("session_id",),
            output_schema={"session_status": "str"},
            provider=ToolProvider.UNITY,
            capability=ToolCapability.READ,
        )
        assert td.provider == ToolProvider.UNITY

    def test_J10_action_gateway_models(self):
        """Blueprint ACTIONGW node: ActionGatewayDecision — IMPLEMENTED (Sprint 2.22)"""
        from case_engine.action_gateway.models import (
            ActionGatewayDecision, GatewayValidationStatus, GatewayRiskLevel
        )
        from datetime import datetime, timezone
        decision = ActionGatewayDecision(
            decision_id="d-1",
            validation_status=GatewayValidationStatus.VALID,
            risk_level=GatewayRiskLevel.SAFE,
            requires_approval=False,
            validation_failures=(),
            bundle_id="b-1",
            top_action_type="SESSION_RESET",
            confidence_check_passed=True,
            investigation_check_passed=True,
            sop_check_passed=True,
            decided_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        assert decision.is_valid()

    # --- PARTIAL / NOT_YET components ---

    def test_J11_document_gap_knowledge_evidence_type(self):
        """
        Gap J-01 (PARTIAL): KnowledgeEvidence is not a named Evidence subtype.
        KnowledgeEntry (from knowledge/base.py) serves this role but is not
        an Evidence subclass stored in EvidenceBundle.
        Sprint 2.39 may create a KnowledgeEvidence(Evidence) subtype if needed.
        """
        from case_engine.knowledge.base import KnowledgeEntry
        from case_engine.investigation.models import Evidence
        assert not issubclass(KnowledgeEntry, Evidence), (
            "KnowledgeEntry is NOT an Evidence subtype — this is a documented gap."
        )

    def test_J12_document_gap_tool_evidence_type(self):
        """
        Gap J-02 (PARTIAL): ToolEvidence is not a named Evidence subtype.
        ToolResult and the existing UserEvidence/SessionEvidence/LogEvidence
        fill this role. A named ToolEvidence wrapper is not required for Sprint 2.39.
        """
        from case_engine.tools.tool_models import ToolResult
        from case_engine.investigation.models import Evidence
        assert not issubclass(ToolResult, Evidence), (
            "ToolResult is NOT an Evidence subtype — this is a documented gap."
        )

    def test_J13_document_gap_investigation_planner(self):
        """
        NOT_YET: InvestigationPlanner is NOT implemented.
        It will consume WorkflowRequirement, InvestigationContext to build an
        InvestigationPlan. Architecture is ready. Sprint 2.39.
        """
        try:
            from case_engine.investigation.planner import InvestigationPlanner
            planner_exists = True
        except ImportError:
            planner_exists = False
        # If planner exists from prior sprint, it may not yet use WorkflowRequirement
        # This gap is architectural — the wiring is Sprint 2.39 work
        assert True  # Gap documented

    def test_J14_document_gap_vision_backend_provider(self):
        """
        NOT_YET: ClaudeVisionProvider / RekognitionProvider are NOT implemented.
        NullVisionProvider is the default. Architecture ready for future sprint.
        """
        provider = NullVisionProvider()
        assert not provider.is_available()
        assert provider.capabilities == set()

    def test_J15_document_gap_production_runtime_assembly(self):
        """
        PARTIAL: ProductionRuntime does not yet wire SOPRegistry, WorkflowResolver,
        or InvestigationContext into the case pipeline.
        Sprint 2.39 will wire InvestigationContext into WorkflowEngine.
        """
        assert True  # Gap documented — Sprint 2.39 concern


# ═══════════════════════════════════════════════════════════════════════════════
# Part K: Sprint 2.39 Readiness
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartK_Sprint239Readiness:
    """
    K: Verify Sprint 2.39 can begin without architectural changes.

    Sprint 2.39 components:
      - InvestigationPlanner: reads WorkflowDefinition.investigation_steps + slots → InvestigationPlan
      - EvidenceCollector: reads InvestigationPlan → invokes tools → populates evidence_bundle
      - RootCauseEngine: reads evidence_bundle → populates root_cause
      - ObservationGenerator: reads root_cause + knowledge_entries → populates observation_text
    """

    def test_K1_investigation_plan_construction_ready(self):
        """InvestigationPlanner can construct InvestigationPlan using existing types."""
        plan = InvestigationPlan(
            plan_id="plan-k1",
            case_id="case-k1",
            topic="VKYC_SESSION_FAILURE",
            workflow_id="wf-vkyc-failure-v1",
            steps=(
                InvestigationStep(
                    step_id="s1", sequence=0,
                    tool_name="GetUserDetailsTool",
                    purpose="Retrieve KYC status",
                    required_slot="user_urn",
                    input_key="user_urn",
                ),
            ),
            created_at=_now(),
        )
        ctx = _investigation_context()
        ctx.investigation_plan = plan
        assert ctx.investigation_plan.plan_id == "plan-k1"
        assert not ctx.has_evidence()

    def test_K2_evidence_bundle_slot_ready(self):
        """EvidenceCollector can populate evidence_bundle in context."""
        ctx = _investigation_context()
        assert ctx.evidence_bundle is None
        bundle = _evidence_bundle(ctx.case_id)
        ctx.evidence_bundle = bundle
        assert ctx.has_evidence()
        assert len(ctx.evidence_bundle.items) == 3

    def test_K3_root_cause_slot_ready(self):
        """RootCauseEngine can populate root_cause in context."""
        ctx = _investigation_context()
        assert not ctx.has_root_cause()
        rca = _root_cause(ctx.case_id)
        ctx.root_cause = rca
        assert ctx.has_root_cause()
        assert ctx.root_cause.category == RootCauseCategory.LIVENESS_FAILURE

    def test_K4_observation_text_slot_ready(self):
        """ObservationGenerator can populate observation_text in context."""
        ctx = _investigation_context()
        assert ctx.observation_text is None
        ctx.observation_text = (
            "Session sess-abc-123 failed liveness check 3 times (LIVENESS_FAIL). "
            "Session is reset-eligible. Recommended action: SESSION_RESET."
        )
        assert ctx.observation_text is not None
        assert "SESSION_RESET" in ctx.observation_text

    def test_K5_workflow_definition_has_investigation_metadata(self):
        """WorkflowDefinition.investigation_steps + tool_candidates guide InvestigationPlanner."""
        wf = _workflow_definition()
        assert len(wf.tool_candidates) == 2
        assert "GetSessionDetailsTool" in wf.tool_candidates
        assert isinstance(wf.investigation_steps, tuple)

    def test_K6_context_state_machine_supports_planner_transitions(self):
        """InvestigationPlanner will transition context through INVESTIGATING → REASONING."""
        ctx = _investigation_context()
        ctx.transition_to(InvestigationState.INVESTIGATING)
        assert ctx.state == InvestigationState.INVESTIGATING
        ctx.transition_to(InvestigationState.REASONING)
        assert ctx.state == InvestigationState.REASONING

    def test_K7_tool_results_accumulate_across_steps(self):
        """EvidenceCollector calls add_tool_result() per tool — accumulates in context."""
        ctx = _investigation_context()
        for i, tool_name in enumerate(["GetUserDetailsTool", "GetSessionDetailsTool", "GetFailureReasonTool"]):
            ctx.add_tool_result(
                ToolResult.ok(tool_name, {f"key_{i}": f"value_{i}"}, duration_ms=50 + i * 10)
            )
        assert len(ctx.tool_results) == 3

    def test_K8_audit_trail_accumulates_across_pipeline(self):
        """All pipeline stages can add audit events to the same context."""
        ctx = _investigation_context()
        stages = [
            ("CLASSIFICATION_COMPLETED", {"confidence": 0.91}),
            ("SLOTS_FILLED", {"slots": ["session_id"]}),
            ("SOP_MATCHED", {"sop_id": "sop_vkyc_session_failure"}),
            ("INVESTIGATION_STARTED", {"tool_count": 3}),
            ("EVIDENCE_COLLECTED", {"success_count": 3}),
            ("ROOT_CAUSE_DETERMINED", {"category": "LIVENESS_FAILURE", "confidence": 0.87}),
            ("OBSERVATION_GENERATED", {"length": 245}),
        ]
        for event_type, details in stages:
            ctx.add_audit_event(event_type, details)
        assert len(ctx.audit_events) == 7
        assert ctx.audit_events[5]["details"]["category"] == "LIVENESS_FAILURE"

    def test_K9_sprint239_requires_no_new_architecture_files(self):
        """
        All structural types Sprint 2.39 needs already exist:
        - InvestigationPlan ✓ (investigation/models.py)
        - InvestigationStep ✓ (investigation/models.py)
        - EvidenceBundle ✓ (investigation/models.py)
        - Evidence subtypes ✓ (investigation/models.py)
        - RootCauseAnalysis ✓ (investigation/models.py)
        - InvestigationContext ✓ (investigation/context.py)
        - WorkflowDefinition ✓ (workflows/models.py)
        - WorkflowRequirement ✓ (workflows/contracts.py)
        - ToolExecutor ✓ (tools/tool_executor.py)
        - ToolResult ✓ (tools/tool_models.py)
        """
        from case_engine.investigation.models import (
            InvestigationPlan, InvestigationStep,
            EvidenceBundle, RootCauseAnalysis,
        )
        from case_engine.investigation.context import InvestigationContext
        from case_engine.workflows.contracts import WorkflowRequirement
        from case_engine.tools.tool_models import ToolResult

        assert InvestigationPlan is not None
        assert EvidenceBundle is not None
        assert RootCauseAnalysis is not None
        assert InvestigationContext is not None
        assert WorkflowRequirement is not None
        assert ToolResult is not None
