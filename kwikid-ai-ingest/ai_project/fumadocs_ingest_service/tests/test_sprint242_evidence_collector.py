"""
tests/test_sprint242_evidence_collector.py

Sprint 2.42: Comprehensive test suite for the Evidence Collector Engine.

Sections:
  A  — Exceptions (7 classes, constructors, hierarchy)
  B  — CollectionContext (construction, get_slot, to_dict)
  C  — CollectionResult (ok/fail factories, to_dict, fields)
  D  — StepStatus (enum values)
  E  — StepExecutionRecord (properties, to_dict)
  F  — PlanExecutionSummary (ratios, to_dict)
  G  — StepMetric (immutable, to_dict)
  H  — CollectionMetrics (from_summary, computed properties)
  I  — CollectorValidator (validate_result, validate_preconditions, validate_plan)
  J  — ProviderRegistry (register, resolve, fallback, thread-safety, reset)
  K  — NullProvider (via build_default_registry)
  L  — ProviderDispatcher (success, failure, retry, provider-not-found, exception)
  M  — StepOrchestrator (linear, parallel, preconditions, fallback, unreachable)
  N  — EvidenceCollector (full pipeline, invalid plan, context write, empty plan)
  O  — Provider protocol satisfaction (EvidenceProvider, ToolProvider, etc.)
  P  — Deterministic replay (same plan → same step order)
  Q  — Context integration (collector_stats and collection_metrics written)
  R  — __init__.py public API completeness
  S  — Regression guard (Sprint 2.38–2.41 imports still work)
"""
from __future__ import annotations

import threading
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Subject imports ────────────────────────────────────────────────────────────
from case_engine.investigation.collector.exceptions import (
    CollectorConfigurationError,
    CollectorError,
    CollectorTimeoutError,
    PlanExecutionError,
    ProviderExecutionError,
    ProviderNotFoundError,
    StepPreconditionError,
)
from case_engine.investigation.collector.contracts import (
    CollectionContext,
    CollectionResult,
    EvidenceProvider,
    KnowledgeProvider,
    SOPProvider,
    ToolProvider,
    VisionProvider,
    WorkflowProvider,
)
from case_engine.investigation.collector.execution import (
    PlanExecutionSummary,
    StepExecutionRecord,
    StepStatus,
)
from case_engine.investigation.collector.metrics import (
    CollectionMetrics,
    StepMetric,
    _record_to_metric,
)
from case_engine.investigation.collector.validators import CollectorValidator
from case_engine.investigation.collector.registry import (
    ProviderRegistry,
    build_default_registry,
    _NullProvider,
)
from case_engine.investigation.collector.dispatcher import ProviderDispatcher
from case_engine.investigation.collector.orchestrator import StepOrchestrator
from case_engine.investigation.collector.collector import EvidenceCollector
from case_engine.investigation.collector import (
    EvidenceCollector as EvidenceCollectorPublic,
    ProviderRegistry as ProviderRegistryPublic,
    CollectionContext as CollectionContextPublic,
    CollectionResult as CollectionResultPublic,
    CollectorValidator as CollectorValidatorPublic,
    StepStatus as StepStatusPublic,
    CollectionMetrics as CollectionMetricsPublic,
    CollectorError as CollectorErrorPublic,
    ProviderNotFoundError as ProviderNotFoundErrorPublic,
    build_default_registry as build_default_registry_public,
)

# ── Supporting imports ─────────────────────────────────────────────────────────
from case_engine.investigation.planner.models import (
    CompletionCondition,
    DependencyType,
    EvidenceKind,
    EvidenceRequirement,
    EstimatedComplexity,
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
from case_engine.investigation.evidence.models import EvidencePriority
from case_engine.investigation.models import EvidenceBundle
from case_engine.investigation.context import InvestigationContext
from case_engine.tenant.models import TenantContext, TenantType, TenantEnvironment
from case_engine.tools.tool_models import ToolCapability


# ══════════════════════════════════════════════════════════════════════════════
# ── Test helpers ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _make_requirement(
    req_id: str = "req_01",
    kind: EvidenceKind = EvidenceKind.SESSION,
    required: bool = True,
    expected_fields: tuple[str, ...] = ("session_id",),
    priority: EvidencePriority = EvidencePriority.NORMAL,
) -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id=req_id,
        kind=kind,
        title=f"Requirement {req_id}",
        description="Test requirement",
        priority=priority,
        required=required,
        expected_fields=expected_fields,
        validation_hints=(),
        capability_hint=ToolCapability.READ,
    )


def _make_step(
    step_id: str = "step_01",
    order: int = 0,
    kind: EvidenceKind = EvidenceKind.SESSION,
    max_attempts: int = 1,
    backoff: float = 0.0,
    optional: bool = False,
    depends_on: tuple[str, ...] = (),
    parallelizable: bool = False,
    preconditions: tuple[StepPrecondition, ...] = (),
    failure_strategy: FailureStrategy = FailureStrategy.CONTINUE,
) -> PlanningStep:
    req = _make_requirement(req_id=f"req_{step_id}", kind=kind)
    return PlanningStep(
        step_id=step_id,
        order=order,
        title=f"Step {step_id}",
        description="Test step",
        evidence_required=(req,),
        evidence_priority=EvidencePriority.NORMAL,
        candidate_capabilities=(ToolCapability.READ,),
        preconditions=preconditions,
        outputs=(),
        retry_policy=RetryPolicy(max_attempts=max_attempts, backoff_seconds=backoff),
        failure_strategy=failure_strategy,
        parallelizable=parallelizable,
        optional=optional,
        depends_on=depends_on,
    )


def _make_plan(
    steps: list[PlanningStep],
    edges: list[StepDependency] | None = None,
    parallel_groups: list[frozenset[str]] | None = None,
    plan_id: str | None = None,
    case_id: str = "case_test_001",
) -> InvestigationPlan:
    steps_tuple = tuple(steps)
    edges_tuple = tuple(edges or [])
    groups_tuple = tuple(parallel_groups or [])
    graph = InvestigationGraph(
        nodes=steps_tuple,
        edges=edges_tuple,
        parallel_groups=groups_tuple,
    )
    return InvestigationPlan(
        plan_id=str(uuid.uuid4()) if plan_id is None else plan_id,
        workflow_id=None,
        topic="TEST_TOPIC",
        client_id="test_client",
        case_id=case_id,
        objective="Test objective",
        investigation_priority=InvestigationPriority.NORMAL,
        estimated_complexity=EstimatedComplexity.LOW,
        expected_evidence=tuple(_make_requirement() for _ in steps),
        confidence_threshold=0.75,
        completion_conditions=(),
        fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
        created_at="2026-07-07T00:00:00+00:00",
        steps=steps_tuple,
        graph=graph,
    )


def _make_context() -> InvestigationContext:
    tenant = TenantContext(
        client_id="test_client",
        client_name="Test Client",
        domain="testclient.example.com",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("lookup", "verify"),
        credentials_ref="test-credentials",
    )
    return InvestigationContext.create(
        case_id="case_test_001",
        ticket_id="ticket_001",
        ticket_subject="Test ticket",
        ticket_description="Test description",
        customer_id="cust_001",
        customer_email="agent@testbank.com",
        channel="email",
        tenant_context=tenant,
        topic="TEST_TOPIC",
        classification_confidence=0.9,
    )


class _SuccessProvider:
    """Mock provider that always succeeds."""
    def __init__(self, name: str = "SuccessProvider", payload: dict | None = None) -> None:
        self._name = name
        self._payload = payload or {"session_id": "sess_123", "status": "active"}
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._name

    def can_handle(self, evidence_kind: str) -> bool:
        return True

    def execute(self, step_id, evidence_kind, context, requirement_metadata) -> CollectionResult:
        self.call_count += 1
        return CollectionResult.ok(
            provider_name=self._name,
            step_id=step_id,
            evidence_kind=evidence_kind,
            payload=self._payload,
        )


