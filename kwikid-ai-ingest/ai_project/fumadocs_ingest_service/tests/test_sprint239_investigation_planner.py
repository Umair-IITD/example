"""
tests/test_sprint239_investigation_planner.py

Sprint 2.39: Investigation Planner (Evidence Planning Engine) — comprehensive tests.

Tests cover:
  A  — planner/models.py enumerations
  B  — EvidenceRequirement
  C  — RetryPolicy
  D  — StepPrecondition and StepOutput
  E  — StepDependency
  F  — PlanningStep
  G  — InvestigationGraph (DAG)
  H  — InvestigationPlan
  I  — PlanningRule ABC + applies_to
  J  — Topic-specific rules (VKYC, OTP, OCR, API, Portal)
  K  — DefaultWorkflowPlanningRule
  L  — MinimalFallbackRule
  M  — InvestigationPlanner (engine.py)
  N  — Backward compatibility (legacy planner still importable)
  O  — Safety contract (immutability, never-raises, no side effects)
  P  — Integration (full plan through InvestigationContext)

100% pure unit tests — no network, no database, no filesystem.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

# ── planner/models.py ──────────────────────────────────────────────────────────
from case_engine.investigation.planner.models import (
    CompletionCondition,
    DependencyType,
    EstimatedComplexity,
    EvidenceKind,
    EvidenceRequirement,
    FailureStrategy,
    FallbackStrategy,
    InvestigationGraph,
    InvestigationPlan,
    InvestigationPriority,
    PlanningStep,
    PreconditionKind,
    RetryPolicy,
    StepDependency,
    StepOutput,
    StepPrecondition,
)

# ── planner/rules.py ───────────────────────────────────────────────────────────
from case_engine.investigation.planner.rules import (
    AgentPortalPlanningRule,
    APICallbackPlanningRule,
    DefaultWorkflowPlanningRule,
    DocumentOCRPlanningRule,
    MinimalFallbackRule,
    OTPDeliveryFailurePlanningRule,
    PlanningRule,
    VKYCSessionFailurePlanningRule,
    get_default_rules,
)

# ── planner/engine.py ──────────────────────────────────────────────────────────
from case_engine.investigation.planner.engine import (
    InvestigationPlanner,
    KnowledgeProvider,
    SOPResolverProtocol,
    WorkflowRepository,
)

# ── planner/__init__.py backward compat ───────────────────────────────────────
from case_engine.investigation.planner import (
    InvestigationPlanner as LegacyInvestigationPlanner,
    _extract_case_id,
)

# ── dependencies ──────────────────────────────────────────────────────────────
from case_engine.investigation.evidence.models import EvidencePriority
from case_engine.tools.tool_models import ToolCapability
from case_engine.investigation.context import InvestigationContext, InvestigationState
from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment
from case_engine.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowStepType


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

def _tenant() -> TenantContext:
    return TenantContext(
        client_id="unity_bank",
        client_name="Unity Bank",
        domain="unitybank.co.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetSessionDetailsTool", "GetUserDetailsTool"),
        credentials_ref="unity_bank_creds",
    )


def _context(topic: str = "VKYC_SESSION_FAILURE", **slot_overrides: Any) -> InvestigationContext:
    ctx = InvestigationContext.create(
        case_id="case-001",
        ticket_id="ticket-001",
        ticket_subject="VKYC session failed",
        ticket_description="User cannot complete VKYC",
        customer_id="cust-001",
        customer_email="user@unitybank.co.in",
        channel="portal",
        tenant_context=_tenant(),
        topic=topic,
        classification_confidence=0.95,
    )
    ctx.slots.update({"session_id": "sess-abc", "phone_number": "+911234567890", **slot_overrides})
    return ctx


def _workflow(
    workflow_id: str = "vkyc_v1",
    topic: str = "VKYC_SESSION_FAILURE",
    required_slots: tuple[str, ...] = ("session_id", "phone_number"),
) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=workflow_id,
        topic=topic,
        version="1.0",
        name="VKYC Session Failure Playbook",
        description="Handles VKYC session failures",
        required_slots=required_slots,
    )


def _evidence_req(
    req_id: str = "req_test",
    kind: EvidenceKind = EvidenceKind.SESSION,
    priority: EvidencePriority = EvidencePriority.HIGH,
    required: bool = True,
) -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id=req_id,
        kind=kind,
        title="Test Evidence",
        description="Test evidence requirement",
        priority=priority,
        required=required,
        expected_fields=("field_a", "field_b"),
        validation_hints=("hint_1",),
        capability_hint=ToolCapability.READ,
    )


def _step(
    step_id: str = "step_01_test",
    order: int = 0,
    parallelizable: bool = False,
    optional: bool = False,
    depends_on: tuple[str, ...] = (),
    failure_strategy: FailureStrategy = FailureStrategy.CONTINUE,
) -> PlanningStep:
    return PlanningStep(
        step_id=step_id,
        order=order,
        title=f"Step {order}",
        description="Test step",
        evidence_required=(_evidence_req(f"req_{step_id}"),),
        evidence_priority=EvidencePriority.HIGH,
        candidate_capabilities=(ToolCapability.READ,),
        preconditions=(),
        outputs=(),
        retry_policy=RetryPolicy(),
        failure_strategy=failure_strategy,
        parallelizable=parallelizable,
        optional=optional,
        depends_on=depends_on,
    )


def _planner(rules: tuple[PlanningRule, ...] | None = None) -> InvestigationPlanner:
    return InvestigationPlanner(rules=rules)


# ═══════════════════════════════════════════════════════════════════════════════
# Section A: Enum tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidenceKind:
    def test_a1_session(self) -> None:
        assert EvidenceKind.SESSION.value == "SESSION"

    def test_a2_log(self) -> None:
        assert EvidenceKind.LOG.value == "LOG"

    def test_a3_summary(self) -> None:
        assert EvidenceKind.SUMMARY.value == "SUMMARY"

    def test_a4_vision(self) -> None:
        assert EvidenceKind.VISION.value == "VISION"

    def test_a5_database(self) -> None:
        assert EvidenceKind.DATABASE.value == "DATABASE"

    def test_a6_api(self) -> None:
        assert EvidenceKind.API.value == "API"

    def test_a7_knowledge(self) -> None:
        assert EvidenceKind.KNOWLEDGE.value == "KNOWLEDGE"

    def test_a8_workflow(self) -> None:
        assert EvidenceKind.WORKFLOW.value == "WORKFLOW"

    def test_a9_configuration(self) -> None:
        assert EvidenceKind.CONFIGURATION.value == "CONFIGURATION"

    def test_a10_human(self) -> None:
        assert EvidenceKind.HUMAN.value == "HUMAN"

    def test_a11_all_ten_kinds(self) -> None:
        assert len(EvidenceKind) == 10


class TestPlannerEnums:
    def test_a12_investigation_priority_values(self) -> None:
        assert set(InvestigationPriority) == {
            InvestigationPriority.CRITICAL, InvestigationPriority.HIGH,
            InvestigationPriority.NORMAL,   InvestigationPriority.LOW,
        }

    def test_a13_estimated_complexity_values(self) -> None:
        assert InvestigationPriority.CRITICAL.value == "CRITICAL"
        assert EstimatedComplexity.TRIVIAL.value == "TRIVIAL"
        assert EstimatedComplexity.VERY_HIGH.value == "VERY_HIGH"

    def test_a14_fallback_strategy_values(self) -> None:
        assert FallbackStrategy.ESCALATE_IMMEDIATELY.value == "ESCALATE_IMMEDIATELY"
        assert FallbackStrategy.PARTIAL_INVESTIGATION.value == "PARTIAL_INVESTIGATION"
        assert FallbackStrategy.SKIP_OPTIONAL_STEPS.value == "SKIP_OPTIONAL_STEPS"
        assert FallbackStrategy.RETRY_FAILED_STEPS.value == "RETRY_FAILED_STEPS"

    def test_a15_failure_strategy_values(self) -> None:
        assert set(v.value for v in FailureStrategy) == {
            "CONTINUE", "RETRY", "SKIP", "ABORT", "ESCALATE",
        }

    def test_a16_dependency_type_values(self) -> None:
        assert set(v.value for v in DependencyType) == {
            "SEQUENTIAL", "CONDITIONAL", "FALLBACK", "PARALLEL",
        }

    def test_a17_precondition_kind_values(self) -> None:
        assert PreconditionKind.SLOT_PRESENT.value == "SLOT_PRESENT"
        assert PreconditionKind.EVIDENCE_PRESENT.value == "EVIDENCE_PRESENT"
        assert PreconditionKind.ALWAYS.value == "ALWAYS"
        assert PreconditionKind.NEVER.value == "NEVER"


# ═══════════════════════════════════════════════════════════════════════════════
# Section B: EvidenceRequirement
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidenceRequirement:
    def test_b1_basic_construction(self) -> None:
        req = _evidence_req()
        assert req.requirement_id == "req_test"
        assert req.kind == EvidenceKind.SESSION
        assert req.priority == EvidencePriority.HIGH
        assert req.required is True

    def test_b2_to_dict_round_trip(self) -> None:
        req = _evidence_req("req_round")
        d = req.to_dict()
        assert d["requirement_id"] == "req_round"
        assert d["kind"] == "SESSION"
        assert d["priority"] == "HIGH"
        assert d["required"] is True
        assert isinstance(d["expected_fields"], list)
        assert d["capability_hint"] == "READ"

    def test_b3_required_false(self) -> None:
        req = _evidence_req(required=False)
        assert req.required is False
        assert req.to_dict()["required"] is False

    def test_b4_capability_hint_none(self) -> None:
        req = EvidenceRequirement(
            requirement_id="req_no_hint",
            kind=EvidenceKind.HUMAN,
            title="Human Confirmation",
            description="Needs human",
            priority=EvidencePriority.NORMAL,
            required=True,
            expected_fields=(),
            validation_hints=(),
            capability_hint=None,
        )
        assert req.capability_hint is None
        assert req.to_dict()["capability_hint"] is None

    def test_b5_expected_fields_is_tuple(self) -> None:
        req = _evidence_req()
        assert isinstance(req.expected_fields, tuple)

    def test_b6_frozen(self) -> None:
        req = _evidence_req()
        with pytest.raises((AttributeError, TypeError)):
            req.required = False  # type: ignore[misc]

    def test_b7_vision_kind(self) -> None:
        req = _evidence_req(kind=EvidenceKind.VISION)
        assert req.kind == EvidenceKind.VISION
        assert req.to_dict()["kind"] == "VISION"


# ═══════════════════════════════════════════════════════════════════════════════
# Section C: RetryPolicy
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetryPolicy:
    def test_c1_defaults(self) -> None:
        r = RetryPolicy()
        assert r.max_attempts == 2
        assert r.backoff_seconds == 0.0
        assert r.retry_on_timeout is True
        assert r.retry_on_partial is False

    def test_c2_to_dict(self) -> None:
        r = RetryPolicy(max_attempts=3, backoff_seconds=1.5)
        d = r.to_dict()
        assert d["max_attempts"] == 3
        assert d["backoff_seconds"] == 1.5

    def test_c3_custom_values(self) -> None:
        r = RetryPolicy(max_attempts=1, backoff_seconds=0.0, retry_on_timeout=False)
        assert r.max_attempts == 1
        assert r.retry_on_timeout is False

    def test_c4_frozen(self) -> None:
        r = RetryPolicy()
        with pytest.raises((AttributeError, TypeError)):
            r.max_attempts = 99  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Section D: StepPrecondition and StepOutput
# ═══════════════════════════════════════════════════════════════════════════════

class TestStepPreconditionAndOutput:
    def test_d1_precondition_construction(self) -> None:
        pre = StepPrecondition(
            precondition_id="pre_1",
            description="session_id must be present",
            kind=PreconditionKind.SLOT_PRESENT,
            key="session_id",
        )
        assert pre.precondition_id == "pre_1"
        assert pre.kind == PreconditionKind.SLOT_PRESENT
        assert pre.key == "session_id"

    def test_d2_precondition_to_dict(self) -> None:
        pre = StepPrecondition("pre_1", "desc", PreconditionKind.ALWAYS, "")
        d = pre.to_dict()
        assert d["kind"] == "ALWAYS"
        assert d["key"] == ""

    def test_d3_output_construction(self) -> None:
        out = StepOutput("out_1", "Session state", EvidenceKind.SESSION, "session_state")
        assert out.output_id == "out_1"
        assert out.kind == EvidenceKind.SESSION
        assert out.key == "session_state"

    def test_d4_output_to_dict(self) -> None:
        out = StepOutput("out_2", "desc", EvidenceKind.LOG, "log_key")
        d = out.to_dict()
        assert d["kind"] == "LOG"
        assert d["key"] == "log_key"

    def test_d5_both_frozen(self) -> None:
        pre = StepPrecondition("pre_x", "d", PreconditionKind.NEVER, "k")
        out = StepOutput("out_x", "d", EvidenceKind.API, "k")
        with pytest.raises((AttributeError, TypeError)):
            pre.key = "other"  # type: ignore[misc]
        with pytest.raises((AttributeError, TypeError)):
            out.key = "other"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Section E: StepDependency
# ═══════════════════════════════════════════════════════════════════════════════

class TestStepDependency:
    def test_e1_sequential(self) -> None:
        dep = StepDependency("step_01", "step_02", DependencyType.SEQUENTIAL)
        assert dep.from_step_id == "step_01"
        assert dep.to_step_id == "step_02"
        assert dep.dependency_type == DependencyType.SEQUENTIAL
        assert dep.condition == ""

    def test_e2_conditional_with_condition(self) -> None:
        dep = StepDependency("step_01", "step_02", DependencyType.CONDITIONAL, "failure_code == LIVENESS")
        assert dep.condition == "failure_code == LIVENESS"

    def test_e3_fallback(self) -> None:
        dep = StepDependency("step_01", "step_02", DependencyType.FALLBACK)
        assert dep.dependency_type == DependencyType.FALLBACK

    def test_e4_to_dict(self) -> None:
        dep = StepDependency("step_01", "step_02", DependencyType.SEQUENTIAL)
        d = dep.to_dict()
        assert d["from_step_id"] == "step_01"
        assert d["to_step_id"] == "step_02"
        assert d["dependency_type"] == "SEQUENTIAL"

    def test_e5_frozen(self) -> None:
        dep = StepDependency("a", "b", DependencyType.PARALLEL)
        with pytest.raises((AttributeError, TypeError)):
            dep.from_step_id = "c"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Section F: PlanningStep
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlanningStep:
    def test_f1_basic_construction(self) -> None:
        s = _step()
        assert s.step_id == "step_01_test"
        assert s.order == 0
        assert isinstance(s.evidence_required, tuple)
        assert isinstance(s.candidate_capabilities, tuple)

    def test_f2_is_root_no_deps(self) -> None:
        s = _step(depends_on=())
        assert s.is_root is True

    def test_f3_is_root_false_with_deps(self) -> None:
        s = _step(depends_on=("step_00",))
        assert s.is_root is False

    def test_f4_required_evidence_count(self) -> None:
        s = PlanningStep(
            step_id="s",
            order=0,
            title="T",
            description="D",
            evidence_required=(
                _evidence_req("r1", required=True),
                _evidence_req("r2", required=False),
                _evidence_req("r3", required=True),
            ),
            evidence_priority=EvidencePriority.HIGH,
            candidate_capabilities=(),
            preconditions=(),
            outputs=(),
            retry_policy=RetryPolicy(),
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=False,
            optional=False,
            depends_on=(),
        )
        assert s.required_evidence_count() == 2

    def test_f5_to_dict(self) -> None:
        s = _step()
        d = s.to_dict()
        assert d["step_id"] == "step_01_test"
        assert d["order"] == 0
        assert isinstance(d["evidence_required"], list)
        assert isinstance(d["candidate_capabilities"], list)
        assert isinstance(d["depends_on"], list)

    def test_f6_frozen(self) -> None:
        s = _step()
        with pytest.raises((AttributeError, TypeError)):
            s.order = 99  # type: ignore[misc]

    def test_f7_parallelizable_flag(self) -> None:
        s = _step(parallelizable=True)
        assert s.parallelizable is True
        assert s.to_dict()["parallelizable"] is True

    def test_f8_optional_flag(self) -> None:
        s = _step(optional=True)
        assert s.optional is True
        assert s.to_dict()["optional"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Section G: InvestigationGraph
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvestigationGraph:
    def _linear_graph(self) -> InvestigationGraph:
        s1 = _step("s1", order=0)
        s2 = _step("s2", order=1, depends_on=("s1",))
        s3 = _step("s3", order=2, depends_on=("s2",))
        dep1 = StepDependency("s1", "s2", DependencyType.SEQUENTIAL)
        dep2 = StepDependency("s2", "s3", DependencyType.SEQUENTIAL)
        return InvestigationGraph(
            nodes=(s1, s2, s3),
            edges=(dep1, dep2),
            parallel_groups=(),
        )

    def _parallel_graph(self) -> InvestigationGraph:
        s1 = _step("s1", order=0)
        s2 = _step("s2", order=1, parallelizable=True, depends_on=("s1",))
        s3 = _step("s3", order=2, parallelizable=True, depends_on=("s1",))
        dep1 = StepDependency("s1", "s2", DependencyType.SEQUENTIAL)
        dep2 = StepDependency("s1", "s3", DependencyType.SEQUENTIAL)
        return InvestigationGraph(
            nodes=(s1, s2, s3),
            edges=(dep1, dep2),
            parallel_groups=(frozenset({"s2", "s3"}),),
        )

    def test_g1_get_root_steps_linear(self) -> None:
        g = self._linear_graph()
        roots = g.get_root_steps()
        assert len(roots) == 1
        assert roots[0].step_id == "s1"

    def test_g2_get_root_steps_parallel(self) -> None:
        g = self._parallel_graph()
        roots = g.get_root_steps()
        assert len(roots) == 1
        assert roots[0].step_id == "s1"

    def test_g3_get_next_steps(self) -> None:
        g = self._linear_graph()
        completed = {"s1"}
        nxt = g.get_next_steps(completed, set())
        assert len(nxt) == 1
        assert nxt[0].step_id == "s2"

    def test_g4_get_next_steps_after_two(self) -> None:
        g = self._linear_graph()
        nxt = g.get_next_steps({"s1", "s2"}, set())
        assert len(nxt) == 1
        assert nxt[0].step_id == "s3"

    def test_g5_get_next_steps_parallel(self) -> None:
        g = self._parallel_graph()
        nxt = g.get_next_steps({"s1"}, set())
        ids = {s.step_id for s in nxt}
        assert ids == {"s2", "s3"}

    def test_g6_is_linear_true(self) -> None:
        assert self._linear_graph().is_linear() is True

    def test_g7_is_linear_false(self) -> None:
        assert self._parallel_graph().is_linear() is False

    def test_g8_topological_order_chain(self) -> None:
        g = self._linear_graph()
        order = g.topological_order()
        assert [s.step_id for s in order] == ["s1", "s2", "s3"]

    def test_g9_step_count(self) -> None:
        g = self._linear_graph()
        assert g.step_count() == 3

    def test_g10_required_step_count(self) -> None:
        s1 = _step("s1", optional=False)
        s2 = _step("s2", optional=True)
        g = InvestigationGraph(nodes=(s1, s2), edges=(), parallel_groups=())
        assert g.required_step_count() == 1

    def test_g11_to_dict(self) -> None:
        g = self._linear_graph()
        d = g.to_dict()
        assert d["node_count"] == 3
        assert d["edge_count"] == 2
        assert d["is_linear"] is True
        assert isinstance(d["nodes"], list)

    def test_g12_frozen(self) -> None:
        g = self._linear_graph()
        with pytest.raises((AttributeError, TypeError)):
            g.nodes = ()  # type: ignore[misc]

    def test_g13_fallback_step_not_in_next_until_prereq_fails(self) -> None:
        s1 = _step("s1", order=0)
        s2 = _step("s2", order=1, depends_on=("s1",))
        dep = StepDependency("s1", "s2", DependencyType.FALLBACK)
        g = InvestigationGraph(nodes=(s1, s2), edges=(dep,), parallel_groups=())
        # s2 is fallback — only ready when s1 is in failed, not completed
        assert g.get_next_steps({"s1"}, set()) == []
        nxt = g.get_next_steps(set(), {"s1"})
        assert any(s.step_id == "s2" for s in nxt)


# ═══════════════════════════════════════════════════════════════════════════════
# Section H: InvestigationPlan
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvestigationPlan:
    def _make_plan(self) -> InvestigationPlan:
        req_req = _evidence_req("req_required", required=True)
        req_opt = _evidence_req("req_optional", required=False)
        s1 = _step("s1", optional=False)
        s2 = _step("s2", optional=True)
        graph = InvestigationGraph(nodes=(s1, s2), edges=(), parallel_groups=())
        return InvestigationPlan(
            plan_id="plan-001",
            workflow_id="wf-001",
            topic="VKYC_SESSION_FAILURE",
            client_id="unity_bank",
            case_id="case-001",
            objective="Determine why VKYC failed.",
            investigation_priority=InvestigationPriority.HIGH,
            estimated_complexity=EstimatedComplexity.LOW,
            expected_evidence=(req_req, req_opt),
            confidence_threshold=0.75,
            completion_conditions=(CompletionCondition("cond_1", "Evidence collected", True),),
            fallback_strategy=FallbackStrategy.SKIP_OPTIONAL_STEPS,
            created_at="2026-07-06T00:00:00+00:00",
            steps=(s1, s2),
            graph=graph,
        )

    def test_h1_construction(self) -> None:
        plan = self._make_plan()
        assert plan.plan_id == "plan-001"
        assert plan.topic == "VKYC_SESSION_FAILURE"

    def test_h2_required_evidence(self) -> None:
        plan = self._make_plan()
        assert len(plan.required_evidence) == 1
        assert plan.required_evidence[0].requirement_id == "req_required"

    def test_h3_optional_evidence(self) -> None:
        plan = self._make_plan()
        assert len(plan.optional_evidence) == 1
        assert plan.optional_evidence[0].requirement_id == "req_optional"

    def test_h4_required_steps(self) -> None:
        plan = self._make_plan()
        assert len(plan.required_steps) == 1
        assert plan.required_steps[0].step_id == "s1"

    def test_h5_step_by_id_found(self) -> None:
        plan = self._make_plan()
        assert plan.step_by_id("s1") is not None
        assert plan.step_by_id("s1").step_id == "s1"

    def test_h6_step_by_id_not_found(self) -> None:
        plan = self._make_plan()
        assert plan.step_by_id("nonexistent") is None

    def test_h7_has_vision_evidence_false(self) -> None:
        plan = self._make_plan()
        assert plan.has_vision_evidence() is False

    def test_h8_has_vision_evidence_true(self) -> None:
        vision_req = _evidence_req("req_vision", kind=EvidenceKind.VISION)
        s = _step("s_vision")
        s_with_vision = PlanningStep(
            **{**s.__dict__, "evidence_required": (vision_req,), "step_id": "s_v"}  # type: ignore[arg-type]
        )
        graph = InvestigationGraph(nodes=(s_with_vision,), edges=(), parallel_groups=())
        plan = InvestigationPlan(
            plan_id="plan-v", workflow_id=None, topic="T", client_id="c",
            case_id="case-v", objective="O",
            investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=EstimatedComplexity.LOW,
            expected_evidence=(vision_req,),
            confidence_threshold=0.75,
            completion_conditions=(),
            fallback_strategy=FallbackStrategy.ESCALATE_IMMEDIATELY,
            created_at="2026-07-06T00:00:00+00:00",
            steps=(s_with_vision,),
            graph=graph,
        )
        assert plan.has_vision_evidence() is True

    def test_h9_to_dict(self) -> None:
        plan = self._make_plan()
        d = plan.to_dict()
        assert d["plan_id"] == "plan-001"
        assert d["topic"] == "VKYC_SESSION_FAILURE"
        assert d["step_count"] == 2
        assert d["required_step_count"] == 1
        assert d["evidence_count"] == 2
        assert d["required_evidence_count"] == 1
        assert isinstance(d["steps"], list)
        assert isinstance(d["graph"], dict)

    def test_h10_frozen(self) -> None:
        plan = self._make_plan()
        with pytest.raises((AttributeError, TypeError)):
            plan.plan_id = "other"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# Section I: PlanningRule ABC + applies_to
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlanningRuleABC:
    def test_i1_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            PlanningRule()  # type: ignore[abstract]

    def test_i2_applies_to_exact_match(self) -> None:
        rule = VKYCSessionFailurePlanningRule()
        assert rule.applies_to("VKYC_SESSION_FAILURE") is True

    def test_i3_applies_to_case_insensitive(self) -> None:
        rule = VKYCSessionFailurePlanningRule()
        assert rule.applies_to("vkyc_session_failure") is True

    def test_i4_applies_to_hyphenated(self) -> None:
        rule = VKYCSessionFailurePlanningRule()
        assert rule.applies_to("VKYC-SESSION-FAILURE") is True

    def test_i5_applies_to_wrong_topic(self) -> None:
        rule = VKYCSessionFailurePlanningRule()
        assert rule.applies_to("OTP_DELIVERY_FAILURE") is False

    def test_i6_otp_applies_to(self) -> None:
        rule = OTPDeliveryFailurePlanningRule()
        assert rule.applies_to("OTP_DELIVERY_FAILURE") is True
        assert rule.applies_to("VKYC_SESSION_FAILURE") is False


# ═══════════════════════════════════════════════════════════════════════════════
# Section J: Topic-specific rules
# ═══════════════════════════════════════════════════════════════════════════════

def _plan_id() -> str:
    return str(uuid.uuid4())


def _make_vkyc_plan() -> InvestigationPlan:
    rule = VKYCSessionFailurePlanningRule()
    return rule.generate_plan(
        plan_id=_plan_id(),
        case_id="case-001",
        client_id="unity_bank",
        topic="VKYC_SESSION_FAILURE",
        workflow_id="wf-001",
        slots={"session_id": "sess-abc", "phone_number": "+911234567890"},
        workflow=None,
        sop=None,
    )


class TestVKYCRule:
    def test_j1_topic_key(self) -> None:
        assert VKYCSessionFailurePlanningRule().topic_key() == "VKYC_SESSION_FAILURE"

    def test_j2_applies_to(self) -> None:
        assert VKYCSessionFailurePlanningRule().applies_to("VKYC_SESSION_FAILURE") is True

    def test_j3_plan_has_four_steps(self) -> None:
        plan = _make_vkyc_plan()
        assert len(plan.steps) == 4

    def test_j4_step_01_no_deps(self) -> None:
        plan = _make_vkyc_plan()
        s1 = plan.step_by_id("step_01_session_evidence")
        assert s1 is not None
        assert s1.is_root is True

    def test_j5_steps_02_03_04_parallel(self) -> None:
        plan = _make_vkyc_plan()
        for sid in ("step_02_user_evidence", "step_03_log_evidence", "step_04_vision_evidence"):
            s = plan.step_by_id(sid)
            assert s is not None
            assert s.parallelizable is True

    def test_j6_step_04_optional(self) -> None:
        plan = _make_vkyc_plan()
        s4 = plan.step_by_id("step_04_vision_evidence")
        assert s4 is not None
        assert s4.optional is True

    def test_j7_step_01_escalate_on_failure(self) -> None:
        plan = _make_vkyc_plan()
        s1 = plan.step_by_id("step_01_session_evidence")
        assert s1.failure_strategy == FailureStrategy.ESCALATE

    def test_j8_plan_priority_high(self) -> None:
        plan = _make_vkyc_plan()
        assert plan.investigation_priority == InvestigationPriority.HIGH

    def test_j9_plan_has_vision_evidence(self) -> None:
        plan = _make_vkyc_plan()
        assert plan.has_vision_evidence() is True

    def test_j10_plan_expected_evidence_count(self) -> None:
        plan = _make_vkyc_plan()
        assert len(plan.expected_evidence) == 4

    def test_j11_required_steps_not_optional(self) -> None:
        plan = _make_vkyc_plan()
        required = plan.required_steps
        assert all(not s.optional for s in required)
        assert len(required) == 3  # step_04 is optional

    def test_j12_graph_parallel_groups_present(self) -> None:
        plan = _make_vkyc_plan()
        assert len(plan.graph.parallel_groups) >= 1

    def test_j13_no_tool_names_in_evidence(self) -> None:
        plan = _make_vkyc_plan()
        for req in plan.expected_evidence:
            assert "Tool" not in req.title
            assert "Tool" not in req.description

    def test_j14_step_session_evidence_kind(self) -> None:
        plan = _make_vkyc_plan()
        s1 = plan.step_by_id("step_01_session_evidence")
        kinds = {r.kind for r in s1.evidence_required}
        assert EvidenceKind.SESSION in kinds

    def test_j15_confidence_threshold_set(self) -> None:
        plan = _make_vkyc_plan()
        assert 0.0 < plan.confidence_threshold <= 1.0

    def test_j16_fallback_strategy_skip_optional(self) -> None:
        plan = _make_vkyc_plan()
        assert plan.fallback_strategy == FallbackStrategy.SKIP_OPTIONAL_STEPS

    def test_j17_completion_conditions_non_empty(self) -> None:
        plan = _make_vkyc_plan()
        assert len(plan.completion_conditions) > 0
        assert all(c.required for c in plan.completion_conditions if c.required)


class TestOTPRule:
    def _plan(self) -> InvestigationPlan:
        rule = OTPDeliveryFailurePlanningRule()
        return rule.generate_plan(
            plan_id=_plan_id(), case_id="c", client_id="cl", topic="OTP_DELIVERY_FAILURE",
            workflow_id=None, slots={}, workflow=None, sop=None,
        )

    def test_j18_topic_key(self) -> None:
        assert OTPDeliveryFailurePlanningRule().topic_key() == "OTP_DELIVERY_FAILURE"

    def test_j19_four_steps(self) -> None:
        assert len(self._plan().steps) == 4

    def test_j20_step_04_optional(self) -> None:
        plan = self._plan()
        s4 = plan.step_by_id("step_04_case_history")
        assert s4 is not None
        assert s4.optional is True

    def test_j21_fallback_partial(self) -> None:
        assert self._plan().fallback_strategy == FallbackStrategy.PARTIAL_INVESTIGATION


class TestOCRRule:
    def _plan(self) -> InvestigationPlan:
        rule = DocumentOCRPlanningRule()
        return rule.generate_plan(
            plan_id=_plan_id(), case_id="c", client_id="cl", topic="DOCUMENT_OCR_FAILURE",
            workflow_id=None, slots={}, workflow=None, sop=None,
        )

    def test_j22_topic_key(self) -> None:
        assert DocumentOCRPlanningRule().topic_key() == "DOCUMENT_OCR_FAILURE"

    def test_j23_three_steps(self) -> None:
        assert len(self._plan().steps) == 3

    def test_j24_vision_step_critical(self) -> None:
        plan = self._plan()
        s2 = plan.step_by_id("step_02_document_vision")
        assert s2 is not None
        vision_reqs = [r for r in s2.evidence_required if r.kind == EvidenceKind.VISION]
        assert len(vision_reqs) == 1
        assert vision_reqs[0].priority == EvidencePriority.CRITICAL

    def test_j25_escalate_immediately(self) -> None:
        assert self._plan().fallback_strategy == FallbackStrategy.ESCALATE_IMMEDIATELY


class TestAPICallbackRule:
    def _plan(self) -> InvestigationPlan:
        rule = APICallbackPlanningRule()
        return rule.generate_plan(
            plan_id=_plan_id(), case_id="c", client_id="cl", topic="API_CALLBACK_FAILURE",
            workflow_id=None, slots={}, workflow=None, sop=None,
        )

    def test_j26_topic_key(self) -> None:
        assert APICallbackPlanningRule().topic_key() == "API_CALLBACK_FAILURE"

    def test_j27_two_steps_linear(self) -> None:
        plan = self._plan()
        assert len(plan.steps) == 2
        assert plan.graph.is_linear() is True

    def test_j28_step_01_escalate(self) -> None:
        plan = self._plan()
        s1 = plan.step_by_id("step_01_failure_log")
        assert s1.failure_strategy == FailureStrategy.ESCALATE


class TestAgentPortalRule:
    def _plan(self) -> InvestigationPlan:
        rule = AgentPortalPlanningRule()
        return rule.generate_plan(
            plan_id=_plan_id(), case_id="c", client_id="cl", topic="AGENT_PORTAL_ISSUE",
            workflow_id=None, slots={}, workflow=None, sop=None,
        )

    def test_j29_topic_key(self) -> None:
        assert AgentPortalPlanningRule().topic_key() == "AGENT_PORTAL_ISSUE"

    def test_j30_two_steps(self) -> None:
        assert len(self._plan().steps) == 2

    def test_j31_no_tool_names(self) -> None:
        plan = self._plan()
        for req in plan.expected_evidence:
            assert "GetSession" not in req.title
            assert "GetUser" not in req.title


class TestGetDefaultRules:
    def test_j32_count(self) -> None:
        rules = get_default_rules()
        assert len(rules) == 5

    def test_j33_all_are_planning_rules(self) -> None:
        for rule in get_default_rules():
            assert isinstance(rule, PlanningRule)

    def test_j34_topic_keys_unique(self) -> None:
        keys = [r.topic_key() for r in get_default_rules()]
        assert len(keys) == len(set(keys))

    def test_j35_covers_known_topics(self) -> None:
        keys = {r.topic_key() for r in get_default_rules()}
        expected = {
            "VKYC_SESSION_FAILURE", "OTP_DELIVERY_FAILURE",
            "DOCUMENT_OCR_FAILURE", "API_CALLBACK_FAILURE", "AGENT_PORTAL_ISSUE",
        }
        assert expected == keys


# ═══════════════════════════════════════════════════════════════════════════════
# Section K: DefaultWorkflowPlanningRule
# ═══════════════════════════════════════════════════════════════════════════════

class TestDefaultWorkflowRule:
    def test_k1_applies_to_always_false(self) -> None:
        assert DefaultWorkflowPlanningRule().applies_to("ANYTHING") is False

    def test_k2_generates_steps_from_slots(self) -> None:
        rule = DefaultWorkflowPlanningRule()
        wf = _workflow(required_slots=("session_id", "phone_number", "application_id"))
        plan = rule.generate_plan(
            plan_id=_plan_id(), case_id="c", client_id="cl", topic="CUSTOM_TOPIC",
            workflow_id=wf.workflow_id, slots={}, workflow=wf, sop=None,
        )
        assert len(plan.steps) == 3

    def test_k3_empty_workflow_returns_fallback(self) -> None:
        rule = DefaultWorkflowPlanningRule()
        wf = _workflow(required_slots=())
        plan = rule.generate_plan(
            plan_id=_plan_id(), case_id="c", client_id="cl", topic="CUSTOM_TOPIC",
            workflow_id=wf.workflow_id, slots={}, workflow=wf, sop=None,
        )
        assert isinstance(plan, InvestigationPlan)
        assert len(plan.steps) == 1  # MinimalFallbackRule's 1-step plan

    def test_k4_plan_is_valid(self) -> None:
        rule = DefaultWorkflowPlanningRule()
        wf = _workflow(required_slots=("session_id",))
        plan = rule.generate_plan(
            plan_id=_plan_id(), case_id="c", client_id="cl", topic="SOME_TOPIC",
            workflow_id="wf-001", slots={}, workflow=wf, sop=None,
        )
        assert plan.workflow_id == "wf-001"
        assert len(plan.steps) >= 1
        assert isinstance(plan.graph, InvestigationGraph)

    def test_k5_none_workflow_returns_fallback(self) -> None:
        rule = DefaultWorkflowPlanningRule()
        plan = rule.generate_plan(
            plan_id=_plan_id(), case_id="c", client_id="cl", topic="T",
            workflow_id=None, slots={}, workflow=None, sop=None,
        )
        assert isinstance(plan, InvestigationPlan)
        assert len(plan.steps) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Section L: MinimalFallbackRule
# ═══════════════════════════════════════════════════════════════════════════════

class TestMinimalFallbackRule:
    def test_l1_applies_to_always_false(self) -> None:
        assert MinimalFallbackRule().applies_to("ANYTHING") is False

    def test_l2_always_returns_valid_plan(self) -> None:
        plan = MinimalFallbackRule().generate_plan(
            _plan_id(), "case-x", "client-x", "UNKNOWN_TOPIC",
            None, {}, None, None,
        )
        assert isinstance(plan, InvestigationPlan)

    def test_l3_one_step(self) -> None:
        plan = MinimalFallbackRule().generate_plan(
            _plan_id(), "c", "cl", "UNKNOWN", None, {}, None, None,
        )
        assert len(plan.steps) == 1

    def test_l4_plan_id_preserved(self) -> None:
        pid = "fixed-plan-id"
        plan = MinimalFallbackRule().generate_plan(
            pid, "c", "cl", "UNKNOWN", None, {}, None, None,
        )
        assert plan.plan_id == pid

    def test_l5_fallback_strategy_escalate(self) -> None:
        plan = MinimalFallbackRule().generate_plan(
            _plan_id(), "c", "cl", "UNKNOWN", None, {}, None, None,
        )
        assert plan.fallback_strategy == FallbackStrategy.ESCALATE_IMMEDIATELY

    def test_l6_confidence_threshold_low(self) -> None:
        plan = MinimalFallbackRule().generate_plan(
            _plan_id(), "c", "cl", "UNKNOWN", None, {}, None, None,
        )
        assert plan.confidence_threshold == 0.5

    def test_l7_trivial_complexity(self) -> None:
        plan = MinimalFallbackRule().generate_plan(
            _plan_id(), "c", "cl", "UNKNOWN", None, {}, None, None,
        )
        assert plan.estimated_complexity == EstimatedComplexity.TRIVIAL


# ═══════════════════════════════════════════════════════════════════════════════
# Section M: InvestigationPlanner (engine.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvestigationPlannerEngine:
    def test_m1_default_construction(self) -> None:
        planner = InvestigationPlanner()
        assert planner is not None

    def test_m2_plan_returns_investigation_plan(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_m3_vkyc_topic(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.topic == "VKYC_SESSION_FAILURE"
        assert len(plan.steps) == 4

    def test_m4_otp_topic(self) -> None:
        ctx = _context("OTP_DELIVERY_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.topic == "OTP_DELIVERY_FAILURE"
        assert len(plan.steps) == 4

    def test_m5_ocr_topic(self) -> None:
        ctx = _context("DOCUMENT_OCR_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.topic == "DOCUMENT_OCR_FAILURE"
        assert len(plan.steps) == 3

    def test_m6_api_callback_topic(self) -> None:
        ctx = _context("API_CALLBACK_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.topic == "API_CALLBACK_FAILURE"
        assert len(plan.steps) == 2

    def test_m7_agent_portal_topic(self) -> None:
        ctx = _context("AGENT_PORTAL_ISSUE")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.topic == "AGENT_PORTAL_ISSUE"
        assert len(plan.steps) == 2

    def test_m8_unknown_topic_returns_fallback(self) -> None:
        ctx = _context("COMPLETELY_UNKNOWN_TOPIC")
        plan = InvestigationPlanner().plan(ctx)
        assert isinstance(plan, InvestigationPlan)
        assert len(plan.steps) >= 1  # MinimalFallbackRule plan

    def test_m9_never_raises_on_broken_rule(self) -> None:
        class BrokenRule(PlanningRule):
            def topic_key(self) -> str:
                return "VKYC_SESSION_FAILURE"
            def generate_plan(self, *args: Any, **kwargs: Any) -> InvestigationPlan:
                raise RuntimeError("Broken rule deliberately fails")

        planner = InvestigationPlanner(rules=(BrokenRule(),))
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_m10_uses_context_workflow_definition(self) -> None:
        ctx = _context("CUSTOM_WORKFLOW_TOPIC")
        ctx.workflow_definition = _workflow(
            workflow_id="custom-wf", topic="CUSTOM_WORKFLOW_TOPIC",
            required_slots=("session_id",),
        )
        plan = InvestigationPlanner().plan(ctx)
        assert isinstance(plan, InvestigationPlan)
        assert plan.workflow_id == "custom-wf"

    def test_m11_with_custom_rules(self) -> None:
        class CustomRule(PlanningRule):
            def topic_key(self) -> str:
                return "MY_CUSTOM_TOPIC"
            def generate_plan(self, plan_id: str, case_id: str, client_id: str,
                              topic: str, workflow_id: str | None, slots: dict[str, Any],
                              workflow: Any, sop: Any) -> InvestigationPlan:
                return MinimalFallbackRule().generate_plan(
                    plan_id, case_id, client_id, topic, workflow_id, slots, workflow, sop,
                )

        planner = InvestigationPlanner(rules=(CustomRule(),))
        ctx = _context("MY_CUSTOM_TOPIC")
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_m12_workflow_repository_used_when_provided(self) -> None:
        class MockRepo:
            def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
                return _workflow(workflow_id=workflow_id, topic="VKYC_SESSION_FAILURE")

        ctx = _context("VKYC_SESSION_FAILURE")
        ctx.workflow_definition = _workflow(workflow_id="wf-from-repo")
        planner = InvestigationPlanner(workflow_repository=MockRepo())
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_m13_workflow_repository_failure_falls_back(self) -> None:
        class BrokenRepo:
            def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
                raise ConnectionError("DB offline")

        ctx = _context("VKYC_SESSION_FAILURE")
        ctx.workflow_definition = _workflow(workflow_id="wf-fallback")
        planner = InvestigationPlanner(workflow_repository=BrokenRepo())
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_m14_sop_resolver_used_when_provided(self) -> None:
        class MockSOP:
            def resolve(self, topic: str, context: dict[str, Any]) -> None:
                return None  # SOP not found is fine

        ctx = _context("VKYC_SESSION_FAILURE")
        planner = InvestigationPlanner(sop_resolver=MockSOP())
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_m15_sop_resolver_failure_continues(self) -> None:
        class BrokenSOP:
            def resolve(self, topic: str, context: dict[str, Any]) -> None:
                raise RuntimeError("SOP service unavailable")

        ctx = _context("VKYC_SESSION_FAILURE")
        planner = InvestigationPlanner(sop_resolver=BrokenSOP())
        plan = planner.plan(ctx)
        assert isinstance(plan, InvestigationPlan)

    def test_m16_case_id_passed_through(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.case_id == "case-001"

    def test_m17_client_id_from_tenant(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.client_id == "unity_bank"

    def test_m18_topic_uppercase_normalized(self) -> None:
        ctx = _context("vkyc_session_failure")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.topic == "VKYC_SESSION_FAILURE"

    def test_m19_topic_hyphenated_normalized(self) -> None:
        ctx = _context("vkyc-session-failure")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.topic == "VKYC_SESSION_FAILURE"

    def test_m20_plan_id_is_unique_uuid(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan1 = InvestigationPlanner().plan(ctx)
        plan2 = InvestigationPlanner().plan(ctx)
        assert plan1.plan_id != plan2.plan_id

    def test_m21_plan_has_steps(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert len(plan.steps) > 0

    def test_m22_plan_has_graph(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert isinstance(plan.graph, InvestigationGraph)

    def test_m23_plan_has_expected_evidence(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert len(plan.expected_evidence) > 0

    def test_m24_workflow_repository_protocol(self) -> None:
        class SimpleRepo:
            def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
                return None
        assert isinstance(SimpleRepo(), WorkflowRepository)

    def test_m25_sop_resolver_protocol(self) -> None:
        class SimpleSOP:
            def resolve(self, topic: str, context: dict[str, Any]) -> None:
                return None
        assert isinstance(SimpleSOP(), SOPResolverProtocol)


# ═══════════════════════════════════════════════════════════════════════════════
# Section N: Backward compatibility
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackwardCompat:
    def test_n1_legacy_planner_importable(self) -> None:
        assert LegacyInvestigationPlanner is not None

    def test_n2_extract_case_id_importable(self) -> None:
        assert _extract_case_id is not None

    def test_n3_extract_case_id_with_meta(self) -> None:
        slots = {"_meta": {"case_id": "case-xyz"}, "session_id": "sess-001"}
        assert _extract_case_id(slots) == "case-xyz"

    def test_n4_extract_case_id_missing(self) -> None:
        assert _extract_case_id({}) == "unknown"

    def test_n5_extract_case_id_non_dict_meta(self) -> None:
        assert _extract_case_id({"_meta": "not-a-dict"}) == "unknown"

    def test_n6_legacy_planner_is_different_from_engine(self) -> None:
        from case_engine.investigation.planner.engine import (
            InvestigationPlanner as EngineInvestigationPlanner,
        )
        assert LegacyInvestigationPlanner is not EngineInvestigationPlanner

    def test_n7_engine_importable_from_planner_engine(self) -> None:
        from case_engine.investigation.planner.engine import InvestigationPlanner as Eng
        assert Eng is not None

    def test_n8_new_investigation_plan_importable_from_models(self) -> None:
        from case_engine.investigation.planner.models import InvestigationPlan as NewPlan
        assert NewPlan is not None

    def test_n9_legacy_planner_plan_method_callable(self) -> None:
        planner = LegacyInvestigationPlanner()
        from case_engine.investigation.models import InvestigationPlan as OldPlan
        result = planner.plan("VKYC_Session_Failure", None, {})
        assert isinstance(result, OldPlan)


# ═══════════════════════════════════════════════════════════════════════════════
# Section O: Safety contract
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafetyContract:
    def test_o1_plan_does_not_modify_context_slots(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        slots_before = dict(ctx.slots)
        InvestigationPlanner().plan(ctx)
        assert ctx.slots == slots_before

    def test_o2_plan_does_not_modify_context_state(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        state_before = ctx.state
        InvestigationPlanner().plan(ctx)
        assert ctx.state == state_before

    def test_o3_plan_does_not_modify_context_audit(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        events_before = len(ctx.audit_events)
        InvestigationPlanner().plan(ctx)
        assert len(ctx.audit_events) == events_before

    def test_o4_plan_is_frozen(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        with pytest.raises((AttributeError, TypeError)):
            plan.plan_id = "tampered"  # type: ignore[misc]

    def test_o5_steps_are_frozen(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        with pytest.raises((AttributeError, TypeError)):
            plan.steps[0].order = 99  # type: ignore[misc]

    def test_o6_evidence_requirements_frozen(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        req = plan.expected_evidence[0]
        with pytest.raises((AttributeError, TypeError)):
            req.required = False  # type: ignore[misc]

    def test_o7_planner_never_raises_on_any_topic(self) -> None:
        topics = [
            "VKYC_SESSION_FAILURE", "OTP_DELIVERY_FAILURE", "DOCUMENT_OCR_FAILURE",
            "API_CALLBACK_FAILURE", "AGENT_PORTAL_ISSUE",
            "UNKNOWN", "GARBAGE_TOPIC", "", "123",
        ]
        for topic in topics:
            ctx = _context(topic)
            plan = InvestigationPlanner().plan(ctx)  # must not raise
            assert isinstance(plan, InvestigationPlan)

    def test_o8_planner_callable_multiple_times(self) -> None:
        planner = InvestigationPlanner()
        ctx = _context("VKYC_SESSION_FAILURE")
        plan1 = planner.plan(ctx)
        plan2 = planner.plan(ctx)
        assert plan1.plan_id != plan2.plan_id  # unique plan each time

    def test_o9_evidence_requirements_contain_no_tool_names(self) -> None:
        tool_suffixes = ("Tool", "GetSession", "GetUser", "GetFailure", "GetCase")
        for topic in ("VKYC_SESSION_FAILURE", "OTP_DELIVERY_FAILURE", "DOCUMENT_OCR_FAILURE"):
            ctx = _context(topic)
            plan = InvestigationPlanner().plan(ctx)
            for req in plan.expected_evidence:
                for suffix in tool_suffixes:
                    assert suffix not in req.title, \
                        f"Tool name found in evidence req title: {req.title!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# Section P: Integration tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    def test_p1_full_plan_vkyc_through_context(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert plan.case_id == ctx.case_id
        assert plan.client_id == ctx.tenant_context.client_id
        assert plan.topic == ctx.topic.upper()

    def test_p2_completion_conditions_non_empty(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert len(plan.completion_conditions) > 0

    def test_p3_plan_root_steps_available(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        roots = plan.graph.get_root_steps()
        assert len(roots) >= 1

    def test_p4_topological_order_correct(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        topo = plan.graph.topological_order()
        assert len(topo) == len(plan.steps)
        # root step must come before its dependents
        root_idx = next(i for i, s in enumerate(topo) if s.is_root)
        assert root_idx == 0

    def test_p5_plan_stored_in_context(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        ctx.investigation_plan = plan  # pipeline stage writes plan to context
        assert ctx.investigation_plan is plan

    def test_p6_fallback_for_unknown_topic_with_workflow(self) -> None:
        ctx = _context("BRAND_NEW_TOPIC")
        ctx.workflow_definition = _workflow(
            workflow_id="new-wf", topic="BRAND_NEW_TOPIC",
            required_slots=("session_id", "phone_number"),
        )
        plan = InvestigationPlanner().plan(ctx)
        assert isinstance(plan, InvestigationPlan)
        assert plan.workflow_id == "new-wf"
        assert len(plan.steps) == 2  # DefaultWorkflowPlanningRule from 2 required_slots

    def test_p7_vkyc_graph_parallel_groups_correct(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        # steps 02, 03, 04 are all parallel after step 01
        all_parallel_ids = set()
        for group in plan.graph.parallel_groups:
            all_parallel_ids.update(group)
        assert "step_01_session_evidence" not in all_parallel_ids

    def test_p8_plan_objective_not_empty(self) -> None:
        for topic in ("VKYC_SESSION_FAILURE", "OTP_DELIVERY_FAILURE", "DOCUMENT_OCR_FAILURE"):
            ctx = _context(topic)
            plan = InvestigationPlanner().plan(ctx)
            assert plan.objective.strip() != ""

    def test_p9_plan_created_at_is_iso(self) -> None:
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        assert "T" in plan.created_at  # ISO 8601 format

    def test_p10_plan_to_dict_serializable(self) -> None:
        import json
        ctx = _context("VKYC_SESSION_FAILURE")
        plan = InvestigationPlanner().plan(ctx)
        d = plan.to_dict()
        # dict must be JSON-serializable (no frozen sets, no enum values raw)
        json.dumps(d)  # will raise if not serializable
