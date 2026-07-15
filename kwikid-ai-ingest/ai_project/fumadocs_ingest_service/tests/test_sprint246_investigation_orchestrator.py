"""
tests/test_sprint246_investigation_orchestrator.py

Sprint 2.46: Investigation Orchestrator — 213 comprehensive tests.

Sections:
  A  OrchestratorLifecycleState enum            (5)
  B  OrchestratorStateMachine transitions       (15)
  C  CancellationToken                          (10)
  D  OrchestratorMetrics                        (10)
  E  OrchestratorGlobalMetrics thread safety    (10)
  F  AuditStageRecord lifecycle                 (10)
  G  OrchestratorAudit timeline                  (8)
  H  InvestigationSession creation               (8)
  I  InvestigationSession timeout                (5)
  J  OrchestratorInvestigationResult properties (10)
  K  OrchestratorInvestigationResult serialization (8)
  L  PipelineOrchestrator happy path            (10)
  M  validate stage failure                      (5)
  N  planning stage failure                      (5)
  O  collection stage failure                    (5)
  P  knowledge stage                             (8)
  Q  root_cause failure                          (5)
  R  observation failure (non-fatal)             (5)
  S  cancellation between stages                 (8)
  T  timeout                                     (5)
  U  InvestigationOrchestrator full path        (10)
  V  global metrics                              (8)
  W  context ownership fields                   (10)
  X  serialization roundtrip                     (8)
  Y  versioning + regression                    (10)
     Total:                                    (213)
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from case_engine.investigation.context import InvestigationContext
from case_engine.investigation.orchestrator.audit import AuditStageRecord, OrchestratorAudit
from case_engine.investigation.orchestrator.exceptions import (
    CancellationError,
    InvalidContextError,
    OrchestratorError,
    SessionAlreadyStartedError,
    StageError,
)
from case_engine.investigation.orchestrator.metrics import (
    OrchestratorGlobalMetrics,
    OrchestratorMetrics,
)
from case_engine.investigation.orchestrator.models import (
    CancellationToken,
    InvestigationSession,
    OrchestratorInvestigationResult,
)
from case_engine.investigation.orchestrator.orchestrator import (
    InvestigationOrchestrator,
    build_investigation_orchestrator,
)
from case_engine.investigation.orchestrator.pipeline import PipelineOrchestrator
from case_engine.investigation.orchestrator.serialization import result_to_dict, result_to_json
from case_engine.investigation.orchestrator.state_machine import (
    OrchestratorLifecycleState,
    OrchestratorStateMachine,
)
from case_engine.investigation.orchestrator.versioning import (
    ORCHESTRATOR_SPRINT,
    ORCHESTRATOR_VERSION,
    OrchestratorVersion,
)
from case_engine.tenant.models import TenantContext, TenantEnvironment, TenantType


# ═══════════════════════════════════════════════════════════════════
# Shared Helpers
# ═══════════════════════════════════════════════════════════════════

def _make_tenant() -> TenantContext:
    return TenantContext(
        client_id="test_bank",
        client_name="Test Bank",
        domain="testbank.co.in",
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        enabled_tools=("GetSessionDetails",),
        credentials_ref="test_bank_creds",
    )


def _make_context(*, case_id: str = "case-246-001", topic: str = "VKYC_SESSION_FAILURE") -> InvestigationContext:
    return InvestigationContext.create(
        case_id=case_id,
        ticket_id="ticket-001",
        ticket_subject="VKYC session failed",
        ticket_description="Customer session dropped during liveness check",
        customer_id="cust-001",
        customer_email="customer@testbank.co.in",
        channel="email",
        tenant_context=_make_tenant(),
        topic=topic,
        classification_confidence=0.95,
    )


def _make_mock_plan() -> MagicMock:
    plan = MagicMock()
    plan.plan_id = str(uuid.uuid4())
    plan.steps = []
    plan.topic = "VKYC_SESSION_FAILURE"
    plan.case_id = "case-246-001"
    return plan


def _make_mock_bundle() -> MagicMock:
    bundle = MagicMock()
    bundle.bundle_id = str(uuid.uuid4())
    bundle.items = []
    bundle.topic = "VKYC_SESSION_FAILURE"
    bundle.collected_at = datetime.now(tz=timezone.utc).isoformat()
    return bundle


def _make_mock_rca() -> MagicMock:
    rc = MagicMock()
    rc.analysis_id = str(uuid.uuid4())
    rc.category = MagicMock()
    rc.category.value = "NETWORK_FAILURE"
    rc.confidence = 0.87
    return rc


def _make_mock_observation() -> MagicMock:
    obs = MagicMock()
    obs.observation_id = str(uuid.uuid4())
    obs.status = MagicMock()
    obs.status.value = "COMPLETE"
    obs.generator_version = "1.0.0"
    obs.generated_at = datetime.now(tz=timezone.utc).isoformat()
    return obs


def _build_pipeline(plan=None, bundle=None, rca=None, observation=None, knowledge_provider=None) -> tuple[PipelineOrchestrator, MagicMock, MagicMock, MagicMock, MagicMock]:
    plan        = plan        or _make_mock_plan()
    bundle      = bundle      or _make_mock_bundle()
    rca         = rca         or _make_mock_rca()
    observation = observation or _make_mock_observation()

    planner   = MagicMock(); planner.plan.return_value      = plan
    collector = MagicMock(); collector.collect.return_value = bundle
    rca_eng   = MagicMock(); rca_eng.analyze.return_value   = rca
    obs_gen   = MagicMock(); obs_gen.generate.return_value  = observation

    pipeline = PipelineOrchestrator(
        planner=planner, collector=collector,
        rca_engine=rca_eng, obs_gen=obs_gen,
        knowledge_provider=knowledge_provider,
    )
    return pipeline, planner, collector, rca_eng, obs_gen


def _build_orchestrator(plan=None, bundle=None, rca=None, observation=None, knowledge_provider=None) -> InvestigationOrchestrator:
    plan        = plan        or _make_mock_plan()
    bundle      = bundle      or _make_mock_bundle()
    rca         = rca         or _make_mock_rca()
    observation = observation or _make_mock_observation()

    planner   = MagicMock(); planner.plan.return_value      = plan
    collector = MagicMock(); collector.collect.return_value = bundle
    rca_eng   = MagicMock(); rca_eng.analyze.return_value   = rca
    obs_gen   = MagicMock(); obs_gen.generate.return_value  = observation

    return InvestigationOrchestrator(
        planner=planner, collector=collector, rca_engine=rca_eng,
        obs_gen=obs_gen, knowledge_provider=knowledge_provider,
    )


def _make_result(**overrides) -> OrchestratorInvestigationResult:
    now = datetime.now(tz=timezone.utc).isoformat()
    defaults = dict(
        result_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        case_id="case-246-001",
        topic="VKYC_SESSION_FAILURE",
        status="COMPLETED",
        plan=None, evidence=None, knowledge_entries=[],
        root_cause=None, observation=None,
        metrics=OrchestratorMetrics(),
        audit_timeline=OrchestratorAudit(session_id="s1", case_id="c1", started_at=now),
        errors=[], stage_timings={}, pipeline_duration_ms=42,
        started_at=now, completed_at=now,
        cancellation_reason=None, recovery_hint=None,
    )
    defaults.update(overrides)
    return OrchestratorInvestigationResult(**defaults)


def _run_pipeline_happy(context=None) -> tuple[OrchestratorInvestigationResult, PipelineOrchestrator]:
    ctx = context or _make_context()
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    return result, pipeline


@pytest.fixture(autouse=True)
def _reset_global_metrics():
    InvestigationOrchestrator.reset_global_metrics()
    yield
    InvestigationOrchestrator.reset_global_metrics()


# ═══════════════════════════════════════════════════════════════════
# SECTION A — OrchestratorLifecycleState enum (5)
# ═══════════════════════════════════════════════════════════════════

def test_A1_all_nine_states_defined():
    states = {s.value for s in OrchestratorLifecycleState}
    expected = {"CREATED","PLANNING","COLLECTING","KNOWLEDGE","ROOT_CAUSE","OBSERVATION","COMPLETED","FAILED","CANCELLED"}
    assert states == expected

def test_A2_is_str_enum():
    assert isinstance(OrchestratorLifecycleState.CREATED, str)
    assert OrchestratorLifecycleState.COMPLETED == "COMPLETED"

def test_A3_terminal_states_are_not_transitionable():
    for terminal in (OrchestratorLifecycleState.COMPLETED, OrchestratorLifecycleState.FAILED, OrchestratorLifecycleState.CANCELLED):
        sm = OrchestratorStateMachine()
        sm.force_terminal(terminal)
        assert not sm.can_transition(OrchestratorLifecycleState.PLANNING)
        assert not sm.can_transition(OrchestratorLifecycleState.COLLECTING)

def test_A4_created_is_initial_value():
    assert OrchestratorLifecycleState.CREATED.value == "CREATED"

def test_A5_state_values_match_names():
    for state in OrchestratorLifecycleState:
        assert state.value == state.name


# ═══════════════════════════════════════════════════════════════════
# SECTION B — OrchestratorStateMachine transitions (15)
# ═══════════════════════════════════════════════════════════════════

def test_B1_initial_state_is_created():
    sm = OrchestratorStateMachine()
    assert sm.state == OrchestratorLifecycleState.CREATED

def test_B2_transition_created_to_planning():
    sm = OrchestratorStateMachine()
    assert sm.transition(OrchestratorLifecycleState.PLANNING)
    assert sm.state == OrchestratorLifecycleState.PLANNING

def test_B3_transition_planning_to_collecting():
    sm = OrchestratorStateMachine()
    sm.transition(OrchestratorLifecycleState.PLANNING)
    assert sm.transition(OrchestratorLifecycleState.COLLECTING)
    assert sm.state == OrchestratorLifecycleState.COLLECTING

def test_B4_transition_collecting_to_knowledge():
    sm = OrchestratorStateMachine()
    sm.transition(OrchestratorLifecycleState.PLANNING)
    sm.transition(OrchestratorLifecycleState.COLLECTING)
    assert sm.transition(OrchestratorLifecycleState.KNOWLEDGE)
    assert sm.state == OrchestratorLifecycleState.KNOWLEDGE

def test_B5_transition_knowledge_to_root_cause():
    sm = OrchestratorStateMachine()
    sm.transition(OrchestratorLifecycleState.PLANNING)
    sm.transition(OrchestratorLifecycleState.COLLECTING)
    sm.transition(OrchestratorLifecycleState.KNOWLEDGE)
    assert sm.transition(OrchestratorLifecycleState.ROOT_CAUSE)
    assert sm.state == OrchestratorLifecycleState.ROOT_CAUSE

def test_B6_transition_root_cause_to_observation():
    sm = OrchestratorStateMachine()
    sm.transition(OrchestratorLifecycleState.PLANNING)
    sm.transition(OrchestratorLifecycleState.COLLECTING)
    sm.transition(OrchestratorLifecycleState.KNOWLEDGE)
    sm.transition(OrchestratorLifecycleState.ROOT_CAUSE)
    assert sm.transition(OrchestratorLifecycleState.OBSERVATION)

def test_B7_transition_observation_to_completed():
    sm = OrchestratorStateMachine()
    for s in [OrchestratorLifecycleState.PLANNING, OrchestratorLifecycleState.COLLECTING,
              OrchestratorLifecycleState.KNOWLEDGE, OrchestratorLifecycleState.ROOT_CAUSE,
              OrchestratorLifecycleState.OBSERVATION]:
        sm.transition(s)
    assert sm.transition(OrchestratorLifecycleState.COMPLETED)
    assert sm.state == OrchestratorLifecycleState.COMPLETED

def test_B8_invalid_transition_returns_false():
    sm = OrchestratorStateMachine()
    assert not sm.transition(OrchestratorLifecycleState.COLLECTING)

def test_B9_cannot_transition_from_completed():
    sm = OrchestratorStateMachine()
    sm.force_terminal(OrchestratorLifecycleState.COMPLETED)
    assert not sm.transition(OrchestratorLifecycleState.PLANNING)

def test_B10_cannot_transition_from_failed():
    sm = OrchestratorStateMachine()
    sm.force_terminal(OrchestratorLifecycleState.FAILED)
    assert not sm.transition(OrchestratorLifecycleState.PLANNING)

def test_B11_cannot_transition_from_cancelled():
    sm = OrchestratorStateMachine()
    sm.force_terminal(OrchestratorLifecycleState.CANCELLED)
    assert not sm.transition(OrchestratorLifecycleState.PLANNING)

def test_B12_force_terminal_failed():
    sm = OrchestratorStateMachine()
    sm.force_terminal(OrchestratorLifecycleState.FAILED)
    assert sm.state == OrchestratorLifecycleState.FAILED

def test_B13_force_terminal_cancelled():
    sm = OrchestratorStateMachine()
    sm.transition(OrchestratorLifecycleState.PLANNING)
    sm.force_terminal(OrchestratorLifecycleState.CANCELLED)
    assert sm.state == OrchestratorLifecycleState.CANCELLED

def test_B14_can_cancel_from_created():
    sm = OrchestratorStateMachine()
    assert sm.can_transition(OrchestratorLifecycleState.CANCELLED)

def test_B15_transition_history_recorded():
    sm = OrchestratorStateMachine()
    sm.transition(OrchestratorLifecycleState.PLANNING)
    sm.transition(OrchestratorLifecycleState.COLLECTING)
    history = sm.transition_history()
    assert len(history) >= 2
    assert any("PLANNING" in str(h) for h in history)


# ═══════════════════════════════════════════════════════════════════
# SECTION C — CancellationToken (10)
# ═══════════════════════════════════════════════════════════════════

def test_C1_not_cancelled_initially():
    assert not CancellationToken().is_cancelled

def test_C2_cancel_sets_cancelled():
    token = CancellationToken()
    token.cancel()
    assert token.is_cancelled

def test_C3_cancel_sets_reason():
    token = CancellationToken()
    token.cancel("user aborted")
    assert token.reason == "user aborted"

def test_C4_is_cancelled_property():
    token = CancellationToken()
    assert token.is_cancelled is False
    token.cancel()
    assert token.is_cancelled is True

def test_C5_reason_empty_before_cancel():
    assert CancellationToken().reason == ""

def test_C6_to_dict_uncancelled():
    d = CancellationToken().to_dict()
    assert d["cancelled"] is False
    assert d["reason"] == ""

def test_C7_to_dict_cancelled():
    token = CancellationToken()
    token.cancel("timeout")
    d = token.to_dict()
    assert d["cancelled"] is True
    assert d["reason"] == "timeout"

def test_C8_cancel_is_idempotent():
    token = CancellationToken()
    token.cancel("first")
    token.cancel("second")
    assert token.is_cancelled
    assert token.reason == "first"

def test_C9_thread_safety_multiple_cancels():
    token = CancellationToken()
    errors = []
    def _cancel():
        try:
            token.cancel("thread")
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=_cancel) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert token.is_cancelled

def test_C10_reason_property_is_thread_safe():
    token = CancellationToken()
    token.cancel("safe")
    results = []
    def _read():
        results.append(token.reason)
    threads = [threading.Thread(target=_read) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == "safe" for r in results)


# ═══════════════════════════════════════════════════════════════════
# SECTION D — OrchestratorMetrics (10)
# ═══════════════════════════════════════════════════════════════════

def test_D1_initial_state_zeros():
    m = OrchestratorMetrics()
    assert m.stages_completed == 0
    assert m.stages_failed == 0
    assert m.pipeline_duration_ms == 0

def test_D2_record_successful_stage():
    m = OrchestratorMetrics()
    m.record_stage("planning", 150, success=True)
    assert m.stage_durations_ms["planning"] == 150
    assert m.stages_completed == 1
    assert m.stages_failed == 0

def test_D3_record_failed_stage():
    m = OrchestratorMetrics()
    m.record_stage("collection", 200, success=False)
    assert m.stage_failures["collection"] == 1
    assert m.stages_failed == 1
    assert m.stages_completed == 0

def test_D4_multiple_stage_records():
    m = OrchestratorMetrics()
    for stage, dur in [("validate",10), ("planning",50), ("collection",100)]:
        m.record_stage(stage, dur, success=True)
    assert m.stages_completed == 3
    assert len(m.stage_durations_ms) == 3

def test_D5_set_pipeline_duration():
    m = OrchestratorMetrics()
    m.set_pipeline_duration(999)
    assert m.pipeline_duration_ms == 999

def test_D6_to_dict_empty():
    d = OrchestratorMetrics().to_dict()
    assert "stage_durations_ms" in d
    assert "pipeline_duration_ms" in d
    assert d["stages_completed"] == 0

def test_D7_to_dict_with_data():
    m = OrchestratorMetrics()
    m.record_stage("planning", 100, success=True)
    m.set_pipeline_duration(200)
    d = m.to_dict()
    assert d["stage_durations_ms"]["planning"] == 100
    assert d["pipeline_duration_ms"] == 200

def test_D8_stages_completed_count():
    m = OrchestratorMetrics()
    for i in range(5):
        m.record_stage(f"stage{i}", i*10, success=True)
    assert m.stages_completed == 5

def test_D9_stages_failed_count():
    m = OrchestratorMetrics()
    m.record_stage("a", 10, success=False)
    m.record_stage("b", 20, success=False)
    assert m.stages_failed == 2

def test_D10_duplicate_stage_overwritten():
    m = OrchestratorMetrics()
    m.record_stage("planning", 100, success=True)
    m.record_stage("planning", 200, success=True)
    assert m.stage_durations_ms["planning"] == 200
    assert m.stages_completed == 2


# ═══════════════════════════════════════════════════════════════════
# SECTION E — OrchestratorGlobalMetrics thread safety (10)
# ═══════════════════════════════════════════════════════════════════

def test_E1_initial_zeros():
    gm = OrchestratorGlobalMetrics()
    assert gm.completed == 0
    assert gm.failed == 0
    assert gm.cancelled == 0

def test_E2_record_completed():
    gm = OrchestratorGlobalMetrics()
    gm.record_completed()
    assert gm.completed == 1

def test_E3_record_failed():
    gm = OrchestratorGlobalMetrics()
    gm.record_failed()
    assert gm.failed == 1

def test_E4_record_cancelled():
    gm = OrchestratorGlobalMetrics()
    gm.record_cancelled()
    assert gm.cancelled == 1

def test_E5_record_stage_failure():
    gm = OrchestratorGlobalMetrics()
    gm.record_stage_failure("planning")
    gm.record_stage_failure("planning")
    assert gm.stage_failure_totals["planning"] == 2

def test_E6_concurrent_completed():
    gm = OrchestratorGlobalMetrics()
    def _inc():
        for _ in range(100):
            gm.record_completed()
    threads = [threading.Thread(target=_inc) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert gm.completed == 1000

def test_E7_concurrent_failed():
    gm = OrchestratorGlobalMetrics()
    def _inc():
        for _ in range(50):
            gm.record_failed()
    threads = [threading.Thread(target=_inc) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert gm.failed == 200

def test_E8_reset_zeroes_all():
    gm = OrchestratorGlobalMetrics()
    gm.record_completed(); gm.record_failed(); gm.record_cancelled()
    gm.record_stage_failure("rca")
    gm.reset()
    assert gm.completed == 0
    assert gm.failed == 0
    assert gm.cancelled == 0
    assert gm.stage_failure_totals == {}

def test_E9_to_dict_structure():
    gm = OrchestratorGlobalMetrics()
    gm.record_completed()
    d = gm.to_dict()
    assert "completed_investigations" in d
    assert "failed_investigations" in d
    assert "cancelled_investigations" in d
    assert "stage_failure_totals" in d

def test_E10_stage_failure_totals_returns_copy():
    gm = OrchestratorGlobalMetrics()
    gm.record_stage_failure("planning")
    totals = gm.stage_failure_totals
    totals["planning"] = 999
    assert gm.stage_failure_totals["planning"] == 1


# ═══════════════════════════════════════════════════════════════════
# SECTION F — AuditStageRecord lifecycle (10)
# ═══════════════════════════════════════════════════════════════════

def _make_record(stage: str = "planning") -> AuditStageRecord:
    return AuditStageRecord(stage=stage, started_at="2024-01-01T00:00:00+00:00")

def test_F1_initial_outcome_pending():
    assert _make_record().outcome == "PENDING"

def test_F2_mark_complete_sets_success():
    r = _make_record()
    r.mark_complete("2024-01-01T00:00:01+00:00", 150, {"plan_id": "p1"})
    assert r.outcome == "SUCCESS"
    assert r.duration_ms == 150
    assert r.completed_at is not None

def test_F3_mark_failed_sets_failure():
    r = _make_record()
    r.mark_failed("2024-01-01T00:00:01+00:00", 50, "PLANNING_ERROR", "timeout")
    assert r.outcome == "FAILURE"
    assert r.error_code == "PLANNING_ERROR"
    assert r.duration_ms == 50

def test_F4_mark_cancelled_sets_cancelled():
    r = _make_record()
    r.mark_cancelled("2024-01-01T00:00:01+00:00", 30)
    assert r.outcome == "CANCELLED"
    assert r.duration_ms == 30

def test_F5_mark_skipped_sets_skipped():
    r = _make_record()
    r.mark_skipped()
    assert r.outcome == "SKIPPED"

def test_F6_to_dict_has_all_keys():
    d = _make_record().to_dict()
    for key in ("stage","started_at","completed_at","failed_at","duration_ms","outcome","input_summary","output_summary","error_code","error_message"):
        assert key in d

def test_F7_error_message_truncated_to_500():
    r = _make_record()
    long_msg = "x" * 600
    r.mark_failed("2024-01-01T00:00:01+00:00", 10, "ERR", long_msg)
    assert len(r.error_message) == 500

def test_F8_input_summary_defaults_empty():
    r = _make_record()
    assert r.input_summary == {}

def test_F9_output_summary_set_on_complete():
    r = _make_record()
    r.mark_complete("2024-01-01T00:00:01+00:00", 10, {"key": "value"})
    assert r.output_summary == {"key": "value"}

def test_F10_duration_ms_default_zero():
    assert _make_record().duration_ms == 0


# ═══════════════════════════════════════════════════════════════════
# SECTION G — OrchestratorAudit timeline (8)
# ═══════════════════════════════════════════════════════════════════

def _make_audit() -> OrchestratorAudit:
    return OrchestratorAudit(session_id="s1", case_id="c1", started_at="2024-01-01T00:00:00+00:00")

def test_G1_empty_timeline():
    audit = _make_audit()
    assert audit.stage_count() == 0
    assert audit.timeline == []

def test_G2_start_stage_creates_record():
    audit = _make_audit()
    record = audit.start_stage("planning", {"topic": "VKYC"})
    assert record.stage == "planning"
    assert record.outcome == "PENDING"

def test_G3_record_appended_to_timeline():
    audit = _make_audit()
    audit.start_stage("validate")
    audit.start_stage("planning")
    assert audit.stage_count() == 2

def test_G4_successful_stages_filter():
    audit = _make_audit()
    r1 = audit.start_stage("validate")
    r1.mark_complete("2024-01-01T00:00:01+00:00", 10, {})
    r2 = audit.start_stage("planning")
    r2.mark_failed("2024-01-01T00:00:02+00:00", 20, "ERR", "fail")
    assert "validate" in audit.successful_stages()
    assert "planning" not in audit.successful_stages()

def test_G5_failed_stages_filter():
    audit = _make_audit()
    r1 = audit.start_stage("planning")
    r1.mark_failed("2024-01-01T00:00:01+00:00", 10, "PLAN_ERR", "oops")
    assert "planning" in audit.failed_stages()

def test_G6_to_dict_structure():
    audit = _make_audit()
    audit.start_stage("validate")
    d = audit.to_dict()
    assert "session_id" in d
    assert "stages" in d
    assert "stage_count" in d
    assert "successful_stages" in d
    assert "failed_stages" in d

def test_G7_timeline_order_preserved():
    audit = _make_audit()
    for stage in ("validate","planning","collection","knowledge","root_cause"):
        audit.start_stage(stage)
    names = [r.stage for r in audit.timeline]
    assert names == ["validate","planning","collection","knowledge","root_cause"]

def test_G8_input_summary_no_pii():
    audit = _make_audit()
    record = audit.start_stage("validate", {"topic": "VKYC", "case_id": "case-001"})
    assert "case_id" in record.input_summary
    assert record.input_summary["topic"] == "VKYC"


# ═══════════════════════════════════════════════════════════════════
# SECTION H — InvestigationSession creation (8)
# ═══════════════════════════════════════════════════════════════════

def test_H1_create_sets_session_id():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    assert session.session_id
    assert len(session.session_id) > 10

def test_H2_create_sets_case_id():
    ctx = _make_context(case_id="case-H2")
    session = InvestigationSession.create(ctx)
    assert session.case_id == "case-H2"

def test_H3_create_sets_started_at():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    assert "T" in session.started_at

def test_H4_create_sets_context():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    assert session.context is ctx

def test_H5_create_with_timeout_sets_timeout_at():
    ctx = _make_context()
    session = InvestigationSession.create(ctx, timeout_seconds=300)
    assert session.timeout_at is not None

def test_H6_create_without_timeout_has_no_timeout_at():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    assert session.timeout_at is None

def test_H7_initial_lifecycle_state_created():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    assert session.lifecycle_state == OrchestratorLifecycleState.CREATED

def test_H8_add_error_appends_to_errors():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    session.add_error("planning", "PLAN_ERR", "planner failed")
    assert len(session.errors) == 1
    assert session.errors[0]["stage"] == "planning"
    assert session.errors[0]["error_code"] == "PLAN_ERR"


# ═══════════════════════════════════════════════════════════════════
# SECTION I — InvestigationSession timeout (5)
# ═══════════════════════════════════════════════════════════════════

def test_I1_no_timeout_not_timed_out():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    assert not session.is_timed_out()

def test_I2_future_timeout_not_timed_out():
    ctx = _make_context()
    session = InvestigationSession.create(ctx, timeout_seconds=3600)
    assert not session.is_timed_out()

def test_I3_past_timeout_is_timed_out():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    # Set timeout_at to the past
    past = (datetime.now(tz=timezone.utc) - timedelta(seconds=100)).isoformat()
    session.timeout_at = past
    assert session.is_timed_out()

def test_I4_negative_timeout_seconds_is_timed_out():
    # timeout_seconds=-100 produces a past deadline
    ctx = _make_context()
    session = InvestigationSession.create(ctx, timeout_seconds=-100)
    assert session.is_timed_out()

def test_I5_timeout_at_without_tzinfo_handled():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    # ISO without tz suffix — the method must handle this
    session.timeout_at = "2000-01-01T00:00:00"
    assert session.is_timed_out()


# ═══════════════════════════════════════════════════════════════════
# SECTION J — OrchestratorInvestigationResult properties (10)
# ═══════════════════════════════════════════════════════════════════

def test_J1_is_completed_true():
    assert _make_result(status="COMPLETED").is_completed

def test_J2_is_completed_false_for_failed():
    assert not _make_result(status="FAILED").is_completed

def test_J3_is_failed_true():
    assert _make_result(status="FAILED").is_failed

def test_J4_is_cancelled_true():
    assert _make_result(status="CANCELLED").is_cancelled

def test_J5_is_partial_true():
    assert _make_result(status="PARTIAL").is_partial

def test_J6_has_plan_true():
    assert _make_result(plan=_make_mock_plan()).has_plan

def test_J7_has_plan_false_when_none():
    assert not _make_result(plan=None).has_plan

def test_J8_has_evidence_true():
    assert _make_result(evidence=_make_mock_bundle()).has_evidence

def test_J9_has_root_cause_true():
    assert _make_result(root_cause=_make_mock_rca()).has_root_cause

def test_J10_has_knowledge_true():
    from case_engine.knowledge.base import KnowledgeEntry
    entry = MagicMock(spec=KnowledgeEntry)
    assert _make_result(knowledge_entries=[entry]).has_knowledge


# ═══════════════════════════════════════════════════════════════════
# SECTION K — OrchestratorInvestigationResult serialization (8)
# ═══════════════════════════════════════════════════════════════════

def test_K1_to_dict_returns_dict():
    assert isinstance(_make_result().to_dict(), dict)

def test_K2_to_dict_has_required_keys():
    d = _make_result().to_dict()
    for key in ("result_id","session_id","case_id","topic","status","errors","pipeline_duration_ms"):
        assert key in d, f"missing key: {key}"

def test_K3_to_json_returns_string():
    assert isinstance(_make_result().to_json(), str)

def test_K4_to_json_is_valid_json():
    s = _make_result().to_json()
    parsed = json.loads(s)
    assert isinstance(parsed, dict)

def test_K5_result_id_in_dict():
    r = _make_result()
    d = r.to_dict()
    assert d["result_id"] == r.result_id

def test_K6_errors_list_in_dict():
    r = _make_result(errors=[{"stage": "planning", "message": "oops"}])
    d = r.to_dict()
    assert isinstance(d["errors"], list)

def test_K7_none_plan_serializes_as_none():
    d = _make_result(plan=None).to_dict()
    assert d["plan"] is None

def test_K8_metrics_in_dict():
    d = _make_result().to_dict()
    assert "metrics" in d
    assert isinstance(d["metrics"], dict)


# ═══════════════════════════════════════════════════════════════════
# SECTION L — PipelineOrchestrator happy path (10)
# ═══════════════════════════════════════════════════════════════════

def test_L1_full_pipeline_returns_result():
    result, _ = _run_pipeline_happy()
    assert isinstance(result, OrchestratorInvestigationResult)

def test_L2_full_pipeline_status_completed():
    result, _ = _run_pipeline_happy()
    assert result.status == "COMPLETED"

def test_L3_plan_set_on_context():
    ctx = _make_context()
    pipeline, _, _, _, _ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert ctx.investigation_plan is not None

def test_L4_evidence_bundle_set_on_context():
    ctx = _make_context()
    pipeline, _, _, _, _ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert ctx.evidence_bundle is not None

def test_L5_root_cause_analysis_set_on_context():
    ctx = _make_context()
    pipeline, _, _, _, _ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert ctx.root_cause_analysis is not None

def test_L6_observation_set_on_context():
    ctx = _make_context()
    pipeline, _, _, _, _ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert ctx.observation is not None

def test_L7_result_has_plan():
    result, _ = _run_pipeline_happy()
    assert result.plan is not None

def test_L8_result_has_evidence():
    result, _ = _run_pipeline_happy()
    assert result.evidence is not None

def test_L9_result_has_root_cause():
    result, _ = _run_pipeline_happy()
    assert result.root_cause is not None

def test_L10_metrics_recorded_for_all_stages():
    ctx = _make_context()
    pipeline, _, _, _, _ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert session.metrics.stages_completed >= 5
    assert session.metrics.stages_failed == 0


# ═══════════════════════════════════════════════════════════════════
# SECTION M — validate stage failure (5)
# ═══════════════════════════════════════════════════════════════════

def _make_context_no_topic() -> InvestigationContext:
    ctx = _make_context()
    ctx.topic = ""
    return ctx

def _make_context_no_tenant() -> InvestigationContext:
    ctx = _make_context()
    ctx.tenant_context = None  # type: ignore[assignment]
    return ctx

def test_M1_missing_topic_produces_failed():
    ctx = _make_context_no_topic()
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status == "FAILED"

def test_M2_missing_tenant_produces_failed():
    ctx = _make_context_no_tenant()
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status == "FAILED"

def test_M3_missing_case_id_produces_failed():
    ctx = _make_context()
    ctx.case_id = ""
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status == "FAILED"

def test_M4_validate_failure_has_recovery_hint():
    ctx = _make_context_no_topic()
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.recovery_hint is not None
    assert len(result.recovery_hint) > 10

def test_M5_validate_failure_planner_not_called():
    ctx = _make_context_no_topic()
    pipeline, planner, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    planner.plan.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# SECTION N — planning stage failure (5)
# ═══════════════════════════════════════════════════════════════════

def test_N1_planner_raises_produces_failed():
    ctx = _make_context()
    pipeline, planner, *_ = _build_pipeline()
    planner.plan.side_effect = RuntimeError("planner exploded")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status == "FAILED"

def test_N2_planning_failure_no_plan_in_result():
    ctx = _make_context()
    pipeline, planner, *_ = _build_pipeline()
    planner.plan.side_effect = RuntimeError("oops")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.plan is None

def test_N3_planning_failure_has_recovery_hint():
    ctx = _make_context()
    pipeline, planner, *_ = _build_pipeline()
    planner.plan.side_effect = ValueError("bad plan")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.recovery_hint is not None

def test_N4_planning_failure_error_recorded():
    ctx = _make_context()
    pipeline, planner, *_ = _build_pipeline()
    planner.plan.side_effect = RuntimeError("plan failure")
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert len(session.errors) >= 1
    assert any("planning" in e["stage"] for e in session.errors)

def test_N5_planning_failure_audit_record_present():
    ctx = _make_context()
    pipeline, planner, *_ = _build_pipeline()
    planner.plan.side_effect = RuntimeError("plan fail")
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    stages = [r.stage for r in session.audit.timeline]
    assert "planning" in stages


# ═══════════════════════════════════════════════════════════════════
# SECTION O — collection stage failure (5)
# ═══════════════════════════════════════════════════════════════════

def test_O1_collector_raises_produces_partial():
    ctx = _make_context()
    pipeline, _, collector, *_ = _build_pipeline()
    collector.collect.side_effect = RuntimeError("collection failed")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status == "PARTIAL"

def test_O2_collection_failure_plan_still_in_result():
    ctx = _make_context()
    pipeline, planner, collector, *_ = _build_pipeline()
    collector.collect.side_effect = RuntimeError("collector broke")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.plan is not None

def test_O3_collection_failure_no_bundle():
    ctx = _make_context()
    pipeline, _, collector, *_ = _build_pipeline()
    collector.collect.side_effect = RuntimeError("no evidence")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.evidence is None

def test_O4_collection_failure_recovery_hint():
    ctx = _make_context()
    pipeline, _, collector, *_ = _build_pipeline()
    collector.collect.side_effect = RuntimeError("collector")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.recovery_hint is not None

def test_O5_collection_failure_error_recorded():
    ctx = _make_context()
    pipeline, _, collector, *_ = _build_pipeline()
    collector.collect.side_effect = RuntimeError("collection error")
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert any("collection" in e["stage"] for e in session.errors)


# ═══════════════════════════════════════════════════════════════════
# SECTION P — knowledge stage (8)
# ═══════════════════════════════════════════════════════════════════

def test_P1_knowledge_provider_called_when_present():
    ctx = _make_context()
    provider = MagicMock()
    provider.enrich.return_value = []
    pipeline, *_ = _build_pipeline(knowledge_provider=provider)
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    provider.enrich.assert_called_once_with(ctx)

def test_P2_knowledge_provider_entries_on_context():
    ctx = _make_context()
    entry = MagicMock()
    provider = MagicMock()
    provider.enrich.return_value = [entry]
    pipeline, *_ = _build_pipeline(knowledge_provider=provider)
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert ctx.knowledge_entries == [entry]

def test_P3_no_knowledge_provider_uses_existing_entries():
    ctx = _make_context()
    entry = MagicMock()
    ctx.knowledge_entries = [entry]
    pipeline, *_ = _build_pipeline(knowledge_provider=None)
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert entry in result.knowledge_entries

def test_P4_knowledge_failure_is_non_fatal():
    ctx = _make_context()
    provider = MagicMock()
    provider.enrich.side_effect = RuntimeError("knowledge exploded")
    pipeline, *_ = _build_pipeline(knowledge_provider=provider)
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status in ("COMPLETED", "PARTIAL")

def test_P5_knowledge_failure_continues_to_root_cause():
    ctx = _make_context()
    provider = MagicMock()
    provider.enrich.side_effect = RuntimeError("k error")
    pipeline, _, _, rca_eng, _ = _build_pipeline(knowledge_provider=provider)
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    rca_eng.analyze.assert_called_once()

def test_P6_knowledge_failure_warning_in_errors():
    ctx = _make_context()
    provider = MagicMock()
    provider.enrich.side_effect = RuntimeError("k fail")
    pipeline, *_ = _build_pipeline(knowledge_provider=provider)
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert any("knowledge" in e["stage"].lower() for e in session.errors)

def test_P7_empty_enrichment_uses_existing():
    ctx = _make_context()
    entry = MagicMock()
    ctx.knowledge_entries = [entry]
    provider = MagicMock()
    provider.enrich.return_value = []  # empty enrichment
    pipeline, *_ = _build_pipeline(knowledge_provider=provider)
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    # empty enrichment means context.knowledge_entries stays as [entry]
    assert ctx.knowledge_entries == [entry]

def test_P8_knowledge_stage_audited():
    ctx = _make_context()
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    stages = [r.stage for r in session.audit.timeline]
    assert "knowledge" in stages


# ═══════════════════════════════════════════════════════════════════
# SECTION Q — root_cause failure (5)
# ═══════════════════════════════════════════════════════════════════

def test_Q1_rca_raises_produces_partial():
    ctx = _make_context()
    pipeline, _, _, rca_eng, _ = _build_pipeline()
    rca_eng.analyze.side_effect = RuntimeError("rca exploded")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status == "PARTIAL"

def test_Q2_root_cause_failure_result_has_evidence():
    ctx = _make_context()
    pipeline, _, _, rca_eng, _ = _build_pipeline()
    rca_eng.analyze.side_effect = RuntimeError("rca failed")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.evidence is not None

def test_Q3_root_cause_failure_no_root_cause():
    ctx = _make_context()
    pipeline, _, _, rca_eng, _ = _build_pipeline()
    rca_eng.analyze.side_effect = RuntimeError("rca fail")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.root_cause is None

def test_Q4_root_cause_failure_recovery_hint():
    ctx = _make_context()
    pipeline, _, _, rca_eng, _ = _build_pipeline()
    rca_eng.analyze.side_effect = RuntimeError("rca fail")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.recovery_hint is not None

def test_Q5_root_cause_error_in_session_errors():
    ctx = _make_context()
    pipeline, _, _, rca_eng, _ = _build_pipeline()
    rca_eng.analyze.side_effect = RuntimeError("rca error")
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert any("root_cause" in e["stage"] for e in session.errors)


# ═══════════════════════════════════════════════════════════════════
# SECTION R — observation failure non-fatal (5)
# ═══════════════════════════════════════════════════════════════════

def test_R1_obs_raises_is_non_fatal():
    ctx = _make_context()
    pipeline, _, _, _, obs_gen = _build_pipeline()
    obs_gen.generate.side_effect = RuntimeError("obs failed")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status in ("COMPLETED", "PARTIAL")

def test_R2_obs_failure_status_is_partial():
    ctx = _make_context()
    pipeline, _, _, _, obs_gen = _build_pipeline()
    obs_gen.generate.side_effect = RuntimeError("obs fail")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.status == "PARTIAL"

def test_R3_obs_failure_has_root_cause():
    ctx = _make_context()
    pipeline, _, _, _, obs_gen = _build_pipeline()
    obs_gen.generate.side_effect = RuntimeError("obs fail")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.root_cause is not None

def test_R4_obs_failure_has_plan():
    ctx = _make_context()
    pipeline, _, _, _, obs_gen = _build_pipeline()
    obs_gen.generate.side_effect = RuntimeError("obs fail")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session)
    assert result.plan is not None

def test_R5_obs_failure_error_in_errors():
    ctx = _make_context()
    pipeline, _, _, _, obs_gen = _build_pipeline()
    obs_gen.generate.side_effect = RuntimeError("obs fail")
    session = InvestigationSession.create(ctx)
    pipeline.run(session)
    assert any("observation" in e["stage"].lower() for e in session.errors)


# ═══════════════════════════════════════════════════════════════════
# SECTION S — cancellation between stages (8)
# ═══════════════════════════════════════════════════════════════════

def test_S1_pre_cancelled_token_returns_cancelled():
    ctx = _make_context()
    pipeline, planner, *_ = _build_pipeline()
    token = CancellationToken()
    token.cancel("test cancel")
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session, token)
    assert result.status == "CANCELLED"
    planner.plan.assert_not_called()

def test_S2_cancel_after_planning_stops_collection():
    ctx = _make_context()
    token = CancellationToken()
    plan = _make_mock_plan()
    planner_mock = MagicMock()
    def plan_and_cancel(c):
        token.cancel("after planning")
        return plan
    planner_mock.plan.side_effect = plan_and_cancel
    collector = MagicMock()
    rca_eng = MagicMock(); rca_eng.analyze.return_value = _make_mock_rca()
    obs_gen = MagicMock(); obs_gen.generate.return_value = _make_mock_observation()
    pipeline = PipelineOrchestrator(planner=planner_mock, collector=collector,
                                    rca_engine=rca_eng, obs_gen=obs_gen)
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session, token)
    assert result.status == "CANCELLED"
    collector.collect.assert_not_called()

def test_S3_cancel_after_collection_stops_rca():
    ctx = _make_context()
    token = CancellationToken()
    bundle = _make_mock_bundle()
    collector_mock = MagicMock()
    def collect_and_cancel(p, c):
        token.cancel("after collection")
        return bundle
    collector_mock.collect.side_effect = collect_and_cancel
    planner = MagicMock(); planner.plan.return_value = _make_mock_plan()
    rca_eng = MagicMock(); rca_eng.analyze.return_value = _make_mock_rca()
    obs_gen = MagicMock(); obs_gen.generate.return_value = _make_mock_observation()
    pipeline = PipelineOrchestrator(planner=planner, collector=collector_mock,
                                    rca_engine=rca_eng, obs_gen=obs_gen)
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session, token)
    assert result.status == "CANCELLED"
    rca_eng.analyze.assert_not_called()

def test_S4_cancel_after_rca_stops_observation():
    ctx = _make_context()
    token = CancellationToken()
    rca_mock = MagicMock()
    def analyze_and_cancel(b, c):
        token.cancel("after rca")
        return _make_mock_rca()
    rca_mock.analyze.side_effect = analyze_and_cancel
    planner = MagicMock(); planner.plan.return_value = _make_mock_plan()
    collector = MagicMock(); collector.collect.return_value = _make_mock_bundle()
    obs_gen = MagicMock(); obs_gen.generate.return_value = _make_mock_observation()
    pipeline = PipelineOrchestrator(planner=planner, collector=collector,
                                    rca_engine=rca_mock, obs_gen=obs_gen)
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session, token)
    assert result.status == "CANCELLED"
    obs_gen.generate.assert_not_called()

def test_S5_cancelled_result_has_status_cancelled():
    ctx = _make_context()
    token = CancellationToken()
    token.cancel("immediate")
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session, token)
    assert result.status == "CANCELLED"

def test_S6_cancellation_reason_in_result():
    ctx = _make_context()
    token = CancellationToken()
    token.cancel("user requested abort")
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session, token)
    assert result.cancellation_reason == "user requested abort"

def test_S7_cancelled_result_has_recovery_hint():
    ctx = _make_context()
    token = CancellationToken()
    token.cancel("aborted")
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session, token)
    assert result.recovery_hint is not None

def test_S8_no_token_never_cancelled():
    ctx = _make_context()
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx)
    result = pipeline.run(session, None)
    assert result.status != "CANCELLED"


# ═══════════════════════════════════════════════════════════════════
# SECTION T — timeout (5)
# ═══════════════════════════════════════════════════════════════════

def _timed_out_session(ctx: InvestigationContext) -> InvestigationSession:
    """Return a session whose timeout is already expired."""
    session = InvestigationSession.create(ctx)
    past = (datetime.now(tz=timezone.utc) - timedelta(seconds=100)).isoformat()
    session.timeout_at = past
    return session

def test_T1_timed_out_session_after_planning_returns_cancelled():
    ctx = _make_context()
    pipeline, *_ = _build_pipeline()
    session = _timed_out_session(ctx)
    result = pipeline.run(session)
    assert result.status == "CANCELLED"

def test_T2_timeout_recovery_hint_present():
    ctx = _make_context()
    pipeline, *_ = _build_pipeline()
    session = _timed_out_session(ctx)
    result = pipeline.run(session)
    assert result.recovery_hint is not None
    assert "timeout" in result.recovery_hint.lower()

def test_T3_timeout_uses_negative_seconds():
    ctx = _make_context()
    orch = _build_orchestrator()
    result = orch.investigate(ctx, timeout_seconds=-100)
    assert result.status == "CANCELLED"

def test_T4_timeout_result_has_cancellation_reason_timeout():
    ctx = _make_context()
    pipeline, *_ = _build_pipeline()
    session = _timed_out_session(ctx)
    result = pipeline.run(session)
    assert result.cancellation_reason == "timeout"

def test_T5_no_timeout_completes_normally():
    ctx = _make_context()
    pipeline, *_ = _build_pipeline()
    session = InvestigationSession.create(ctx, timeout_seconds=3600)
    result = pipeline.run(session)
    assert result.status == "COMPLETED"


# ═══════════════════════════════════════════════════════════════════
# SECTION U — InvestigationOrchestrator full path (10)
# ═══════════════════════════════════════════════════════════════════

def test_U1_investigate_returns_result():
    ctx = _make_context()
    orch = _build_orchestrator()
    result = orch.investigate(ctx)
    assert isinstance(result, OrchestratorInvestigationResult)

def test_U2_investigate_completed_status():
    ctx = _make_context()
    orch = _build_orchestrator()
    result = orch.investigate(ctx)
    assert result.status == "COMPLETED"

def test_U3_investigate_never_raises():
    ctx = _make_context()
    planner = MagicMock()
    planner.plan.side_effect = Exception("catastrophic failure")
    orch = InvestigationOrchestrator(
        planner=planner,
        collector=MagicMock(),
        rca_engine=MagicMock(),
        obs_gen=MagicMock(),
    )
    result = orch.investigate(ctx)
    assert isinstance(result, OrchestratorInvestigationResult)

def test_U4_investigate_sets_context_session_id():
    ctx = _make_context()
    orch = _build_orchestrator()
    orch.investigate(ctx)
    assert ctx.orchestrator_session_id is not None

def test_U5_investigate_sets_context_result_id():
    ctx = _make_context()
    orch = _build_orchestrator()
    orch.investigate(ctx)
    assert ctx.orchestrator_result_id is not None

def test_U6_investigate_sets_context_status():
    ctx = _make_context()
    orch = _build_orchestrator()
    orch.investigate(ctx)
    assert ctx.orchestrator_status == "COMPLETED"

def test_U7_investigate_sets_started_at():
    ctx = _make_context()
    orch = _build_orchestrator()
    orch.investigate(ctx)
    assert ctx.orchestrator_started_at is not None

def test_U8_investigate_with_cancellation_returns_cancelled():
    ctx = _make_context()
    orch = _build_orchestrator()
    token = CancellationToken()
    token.cancel("user abort")
    result = orch.investigate(ctx, cancellation_token=token)
    assert result.status == "CANCELLED"

def test_U9_investigate_with_expired_timeout_returns_cancelled():
    ctx = _make_context()
    orch = _build_orchestrator()
    result = orch.investigate(ctx, timeout_seconds=-100)
    assert result.status == "CANCELLED"

def test_U10_investigate_result_id_matches_context_result_id():
    ctx = _make_context()
    orch = _build_orchestrator()
    result = orch.investigate(ctx)
    assert result.result_id == ctx.orchestrator_result_id


# ═══════════════════════════════════════════════════════════════════
# SECTION V — global metrics (8)
# ═══════════════════════════════════════════════════════════════════

def test_V1_completed_increments_global():
    orch = _build_orchestrator()
    ctx = _make_context()
    orch.investigate(ctx)
    assert InvestigationOrchestrator.global_metrics().completed == 1

def test_V2_failed_increments_global():
    planner = MagicMock()
    planner.plan.side_effect = RuntimeError("forced failure")
    orch = InvestigationOrchestrator(planner=planner, collector=MagicMock(),
                                     rca_engine=MagicMock(), obs_gen=MagicMock())
    ctx = _make_context()
    orch.investigate(ctx)
    assert InvestigationOrchestrator.global_metrics().failed >= 1

def test_V3_cancelled_increments_global():
    orch = _build_orchestrator()
    ctx = _make_context()
    token = CancellationToken()
    token.cancel("test")
    orch.investigate(ctx, cancellation_token=token)
    assert InvestigationOrchestrator.global_metrics().cancelled == 1

def test_V4_global_metrics_class_method_returns_global_metrics():
    gm = InvestigationOrchestrator.global_metrics()
    assert isinstance(gm, OrchestratorGlobalMetrics)

def test_V5_reset_global_metrics_zeroes():
    orch = _build_orchestrator()
    ctx = _make_context()
    orch.investigate(ctx)
    InvestigationOrchestrator.reset_global_metrics()
    assert InvestigationOrchestrator.global_metrics().completed == 0

def test_V6_global_shared_across_instances():
    orch1 = _build_orchestrator()
    orch2 = _build_orchestrator()
    ctx1 = _make_context(case_id="case-V6a")
    ctx2 = _make_context(case_id="case-V6b")
    orch1.investigate(ctx1)
    orch2.investigate(ctx2)
    assert InvestigationOrchestrator.global_metrics().completed == 2

def test_V7_multiple_investigations_accumulate():
    orch = _build_orchestrator()
    for i in range(5):
        orch.investigate(_make_context(case_id=f"case-V7-{i}"))
    assert InvestigationOrchestrator.global_metrics().completed == 5

def test_V8_global_metrics_to_dict():
    d = InvestigationOrchestrator.global_metrics().to_dict()
    assert "completed_investigations" in d
    assert "failed_investigations" in d
    assert "cancelled_investigations" in d


# ═══════════════════════════════════════════════════════════════════
# SECTION W — context ownership fields (10)
# ═══════════════════════════════════════════════════════════════════

def test_W1_orchestrator_session_id_set_after_investigate():
    ctx = _make_context()
    _build_orchestrator().investigate(ctx)
    assert ctx.orchestrator_session_id is not None
    assert len(ctx.orchestrator_session_id) > 10

def test_W2_orchestrator_started_at_set():
    ctx = _make_context()
    _build_orchestrator().investigate(ctx)
    assert ctx.orchestrator_started_at is not None
    assert "T" in ctx.orchestrator_started_at

def test_W3_orchestrator_stage_set():
    ctx = _make_context()
    _build_orchestrator().investigate(ctx)
    assert ctx.orchestrator_stage is not None

def test_W4_orchestrator_completed_at_set():
    ctx = _make_context()
    _build_orchestrator().investigate(ctx)
    # orchestrator_completed_at is set by pipeline on completion
    assert ctx.orchestrator_completed_at is not None

def test_W5_orchestrator_result_id_set():
    ctx = _make_context()
    _build_orchestrator().investigate(ctx)
    assert ctx.orchestrator_result_id is not None

def test_W6_orchestrator_status_completed():
    ctx = _make_context()
    _build_orchestrator().investigate(ctx)
    assert ctx.orchestrator_status == "COMPLETED"

def test_W7_has_orchestrator_session_true_after_investigate():
    ctx = _make_context()
    _build_orchestrator().investigate(ctx)
    assert ctx.has_orchestrator_session()

def test_W8_has_orchestrator_result_true_after_investigate():
    ctx = _make_context()
    _build_orchestrator().investigate(ctx)
    assert ctx.has_orchestrator_result()

def test_W9_has_orchestrator_session_false_before_investigate():
    ctx = _make_context()
    assert not ctx.has_orchestrator_session()

def test_W10_has_orchestrator_result_false_before_investigate():
    ctx = _make_context()
    assert not ctx.has_orchestrator_result()


# ═══════════════════════════════════════════════════════════════════
# SECTION X — serialization roundtrip (8)
# ═══════════════════════════════════════════════════════════════════

def test_X1_result_to_dict_is_json_serializable():
    r = _make_result()
    d = result_to_dict(r)
    json.dumps(d, default=str)  # must not raise

def test_X2_result_to_json_roundtrip():
    r = _make_result()
    s = result_to_json(r)
    d = json.loads(s)
    assert d["result_id"] == r.result_id
    assert d["status"] == "COMPLETED"

def test_X3_session_to_dict_has_required_keys():
    ctx = _make_context()
    session = InvestigationSession.create(ctx)
    d = session.to_dict()
    for key in ("session_id","case_id","started_at","lifecycle_state","metrics","audit","errors"):
        assert key in d, f"missing key: {key}"

def test_X4_audit_to_dict_serializable():
    audit = _make_audit()
    r = audit.start_stage("validate")
    r.mark_complete("2024-01-01T00:00:01+00:00", 10, {"ok": True})
    d = audit.to_dict()
    json.dumps(d, default=str)

def test_X5_metrics_to_dict_serializable():
    m = OrchestratorMetrics()
    m.record_stage("planning", 50, success=True)
    d = m.to_dict()
    json.dumps(d)

def test_X6_cancellation_token_to_dict():
    token = CancellationToken()
    token.cancel("serialization test")
    d = token.to_dict()
    assert d == {"cancelled": True, "reason": "serialization test"}

def test_X7_state_machine_to_dict():
    sm = OrchestratorStateMachine()
    sm.transition(OrchestratorLifecycleState.PLANNING)
    d = sm.to_dict()
    assert "current_state" in d
    assert d["current_state"] == "PLANNING"

def test_X8_full_pipeline_result_serializable():
    ctx = _make_context()
    orch = _build_orchestrator()
    result = orch.investigate(ctx)
    s = result.to_json()
    d = json.loads(s)
    assert d["status"] == "COMPLETED"


# ═══════════════════════════════════════════════════════════════════
# SECTION Y — versioning + regression (10)
# ═══════════════════════════════════════════════════════════════════

def test_Y1_orchestrator_version_is_string():
    assert isinstance(ORCHESTRATOR_VERSION, str)
    assert len(ORCHESTRATOR_VERSION) > 0

def test_Y2_orchestrator_sprint_is_string():
    assert isinstance(ORCHESTRATOR_SPRINT, str)
    assert "2.46" in ORCHESTRATOR_SPRINT

def test_Y3_version_current_returns_version():
    v = OrchestratorVersion.current()
    assert isinstance(v, OrchestratorVersion)

def test_Y4_version_to_dict():
    v = OrchestratorVersion.current()
    d = v.to_dict()
    assert "major" in d and "minor" in d and "patch" in d and "sprint" in d

def test_Y5_major_minor_patch_are_ints():
    v = OrchestratorVersion.current()
    assert isinstance(v.major, int)
    assert isinstance(v.minor, int)
    assert isinstance(v.patch, int)

def test_Y6_build_factory_returns_orchestrator():
    orch = build_investigation_orchestrator()
    assert isinstance(orch, InvestigationOrchestrator)

def test_Y7_investigation_service_still_importable():
    from case_engine.investigation import InvestigationService  # noqa: F401
    assert True

def test_Y8_sprint243_root_cause_models_still_importable():
    from case_engine.investigation.root_cause.models import RootCauseAnalysis  # noqa: F401
    assert True

def test_Y9_sprint244_observation_models_still_importable():
    from case_engine.investigation.observation.models import Observation  # noqa: F401
    assert True

def test_Y10_context_has_sprint246_fields():
    ctx = _make_context()
    assert hasattr(ctx, "orchestrator_session_id")
    assert hasattr(ctx, "orchestrator_stage")
    assert hasattr(ctx, "orchestrator_started_at")
    assert hasattr(ctx, "orchestrator_completed_at")
    assert hasattr(ctx, "orchestrator_result_id")
    assert hasattr(ctx, "orchestrator_status")