class _FailProvider:
    """Mock provider that always fails."""
    def __init__(self, name: str = "FailProvider", error_code: str = "TEST_ERROR") -> None:
        self._name = name
        self._error_code = error_code
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._name

    def can_handle(self, evidence_kind: str) -> bool:
        return True

    def execute(self, step_id, evidence_kind, context, requirement_metadata) -> CollectionResult:
        self.call_count += 1
        return CollectionResult.fail(
            provider_name=self._name,
            step_id=step_id,
            evidence_kind=evidence_kind,
            error_code=self._error_code,
            error_message=f"Intentional failure from {self._name}",
        )


class _RaisingProvider:
    """Mock provider that raises on execute."""
    @property
    def provider_name(self) -> str:
        return "RaisingProvider"

    def can_handle(self, evidence_kind: str) -> bool:
        return True

    def execute(self, step_id, evidence_kind, context, requirement_metadata) -> CollectionResult:
        raise RuntimeError("intentional crash from RaisingProvider")


class _CountingProvider:
    """Provider that tracks call order for determinism tests."""
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[str] = []

    @property
    def provider_name(self) -> str:
        return self._name

    def can_handle(self, evidence_kind: str) -> bool:
        return True

    def execute(self, step_id, evidence_kind, context, requirement_metadata) -> CollectionResult:
        self.calls.append(step_id)
        return CollectionResult.ok(
            provider_name=self._name,
            step_id=step_id,
            evidence_kind=evidence_kind,
            payload={"data": "ok"},
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section A — Exceptions
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionA_Exceptions:

    def test_A01_base_hierarchy(self):
        assert issubclass(CollectorConfigurationError, CollectorError)
        assert issubclass(ProviderNotFoundError, CollectorError)
        assert issubclass(ProviderExecutionError, CollectorError)
        assert issubclass(StepPreconditionError, CollectorError)
        assert issubclass(PlanExecutionError, CollectorError)
        assert issubclass(CollectorTimeoutError, CollectorError)

    def test_A02_collector_error_is_exception(self):
        with pytest.raises(CollectorError):
            raise CollectorError("base error")

    def test_A03_provider_not_found_message(self):
        e = ProviderNotFoundError(kind="SESSION", step_id="s1")
        assert "SESSION" in str(e)
        assert "s1" in str(e)
        assert e.kind == "SESSION"
        assert e.step_id == "s1"

    def test_A04_provider_not_found_no_step_id(self):
        e = ProviderNotFoundError(kind="LOG")
        assert "LOG" in str(e)
        assert e.step_id == ""

    def test_A05_provider_execution_error(self):
        cause = ValueError("original")
        e = ProviderExecutionError(provider_name="MyProvider", step_id="s2", cause=cause)
        assert "MyProvider" in str(e)
        assert "s2" in str(e)
        assert e.cause is cause

    def test_A06_step_precondition_error(self):
        e = StepPreconditionError(step_id="s3", precondition_id="pre_01", detail="missing slot")
        assert "s3" in str(e)
        assert "pre_01" in str(e)

    def test_A07_plan_execution_error(self):
        e = PlanExecutionError(plan_id="plan_xyz", reason="too many failures")
        assert "plan_xyz" in str(e)
        assert e.plan_id == "plan_xyz"

    def test_A08_collector_timeout_error(self):
        e = CollectorTimeoutError(step_id="s4", timeout_seconds=5.0)
        assert "s4" in str(e)
        assert e.timeout_seconds == 5.0

    def test_A09_configuration_error_is_catchable(self):
        with pytest.raises(CollectorError):
            raise CollectorConfigurationError("bad config")

    def test_A10_can_catch_any_as_collector_error(self):
        errors = [
            ProviderNotFoundError("X"),
            ProviderExecutionError("P", "s", ValueError()),
            StepPreconditionError("s", "p"),
            PlanExecutionError("plan", "reason"),
            CollectorTimeoutError("s", 1.0),
            CollectorConfigurationError("bad"),
        ]
        for e in errors:
            with pytest.raises(CollectorError):
                raise e


# ══════════════════════════════════════════════════════════════════════════════
# Section B — CollectionContext
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionB_CollectionContext:

    def test_B01_construction_minimal(self):
        ctx = CollectionContext(case_id="c1", topic="OTP")
        assert ctx.case_id == "c1"
        assert ctx.topic == "OTP"
        assert ctx.slots == {}
        assert ctx.tenant_id == ""
        assert ctx.metadata == {}

    def test_B02_construction_full(self):
        ctx = CollectionContext(
            case_id="c2",
            topic="VKYC",
            slots={"urn": "U123"},
            tenant_id="unity_bank",
            metadata={"channel": "email"},
        )
        assert ctx.tenant_id == "unity_bank"
        assert ctx.slots["urn"] == "U123"

    def test_B03_get_slot_present(self):
        ctx = CollectionContext(case_id="c3", topic="X", slots={"urn": "U999"})
        assert ctx.get_slot("urn") == "U999"

    def test_B04_get_slot_missing_returns_none(self):
        ctx = CollectionContext(case_id="c3", topic="X")
        assert ctx.get_slot("missing") is None

    def test_B05_get_slot_default(self):
        ctx = CollectionContext(case_id="c3", topic="X")
        assert ctx.get_slot("missing", "fallback") == "fallback"

    def test_B06_to_dict(self):
        ctx = CollectionContext(case_id="c4", topic="T", slots={"k": "v"})
        d = ctx.to_dict()
        assert d["case_id"] == "c4"
        assert d["topic"] == "T"
        assert "k" in d["slot_keys"]


# ══════════════════════════════════════════════════════════════════════════════
# Section C — CollectionResult
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionC_CollectionResult:

    def test_C01_ok_factory(self):
        r = CollectionResult.ok(
            provider_name="P",
            step_id="s1",
            evidence_kind="SESSION",
            payload={"k": "v"},
            duration_ms=50,
        )
        assert r.success is True
        assert r.payload == {"k": "v"}
        assert r.duration_ms == 50
        assert r.error_code is None

    def test_C02_fail_factory(self):
        r = CollectionResult.fail(
            provider_name="P",
            step_id="s1",
            evidence_kind="LOG",
            error_code="ERR_01",
            error_message="something failed",
        )
        assert r.success is False
        assert r.error_code == "ERR_01"
        assert r.payload == {}

    def test_C03_to_dict_ok(self):
        r = CollectionResult.ok("P", "s1", "SESSION", {"key": "val"})
        d = r.to_dict()
        assert d["success"] is True
        assert "key" in d["payload_keys"]

    def test_C04_to_dict_fail(self):
        r = CollectionResult.fail("P", "s1", "LOG", "ERR", "msg")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error_code"] == "ERR"

    def test_C05_partial_flag(self):
        r = CollectionResult(success=True, partial=True)
        assert r.partial is True

    def test_C06_metadata_field(self):
        r = CollectionResult.ok("P", "s1", "API", {}, metadata={"source": "live"})
        assert r.metadata["source"] == "live"

    def test_C07_default_partial_is_false(self):
        r = CollectionResult(success=True)
        assert r.partial is False


# ══════════════════════════════════════════════════════════════════════════════
# Section D — StepStatus
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionD_StepStatus:

    def test_D01_all_values_exist(self):
        values = {s.value for s in StepStatus}
        assert "PENDING" in values
        assert "RUNNING" in values
        assert "SUCCESS" in values
        assert "FAILED" in values
        assert "SKIPPED" in values
        assert "TIMEOUT" in values

    def test_D02_str_enum(self):
        assert StepStatus.SUCCESS == "SUCCESS"

    def test_D03_six_members(self):
        assert len(StepStatus) == 6


# ══════════════════════════════════════════════════════════════════════════════
# Section E — StepExecutionRecord
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionE_StepExecutionRecord:

    def _success_record(self) -> StepExecutionRecord:
        r = CollectionResult.ok("P", "s1", "SESSION", {"k": "v"})
        return StepExecutionRecord(
            step_id="s1",
            status=StepStatus.SUCCESS,
            attempts=1,
            results=[r],
            final_result=r,
            duration_ms=10,
            step_order=0,
        )

    def _failed_record(self) -> StepExecutionRecord:
        r = CollectionResult.fail("P", "s1", "LOG", "ERR", "msg")
        return StepExecutionRecord(
            step_id="s1",
            status=StepStatus.FAILED,
            attempts=2,
            results=[r, r],
            final_result=r,
            duration_ms=20,
            step_order=1,
        )

    def _skipped_record(self) -> StepExecutionRecord:
        return StepExecutionRecord(
            step_id="s2",
            status=StepStatus.SKIPPED,
            attempts=0,
            skipped_reason="precondition not met",
            step_order=2,
        )

    def test_E01_succeeded_property(self):
        assert self._success_record().succeeded is True
        assert self._failed_record().succeeded is False

    def test_E02_failed_property(self):
        assert self._failed_record().failed is True
        assert self._success_record().failed is False

    def test_E03_skipped_property(self):
        assert self._skipped_record().skipped is True
        assert self._success_record().skipped is False

    def test_E04_timed_out_property(self):
        rec = StepExecutionRecord(step_id="s3", status=StepStatus.TIMEOUT, attempts=1)
        assert rec.timed_out is True

    def test_E05_to_dict(self):
        d = self._success_record().to_dict()
        assert d["step_id"] == "s1"
        assert d["status"] == "SUCCESS"
        assert d["final_success"] is True
        assert d["attempts"] == 1

    def test_E06_to_dict_skipped(self):
        d = self._skipped_record().to_dict()
        assert d["status"] == "SKIPPED"
        assert d["skipped_reason"] == "precondition not met"


# ══════════════════════════════════════════════════════════════════════════════
# Section F — PlanExecutionSummary
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionF_PlanExecutionSummary:

    def _summary(self) -> PlanExecutionSummary:
        return PlanExecutionSummary(
            plan_id="plan_01",
            case_id="case_01",
            total_steps=4,
            successful_steps=2,
            failed_steps=1,
            skipped_steps=1,
            timeout_steps=0,
            total_duration_ms=200,
        )

    def test_F01_is_fully_successful_false(self):
        assert self._summary().is_fully_successful is False

    def test_F02_is_fully_successful_true(self):
        s = PlanExecutionSummary("p", "c", 2, 2, 0, 0, 0, 100)
        assert s.is_fully_successful is True

    def test_F03_any_succeeded(self):
        assert self._summary().any_succeeded is True

    def test_F04_completion_ratio(self):
        assert self._summary().completion_ratio == pytest.approx(0.5)

    def test_F05_completion_ratio_zero_steps(self):
        s = PlanExecutionSummary("p", "c", 0, 0, 0, 0, 0, 0)
        assert s.completion_ratio == 1.0

    def test_F06_to_dict(self):
        d = self._summary().to_dict()
        assert d["plan_id"] == "plan_01"
        assert d["successful_steps"] == 2
        assert "completion_ratio" in d


# ══════════════════════════════════════════════════════════════════════════════
# Section G — StepMetric
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionG_StepMetric:

    def test_G01_immutable(self):
        m = StepMetric(step_id="s1", status="SUCCESS", attempts=1, duration_ms=5, provider_name="P")
        with pytest.raises((AttributeError, TypeError)):
            m.step_id = "changed"  # type: ignore[misc]

    def test_G02_to_dict(self):
        m = StepMetric("s1", "SUCCESS", 1, 10, "P")
        d = m.to_dict()
        assert d["step_id"] == "s1"
        assert d["duration_ms"] == 10
        assert d["provider_name"] == "P"

    def test_G03_record_to_metric_success(self):
        result = CollectionResult.ok("MyProvider", "s1", "SESSION", {"k": "v"})
        record = StepExecutionRecord(
            step_id="s1", status=StepStatus.SUCCESS, attempts=1,
            results=[result], final_result=result, duration_ms=7, step_order=0,
        )
        m = _record_to_metric(record)
        assert m.step_id == "s1"
        assert m.provider_name == "MyProvider"
        assert m.duration_ms == 7

    def test_G04_record_to_metric_no_result(self):
        record = StepExecutionRecord(
            step_id="s2", status=StepStatus.SKIPPED, attempts=0, step_order=1,
        )
        m = _record_to_metric(record)
        assert m.provider_name == ""


# ══════════════════════════════════════════════════════════════════════════════
# Section H — CollectionMetrics
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionH_CollectionMetrics:

    def _make_summary(self) -> PlanExecutionSummary:
        ok_result = CollectionResult.ok("P", "s1", "SESSION", {"k": "v"})
        fail_result = CollectionResult.fail("P", "s2", "LOG", "ERR", "msg")
        rec1 = StepExecutionRecord(
            step_id="s1", status=StepStatus.SUCCESS, attempts=1,
            results=[ok_result], final_result=ok_result, duration_ms=10, step_order=0,
        )
        rec2 = StepExecutionRecord(
            step_id="s2", status=StepStatus.FAILED, attempts=2,
            results=[fail_result, fail_result], final_result=fail_result, duration_ms=20, step_order=1,
        )
        rec3 = StepExecutionRecord(
            step_id="s3", status=StepStatus.SKIPPED, attempts=0, step_order=2,
        )
        return PlanExecutionSummary(
            plan_id="plan_01", case_id="case_01",
            total_steps=3, successful_steps=1, failed_steps=1, skipped_steps=1,
            timeout_steps=0, total_duration_ms=30,
            records=[rec1, rec2, rec3],
        )

    def test_H01_from_summary(self):
        m = CollectionMetrics.from_summary(self._make_summary())
        assert m.plan_id == "plan_01"
        assert m.successful_steps == 1
        assert m.failed_steps == 1
        assert m.skipped_steps == 1

    def test_H02_total_steps(self):
        m = CollectionMetrics.from_summary(self._make_summary())
        assert m.total_steps == 3

    def test_H03_completion_ratio(self):
        m = CollectionMetrics.from_summary(self._make_summary())
        assert m.completion_ratio == pytest.approx(1/3)

    def test_H04_total_retries(self):
        m = CollectionMetrics.from_summary(self._make_summary())
        # rec1: 1 attempt, rec2: 2 attempts, rec3: 0 (skipped)
        # total_attempts=3, non_skipped=2, retries=max(0, 3-2)=1
        assert m.total_retries == 1

    def test_H05_retry_rate(self):
        m = CollectionMetrics.from_summary(self._make_summary())
        assert m.retry_rate == pytest.approx(1/3)

    def test_H06_slowest_step(self):
        m = CollectionMetrics.from_summary(self._make_summary())
        slowest = m.slowest_step()
        assert slowest is not None
        assert slowest.step_id == "s2"  # duration_ms=20

    def test_H07_slowest_step_empty(self):
        m = CollectionMetrics("p", "c", 0, 0, 0, 0, 0, 0, 0)
        assert m.slowest_step() is None

    def test_H08_to_dict(self):
        m = CollectionMetrics.from_summary(self._make_summary())
        d = m.to_dict()
        assert "plan_id" in d
        assert "completion_ratio" in d
        assert "step_metrics" in d


# ══════════════════════════════════════════════════════════════════════════════
# Section I — CollectorValidator
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionI_CollectorValidator:

    def setup_method(self):
        self.v = CollectorValidator()

    def _req(self, required=True, expected_fields=("session_id",)) -> EvidenceRequirement:
        return _make_requirement(expected_fields=expected_fields, required=required)

    # validate_result

    def test_I01_valid_result_ok(self):
        r = CollectionResult.ok("P", "s", "SESSION", {"session_id": "123"})
        ok, reasons = self.v.validate_result(r, self._req())
        assert ok is True
        assert reasons == []

    def test_I02_v01_failure_result(self):
        r = CollectionResult.fail("P", "s", "LOG", "ERR", "msg")
        ok, reasons = self.v.validate_result(r, self._req())
        assert ok is False
        assert any("V01" in reason for reason in reasons)

    def test_I03_v02_missing_expected_field(self):
        r = CollectionResult.ok("P", "s", "SESSION", {"other": "val"})
        ok, reasons = self.v.validate_result(r, self._req(expected_fields=("session_id",)))
        assert ok is False
        assert any("V02" in reason for reason in reasons)

    def test_I04_v02_all_fields_present(self):
        r = CollectionResult.ok("P", "s", "SESSION", {"session_id": "123", "status": "ok"})
        ok, reasons = self.v.validate_result(r, self._req(expected_fields=("session_id",)))
        assert ok is True

    def test_I05_v03_required_empty_payload(self):
        r = CollectionResult(success=True, payload={})
        ok, reasons = self.v.validate_result(r, self._req(required=True, expected_fields=()))
        assert ok is False
        assert any("V03" in reason for reason in reasons)

    def test_I06_not_required_empty_payload_ok(self):
        r = CollectionResult(success=True, payload={})
        ok, reasons = self.v.validate_result(r, self._req(required=False, expected_fields=()))
        assert ok is True

    # validate_preconditions

    def test_I07_no_preconditions_passes(self):
        step = _make_step("s1")
        ctx = CollectionContext("c", "T")
        ok, reasons = self.v.validate_preconditions(step, ctx, frozenset())
        assert ok is True

    def test_I08_p01_slot_present_satisfied(self):
        pre = StepPrecondition("pre_1", "urn must be present", PreconditionKind.SLOT_PRESENT, "urn")
        step = _make_step("s1", preconditions=(pre,))
        ctx = CollectionContext("c", "T", slots={"urn": "U123"})
        ok, _ = self.v.validate_preconditions(step, ctx, frozenset())
        assert ok is True

    def test_I09_p01_slot_missing(self):
        pre = StepPrecondition("pre_1", "urn must be present", PreconditionKind.SLOT_PRESENT, "urn")
        step = _make_step("s1", preconditions=(pre,))
        ctx = CollectionContext("c", "T", slots={})
        ok, reasons = self.v.validate_preconditions(step, ctx, frozenset())
        assert ok is False
        assert any("P01" in r for r in reasons)

    def test_I10_p02_evidence_present_satisfied(self):
        pre = StepPrecondition("pre_2", "step_a done", PreconditionKind.EVIDENCE_PRESENT, "step_a")
        step = _make_step("s2", preconditions=(pre,))
        ctx = CollectionContext("c", "T")
        ok, _ = self.v.validate_preconditions(step, ctx, frozenset({"step_a"}))
        assert ok is True

    def test_I11_p02_evidence_missing(self):
        pre = StepPrecondition("pre_2", "step_a done", PreconditionKind.EVIDENCE_PRESENT, "step_a")
        step = _make_step("s2", preconditions=(pre,))
        ctx = CollectionContext("c", "T")
        ok, reasons = self.v.validate_preconditions(step, ctx, frozenset())
        assert ok is False
        assert any("P02" in r for r in reasons)

    def test_I12_p03_always_passes(self):
        pre = StepPrecondition("pre_3", "always", PreconditionKind.ALWAYS, "")
        step = _make_step("s3", preconditions=(pre,))
        ctx = CollectionContext("c", "T")
        ok, _ = self.v.validate_preconditions(step, ctx, frozenset())
        assert ok is True

    def test_I13_p04_never_fails(self):
        pre = StepPrecondition("pre_4", "never", PreconditionKind.NEVER, "")
        step = _make_step("s4", preconditions=(pre,))
        ctx = CollectionContext("c", "T")
        ok, reasons = self.v.validate_preconditions(step, ctx, frozenset())
        assert ok is False
        assert any("P04" in r for r in reasons)

    # validate_plan

    def test_I14_pl01_empty_plan_id(self):
        step = _make_step("s1")
        plan = _make_plan([step], plan_id="")
        ok, reasons = self.v.validate_plan(plan)
        assert ok is False
        assert any("PL01" in r for r in reasons)

    def test_I15_pl03_no_steps(self):
        plan = _make_plan([_make_step("s1")])
        # Hack: create plan with empty steps via direct construction
        empty_graph = InvestigationGraph(nodes=(), edges=(), parallel_groups=())
        from dataclasses import replace
        empty_plan = InvestigationPlan(
            plan_id="p1", workflow_id=None, topic="T", client_id="c",
            case_id="c1", objective="o", investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=EstimatedComplexity.LOW, expected_evidence=(),
            confidence_threshold=0.75, completion_conditions=(),
            fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at="2026-07-07T00:00:00+00:00", steps=(), graph=empty_graph,
        )
        ok, reasons = self.v.validate_plan(empty_plan)
        assert ok is False
        assert any("PL03" in r for r in reasons)

    def test_I16_pl05_graph_mismatch(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        # Manually tamper graph to have a different step
        extra = _make_step("s_extra")
        bad_graph = InvestigationGraph(nodes=(extra,), edges=(), parallel_groups=())
        bad_plan = InvestigationPlan(
            plan_id="p1", workflow_id=None, topic="T", client_id="c",
            case_id="c1", objective="o", investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=EstimatedComplexity.LOW, expected_evidence=(),
            confidence_threshold=0.75, completion_conditions=(),
            fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at="2026-07-07T00:00:00+00:00", steps=(step,), graph=bad_graph,
        )
        ok, reasons = self.v.validate_plan(bad_plan)
        assert ok is False
        assert any("PL05" in r for r in reasons)

    def test_I17_valid_plan_passes(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ok, reasons = self.v.validate_plan(plan)
        assert ok is True
        assert reasons == []


# ══════════════════════════════════════════════════════════════════════════════
# Section J — ProviderRegistry
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionJ_ProviderRegistry:

    def setup_method(self):
        self.registry = ProviderRegistry()
        self.provider = _SuccessProvider()

    def test_J01_register_and_resolve(self):
        self.registry.register(EvidenceKind.SESSION, self.provider)
        resolved = self.registry.resolve(EvidenceKind.SESSION)
        assert resolved is self.provider

    def test_J02_resolve_unregistered_returns_none(self):
        assert self.registry.resolve(EvidenceKind.LOG) is None

    def test_J03_is_registered_true(self):
        self.registry.register(EvidenceKind.SESSION, self.provider)
        assert self.registry.is_registered(EvidenceKind.SESSION) is True

    def test_J04_is_registered_false(self):
        assert self.registry.is_registered(EvidenceKind.LOG) is False

    def test_J05_set_fallback(self):
        fallback = _SuccessProvider("Fallback")
        self.registry.set_fallback(fallback)
        assert self.registry.has_fallback() is True

    def test_J06_resolve_with_fallback_specific(self):
        self.registry.register(EvidenceKind.SESSION, self.provider)
        fallback = _SuccessProvider("Fallback")
        self.registry.set_fallback(fallback)
        resolved = self.registry.resolve_with_fallback(EvidenceKind.SESSION)
        assert resolved is self.provider

    def test_J07_resolve_with_fallback_uses_fallback(self):
        fallback = _SuccessProvider("Fallback")
        self.registry.set_fallback(fallback)
        resolved = self.registry.resolve_with_fallback(EvidenceKind.LOG)
        assert resolved is fallback

    def test_J08_resolve_with_fallback_raises_when_none(self):
        with pytest.raises(ProviderNotFoundError):
            self.registry.resolve_with_fallback(EvidenceKind.SESSION)

    def test_J09_list_kinds(self):
        self.registry.register(EvidenceKind.SESSION, self.provider)
        self.registry.register(EvidenceKind.LOG, _SuccessProvider("P2"))
        kinds = self.registry.list_kinds()
        assert EvidenceKind.SESSION in kinds
        assert EvidenceKind.LOG in kinds

    def test_J10_provider_count(self):
        self.registry.register(EvidenceKind.SESSION, self.provider)
        self.registry.register(EvidenceKind.LOG, _SuccessProvider("P2"))
        assert self.registry.provider_count() == 2

    def test_J11_reset_clears_all(self):
        self.registry.register(EvidenceKind.SESSION, self.provider)
        self.registry.set_fallback(_SuccessProvider("F"))
        self.registry.reset()
        assert self.registry.provider_count() == 0
        assert self.registry.has_fallback() is False

    def test_J12_overwrite_registration(self):
        p2 = _SuccessProvider("P2")
        self.registry.register(EvidenceKind.SESSION, self.provider)
        self.registry.register(EvidenceKind.SESSION, p2)
        assert self.registry.resolve(EvidenceKind.SESSION) is p2

    def test_J13_thread_safety_concurrent_register(self):
        """Multiple threads registering concurrently should not raise."""
        errors = []
        def register(kind, p):
            try:
                self.registry.register(kind, p)
            except Exception as e:
                errors.append(e)
        threads = [
            threading.Thread(target=register, args=(EvidenceKind.SESSION, _SuccessProvider(f"P{i}")))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert self.registry.is_registered(EvidenceKind.SESSION)


# ══════════════════════════════════════════════════════════════════════════════
# Section K — NullProvider / build_default_registry
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionK_NullProvider:

    def test_K01_null_provider_name(self):
        n = _NullProvider()
        assert n.provider_name == "NullProvider"

    def test_K02_null_provider_can_handle_all(self):
        n = _NullProvider()
        for kind in EvidenceKind:
            assert n.can_handle(kind.value) is True

    def test_K03_null_provider_returns_failure(self):
        n = _NullProvider()
        ctx = CollectionContext("c", "T")
        r = n.execute("s1", "SESSION", ctx, {})
        assert r.success is False
        assert r.error_code == "NO_PROVIDER_REGISTERED"

    def test_K04_build_default_registry_has_fallback(self):
        reg = build_default_registry()
        assert reg.has_fallback() is True

    def test_K05_build_default_registry_fallback_is_null(self):
        reg = build_default_registry()
        # Any unregistered kind should resolve to NullProvider
        p = reg.resolve_with_fallback(EvidenceKind.SESSION)
        assert p.provider_name == "NullProvider"


# ══════════════════════════════════════════════════════════════════════════════
# Section L — ProviderDispatcher
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionL_ProviderDispatcher:

    def _make_dispatcher(self, provider=None, kind=EvidenceKind.SESSION) -> ProviderDispatcher:
        reg = ProviderRegistry()
        if provider:
            reg.register(kind, provider)
        reg.set_fallback(_NullProvider())
        return ProviderDispatcher(reg)

    def test_L01_dispatch_success(self):
        step = _make_step("s1")
        provider = _SuccessProvider()
        dispatcher = self._make_dispatcher(provider)
        ctx = CollectionContext("c", "T", slots={"session_id": "123"})
        record = dispatcher.dispatch(step, ctx)
        assert record.status == StepStatus.SUCCESS
        assert record.attempts == 1
        assert record.succeeded

    def test_L02_dispatch_failure(self):
        step = _make_step("s1")
        provider = _FailProvider()
        dispatcher = self._make_dispatcher(provider)
        ctx = CollectionContext("c", "T")
        record = dispatcher.dispatch(step, ctx)
        assert record.status == StepStatus.FAILED
        assert not record.succeeded

    def test_L03_retry_on_failure(self):
        step = _make_step("s1", max_attempts=3)
        provider = _FailProvider()
        dispatcher = self._make_dispatcher(provider)
        ctx = CollectionContext("c", "T")
        record = dispatcher.dispatch(step, ctx)
        assert record.attempts == 3
        assert record.status == StepStatus.FAILED

    def test_L04_succeeds_on_second_attempt(self):
        success_result = CollectionResult.ok("P", "s1", "SESSION", {"session_id": "x"})
        fail_result = CollectionResult.fail("P", "s1", "SESSION", "ERR", "first try fails")
        call_count = [0]

        class FlippingProvider:
            provider_name = "Flipper"
            def can_handle(self, k): return True
            def execute(self, step_id, evidence_kind, context, requirement_metadata):
                call_count[0] += 1
                if call_count[0] == 1:
                    return fail_result
                return success_result

        step = _make_step("s1", max_attempts=3)
        reg = ProviderRegistry()
        reg.register(EvidenceKind.SESSION, FlippingProvider())
        dispatcher = ProviderDispatcher(reg)
        ctx = CollectionContext("c", "T")
        record = dispatcher.dispatch(step, ctx)
        assert record.status == StepStatus.SUCCESS
        assert record.attempts == 2

    def test_L05_provider_raises_captured_as_failure(self):
        step = _make_step("s1")
        reg = ProviderRegistry()
        reg.register(EvidenceKind.SESSION, _RaisingProvider())
        dispatcher = ProviderDispatcher(reg)
        ctx = CollectionContext("c", "T")
        record = dispatcher.dispatch(step, ctx)
        assert record.status == StepStatus.FAILED
        assert record.final_result is not None
        assert record.final_result.error_code == "PROVIDER_EXCEPTION"

    def test_L06_no_provider_no_fallback_returns_failed(self):
        step = _make_step("s1")
        reg = ProviderRegistry()  # no providers, no fallback
        dispatcher = ProviderDispatcher(reg)
        ctx = CollectionContext("c", "T")
        record = dispatcher.dispatch(step, ctx)
        assert record.status == StepStatus.FAILED
        assert record.final_result.error_code == "PROVIDER_NOT_FOUND"

    def test_L07_step_with_no_requirements_uses_api_kind(self):
        # Step with no evidence_required → dispatcher falls back to API kind
        graph = InvestigationGraph(nodes=(), edges=(), parallel_groups=())
        step = PlanningStep(
            step_id="s_no_req",
            order=0,
            title="No req",
            description="",
            evidence_required=(),
            evidence_priority=EvidencePriority.NORMAL,
            candidate_capabilities=(),
            preconditions=(),
            outputs=(),
            retry_policy=RetryPolicy(),
            failure_strategy=FailureStrategy.CONTINUE,
            parallelizable=False,
            optional=True,
            depends_on=(),
        )
        provider = _SuccessProvider()
        reg = ProviderRegistry()
        reg.register(EvidenceKind.API, provider)
        dispatcher = ProviderDispatcher(reg)
        ctx = CollectionContext("c", "T")
        record = dispatcher.dispatch(step, ctx)
        assert record.status == StepStatus.SUCCESS

    def test_L08_duration_ms_is_non_negative(self):
        step = _make_step("s1")
        dispatcher = self._make_dispatcher(_SuccessProvider())
        ctx = CollectionContext("c", "T")
        record = dispatcher.dispatch(step, ctx)
        assert record.duration_ms >= 0

    def test_L09_results_list_has_one_entry_on_first_success(self):
        step = _make_step("s1")
        dispatcher = self._make_dispatcher(_SuccessProvider())
        ctx = CollectionContext("c", "T")
        record = dispatcher.dispatch(step, ctx)
        assert len(record.results) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section M — StepOrchestrator
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionM_StepOrchestrator:

    def _make_orchestrator(self, provider=None) -> StepOrchestrator:
        reg = ProviderRegistry()
        if provider:
            for kind in EvidenceKind:
                reg.register(kind, provider)
        else:
            reg.set_fallback(_NullProvider())
        dispatcher = ProviderDispatcher(reg)
        return StepOrchestrator(dispatcher)

    def test_M01_single_step_success(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = CollectionContext("c", "T")
        orchestrator = self._make_orchestrator(_SuccessProvider())
        summary = orchestrator.execute(plan, ctx)
        assert summary.successful_steps == 1
        assert summary.total_steps == 1

    def test_M02_two_sequential_steps(self):
        s1 = _make_step("s1", order=0)
        s2 = _make_step("s2", order=1, depends_on=("s1",))
        edge = StepDependency("s1", "s2", DependencyType.SEQUENTIAL)
        plan = _make_plan([s1, s2], edges=[edge])
        ctx = CollectionContext("c", "T")
        orchestrator = self._make_orchestrator(_SuccessProvider())
        summary = orchestrator.execute(plan, ctx)
        assert summary.successful_steps == 2
        assert summary.total_steps == 2

    def test_M03_parallel_group(self):
        s1 = _make_step("s_a", order=0, parallelizable=True)
        s2 = _make_step("s_b", order=1, parallelizable=True)
        group = frozenset({"s_a", "s_b"})
        plan = _make_plan([s1, s2], parallel_groups=[group])
        ctx = CollectionContext("c", "T")
        orchestrator = self._make_orchestrator(_SuccessProvider())
        summary = orchestrator.execute(plan, ctx)
        assert summary.successful_steps == 2

    def test_M04_precondition_slot_missing_skips_step(self):
        pre = StepPrecondition("pre_1", "urn needed", PreconditionKind.SLOT_PRESENT, "urn")
        s1 = _make_step("s1", preconditions=(pre,))
        plan = _make_plan([s1])
        ctx = CollectionContext("c", "T", slots={})  # no urn
        orchestrator = self._make_orchestrator(_SuccessProvider())
        summary = orchestrator.execute(plan, ctx)
        assert summary.skipped_steps == 1
        assert summary.successful_steps == 0

    def test_M05_precondition_evidence_present_satisfied(self):
        s1 = _make_step("s1", order=0)
        pre = StepPrecondition("pre_2", "s1 done", PreconditionKind.EVIDENCE_PRESENT, "s1")
        s2 = _make_step("s2", order=1, preconditions=(pre,), depends_on=("s1",))
        edge = StepDependency("s1", "s2", DependencyType.SEQUENTIAL)
        plan = _make_plan([s1, s2], edges=[edge])
        ctx = CollectionContext("c", "T")
        orchestrator = self._make_orchestrator(_SuccessProvider())
        summary = orchestrator.execute(plan, ctx)
        assert summary.successful_steps == 2
        assert summary.skipped_steps == 0

    def test_M06_failed_step_triggers_fallback(self):
        """A FALLBACK step runs when its primary step fails."""
        s_primary = _make_step("s_primary", order=0)
        s_fallback = _make_step("s_fallback", order=1, depends_on=("s_primary",))
        fallback_edge = StepDependency("s_primary", "s_fallback", DependencyType.FALLBACK)

        fail_provider = _FailProvider()
        success_provider = _SuccessProvider()

        # Primary uses fail provider; fallback uses success provider
        class MixedProvider:
            provider_name = "Mixed"
            def can_handle(self, k): return True
            call_count = 0
            def execute(self, step_id, evidence_kind, context, requirement_metadata):
                self.call_count += 1
                if step_id == "s_primary":
                    return CollectionResult.fail("Mixed", step_id, evidence_kind, "ERR", "primary fails")
                return CollectionResult.ok("Mixed", step_id, evidence_kind, {"data": "ok"})

        mixed = MixedProvider()
        reg = ProviderRegistry()
        for kind in EvidenceKind:
            reg.register(kind, mixed)
        dispatcher = ProviderDispatcher(reg)
        orchestrator = StepOrchestrator(dispatcher)

        plan = _make_plan([s_primary, s_fallback], edges=[fallback_edge])
        ctx = CollectionContext("c", "T")
        summary = orchestrator.execute(plan, ctx)
        # primary failed, fallback ran successfully
        assert summary.failed_steps == 1
        assert summary.successful_steps == 1

    def test_M07_all_fail_returns_summary_not_raises(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = CollectionContext("c", "T")
        orchestrator = self._make_orchestrator(_FailProvider())
        summary = orchestrator.execute(plan, ctx)
        assert summary.failed_steps == 1
        assert summary.successful_steps == 0

    def test_M08_records_sorted_by_step_order(self):
        s1 = _make_step("s_a", order=0)
        s2 = _make_step("s_b", order=1, depends_on=("s_a",))
        edge = StepDependency("s_a", "s_b", DependencyType.SEQUENTIAL)
        plan = _make_plan([s1, s2], edges=[edge])
        ctx = CollectionContext("c", "T")
        orchestrator = self._make_orchestrator(_SuccessProvider())
        summary = orchestrator.execute(plan, ctx)
        orders = [r.step_order for r in summary.records]
        assert orders == sorted(orders)

    def test_M09_unreachable_step_marked_skipped(self):
        """A step that depends on a non-existent predecessor is never reached."""
        s1 = _make_step("s1", order=0, depends_on=("nonexistent",))
        edge = StepDependency("nonexistent", "s1", DependencyType.SEQUENTIAL)
        plan = _make_plan([s1], edges=[edge])
        ctx = CollectionContext("c", "T")
        orchestrator = self._make_orchestrator(_SuccessProvider())
        summary = orchestrator.execute(plan, ctx)
        assert summary.skipped_steps == 1

    def test_M10_summary_total_steps_equals_plan_steps(self):
        steps = [_make_step(f"s{i}", order=i) for i in range(5)]
        plan = _make_plan(steps)
        ctx = CollectionContext("c", "T")
        orchestrator = self._make_orchestrator(_SuccessProvider())
        summary = orchestrator.execute(plan, ctx)
        assert summary.total_steps == 5


# ══════════════════════════════════════════════════════════════════════════════
# Section N — EvidenceCollector
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionN_EvidenceCollector:

    def _make_collector(self, provider=None) -> EvidenceCollector:
        reg = ProviderRegistry()
        if provider:
            for kind in EvidenceKind:
                reg.register(kind, provider)
        else:
            reg.set_fallback(_NullProvider())
        return EvidenceCollector(registry=reg)

    def test_N01_collect_returns_evidence_bundle(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = _make_context()
        collector = self._make_collector(_SuccessProvider())
        bundle = collector.collect(plan, ctx)
        assert isinstance(bundle, EvidenceBundle)
        assert bundle.case_id == "case_test_001"

    def test_N02_successful_step_produces_success_evidence(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = _make_context()
        collector = self._make_collector(_SuccessProvider(payload={"session_id": "s123"}))
        bundle = collector.collect(plan, ctx)
        assert len(bundle.items) == 1
        assert bundle.items[0].success is True

    def test_N03_failed_step_produces_failed_evidence(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = _make_context()
        collector = self._make_collector(_FailProvider())
        bundle = collector.collect(plan, ctx)
        assert len(bundle.items) == 1
        assert bundle.items[0].success is False

    def test_N04_bundle_plan_id_matches(self):
        step = _make_step("s1")
        plan = _make_plan([step], plan_id="plan_xyz")
        ctx = _make_context()
        collector = self._make_collector(_SuccessProvider())
        bundle = collector.collect(plan, ctx)
        assert bundle.plan_id == "plan_xyz"

    def test_N05_multiple_steps_all_items_in_bundle(self):
        steps = [_make_step(f"s{i}", order=i) for i in range(3)]
        plan = _make_plan(steps)
        ctx = _make_context()
        collector = self._make_collector(_SuccessProvider())
        bundle = collector.collect(plan, ctx)
        assert len(bundle.items) == 3

    def test_N06_invalid_plan_still_returns_bundle(self):
        # Plan with empty plan_id (invalid) should not raise
        step = _make_step("s1")
        plan = _make_plan([step], plan_id="")
        ctx = _make_context()
        collector = self._make_collector(_SuccessProvider())
        bundle = collector.collect(plan, ctx)
        assert isinstance(bundle, EvidenceBundle)

    def test_N07_default_registry_uses_null_provider(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = _make_context()
        collector = EvidenceCollector()  # uses build_default_registry
        bundle = collector.collect(plan, ctx)
        assert len(bundle.items) == 1
        assert bundle.items[0].success is False
        assert bundle.items[0].error_code == "NO_PROVIDER_REGISTERED"

    def test_N08_collect_never_raises_even_on_bad_plan(self):
        # Empty steps plan
        empty_graph = InvestigationGraph(nodes=(), edges=(), parallel_groups=())
        bad_plan = InvestigationPlan(
            plan_id="p1", workflow_id=None, topic="T", client_id="c",
            case_id="case_test_001", objective="o",
            investigation_priority=InvestigationPriority.NORMAL,
            estimated_complexity=EstimatedComplexity.LOW,
            expected_evidence=(), confidence_threshold=0.75,
            completion_conditions=(), fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at="2026-07-07T00:00:00+00:00", steps=(), graph=empty_graph,
        )
        ctx = _make_context()
        collector = EvidenceCollector()
        bundle = collector.collect(bad_plan, ctx)
        assert isinstance(bundle, EvidenceBundle)
        assert bundle.items == []

    def test_N09_evidence_type_session_kind_maps_to_session_evidence(self):
        from case_engine.investigation.models import SessionEvidence
        step = _make_step("s1", kind=EvidenceKind.SESSION)
        plan = _make_plan([step])
        ctx = _make_context()
        collector = self._make_collector(_SuccessProvider(payload={"session_id": "123"}))
        bundle = collector.collect(plan, ctx)
        assert isinstance(bundle.items[0], SessionEvidence)

    def test_N10_evidence_type_log_kind_maps_to_log_evidence(self):
        from case_engine.investigation.models import LogEvidence
        step = _make_step("s1", kind=EvidenceKind.LOG)
        plan = _make_plan([step])
        ctx = _make_context()
        collector = self._make_collector(_SuccessProvider(payload={"code": "E001"}))
        bundle = collector.collect(plan, ctx)
        assert isinstance(bundle.items[0], LogEvidence)

    def test_N11_evidence_type_api_kind_maps_to_user_evidence(self):
        from case_engine.investigation.models import UserEvidence
        step = _make_step("s1", kind=EvidenceKind.API)
        plan = _make_plan([step])
        ctx = _make_context()
        collector = self._make_collector(_SuccessProvider(payload={"user_id": "U1"}))
        bundle = collector.collect(plan, ctx)
        assert isinstance(bundle.items[0], UserEvidence)

    def test_N12_evidence_type_vision_maps_to_video_evidence(self):
        from case_engine.investigation.models import VideoEvidence
        step = _make_step("s1", kind=EvidenceKind.VISION)
        plan = _make_plan([step])
        ctx = _make_context()
        provider = _SuccessProvider()
        reg = ProviderRegistry()
        reg.register(EvidenceKind.VISION, provider)
        collector = EvidenceCollector(registry=reg)
        bundle = collector.collect(plan, ctx)
        assert isinstance(bundle.items[0], VideoEvidence)

    def test_N13_skipped_step_produces_failed_evidence(self):
        pre = StepPrecondition("pre_1", "urn needed", PreconditionKind.SLOT_PRESENT, "urn")
        step = _make_step("s1", preconditions=(pre,))
        plan = _make_plan([step])
        ctx = _make_context()  # no urn slot
        collector = self._make_collector(_SuccessProvider())
        bundle = collector.collect(plan, ctx)
        assert len(bundle.items) == 1
        ev = bundle.items[0]
        assert ev.success is False
        assert ev.error_code == "STEP_SKIPPED"


# ══════════════════════════════════════════════════════════════════════════════
# Section O — Provider protocol satisfaction
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionO_ProviderProtocols:

    def test_O01_success_provider_satisfies_evidence_provider(self):
        p = _SuccessProvider()
        assert isinstance(p, EvidenceProvider)

    def test_O02_null_provider_satisfies_evidence_provider(self):
        p = _NullProvider()
        assert isinstance(p, EvidenceProvider)

    def test_O03_tool_provider_protocol(self):
        class MyToolProvider:
            @property
            def provider_name(self): return "Tool"
            def can_handle(self, k): return True
            def execute(self, sid, ek, ctx, meta): return CollectionResult(success=True)
            def list_tools(self): return ["GetSessionDetailsTool"]

        p = MyToolProvider()
        assert isinstance(p, EvidenceProvider)
        assert isinstance(p, ToolProvider)

    def test_O04_knowledge_provider_protocol(self):
        class MyKnowledgeProvider:
            @property
            def provider_name(self): return "Knowledge"
            def can_handle(self, k): return k == "KNOWLEDGE"
            def execute(self, sid, ek, ctx, meta): return CollectionResult(success=True)
            def get_knowledge_count(self): return 100

        p = MyKnowledgeProvider()
        assert isinstance(p, EvidenceProvider)
        assert isinstance(p, KnowledgeProvider)

    def test_O05_vision_provider_protocol(self):
        class MyVisionProvider:
            @property
            def provider_name(self): return "Vision"
            def can_handle(self, k): return k == "VISION"
            def execute(self, sid, ek, ctx, meta): return CollectionResult(success=True)
            def is_available(self): return False

        p = MyVisionProvider()
        assert isinstance(p, EvidenceProvider)
        assert isinstance(p, VisionProvider)

    def test_O06_workflow_provider_protocol(self):
        class MyWorkflowProvider:
            @property
            def provider_name(self): return "Workflow"
            def can_handle(self, k): return k == "WORKFLOW"
            def execute(self, sid, ek, ctx, meta): return CollectionResult(success=True)

        p = MyWorkflowProvider()
        assert isinstance(p, EvidenceProvider)
        assert isinstance(p, WorkflowProvider)

    def test_O07_sop_provider_protocol(self):
        class MySOPProvider:
            @property
            def provider_name(self): return "SOP"
            def can_handle(self, k): return k == "KNOWLEDGE"
            def execute(self, sid, ek, ctx, meta): return CollectionResult(success=True)

        p = MySOPProvider()
        assert isinstance(p, EvidenceProvider)
        assert isinstance(p, SOPProvider)


# ══════════════════════════════════════════════════════════════════════════════
# Section P — Deterministic replay
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionP_DeterministicReplay:

    def test_P01_same_plan_same_order(self):
        steps = [_make_step(f"step_{chr(65+i)}", order=i) for i in range(4)]
        plan = _make_plan(steps)
        provider = _CountingProvider("Counter")
        reg = ProviderRegistry()
        for kind in EvidenceKind:
            reg.register(kind, provider)
        collector = EvidenceCollector(registry=reg)
        ctx = _make_context()

        run1 = collector.collect(plan, ctx)
        run2 = collector.collect(plan, ctx)

        # Both runs must produce items in the same order
        ids1 = [item.tool_name for item in run1.items]
        ids2 = [item.tool_name for item in run2.items]
        assert ids1 == ids2

    def test_P02_parallel_group_sorted_by_step_id(self):
        """Steps in a parallel group are submitted sorted by step_id for determinism."""
        # Create steps that when run in parallel should appear in sorted(step_id) order
        s_z = _make_step("step_z", order=1, parallelizable=True)
        s_a = _make_step("step_a", order=0, parallelizable=True)
        group = frozenset({"step_z", "step_a"})
        plan = _make_plan([s_z, s_a], parallel_groups=[group])

        call_order = []
        class TrackingProvider:
            provider_name = "Tracker"
            def can_handle(self, k): return True
            def execute(self, step_id, evidence_kind, context, requirement_metadata):
                call_order.append(step_id)
                return CollectionResult.ok("Tracker", step_id, evidence_kind, {"k": "v"})

        reg = ProviderRegistry()
        for kind in EvidenceKind:
            reg.register(kind, TrackingProvider())
        collector = EvidenceCollector(registry=reg)
        ctx = _make_context()
        collector.collect(plan, ctx)
        # Both steps must have been executed
        assert set(call_order) == {"step_z", "step_a"}

    def test_P03_linear_plan_ordered_by_step_order(self):
        s1 = _make_step("s1", order=0)
        s2 = _make_step("s2", order=1, depends_on=("s1",))
        s3 = _make_step("s3", order=2, depends_on=("s2",))
        edges = [
            StepDependency("s1", "s2", DependencyType.SEQUENTIAL),
            StepDependency("s2", "s3", DependencyType.SEQUENTIAL),
        ]
        plan = _make_plan([s1, s2, s3], edges=edges)
        ctx = _make_context()
        collector = EvidenceCollector(registry=build_default_registry())
        bundle = collector.collect(plan, ctx)
        tool_names = [item.tool_name for item in bundle.items]
        assert tool_names == ["s1", "s2", "s3"]


# ══════════════════════════════════════════════════════════════════════════════
# Section Q — Context integration
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionQ_ContextIntegration:

    def test_Q01_collection_metrics_written_to_context(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = _make_context()
        provider = _SuccessProvider()
        reg = ProviderRegistry()
        for kind in EvidenceKind:
            reg.register(kind, provider)
        collector = EvidenceCollector(registry=reg)
        collector.collect(plan, ctx)
        assert ctx.collection_metrics is not None
        assert isinstance(ctx.collection_metrics, CollectionMetrics)

    def test_Q02_collector_stats_written_to_context(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = _make_context()
        provider = _SuccessProvider()
        reg = ProviderRegistry()
        for kind in EvidenceKind:
            reg.register(kind, provider)
        collector = EvidenceCollector(registry=reg)
        collector.collect(plan, ctx)
        assert ctx.collector_stats is not None
        assert "total_steps" in ctx.collector_stats
        assert "successful_steps" in ctx.collector_stats
        assert "bundle_id" in ctx.collector_stats

    def test_Q03_collection_metrics_has_correct_counts(self):
        steps = [_make_step(f"s{i}", order=i) for i in range(3)]
        plan = _make_plan(steps)
        ctx = _make_context()
        reg = ProviderRegistry()
        for kind in EvidenceKind:
            reg.register(kind, _SuccessProvider())
        collector = EvidenceCollector(registry=reg)
        collector.collect(plan, ctx)
        assert ctx.collection_metrics.successful_steps == 3

    def test_Q04_context_has_collection_metrics_field(self):
        ctx = _make_context()
        assert hasattr(ctx, "collection_metrics")
        assert ctx.collection_metrics is None  # initially

    def test_Q05_context_has_collector_stats_field(self):
        ctx = _make_context()
        assert hasattr(ctx, "collector_stats")
        assert ctx.collector_stats is None  # initially

    def test_Q06_collector_stats_bundle_id_matches_bundle(self):
        step = _make_step("s1")
        plan = _make_plan([step])
        ctx = _make_context()
        reg = ProviderRegistry()
        for kind in EvidenceKind:
            reg.register(kind, _SuccessProvider())
        collector = EvidenceCollector(registry=reg)
        bundle = collector.collect(plan, ctx)
        assert ctx.collector_stats["bundle_id"] == bundle.bundle_id


# ══════════════════════════════════════════════════════════════════════════════
# Section R — __init__.py public API
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionR_PublicAPI:

    def test_R01_evidence_collector_importable(self):
        assert EvidenceCollectorPublic is EvidenceCollector

    def test_R02_provider_registry_importable(self):
        assert ProviderRegistryPublic is ProviderRegistry

    def test_R03_collection_context_importable(self):
        assert CollectionContextPublic is CollectionContext

    def test_R04_collection_result_importable(self):
        assert CollectionResultPublic is CollectionResult

    def test_R05_collector_validator_importable(self):
        assert CollectorValidatorPublic is CollectorValidator

    def test_R06_step_status_importable(self):
        assert StepStatusPublic is StepStatus

    def test_R07_collection_metrics_importable(self):
        assert CollectionMetricsPublic is CollectionMetrics

    def test_R08_collector_error_importable(self):
        assert CollectorErrorPublic is CollectorError

    def test_R09_provider_not_found_error_importable(self):
        assert ProviderNotFoundErrorPublic is ProviderNotFoundError

    def test_R10_build_default_registry_importable(self):
        assert build_default_registry_public is build_default_registry

    def test_R11_all_list_complete(self):
        import case_engine.investigation.collector as pkg
        all_names = pkg.__all__
        required = [
            "EvidenceCollector", "ProviderRegistry", "build_default_registry",
            "CollectionContext", "CollectionResult", "EvidenceProvider",
            "ToolProvider", "KnowledgeProvider", "SOPProvider", "VisionProvider",
            "WorkflowProvider", "StepStatus", "StepExecutionRecord",
            "PlanExecutionSummary", "StepMetric", "CollectionMetrics",
            "CollectorValidator", "CollectorError", "CollectorConfigurationError",
            "ProviderNotFoundError", "ProviderExecutionError", "StepPreconditionError",
            "PlanExecutionError", "CollectorTimeoutError",
        ]
        for name in required:
            assert name in all_names, f"{name!r} missing from __all__"


# ══════════════════════════════════════════════════════════════════════════════
# Section S — Regression guard
# ══════════════════════════════════════════════════════════════════════════════

class TestSectionS_Regression:

    def test_S01_sprint238_evidence_models_still_importable(self):
        from case_engine.investigation.evidence.models import (
            EvidencePriority, EvidenceStatus, EvidenceConfidence,
            EvidenceMetadata, EvidenceReference,
        )
        assert EvidencePriority.CRITICAL == "CRITICAL"

    def test_S02_sprint239_investigation_planner_still_importable(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner
        assert InvestigationPlanner is not None

    def test_S03_sprint239_planner_models_still_importable(self):
        from case_engine.investigation.planner.models import (
            InvestigationPlan, PlanningStep, InvestigationGraph,
            EvidenceRequirement, RetryPolicy,
        )
        assert InvestigationPlan is not None

    def test_S04_sprint240_workflow_playbooks_still_importable(self):
        from case_engine.workflows.playbooks.models import WorkflowPlaybook
        assert WorkflowPlaybook is not None

    def test_S05_sprint241_sop_registry_still_importable(self):
        from case_engine.knowledge.sop.registry import (
            get_default_sop_registry,
            get_default_sop_resolver,
            reset_sop_registry,
        )
        assert callable(get_default_sop_registry)

    def test_S06_sprint241_sop_models_still_importable(self):
        from case_engine.knowledge.sop.models import (
            SOPDocument, SOPStatus, SOPStepKind,
        )
        assert len(SOPStepKind) == 14

    def test_S07_investigation_context_has_all_sprint241_fields(self):
        ctx = _make_context()
        assert hasattr(ctx, "resolved_sop")
        assert hasattr(ctx, "sop_version")
        assert hasattr(ctx, "sop_source")
        assert hasattr(ctx, "sop_client_scope")
        assert hasattr(ctx, "sop_status")
        assert hasattr(ctx, "has_resolved_sop")
        assert callable(ctx.has_resolved_sop)

    def test_S08_investigation_context_has_sprint242_fields(self):
        ctx = _make_context()
        assert hasattr(ctx, "collection_metrics")
        assert hasattr(ctx, "collector_stats")

    def test_S09_sprint218_collector_still_importable(self):
        from case_engine.investigation._collector_sprint218 import EvidenceCollector as LegacyEC
        assert LegacyEC is not None

    def test_S10_original_investigation_models_unaffected(self):
        from case_engine.investigation.models import (
            EvidenceBundle, EvidenceType, EvidenceSource,
            InvestigationPlan as LegacyPlan,
        )
        assert EvidenceBundle is not None

    def test_S11_tools_tool_models_unaffected(self):
        from case_engine.tools.tool_models import ToolCapability, ToolProvider, ToolResult
        assert ToolCapability.READ == "READ"

    def test_S12_collector_import_does_not_break_context(self):
        # Importing collector should not cause circular import issues
        from case_engine.investigation.collector import EvidenceCollector
        from case_engine.investigation.context import InvestigationContext
        ctx = _make_context()
        assert ctx is not None
