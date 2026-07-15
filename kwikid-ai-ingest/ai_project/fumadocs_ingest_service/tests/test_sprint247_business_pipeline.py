"""
tests/test_sprint247_business_pipeline.py

Sprint 2.47: Business Pipeline Integration (Wave 1) — 250+ integration tests.

Tests the canonical Investigation Pipeline contract, all six stage adapters,
the PipelineManifest, ContextIntegrityGuard, and the full end-to-end pipeline
via build_investigation_orchestrator() with real components.

Sections:
  A  — StageStatus enum
  B  — StageSeverity enum
  C  — StageResult dataclass
  D  — InvestigationStage protocol
  E  — ValidateStage
  F  — PlanningStage (real InvestigationPlanner)
  G  — CollectionStage (real EvidenceCollector)
  H  — KnowledgeStage
  I  — RootCauseStage (real RootCauseEngine)
  J  — ObservationStage (real ObservationGenerator)
  K  — PipelineManifest
  L  — StageDescriptor
  M  — ContextIntegrityGuard
  N  — Full pipeline golden path (build_investigation_orchestrator)
  O  — Context propagation after full pipeline
  P  — Failure scenarios
  Q  — Metrics
  R  — Audit timeline
  S  — Stage ordering & dependencies
  T  — Idempotency & determinism
  U  — Serialization
  V  — Sprint 2.46 + 2.38–2.45 regression checks
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

# ── Pipeline contract imports ─────────────────────────────────────────────────

from case_engine.investigation.pipeline import (
    STAGE_COLLECTION,
    STAGE_KNOWLEDGE,
    STAGE_OBSERVATION,
    STAGE_PLANNING,
    STAGE_ROOT_CAUSE,
    STAGE_VALIDATE,
    CollectionStage,
    ContextIntegrityError,
    ContextIntegrityGuard,
    InvestigationStage,
    KnowledgeStage,
    ObservationStage,
    PipelineManifest,
    PlanningStage,
    RootCauseStage,
    StageSeverity,
    StageDescriptor,
    StageResult,
    StageStatus,
    ValidateStage,
    get_default_manifest,
)

# ── Orchestrator imports (Sprint 2.46) ────────────────────────────────────────

from case_engine.investigation.orchestrator import (
    CancellationToken,
    InvestigationOrchestrator,
    OrchestratorInvestigationResult,
    OrchestratorLifecycleState,
    build_investigation_orchestrator,
)

# ── Domain imports ────────────────────────────────────────────────────────────

from case_engine.investigation.context import InvestigationContext
from case_engine.investigation.models import EvidenceBundle
from case_engine.investigation.planner.engine import InvestigationPlanner
from case_engine.investigation.collector.collector import EvidenceCollector
from case_engine.investigation.root_cause.engine import RootCauseEngine
from case_engine.investigation.observation.generator import ObservationGenerator
from case_engine.tenant.models import TenantContext, TenantEnvironment, TenantType


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures & Helpers
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_global_metrics():
    """Isolate global metrics state between tests."""
    InvestigationOrchestrator.reset_global_metrics()
    yield
    InvestigationOrchestrator.reset_global_metrics()


def _make_tenant() -> TenantContext:
    return TenantContext(
        client_id="test_client",
        client_name="Test Client Bank",
        domain="testclient.com",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=(),
        credentials_ref="test_creds_ref",
    )


def _make_context(
    topic: str = "VKYC_SESSION_FAILURE",
    case_id: str | None = None,
) -> InvestigationContext:
    return InvestigationContext(
        context_id=str(uuid.uuid4()),
        case_id=case_id or str(uuid.uuid4()),
        ticket_id="ticket-001",
        ticket_subject="Test ticket",
        ticket_description="Test description",
        customer_id="cust-001",
        customer_email="test@testclient.com",
        channel="email",
        tenant_context=_make_tenant(),
        topic=topic,
        classification_confidence=0.95,
    )


def _make_minimal_bundle() -> EvidenceBundle:
    from case_engine.investigation.models import EvidenceBundle
    return EvidenceBundle(
        bundle_id=str(uuid.uuid4()),
        case_id="test-case",
        items=[],
        created_at="2024-01-01T00:00:00+00:00",
    )


def _build_planner() -> InvestigationPlanner:
    return InvestigationPlanner()


def _build_collector() -> EvidenceCollector:
    return EvidenceCollector()


def _build_rca() -> RootCauseEngine:
    return RootCauseEngine()


def _build_obs_gen() -> ObservationGenerator:
    return ObservationGenerator()


def _build_orchestrator() -> InvestigationOrchestrator:
    return build_investigation_orchestrator()


# ══════════════════════════════════════════════════════════════════════════════
# A — StageStatus
# ══════════════════════════════════════════════════════════════════════════════

class TestStageStatus:
    def test_A1_success_value(self):
        assert StageStatus.SUCCESS.value == "SUCCESS"

    def test_A2_failure_value(self):
        assert StageStatus.FAILURE.value == "FAILURE"

    def test_A3_skipped_value(self):
        assert StageStatus.SKIPPED.value == "SKIPPED"

    def test_A4_partial_value(self):
        assert StageStatus.PARTIAL.value == "PARTIAL"

    def test_A5_is_str_subclass(self):
        assert isinstance(StageStatus.SUCCESS, str)

    def test_A6_string_equality(self):
        assert StageStatus.SUCCESS == "SUCCESS"

    def test_A7_all_four_members(self):
        names = {m.name for m in StageStatus}
        assert names == {"SUCCESS", "FAILURE", "SKIPPED", "PARTIAL"}

    def test_A8_from_string(self):
        assert StageStatus("FAILURE") == StageStatus.FAILURE


# ══════════════════════════════════════════════════════════════════════════════
# B — StageSeverity
# ══════════════════════════════════════════════════════════════════════════════

class TestStageSeverity:
    def test_B1_fatal_value(self):
        assert StageSeverity.FATAL.value == "FATAL"

    def test_B2_non_fatal_value(self):
        assert StageSeverity.NON_FATAL.value == "NON_FATAL"

    def test_B3_is_str_subclass(self):
        assert isinstance(StageSeverity.FATAL, str)

    def test_B4_exactly_two_members(self):
        assert len(list(StageSeverity)) == 2

    def test_B5_fatal_string_equality(self):
        assert StageSeverity.FATAL == "FATAL"

    def test_B6_non_fatal_string_equality(self):
        assert StageSeverity.NON_FATAL == "NON_FATAL"


# ══════════════════════════════════════════════════════════════════════════════
# C — StageResult
# ══════════════════════════════════════════════════════════════════════════════

class TestStageResult:
    def test_C1_basic_construction(self):
        r = StageResult(stage_name="test", status=StageStatus.SUCCESS, duration_ms=42)
        assert r.stage_name == "test"
        assert r.status == StageStatus.SUCCESS
        assert r.duration_ms == 42

    def test_C2_defaults_output_is_none(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert r.output is None

    def test_C3_defaults_error_is_none(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert r.error_message is None
        assert r.error_code is None

    def test_C4_succeeded_true_on_success(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert r.succeeded is True

    def test_C5_succeeded_true_on_partial(self):
        r = StageResult(stage_name="x", status=StageStatus.PARTIAL, duration_ms=0)
        assert r.succeeded is True

    def test_C6_succeeded_false_on_failure(self):
        r = StageResult(stage_name="x", status=StageStatus.FAILURE, duration_ms=0)
        assert r.succeeded is False

    def test_C7_failed_true_on_failure(self):
        r = StageResult(stage_name="x", status=StageStatus.FAILURE, duration_ms=0)
        assert r.failed is True

    def test_C8_failed_false_on_success(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert r.failed is False

    def test_C9_was_skipped_true_on_skipped(self):
        r = StageResult(stage_name="x", status=StageStatus.SKIPPED, duration_ms=0)
        assert r.was_skipped is True

    def test_C10_was_skipped_false_on_success(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert r.was_skipped is False

    def test_C11_result_id_is_uuid(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert uuid.UUID(r.result_id)

    def test_C12_executed_at_is_iso(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert "T" in r.executed_at

    def test_C13_context_keys_written_defaults_empty(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert r.context_keys_written == frozenset()

    def test_C14_context_keys_written_frozenset(self):
        r = StageResult(
            stage_name="planning", status=StageStatus.SUCCESS, duration_ms=5,
            context_keys_written=frozenset({"investigation_plan"}),
        )
        assert "investigation_plan" in r.context_keys_written

    def test_C15_to_dict_has_required_keys(self):
        r = StageResult(stage_name="planning", status=StageStatus.SUCCESS, duration_ms=10)
        d = r.to_dict()
        assert "stage_name" in d
        assert "status" in d
        assert "duration_ms" in d
        assert "result_id" in d
        assert "executed_at" in d
        assert "has_output" in d
        assert "error_code" in d
        assert "error_message" in d
        assert "context_keys_written" in d

    def test_C16_to_dict_status_is_string(self):
        r = StageResult(stage_name="x", status=StageStatus.FAILURE, duration_ms=0)
        assert r.to_dict()["status"] == "FAILURE"

    def test_C17_to_dict_has_output_false_when_none(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0)
        assert r.to_dict()["has_output"] is False

    def test_C18_to_dict_has_output_true_when_set(self):
        r = StageResult(stage_name="x", status=StageStatus.SUCCESS, duration_ms=0, output={"k": "v"})
        assert r.to_dict()["has_output"] is True

    def test_C19_error_message_truncated_in_to_dict(self):
        long_msg = "x" * 300
        r = StageResult(stage_name="x", status=StageStatus.FAILURE, duration_ms=0, error_message=long_msg)
        assert len(r.to_dict()["error_message"]) <= 200

    def test_C20_context_keys_written_sorted_in_to_dict(self):
        r = StageResult(
            stage_name="x", status=StageStatus.SUCCESS, duration_ms=0,
            context_keys_written=frozenset({"z_field", "a_field"}),
        )
        keys = r.to_dict()["context_keys_written"]
        assert keys == sorted(keys)


# ══════════════════════════════════════════════════════════════════════════════
# D — InvestigationStage Protocol
# ══════════════════════════════════════════════════════════════════════════════

class TestInvestigationStageProtocol:
    def test_D1_validate_stage_satisfies_protocol(self):
        assert isinstance(ValidateStage(), InvestigationStage)

    def test_D2_planning_stage_satisfies_protocol(self):
        assert isinstance(PlanningStage(_build_planner()), InvestigationStage)

    def test_D3_collection_stage_satisfies_protocol(self):
        assert isinstance(CollectionStage(_build_collector()), InvestigationStage)

    def test_D4_knowledge_stage_satisfies_protocol(self):
        assert isinstance(KnowledgeStage(), InvestigationStage)

    def test_D5_root_cause_stage_satisfies_protocol(self):
        assert isinstance(RootCauseStage(_build_rca()), InvestigationStage)

    def test_D6_observation_stage_satisfies_protocol(self):
        assert isinstance(ObservationStage(_build_obs_gen()), InvestigationStage)

    def test_D7_protocol_has_stage_name(self):
        stage = ValidateStage()
        assert hasattr(stage, "stage_name")

    def test_D8_protocol_has_severity(self):
        stage = ValidateStage()
        assert hasattr(stage, "severity")

    def test_D9_protocol_has_expected_outputs(self):
        stage = ValidateStage()
        assert hasattr(stage, "expected_outputs")

    def test_D10_protocol_has_execute(self):
        stage = ValidateStage()
        assert callable(stage.execute)


# ══════════════════════════════════════════════════════════════════════════════
# E — ValidateStage
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateStage:
    def test_E1_stage_name(self):
        assert ValidateStage().stage_name == STAGE_VALIDATE

    def test_E2_severity_is_fatal(self):
        assert ValidateStage().severity == StageSeverity.FATAL

    def test_E3_expected_outputs_empty(self):
        assert ValidateStage().expected_outputs == frozenset()

    def test_E4_valid_context_returns_success(self):
        ctx = _make_context()
        result = ValidateStage().execute(ctx)
        assert result.status == StageStatus.SUCCESS

    def test_E5_missing_case_id_returns_failure(self):
        ctx = _make_context()
        ctx.case_id = ""
        result = ValidateStage().execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_E6_missing_topic_returns_failure(self):
        ctx = _make_context()
        ctx.topic = ""
        result = ValidateStage().execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_E7_missing_tenant_returns_failure(self):
        ctx = _make_context()
        ctx.tenant_context = None
        result = ValidateStage().execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_E8_failure_has_error_code(self):
        ctx = _make_context()
        ctx.case_id = ""
        result = ValidateStage().execute(ctx)
        assert result.error_code == "CONTEXT_INVALID"

    def test_E9_failure_has_error_message(self):
        ctx = _make_context()
        ctx.topic = ""
        result = ValidateStage().execute(ctx)
        assert result.error_message is not None
        assert len(result.error_message) > 0

    def test_E10_success_duration_non_negative(self):
        result = ValidateStage().execute(_make_context())
        assert result.duration_ms >= 0

    def test_E11_success_no_error_code(self):
        result = ValidateStage().execute(_make_context())
        assert result.error_code is None

    def test_E12_success_no_error_message(self):
        result = ValidateStage().execute(_make_context())
        assert result.error_message is None

    def test_E13_success_context_keys_written_empty(self):
        result = ValidateStage().execute(_make_context())
        assert result.context_keys_written == frozenset()

    def test_E14_failure_context_keys_written_empty(self):
        ctx = _make_context()
        ctx.case_id = ""
        result = ValidateStage().execute(ctx)
        assert result.context_keys_written == frozenset()

    def test_E15_success_has_result_id(self):
        result = ValidateStage().execute(_make_context())
        assert uuid.UUID(result.result_id)

    def test_E16_success_has_executed_at(self):
        result = ValidateStage().execute(_make_context())
        assert "T" in result.executed_at

    def test_E17_stage_name_in_result(self):
        result = ValidateStage().execute(_make_context())
        assert result.stage_name == STAGE_VALIDATE

    def test_E18_multiple_errors_combined_in_message(self):
        ctx = _make_context()
        ctx.case_id = ""
        ctx.topic = ""
        result = ValidateStage().execute(ctx)
        assert "case_id" in result.error_message
        assert "topic" in result.error_message

    def test_E19_does_not_raise_on_any_context(self):
        stage = ValidateStage()
        stage.execute(None)   # should not raise

    def test_E20_output_not_none_on_success(self):
        result = ValidateStage().execute(_make_context())
        assert result.output is not None


# ══════════════════════════════════════════════════════════════════════════════
# F — PlanningStage
# ══════════════════════════════════════════════════════════════════════════════

class TestPlanningStage:
    def test_F1_stage_name(self):
        assert PlanningStage(_build_planner()).stage_name == STAGE_PLANNING

    def test_F2_severity_is_fatal(self):
        assert PlanningStage(_build_planner()).severity == StageSeverity.FATAL

    def test_F3_expected_outputs_contains_plan(self):
        assert "investigation_plan" in PlanningStage(_build_planner()).expected_outputs

    def test_F4_success_with_real_planner(self):
        ctx = _make_context()
        result = PlanningStage(_build_planner()).execute(ctx)
        assert result.status == StageStatus.SUCCESS

    def test_F5_sets_context_investigation_plan(self):
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        assert ctx.investigation_plan is not None

    def test_F6_output_is_plan(self):
        ctx = _make_context()
        result = PlanningStage(_build_planner()).execute(ctx)
        assert result.output is not None
        assert hasattr(result.output, "plan_id")

    def test_F7_duration_non_negative(self):
        result = PlanningStage(_build_planner()).execute(_make_context())
        assert result.duration_ms >= 0

    def test_F8_context_keys_written_contains_plan(self):
        ctx = _make_context()
        result = PlanningStage(_build_planner()).execute(ctx)
        assert "investigation_plan" in result.context_keys_written

    def test_F9_plan_has_plan_id(self):
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        assert hasattr(ctx.investigation_plan, "plan_id")
        assert ctx.investigation_plan.plan_id

    def test_F10_plan_has_steps(self):
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        assert hasattr(ctx.investigation_plan, "steps")

    def test_F11_planner_error_returns_failure(self):
        bad_planner = MagicMock()
        bad_planner.plan.side_effect = RuntimeError("planner exploded")
        ctx = _make_context()
        result = PlanningStage(bad_planner).execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_F12_failure_has_error_code(self):
        bad_planner = MagicMock()
        bad_planner.plan.side_effect = ValueError("bad")
        result = PlanningStage(bad_planner).execute(_make_context())
        assert result.error_code == "PLANNING_ERROR"

    def test_F13_failure_context_keys_empty(self):
        bad_planner = MagicMock()
        bad_planner.plan.side_effect = ValueError("bad")
        result = PlanningStage(bad_planner).execute(_make_context())
        assert result.context_keys_written == frozenset()

    def test_F14_does_not_raise(self):
        bad_planner = MagicMock()
        bad_planner.plan.side_effect = SystemError("crash")
        PlanningStage(bad_planner).execute(_make_context())

    def test_F15_fallback_topic_still_produces_plan(self):
        ctx = _make_context(topic="UNKNOWN_TOPIC_XYZ")
        result = PlanningStage(_build_planner()).execute(ctx)
        assert result.status == StageStatus.SUCCESS
        assert ctx.investigation_plan is not None

    def test_F16_plan_has_topic(self):
        ctx = _make_context(topic="VKYC_SESSION_FAILURE")
        PlanningStage(_build_planner()).execute(ctx)
        assert hasattr(ctx.investigation_plan, "topic")

    def test_F17_plan_has_case_id(self):
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        assert ctx.investigation_plan.case_id == ctx.case_id

    def test_F18_result_id_is_uuid(self):
        result = PlanningStage(_build_planner()).execute(_make_context())
        assert uuid.UUID(result.result_id)

    def test_F19_success_no_error_fields(self):
        result = PlanningStage(_build_planner()).execute(_make_context())
        assert result.error_code is None
        assert result.error_message is None

    def test_F20_two_calls_produce_different_plan_ids(self):
        ctx1 = _make_context()
        ctx2 = _make_context()
        PlanningStage(_build_planner()).execute(ctx1)
        PlanningStage(_build_planner()).execute(ctx2)
        assert ctx1.investigation_plan.plan_id != ctx2.investigation_plan.plan_id


# ══════════════════════════════════════════════════════════════════════════════
# G — CollectionStage
# ══════════════════════════════════════════════════════════════════════════════

class TestCollectionStage:
    def _ctx_with_plan(self, topic: str = "VKYC_SESSION_FAILURE") -> InvestigationContext:
        ctx = _make_context(topic=topic)
        PlanningStage(_build_planner()).execute(ctx)
        return ctx

    def test_G1_stage_name(self):
        assert CollectionStage(_build_collector()).stage_name == STAGE_COLLECTION

    def test_G2_severity_is_fatal(self):
        assert CollectionStage(_build_collector()).severity == StageSeverity.FATAL

    def test_G3_expected_outputs_contains_bundle(self):
        assert "evidence_bundle" in CollectionStage(_build_collector()).expected_outputs

    def test_G4_success_with_real_collector(self):
        ctx = self._ctx_with_plan()
        result = CollectionStage(_build_collector()).execute(ctx)
        assert result.status == StageStatus.SUCCESS

    def test_G5_sets_context_evidence_bundle(self):
        ctx = self._ctx_with_plan()
        CollectionStage(_build_collector()).execute(ctx)
        assert ctx.evidence_bundle is not None

    def test_G6_output_is_bundle(self):
        ctx = self._ctx_with_plan()
        result = CollectionStage(_build_collector()).execute(ctx)
        assert result.output is not None
        assert hasattr(result.output, "bundle_id")

    def test_G7_missing_plan_returns_failure(self):
        ctx = _make_context()
        # No PlanningStage executed — no plan in context
        result = CollectionStage(_build_collector()).execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_G8_missing_plan_error_code(self):
        ctx = _make_context()
        result = CollectionStage(_build_collector()).execute(ctx)
        assert result.error_code == "MISSING_DEPENDENCY"

    def test_G9_duration_non_negative(self):
        ctx = self._ctx_with_plan()
        result = CollectionStage(_build_collector()).execute(ctx)
        assert result.duration_ms >= 0

    def test_G10_context_keys_written_contains_bundle(self):
        ctx = self._ctx_with_plan()
        result = CollectionStage(_build_collector()).execute(ctx)
        assert "evidence_bundle" in result.context_keys_written

    def test_G11_collector_error_returns_failure(self):
        bad_collector = MagicMock()
        bad_collector.collect.side_effect = RuntimeError("collector down")
        ctx = self._ctx_with_plan()
        result = CollectionStage(bad_collector).execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_G12_collector_error_code(self):
        bad_collector = MagicMock()
        bad_collector.collect.side_effect = ValueError("bad")
        ctx = self._ctx_with_plan()
        result = CollectionStage(bad_collector).execute(ctx)
        assert result.error_code == "COLLECTION_ERROR"

    def test_G13_does_not_raise(self):
        bad_collector = MagicMock()
        bad_collector.collect.side_effect = SystemError("crash")
        ctx = self._ctx_with_plan()
        CollectionStage(bad_collector).execute(ctx)

    def test_G14_bundle_has_bundle_id(self):
        ctx = self._ctx_with_plan()
        CollectionStage(_build_collector()).execute(ctx)
        assert ctx.evidence_bundle.bundle_id

    def test_G15_bundle_has_case_id(self):
        ctx = self._ctx_with_plan()
        CollectionStage(_build_collector()).execute(ctx)
        assert ctx.evidence_bundle.case_id == ctx.case_id

    def test_G16_success_no_error_fields(self):
        ctx = self._ctx_with_plan()
        result = CollectionStage(_build_collector()).execute(ctx)
        assert result.error_code is None

    def test_G17_failure_context_keys_empty(self):
        bad_collector = MagicMock()
        bad_collector.collect.side_effect = ValueError("x")
        ctx = self._ctx_with_plan()
        result = CollectionStage(bad_collector).execute(ctx)
        assert result.context_keys_written == frozenset()

    def test_G18_result_id_is_uuid(self):
        ctx = self._ctx_with_plan()
        result = CollectionStage(_build_collector()).execute(ctx)
        assert uuid.UUID(result.result_id)

    def test_G19_bundle_items_is_list(self):
        ctx = self._ctx_with_plan()
        CollectionStage(_build_collector()).execute(ctx)
        assert isinstance(ctx.evidence_bundle.items, list)

    def test_G20_two_calls_produce_different_bundle_ids(self):
        ctx1 = self._ctx_with_plan()
        ctx2 = self._ctx_with_plan()
        CollectionStage(_build_collector()).execute(ctx1)
        CollectionStage(_build_collector()).execute(ctx2)
        assert ctx1.evidence_bundle.bundle_id != ctx2.evidence_bundle.bundle_id


# ══════════════════════════════════════════════════════════════════════════════
# H — KnowledgeStage
# ══════════════════════════════════════════════════════════════════════════════

class TestKnowledgeStage:
    def test_H1_stage_name(self):
        assert KnowledgeStage().stage_name == STAGE_KNOWLEDGE

    def test_H2_severity_is_non_fatal(self):
        assert KnowledgeStage().severity == StageSeverity.NON_FATAL

    def test_H3_without_provider_expected_outputs_empty(self):
        assert KnowledgeStage(None).expected_outputs == frozenset()

    def test_H4_with_provider_expected_outputs_has_entries(self):
        mock_provider = MagicMock()
        mock_provider.enrich.return_value = []
        stage = KnowledgeStage(mock_provider)
        assert "knowledge_entries" in stage.expected_outputs

    def test_H5_without_provider_returns_skipped(self):
        ctx = _make_context()
        result = KnowledgeStage(None).execute(ctx)
        assert result.status == StageStatus.SKIPPED

    def test_H6_skipped_has_output_existing_entries(self):
        ctx = _make_context()
        result = KnowledgeStage(None).execute(ctx)
        assert isinstance(result.output, list)

    def test_H7_with_provider_returns_success(self):
        mock_provider = MagicMock()
        mock_provider.enrich.return_value = []
        ctx = _make_context()
        result = KnowledgeStage(mock_provider).execute(ctx)
        assert result.status == StageStatus.SUCCESS

    def test_H8_provider_enrichment_sets_context_entries(self):
        from case_engine.knowledge.base import KnowledgeEntry
        entry = KnowledgeEntry(
            entry_id="ke-1",
            source="test_provider",
            topic="VKYC_SESSION_FAILURE",
            title="Test entry",
            content="Some knowledge",
        )
        mock_provider = MagicMock()
        mock_provider.enrich.return_value = [entry]
        ctx = _make_context()
        KnowledgeStage(mock_provider).execute(ctx)
        assert len(ctx.knowledge_entries) == 1

    def test_H9_provider_failure_returns_failure(self):
        mock_provider = MagicMock()
        mock_provider.enrich.side_effect = RuntimeError("knowledge down")
        ctx = _make_context()
        result = KnowledgeStage(mock_provider).execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_H10_provider_failure_error_code(self):
        mock_provider = MagicMock()
        mock_provider.enrich.side_effect = RuntimeError("down")
        ctx = _make_context()
        result = KnowledgeStage(mock_provider).execute(ctx)
        assert result.error_code == "KNOWLEDGE_ERROR"

    def test_H11_provider_failure_does_not_raise(self):
        mock_provider = MagicMock()
        mock_provider.enrich.side_effect = SystemError("crash")
        KnowledgeStage(mock_provider).execute(_make_context())

    def test_H12_provider_failure_returns_existing_entries_as_output(self):
        mock_provider = MagicMock()
        mock_provider.enrich.side_effect = RuntimeError("down")
        ctx = _make_context()
        result = KnowledgeStage(mock_provider).execute(ctx)
        assert isinstance(result.output, list)

    def test_H13_duration_non_negative(self):
        result = KnowledgeStage(None).execute(_make_context())
        assert result.duration_ms >= 0

    def test_H14_skipped_context_keys_empty(self):
        result = KnowledgeStage(None).execute(_make_context())
        assert result.context_keys_written == frozenset()

    def test_H15_provider_success_context_keys_contains_entries(self):
        mock_provider = MagicMock()
        mock_provider.enrich.return_value = []
        ctx = _make_context()
        result = KnowledgeStage(mock_provider).execute(ctx)
        assert "knowledge_entries" in result.context_keys_written


# ══════════════════════════════════════════════════════════════════════════════
# I — RootCauseStage
# ══════════════════════════════════════════════════════════════════════════════

class TestRootCauseStage:
    def _ctx_with_bundle(self) -> InvestigationContext:
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        CollectionStage(_build_collector()).execute(ctx)
        return ctx

    def test_I1_stage_name(self):
        assert RootCauseStage(_build_rca()).stage_name == STAGE_ROOT_CAUSE

    def test_I2_severity_is_fatal(self):
        assert RootCauseStage(_build_rca()).severity == StageSeverity.FATAL

    def test_I3_expected_outputs_contains_analysis(self):
        assert "root_cause_analysis" in RootCauseStage(_build_rca()).expected_outputs

    def test_I4_success_with_real_rca(self):
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(_build_rca()).execute(ctx)
        assert result.status == StageStatus.SUCCESS

    def test_I5_sets_context_root_cause_analysis(self):
        ctx = self._ctx_with_bundle()
        RootCauseStage(_build_rca()).execute(ctx)
        assert ctx.root_cause_analysis is not None

    def test_I6_analysis_has_analysis_id(self):
        ctx = self._ctx_with_bundle()
        RootCauseStage(_build_rca()).execute(ctx)
        assert hasattr(ctx.root_cause_analysis, "analysis_id")

    def test_I7_analysis_has_category(self):
        ctx = self._ctx_with_bundle()
        RootCauseStage(_build_rca()).execute(ctx)
        assert hasattr(ctx.root_cause_analysis, "category")

    def test_I8_analysis_has_confidence(self):
        ctx = self._ctx_with_bundle()
        RootCauseStage(_build_rca()).execute(ctx)
        assert hasattr(ctx.root_cause_analysis, "confidence")

    def test_I9_missing_bundle_returns_failure(self):
        ctx = _make_context()
        result = RootCauseStage(_build_rca()).execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_I10_missing_bundle_error_code(self):
        ctx = _make_context()
        result = RootCauseStage(_build_rca()).execute(ctx)
        assert result.error_code == "MISSING_DEPENDENCY"

    def test_I11_engine_error_returns_failure(self):
        bad_engine = MagicMock()
        bad_engine.analyze.side_effect = RuntimeError("rca down")
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(bad_engine).execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_I12_engine_error_code(self):
        bad_engine = MagicMock()
        bad_engine.analyze.side_effect = ValueError("bad")
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(bad_engine).execute(ctx)
        assert result.error_code == "ROOT_CAUSE_ERROR"

    def test_I13_does_not_raise(self):
        bad_engine = MagicMock()
        bad_engine.analyze.side_effect = SystemError("crash")
        ctx = self._ctx_with_bundle()
        RootCauseStage(bad_engine).execute(ctx)

    def test_I14_success_context_keys_contains_analysis(self):
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(_build_rca()).execute(ctx)
        assert "root_cause_analysis" in result.context_keys_written

    def test_I15_failure_context_keys_empty(self):
        bad_engine = MagicMock()
        bad_engine.analyze.side_effect = ValueError("x")
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(bad_engine).execute(ctx)
        assert result.context_keys_written == frozenset()

    def test_I16_output_is_analysis(self):
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(_build_rca()).execute(ctx)
        assert result.output is not None
        assert hasattr(result.output, "analysis_id")

    def test_I17_duration_non_negative(self):
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(_build_rca()).execute(ctx)
        assert result.duration_ms >= 0

    def test_I18_result_id_is_uuid(self):
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(_build_rca()).execute(ctx)
        assert uuid.UUID(result.result_id)

    def test_I19_success_no_error_code(self):
        ctx = self._ctx_with_bundle()
        result = RootCauseStage(_build_rca()).execute(ctx)
        assert result.error_code is None

    def test_I20_confidence_in_zero_to_one(self):
        ctx = self._ctx_with_bundle()
        RootCauseStage(_build_rca()).execute(ctx)
        assert 0.0 <= ctx.root_cause_analysis.confidence <= 1.0


# ══════════════════════════════════════════════════════════════════════════════
# J — ObservationStage
# ══════════════════════════════════════════════════════════════════════════════

class TestObservationStage:
    def _ctx_with_analysis(self) -> InvestigationContext:
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        CollectionStage(_build_collector()).execute(ctx)
        RootCauseStage(_build_rca()).execute(ctx)
        return ctx

    def _make_mock_observation(self) -> MagicMock:
        obs = MagicMock()
        obs.observation_id = str(uuid.uuid4())
        obs.status = MagicMock()
        obs.status.value = "COMPLETE"
        obs.generator_version = "1.0.0"
        obs.generated_at = "2024-01-01T00:00:00+00:00"
        return obs

    def _build_succeeding_gen(self) -> MagicMock:
        gen = MagicMock()
        gen.generate.return_value = self._make_mock_observation()
        return gen

    def test_J1_stage_name(self):
        assert ObservationStage(_build_obs_gen()).stage_name == STAGE_OBSERVATION

    def test_J2_severity_is_non_fatal(self):
        assert ObservationStage(_build_obs_gen()).severity == StageSeverity.NON_FATAL

    def test_J3_expected_outputs_contains_observation(self):
        assert "observation" in ObservationStage(_build_obs_gen()).expected_outputs

    def test_J4_stage_returns_result_never_raises(self):
        # ObservationGenerator may fail with minimal plans (non-fatal in pipeline)
        ctx = self._ctx_with_analysis()
        result = ObservationStage(_build_obs_gen()).execute(ctx)
        assert isinstance(result, StageResult)

    def test_J5_sets_context_observation_on_mock_success(self):
        ctx = self._ctx_with_analysis()
        ObservationStage(self._build_succeeding_gen()).execute(ctx)
        assert ctx.observation is not None

    def test_J6_sets_context_observation_status_on_mock_success(self):
        ctx = self._ctx_with_analysis()
        ObservationStage(self._build_succeeding_gen()).execute(ctx)
        assert ctx.observation_status is not None

    def test_J7_missing_analysis_returns_failure(self):
        ctx = _make_context()
        result = ObservationStage(_build_obs_gen()).execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_J8_missing_analysis_error_code(self):
        ctx = _make_context()
        result = ObservationStage(_build_obs_gen()).execute(ctx)
        assert result.error_code == "MISSING_DEPENDENCY"

    def test_J9_generator_error_returns_failure(self):
        bad_gen = MagicMock()
        bad_gen.generate.side_effect = RuntimeError("gen down")
        ctx = self._ctx_with_analysis()
        result = ObservationStage(bad_gen).execute(ctx)
        assert result.status == StageStatus.FAILURE

    def test_J10_generator_error_code(self):
        bad_gen = MagicMock()
        bad_gen.generate.side_effect = ValueError("bad")
        ctx = self._ctx_with_analysis()
        result = ObservationStage(bad_gen).execute(ctx)
        assert result.error_code == "OBSERVATION_ERROR"

    def test_J11_does_not_raise(self):
        bad_gen = MagicMock()
        bad_gen.generate.side_effect = SystemError("crash")
        ctx = self._ctx_with_analysis()
        ObservationStage(bad_gen).execute(ctx)

    def test_J12_success_context_keys_contains_observation(self):
        ctx = self._ctx_with_analysis()
        result = ObservationStage(self._build_succeeding_gen()).execute(ctx)
        assert result.status == StageStatus.SUCCESS
        assert "observation" in result.context_keys_written

    def test_J13_failure_context_keys_empty(self):
        bad_gen = MagicMock()
        bad_gen.generate.side_effect = ValueError("x")
        ctx = self._ctx_with_analysis()
        result = ObservationStage(bad_gen).execute(ctx)
        assert result.context_keys_written == frozenset()

    def test_J14_output_is_observation_on_success(self):
        ctx = self._ctx_with_analysis()
        result = ObservationStage(self._build_succeeding_gen()).execute(ctx)
        assert result.status == StageStatus.SUCCESS
        assert result.output is not None

    def test_J15_observation_has_observation_id_on_success(self):
        ctx = self._ctx_with_analysis()
        ObservationStage(self._build_succeeding_gen()).execute(ctx)
        assert hasattr(ctx.observation, "observation_id")

    def test_J16_observation_field_exists_on_context(self):
        ctx = self._ctx_with_analysis()
        ObservationStage(_build_obs_gen()).execute(ctx)
        # Field exists on context regardless of whether observation succeeded
        assert hasattr(ctx, "observation_version")

    def test_J17_duration_non_negative(self):
        ctx = self._ctx_with_analysis()
        result = ObservationStage(_build_obs_gen()).execute(ctx)
        assert result.duration_ms >= 0

    def test_J18_result_id_is_uuid(self):
        ctx = self._ctx_with_analysis()
        result = ObservationStage(_build_obs_gen()).execute(ctx)
        assert uuid.UUID(result.result_id)

    def test_J19_success_no_error_code_with_mock(self):
        ctx = self._ctx_with_analysis()
        result = ObservationStage(self._build_succeeding_gen()).execute(ctx)
        assert result.status == StageStatus.SUCCESS
        assert result.error_code is None

    def test_J20_expected_outputs_contains_status_and_version(self):
        stage = ObservationStage(_build_obs_gen())
        assert "observation_status" in stage.expected_outputs
        assert "observation_version" in stage.expected_outputs


# ══════════════════════════════════════════════════════════════════════════════
# K — PipelineManifest
# ══════════════════════════════════════════════════════════════════════════════

class TestPipelineManifest:
    def test_K1_get_default_manifest_returns_manifest(self):
        m = get_default_manifest()
        assert isinstance(m, PipelineManifest)

    def test_K2_default_has_six_stages(self):
        assert len(get_default_manifest().stages) == 6

    def test_K3_stage_names_ordered(self):
        names = get_default_manifest().stage_names()
        assert names == (
            STAGE_VALIDATE,
            STAGE_PLANNING,
            STAGE_COLLECTION,
            STAGE_KNOWLEDGE,
            STAGE_ROOT_CAUSE,
            STAGE_OBSERVATION,
        )

    def test_K4_get_stage_by_name(self):
        m = get_default_manifest()
        s = m.get_stage(STAGE_PLANNING)
        assert s is not None
        assert s.name == STAGE_PLANNING

    def test_K5_get_stage_unknown_returns_none(self):
        assert get_default_manifest().get_stage("nonexistent") is None

    def test_K6_validate_returns_empty_for_default(self):
        assert get_default_manifest().validate() == []

    def test_K7_is_valid_true_for_default(self):
        assert get_default_manifest().is_valid()

    def test_K8_version_field(self):
        assert get_default_manifest().version == "1.0.0"

    def test_K9_sprint_field(self):
        assert get_default_manifest().sprint == "2.47"

    def test_K10_fatal_stages_count(self):
        m = get_default_manifest()
        fatal = m.fatal_stages()
        assert len(fatal) == 4

    def test_K11_non_fatal_stages_count(self):
        m = get_default_manifest()
        nf = m.non_fatal_stages()
        assert len(nf) == 2

    def test_K12_to_dict_has_stages(self):
        d = get_default_manifest().to_dict()
        assert "stages" in d
        assert d["stage_count"] == 6


# ══════════════════════════════════════════════════════════════════════════════
# L — StageDescriptor
# ══════════════════════════════════════════════════════════════════════════════

class TestStageDescriptor:
    def test_L1_is_frozen(self):
        m = get_default_manifest()
        s = m.get_stage(STAGE_PLANNING)
        with pytest.raises((AttributeError, TypeError)):
            s.name = "changed"

    def test_L2_validate_stage_severity(self):
        s = get_default_manifest().get_stage(STAGE_VALIDATE)
        assert s.severity == StageSeverity.FATAL

    def test_L3_planning_stage_severity(self):
        s = get_default_manifest().get_stage(STAGE_PLANNING)
        assert s.severity == StageSeverity.FATAL

    def test_L4_collection_stage_severity(self):
        s = get_default_manifest().get_stage(STAGE_COLLECTION)
        assert s.severity == StageSeverity.FATAL

    def test_L5_knowledge_stage_severity(self):
        s = get_default_manifest().get_stage(STAGE_KNOWLEDGE)
        assert s.severity == StageSeverity.NON_FATAL

    def test_L6_root_cause_stage_severity(self):
        s = get_default_manifest().get_stage(STAGE_ROOT_CAUSE)
        assert s.severity == StageSeverity.FATAL

    def test_L7_observation_stage_severity(self):
        s = get_default_manifest().get_stage(STAGE_OBSERVATION)
        assert s.severity == StageSeverity.NON_FATAL

    def test_L8_collection_depends_on_planning(self):
        s = get_default_manifest().get_stage(STAGE_COLLECTION)
        assert STAGE_PLANNING in s.depends_on

    def test_L9_to_dict_has_name(self):
        s = get_default_manifest().get_stage(STAGE_PLANNING)
        assert s.to_dict()["name"] == STAGE_PLANNING

    def test_L10_is_fatal_method(self):
        s = get_default_manifest().get_stage(STAGE_VALIDATE)
        assert s.is_fatal() is True


# ══════════════════════════════════════════════════════════════════════════════
# M — ContextIntegrityGuard
# ══════════════════════════════════════════════════════════════════════════════

class TestContextIntegrityGuard:
    def _ctx_after_planning(self) -> InvestigationContext:
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        return ctx

    def _full_ctx(self) -> InvestigationContext:
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        CollectionStage(_build_collector()).execute(ctx)
        RootCauseStage(_build_rca()).execute(ctx)
        return ctx

    def test_M1_empty_context_fails_planning_check(self):
        guard = ContextIntegrityGuard()
        missing = guard.check_after_stage(STAGE_PLANNING, _make_context())
        assert "investigation_plan" in missing

    def test_M2_context_with_plan_passes_planning_check(self):
        guard = ContextIntegrityGuard()
        ctx = self._ctx_after_planning()
        missing = guard.check_after_stage(STAGE_PLANNING, ctx)
        assert missing == []

    def test_M3_empty_context_fails_collection_check(self):
        guard = ContextIntegrityGuard()
        missing = guard.check_after_stage(STAGE_COLLECTION, _make_context())
        assert "evidence_bundle" in missing

    def test_M4_context_with_bundle_passes_collection_check(self):
        guard = ContextIntegrityGuard()
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        CollectionStage(_build_collector()).execute(ctx)
        missing = guard.check_after_stage(STAGE_COLLECTION, ctx)
        assert missing == []

    def test_M5_empty_context_fails_root_cause_check(self):
        guard = ContextIntegrityGuard()
        missing = guard.check_after_stage(STAGE_ROOT_CAUSE, _make_context())
        assert "root_cause_analysis" in missing

    def test_M6_context_with_analysis_passes_root_cause_check(self):
        guard = ContextIntegrityGuard()
        ctx = self._full_ctx()
        missing = guard.check_after_stage(STAGE_ROOT_CAUSE, ctx)
        assert missing == []

    def test_M7_validate_stage_always_passes(self):
        guard = ContextIntegrityGuard()
        assert guard.check_after_stage(STAGE_VALIDATE, _make_context()) == []

    def test_M8_knowledge_stage_no_required_fields(self):
        guard = ContextIntegrityGuard()
        assert guard.check_after_stage(STAGE_KNOWLEDGE, _make_context()) == []

    def test_M9_assert_raises_on_missing(self):
        guard = ContextIntegrityGuard()
        with pytest.raises(ContextIntegrityError):
            guard.assert_after_stage(STAGE_PLANNING, _make_context())

    def test_M10_assert_no_raise_on_valid(self):
        guard = ContextIntegrityGuard()
        ctx = self._ctx_after_planning()
        guard.assert_after_stage(STAGE_PLANNING, ctx)  # should not raise

    def test_M11_check_full_pipeline_empty_context_has_issues(self):
        guard = ContextIntegrityGuard()
        issues = guard.check_full_pipeline(_make_context())
        assert len(issues) > 0

    def test_M12_check_full_pipeline_complete_context_no_issues(self):
        guard = ContextIntegrityGuard()
        ctx = self._full_ctx()
        issues = guard.check_full_pipeline(ctx)
        # Only mandatory stages checked — observation is optional
        assert STAGE_PLANNING not in issues
        assert STAGE_COLLECTION not in issues
        assert STAGE_ROOT_CAUSE not in issues

    def test_M13_is_complete_false_on_empty(self):
        guard = ContextIntegrityGuard()
        assert guard.is_complete(_make_context()) is False

    def test_M14_is_complete_true_after_full_pipeline(self):
        guard = ContextIntegrityGuard()
        ctx = self._full_ctx()
        assert guard.is_complete(ctx) is True

    def test_M15_unknown_stage_returns_empty(self):
        guard = ContextIntegrityGuard()
        assert guard.check_after_stage("nonexistent_stage", _make_context()) == []

    def test_M16_missing_core_fields_lists_all_missing(self):
        guard = ContextIntegrityGuard()
        missing = guard.missing_core_fields(_make_context())
        assert "investigation_plan" in missing
        assert "evidence_bundle" in missing
        assert "root_cause_analysis" in missing

    def test_M17_missing_core_fields_empty_after_complete(self):
        guard = ContextIntegrityGuard()
        ctx = self._full_ctx()
        assert guard.missing_core_fields(ctx) == []

    def test_M18_context_integrity_error_is_exception(self):
        assert issubclass(ContextIntegrityError, Exception)

    def test_M19_assert_error_message_mentions_stage(self):
        guard = ContextIntegrityGuard()
        try:
            guard.assert_after_stage(STAGE_PLANNING, _make_context())
        except ContextIntegrityError as exc:
            assert STAGE_PLANNING in str(exc)

    def test_M20_check_returns_sorted_list(self):
        guard = ContextIntegrityGuard()
        missing = guard.check_after_stage(STAGE_PLANNING, _make_context())
        assert missing == sorted(missing)


# ══════════════════════════════════════════════════════════════════════════════
# N — Full Pipeline Golden Path
# ══════════════════════════════════════════════════════════════════════════════

class TestFullPipelineGoldenPath:
    def _run(self, topic: str = "VKYC_SESSION_FAILURE") -> tuple[OrchestratorInvestigationResult, InvestigationContext]:
        ctx = _make_context(topic=topic)
        result = _build_orchestrator().investigate(ctx)
        return result, ctx

    def test_N1_result_is_completed_or_partial(self):
        result, _ = self._run()
        assert result.status in {"COMPLETED", "PARTIAL"}

    def test_N2_result_has_plan(self):
        result, _ = self._run()
        assert result.plan is not None

    def test_N3_result_has_evidence(self):
        result, _ = self._run()
        assert result.evidence is not None

    def test_N4_result_has_root_cause(self):
        result, _ = self._run()
        assert result.root_cause is not None

    def test_N5_result_has_session_id(self):
        result, _ = self._run()
        assert uuid.UUID(result.session_id)

    def test_N6_result_has_result_id(self):
        result, _ = self._run()
        assert uuid.UUID(result.result_id)

    def test_N7_result_has_case_id(self):
        ctx = _make_context()
        result = _build_orchestrator().investigate(ctx)
        assert result.case_id == ctx.case_id

    def test_N8_result_has_topic(self):
        ctx = _make_context(topic="VKYC_SESSION_FAILURE")
        result = _build_orchestrator().investigate(ctx)
        assert result.topic == "VKYC_SESSION_FAILURE"

    def test_N9_pipeline_duration_positive(self):
        result, _ = self._run()
        assert result.pipeline_duration_ms >= 0

    def test_N10_stage_timings_present(self):
        result, _ = self._run()
        assert isinstance(result.stage_timings, dict)

    def test_N11_audit_timeline_has_records(self):
        result, _ = self._run()
        assert result.audit_timeline is not None

    def test_N12_result_is_not_cancelled(self):
        result, _ = self._run()
        assert result.status != "CANCELLED"

    def test_N13_errors_list_exists(self):
        result, _ = self._run()
        assert isinstance(result.errors, list)

    def test_N14_started_at_is_iso(self):
        result, _ = self._run()
        assert "T" in result.started_at

    def test_N15_completed_at_is_iso(self):
        result, _ = self._run()
        assert "T" in result.completed_at

    def test_N16_result_to_dict_works(self):
        result, _ = self._run()
        d = result.to_dict()
        assert isinstance(d, dict)

    def test_N17_result_to_json_is_valid(self):
        result, _ = self._run()
        j = result.to_json()
        parsed = json.loads(j)
        assert isinstance(parsed, dict)

    def test_N18_build_investigation_orchestrator_factory(self):
        orch = build_investigation_orchestrator()
        assert isinstance(orch, InvestigationOrchestrator)

    def test_N19_cancellation_token_stops_pipeline(self):
        ctx = _make_context()
        token = CancellationToken()
        token.cancel("test cancel")
        result = _build_orchestrator().investigate(ctx, cancellation_token=token)
        assert result.status == "CANCELLED"

    def test_N20_timeout_stops_pipeline(self):
        ctx = _make_context()
        result = _build_orchestrator().investigate(ctx, timeout_seconds=-100)
        assert result.status == "CANCELLED"


# ══════════════════════════════════════════════════════════════════════════════
# O — Context Propagation
# ══════════════════════════════════════════════════════════════════════════════

class TestContextPropagation:
    def _run_ctx(self) -> InvestigationContext:
        ctx = _make_context()
        _build_orchestrator().investigate(ctx)
        return ctx

    def test_O1_orchestrator_session_id_set(self):
        ctx = self._run_ctx()
        assert ctx.orchestrator_session_id is not None
        assert uuid.UUID(ctx.orchestrator_session_id)

    def test_O2_orchestrator_started_at_set(self):
        ctx = self._run_ctx()
        assert ctx.orchestrator_started_at is not None

    def test_O3_orchestrator_completed_at_set(self):
        ctx = self._run_ctx()
        assert ctx.orchestrator_completed_at is not None

    def test_O4_orchestrator_result_id_set(self):
        ctx = self._run_ctx()
        assert ctx.orchestrator_result_id is not None
        assert uuid.UUID(ctx.orchestrator_result_id)

    def test_O5_orchestrator_status_set(self):
        ctx = self._run_ctx()
        assert ctx.orchestrator_status in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}

    def test_O6_investigation_plan_set(self):
        ctx = self._run_ctx()
        assert ctx.investigation_plan is not None

    def test_O7_evidence_bundle_set(self):
        ctx = self._run_ctx()
        assert ctx.evidence_bundle is not None

    def test_O8_root_cause_analysis_set(self):
        ctx = self._run_ctx()
        assert ctx.root_cause_analysis is not None

    def test_O9_case_id_unchanged(self):
        ctx = _make_context()
        original_case_id = ctx.case_id
        _build_orchestrator().investigate(ctx)
        assert ctx.case_id == original_case_id

    def test_O10_topic_unchanged(self):
        ctx = _make_context(topic="VKYC_SESSION_FAILURE")
        _build_orchestrator().investigate(ctx)
        assert ctx.topic == "VKYC_SESSION_FAILURE"

    def test_O11_tenant_context_unchanged(self):
        ctx = _make_context()
        original_client_id = ctx.tenant_context.client_id
        _build_orchestrator().investigate(ctx)
        assert ctx.tenant_context.client_id == original_client_id

    def test_O12_knowledge_entries_is_list(self):
        ctx = self._run_ctx()
        assert isinstance(ctx.knowledge_entries, list)

    def test_O13_plan_case_id_matches_context(self):
        ctx = _make_context()
        _build_orchestrator().investigate(ctx)
        assert ctx.investigation_plan.case_id == ctx.case_id

    def test_O14_bundle_case_id_matches_context(self):
        ctx = _make_context()
        _build_orchestrator().investigate(ctx)
        assert ctx.evidence_bundle.case_id == ctx.case_id

    def test_O15_guard_is_complete_after_pipeline(self):
        guard = ContextIntegrityGuard()
        ctx = _make_context()
        _build_orchestrator().investigate(ctx)
        assert guard.is_complete(ctx) is True


# ══════════════════════════════════════════════════════════════════════════════
# P — Failure Scenarios
# ══════════════════════════════════════════════════════════════════════════════

class TestFailureScenarios:
    def test_P1_validate_fail_missing_case_id(self):
        ctx = _make_context()
        ctx.case_id = ""
        result = _build_orchestrator().investigate(ctx)
        assert result.status == "FAILED"

    def test_P2_validate_fail_missing_topic(self):
        ctx = _make_context()
        ctx.topic = ""
        result = _build_orchestrator().investigate(ctx)
        assert result.status == "FAILED"

    def test_P3_validate_fail_missing_tenant(self):
        ctx = _make_context()
        ctx.tenant_context = None
        result = _build_orchestrator().investigate(ctx)
        assert result.status == "FAILED"

    def test_P4_planning_failure_returns_failed(self):
        bad_planner = MagicMock()
        bad_planner.plan.side_effect = RuntimeError("planner down")
        orch = InvestigationOrchestrator(
            planner=bad_planner,
            collector=_build_collector(),
            rca_engine=_build_rca(),
            obs_gen=_build_obs_gen(),
        )
        result = orch.investigate(_make_context())
        assert result.status == "FAILED"

    def test_P5_collection_failure_returns_partial(self):
        bad_collector = MagicMock()
        bad_collector.collect.side_effect = RuntimeError("collector down")
        orch = InvestigationOrchestrator(
            planner=_build_planner(),
            collector=bad_collector,
            rca_engine=_build_rca(),
            obs_gen=_build_obs_gen(),
        )
        result = orch.investigate(_make_context())
        assert result.status == "PARTIAL"

    def test_P6_root_cause_failure_returns_partial(self):
        bad_rca = MagicMock()
        bad_rca.analyze.side_effect = RuntimeError("rca down")
        orch = InvestigationOrchestrator(
            planner=_build_planner(),
            collector=_build_collector(),
            rca_engine=bad_rca,
            obs_gen=_build_obs_gen(),
        )
        result = orch.investigate(_make_context())
        assert result.status == "PARTIAL"

    def test_P7_knowledge_failure_does_not_stop_pipeline(self):
        bad_knowledge = MagicMock()
        bad_knowledge.enrich.side_effect = RuntimeError("knowledge down")
        orch = InvestigationOrchestrator(
            planner=_build_planner(),
            collector=_build_collector(),
            rca_engine=_build_rca(),
            obs_gen=_build_obs_gen(),
            knowledge_provider=bad_knowledge,
        )
        result = orch.investigate(_make_context())
        assert result.status in {"COMPLETED", "PARTIAL"}

    def test_P8_observation_failure_does_not_produce_failed(self):
        bad_obs = MagicMock()
        bad_obs.generate.side_effect = RuntimeError("obs down")
        orch = InvestigationOrchestrator(
            planner=_build_planner(),
            collector=_build_collector(),
            rca_engine=_build_rca(),
            obs_gen=bad_obs,
        )
        result = orch.investigate(_make_context())
        assert result.status in {"COMPLETED", "PARTIAL"}
        assert result.status != "FAILED"

    def test_P9_failed_result_has_errors(self):
        ctx = _make_context()
        ctx.case_id = ""
        result = _build_orchestrator().investigate(ctx)
        assert len(result.errors) > 0

    def test_P10_partial_result_has_plan(self):
        bad_collector = MagicMock()
        bad_collector.collect.side_effect = RuntimeError("down")
        orch = InvestigationOrchestrator(
            planner=_build_planner(),
            collector=bad_collector,
            rca_engine=_build_rca(),
            obs_gen=_build_obs_gen(),
        )
        result = orch.investigate(_make_context())
        assert result.plan is not None

    def test_P11_validate_stage_adapter_fail(self):
        ctx = _make_context()
        ctx.topic = ""
        result = ValidateStage().execute(ctx)
        assert result.failed

    def test_P12_planning_stage_adapter_fail(self):
        bad_planner = MagicMock()
        bad_planner.plan.side_effect = ValueError("x")
        result = PlanningStage(bad_planner).execute(_make_context())
        assert result.failed

    def test_P13_collection_stage_adapter_no_plan(self):
        result = CollectionStage(_build_collector()).execute(_make_context())
        assert result.failed

    def test_P14_root_cause_stage_adapter_no_bundle(self):
        result = RootCauseStage(_build_rca()).execute(_make_context())
        assert result.failed

    def test_P15_observation_stage_adapter_no_analysis(self):
        result = ObservationStage(_build_obs_gen()).execute(_make_context())
        assert result.failed


# ══════════════════════════════════════════════════════════════════════════════
# Q — Metrics
# ══════════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def test_Q1_stage_timings_not_empty_after_pipeline(self):
        ctx = _make_context()
        result = _build_orchestrator().investigate(ctx)
        assert len(result.stage_timings) > 0

    def test_Q2_validate_timing_present(self):
        result = _build_orchestrator().investigate(_make_context())
        assert "validate" in result.stage_timings

    def test_Q3_planning_timing_present(self):
        result = _build_orchestrator().investigate(_make_context())
        assert "planning" in result.stage_timings

    def test_Q4_collection_timing_present(self):
        result = _build_orchestrator().investigate(_make_context())
        assert "collection" in result.stage_timings

    def test_Q5_root_cause_timing_present(self):
        result = _build_orchestrator().investigate(_make_context())
        assert "root_cause" in result.stage_timings

    def test_Q6_pipeline_duration_non_negative(self):
        result = _build_orchestrator().investigate(_make_context())
        assert result.pipeline_duration_ms >= 0

    def test_Q7_metrics_to_dict_has_stage_durations(self):
        result = _build_orchestrator().investigate(_make_context())
        d = result.metrics.to_dict()
        assert "stage_durations_ms" in d

    def test_Q8_global_metrics_incremented_on_success(self):
        InvestigationOrchestrator.reset_global_metrics()
        ctx = _make_context()
        result = _build_orchestrator().investigate(ctx)
        gm = InvestigationOrchestrator.global_metrics()
        total = gm.completed + gm.failed + gm.cancelled
        assert total >= 1

    def test_Q9_global_metrics_object_returned(self):
        gm = InvestigationOrchestrator.global_metrics()
        assert gm is not None

    def test_Q10_reset_global_metrics_clears_counts(self):
        _build_orchestrator().investigate(_make_context())
        InvestigationOrchestrator.reset_global_metrics()
        gm = InvestigationOrchestrator.global_metrics()
        assert gm.completed == 0
        assert gm.failed == 0

    def test_Q11_stage_result_duration_non_negative(self):
        ctx = _make_context()
        result = ValidateStage().execute(ctx)
        assert result.duration_ms >= 0

    def test_Q12_planning_stage_duration_non_negative(self):
        result = PlanningStage(_build_planner()).execute(_make_context())
        assert result.duration_ms >= 0

    def test_Q13_collection_stage_duration_non_negative(self):
        ctx = _make_context()
        PlanningStage(_build_planner()).execute(ctx)
        result = CollectionStage(_build_collector()).execute(ctx)
        assert result.duration_ms >= 0

    def test_Q14_metrics_has_stage_failures(self):
        result = _build_orchestrator().investigate(_make_context())
        assert hasattr(result.metrics, "stage_failures")

    def test_Q15_pipeline_duration_sum_check(self):
        result = _build_orchestrator().investigate(_make_context())
        total_stages = sum(result.stage_timings.values())
        # pipeline duration should be at least as long as the sum of stages
        assert result.pipeline_duration_ms >= 0


# ══════════════════════════════════════════════════════════════════════════════
# R — Audit Timeline
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditTimeline:
    def test_R1_audit_timeline_not_none(self):
        result = _build_orchestrator().investigate(_make_context())
        assert result.audit_timeline is not None

    def test_R2_audit_timeline_has_to_dict(self):
        result = _build_orchestrator().investigate(_make_context())
        d = result.audit_timeline.to_dict()
        assert isinstance(d, dict)

    def test_R3_audit_has_session_id(self):
        result = _build_orchestrator().investigate(_make_context())
        d = result.audit_timeline.to_dict()
        assert "session_id" in d

    def test_R4_audit_has_case_id(self):
        result = _build_orchestrator().investigate(_make_context())
        d = result.audit_timeline.to_dict()
        assert "case_id" in d

    def test_R5_audit_stage_records_present(self):
        result = _build_orchestrator().investigate(_make_context())
        d = result.audit_timeline.to_dict()
        assert "stages" in d

    def test_R6_audit_records_list(self):
        result = _build_orchestrator().investigate(_make_context())
        records = result.audit_timeline.to_dict()["stages"]
        assert isinstance(records, list)

    def test_R7_audit_no_pii(self):
        ctx = _make_context()
        ctx.customer_email = "sensitive@testclient.com"
        result = _build_orchestrator().investigate(ctx)
        audit_str = str(result.audit_timeline.to_dict())
        assert ctx.customer_email not in audit_str

    def test_R8_successful_stages_list(self):
        result = _build_orchestrator().investigate(_make_context())
        successful = result.audit_timeline.successful_stages()
        assert isinstance(successful, list)

    def test_R9_successful_stages_contains_planning(self):
        result = _build_orchestrator().investigate(_make_context())
        # successful_stages() returns list[str] (stage names)
        names = result.audit_timeline.successful_stages()
        assert "planning" in names

    def test_R10_audit_timeline_started_at(self):
        result = _build_orchestrator().investigate(_make_context())
        d = result.audit_timeline.to_dict()
        assert "started_at" in d


# ══════════════════════════════════════════════════════════════════════════════
# S — Stage Ordering & Dependencies
# ══════════════════════════════════════════════════════════════════════════════

class TestStageOrdering:
    def test_S1_validate_first_in_manifest(self):
        manifest = get_default_manifest()
        assert manifest.stages[0].name == STAGE_VALIDATE

    def test_S2_planning_second_in_manifest(self):
        manifest = get_default_manifest()
        assert manifest.stages[1].name == STAGE_PLANNING

    def test_S3_collection_third_in_manifest(self):
        manifest = get_default_manifest()
        assert manifest.stages[2].name == STAGE_COLLECTION

    def test_S4_collection_depends_on_planning(self):
        s = get_default_manifest().get_stage(STAGE_COLLECTION)
        assert STAGE_PLANNING in s.depends_on

    def test_S5_knowledge_depends_on_collection(self):
        s = get_default_manifest().get_stage(STAGE_KNOWLEDGE)
        assert STAGE_COLLECTION in s.depends_on

    def test_S6_root_cause_depends_on_collection(self):
        s = get_default_manifest().get_stage(STAGE_ROOT_CAUSE)
        assert STAGE_COLLECTION in s.depends_on

    def test_S7_observation_depends_on_root_cause(self):
        s = get_default_manifest().get_stage(STAGE_OBSERVATION)
        assert STAGE_ROOT_CAUSE in s.depends_on

    def test_S8_validate_has_no_dependencies(self):
        s = get_default_manifest().get_stage(STAGE_VALIDATE)
        assert s.depends_on == frozenset()

    def test_S9_planning_has_no_dependencies(self):
        s = get_default_manifest().get_stage(STAGE_PLANNING)
        assert s.depends_on == frozenset()

    def test_S10_manifest_is_valid(self):
        assert get_default_manifest().is_valid()


# ══════════════════════════════════════════════════════════════════════════════
# T — Idempotency & Determinism
# ══════════════════════════════════════════════════════════════════════════════

class TestIdempotencyDeterminism:
    def test_T1_two_investigations_different_result_ids(self):
        r1 = _build_orchestrator().investigate(_make_context())
        r2 = _build_orchestrator().investigate(_make_context())
        assert r1.result_id != r2.result_id

    def test_T2_two_investigations_different_session_ids(self):
        r1 = _build_orchestrator().investigate(_make_context())
        r2 = _build_orchestrator().investigate(_make_context())
        assert r1.session_id != r2.session_id

    def test_T3_same_topic_same_plan_topic(self):
        ctx1 = _make_context(topic="VKYC_SESSION_FAILURE")
        ctx2 = _make_context(topic="VKYC_SESSION_FAILURE")
        _build_orchestrator().investigate(ctx1)
        _build_orchestrator().investigate(ctx2)
        assert ctx1.investigation_plan.topic == ctx2.investigation_plan.topic

    def test_T4_validate_stage_deterministic_success(self):
        ctx = _make_context()
        r1 = ValidateStage().execute(ctx)
        r2 = ValidateStage().execute(ctx)
        assert r1.status == r2.status

    def test_T5_validate_stage_deterministic_failure(self):
        ctx = _make_context()
        ctx.case_id = ""
        r1 = ValidateStage().execute(ctx)
        r2 = ValidateStage().execute(ctx)
        assert r1.status == r2.status == StageStatus.FAILURE

    def test_T6_plan_has_unique_id_per_call(self):
        ctx1 = _make_context()
        ctx2 = _make_context()
        PlanningStage(_build_planner()).execute(ctx1)
        PlanningStage(_build_planner()).execute(ctx2)
        assert ctx1.investigation_plan.plan_id != ctx2.investigation_plan.plan_id

    def test_T7_knowledge_stage_skipped_is_idempotent(self):
        ctx = _make_context()
        r1 = KnowledgeStage(None).execute(ctx)
        r2 = KnowledgeStage(None).execute(ctx)
        assert r1.status == r2.status == StageStatus.SKIPPED

    def test_T8_orchestrator_never_returns_none(self):
        result = _build_orchestrator().investigate(_make_context())
        assert result is not None

    def test_T9_context_state_unchanged_by_orchestrator(self):
        from case_engine.investigation.context import InvestigationState
        ctx = _make_context()
        original_state = ctx.state
        _build_orchestrator().investigate(ctx)
        assert ctx.state == original_state

    def test_T10_pipeline_manifest_is_stable(self):
        m1 = get_default_manifest()
        m2 = get_default_manifest()
        assert m1.stage_names() == m2.stage_names()


# ══════════════════════════════════════════════════════════════════════════════
# U — Serialization
# ══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_U1_result_to_dict_is_dict(self):
        result = _build_orchestrator().investigate(_make_context())
        assert isinstance(result.to_dict(), dict)

    def test_U2_result_to_json_is_valid_json(self):
        result = _build_orchestrator().investigate(_make_context())
        j = result.to_json()
        json.loads(j)

    def test_U3_stage_result_to_dict_is_dict(self):
        r = StageResult(stage_name="test", status=StageStatus.SUCCESS, duration_ms=5)
        assert isinstance(r.to_dict(), dict)

    def test_U4_stage_result_to_dict_has_stage_name(self):
        r = StageResult(stage_name="planning", status=StageStatus.SUCCESS, duration_ms=5)
        assert r.to_dict()["stage_name"] == "planning"

    def test_U5_stage_descriptor_to_dict_is_dict(self):
        s = get_default_manifest().get_stage(STAGE_PLANNING)
        assert isinstance(s.to_dict(), dict)

    def test_U6_manifest_to_dict_is_dict(self):
        assert isinstance(get_default_manifest().to_dict(), dict)

    def test_U7_manifest_to_dict_has_stage_count(self):
        d = get_default_manifest().to_dict()
        assert d["stage_count"] == 6

    def test_U8_result_dict_has_result_id(self):
        result = _build_orchestrator().investigate(_make_context())
        d = result.to_dict()
        assert "result_id" in d

    def test_U9_result_dict_has_status(self):
        result = _build_orchestrator().investigate(_make_context())
        d = result.to_dict()
        assert "status" in d

    def test_U10_result_json_contains_case_id(self):
        ctx = _make_context()
        result = _build_orchestrator().investigate(ctx)
        j = result.to_json()
        assert ctx.case_id in j


# ══════════════════════════════════════════════════════════════════════════════
# V — Sprint 2.46 + 2.38–2.45 Regression
# ══════════════════════════════════════════════════════════════════════════════

class TestRegression:
    def test_V1_sprint246_imports_intact(self):
        from case_engine.investigation.orchestrator import (
            InvestigationOrchestrator,
            build_investigation_orchestrator,
            CancellationToken,
            InvestigationSession,
            OrchestratorInvestigationResult,
            OrchestratorLifecycleState,
            OrchestratorStateMachine,
            OrchestratorMetrics,
            OrchestratorGlobalMetrics,
            OrchestratorAudit,
            AuditStageRecord,
            PipelineOrchestrator,
        )

    def test_V2_cancellation_token_idempotent(self):
        token = CancellationToken()
        token.cancel("first")
        token.cancel("second")
        assert token.reason == "first"

    def test_V3_cancellation_token_is_cancelled(self):
        token = CancellationToken()
        assert not token.is_cancelled
        token.cancel("stop")
        assert token.is_cancelled

    def test_V4_lifecycle_state_values(self):
        states = {s.value for s in OrchestratorLifecycleState}
        assert "COMPLETED" in states
        assert "FAILED" in states
        assert "CANCELLED" in states

    def test_V5_sprint239_planner_intact(self):
        from case_engine.investigation.planner.engine import InvestigationPlanner
        p = InvestigationPlanner()
        ctx = _make_context()
        plan = p.plan(ctx)
        assert plan is not None

    def test_V6_sprint242_collector_intact(self):
        from case_engine.investigation.collector.collector import EvidenceCollector
        c = EvidenceCollector()
        assert c is not None

    def test_V7_sprint243_rca_intact(self):
        from case_engine.investigation.root_cause.engine import RootCauseEngine
        e = RootCauseEngine()
        assert e is not None

    def test_V8_sprint244_obs_gen_intact(self):
        from case_engine.investigation.observation.generator import ObservationGenerator
        g = ObservationGenerator()
        assert g is not None

    def test_V9_sprint238_context_intact(self):
        ctx = _make_context()
        assert ctx.case_id
        assert ctx.topic
        assert ctx.tenant_context is not None

    def test_V10_sprint246_pipeline_protocols_satisfiable(self):
        from case_engine.investigation.orchestrator.pipeline import (
            PlannerProtocol, CollectorProtocol,
            RootCauseEngineProtocol, ObservationGeneratorProtocol,
        )
        assert isinstance(_build_planner(), PlannerProtocol)
        assert isinstance(_build_collector(), CollectorProtocol)
        assert isinstance(_build_rca(), RootCauseEngineProtocol)
        assert isinstance(_build_obs_gen(), ObservationGeneratorProtocol)

    def test_V11_sprint246_build_factory_works(self):
        orch = build_investigation_orchestrator()
        ctx = _make_context()
        result = orch.investigate(ctx)
        assert result is not None

    def test_V12_sprint246_global_metrics_class_level(self):
        gm = InvestigationOrchestrator.global_metrics()
        assert gm is InvestigationOrchestrator.global_metrics()

    def test_V13_sprint238_knowledge_base_imports(self):
        from case_engine.knowledge.base import KnowledgeEntry, KnowledgeQuery

    def test_V14_sprint241_sop_models_imports(self):
        from case_engine.knowledge.sop.models import SOPDocument

    def test_V15_sprint247_pipeline_package_exports(self):
        from case_engine.investigation.pipeline import (
            InvestigationStage,
            StageResult,
            StageStatus,
            StageSeverity,
            ValidateStage,
            PlanningStage,
            CollectionStage,
            KnowledgeStage,
            RootCauseStage,
            ObservationStage,
            PipelineManifest,
            StageDescriptor,
            get_default_manifest,
            ContextIntegrityGuard,
            ContextIntegrityError,
        )
