"""
case_engine/investigation/orchestrator/pipeline.py

Sprint 2.46: PipelineOrchestrator — deterministic stage executor.

Pipeline sequence (PART E):
  1. validate   — check InvestigationContext has required fields
  2. planning   — InvestigationPlanner.plan(context) → InvestigationPlan
  3. collection — EvidenceCollector.collect(plan, context) → EvidenceBundle
  4. knowledge  — KnowledgeEnrichmentProvider.enrich(context) [optional, non-fatal]
  5. root_cause — RootCauseEngine.analyze(bundle) → RootCauseAnalysis
  6. observation — ObservationGenerator.generate(analysis, bundle, context) [non-fatal]

Failure Strategy (PART F):
  - validate failure  → FAILED result (no recovery — context is invalid)
  - planning failure  → FAILED result
  - collection failure → PARTIAL result (evidence collection incomplete)
  - knowledge failure  → WARNING logged, continue with existing context.knowledge_entries
  - root_cause failure → PARTIAL result (cannot reason without evidence)
  - observation failure → continue — PARTIAL or COMPLETED based on prior stages

Cancellation (PART G):
  - Checked between every stage via CancellationToken.is_cancelled
  - Timeout checked via InvestigationSession.is_timed_out()
  - Both produce CANCELLED status with recovery_hint

Audit (PART J):
  - Every stage creates an AuditStageRecord via session.audit.start_stage()
  - Records: started_at, completed_at/failed_at, duration_ms, input_summary (no PII)
  - input_summary stores key names / structural info only

Dependency direction:
  pipeline.py → orchestrator/models.py (CancellationToken, InvestigationSession, OrchestratorInvestigationResult)
  pipeline.py → orchestrator/state_machine.py (OrchestratorLifecycleState)
  pipeline.py → orchestrator/exceptions.py (InvalidContextError)
  pipeline.py → investigation/context.py via TYPE_CHECKING
  pipeline.py does NOT import from: workflows/, tenant/, action_gateway/, execution/
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable, TYPE_CHECKING

from case_engine.investigation.orchestrator.exceptions import InvalidContextError
from case_engine.investigation.orchestrator.models import (
    CancellationToken,
    InvestigationSession,
    OrchestratorInvestigationResult,
)
from case_engine.investigation.orchestrator.state_machine import OrchestratorLifecycleState

if TYPE_CHECKING:
    from case_engine.investigation.context import InvestigationContext

LOGGER = logging.getLogger(__name__)

# ── Stage name constants ───────────────────────────────────────────────────────

_STAGE_VALIDATE    = "validate"
_STAGE_PLANNING    = "planning"
_STAGE_COLLECTION  = "collection"
_STAGE_KNOWLEDGE   = "knowledge"
_STAGE_ROOT_CAUSE  = "root_cause"
_STAGE_OBSERVATION = "observation"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _ms(start: float) -> int:
    return max(0, int((time.monotonic() - start) * 1000))


# ── Service Protocols ──────────────────────────────────────────────────────────

@runtime_checkable
class PlannerProtocol(Protocol):
    """Structural protocol for InvestigationPlanner (Sprint 2.39)."""
    def plan(self, context: Any) -> Any: ...


@runtime_checkable
class CollectorProtocol(Protocol):
    """Structural protocol for EvidenceCollector (Sprint 2.42)."""
    def collect(self, plan: Any, context: Any) -> Any: ...


@runtime_checkable
class RootCauseEngineProtocol(Protocol):
    """Structural protocol for RootCauseEngine (Sprint 2.43)."""
    def analyze(self, bundle: Any, context: Any = ...) -> Any: ...


@runtime_checkable
class ObservationGeneratorProtocol(Protocol):
    """Structural protocol for ObservationGenerator (Sprint 2.44)."""
    def generate(self, analysis: Any, bundle: Any = ..., context: Any = ...) -> Any: ...


@runtime_checkable
class KnowledgeEnrichmentProvider(Protocol):
    """
    Structural protocol for knowledge enrichment during orchestration.

    If provided, the knowledge stage calls enrich(context) to populate
    context.knowledge_entries with additional entries for this investigation.
    If absent, the knowledge stage is a no-op (existing entries are used).
    """
    def enrich(self, context: Any) -> list: ...


# ── Pipeline ───────────────────────────────────────────────────────────────────

class PipelineOrchestrator:
    """
    Internal, stateless pipeline executor.

    One instance is created per InvestigationOrchestrator and reused.
    Each run() call operates on a unique InvestigationSession — no shared state.
    """

    def __init__(
        self,
        planner:             PlannerProtocol,
        collector:           CollectorProtocol,
        rca_engine:          RootCauseEngineProtocol,
        obs_gen:             ObservationGeneratorProtocol,
        knowledge_provider:  KnowledgeEnrichmentProvider | None = None,
    ) -> None:
        self._planner           = planner
        self._collector         = collector
        self._rca               = rca_engine
        self._obs               = obs_gen
        self._knowledge         = knowledge_provider

    def run(
        self,
        session:            InvestigationSession,
        cancellation_token: CancellationToken | None = None,
    ) -> OrchestratorInvestigationResult:
        """
        Execute the full investigation pipeline for this session. Never raises.

        Returns OrchestratorInvestigationResult in all cases.
        Status is COMPLETED, PARTIAL, FAILED, or CANCELLED.
        """
        pipeline_start = time.monotonic()
        context        = session.context

        plan       : Any       = None
        bundle     : Any       = None
        knowledge  : list      = []
        root_cause : Any       = None
        observation: Any       = None

        # ── Stage 1: Validate ────────────────────────────────────────────────

        record = session.audit.start_stage(_STAGE_VALIDATE, {
            "case_id": context.case_id,
            "topic":   context.topic,
        })
        t0 = time.monotonic()
        try:
            self._validate_context(context)
            dur = _ms(t0)
            record.mark_complete(_now_iso(), dur, {"ok": True})
            session.metrics.record_stage(_STAGE_VALIDATE, dur, success=True)
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            record.mark_failed(_now_iso(), dur, "CONTEXT_INVALID", str(exc))
            session.metrics.record_stage(_STAGE_VALIDATE, dur, success=False)
            session.add_error(_STAGE_VALIDATE, "CONTEXT_INVALID", str(exc))
            session.state_machine.force_terminal(OrchestratorLifecycleState.FAILED)
            return self._build_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                status="FAILED",
                recovery_hint=(
                    "InvestigationContext is missing required fields (topic, tenant_context, case_id). "
                    "Ensure the context is fully populated before invoking the orchestrator."
                ),
            )

        # ── Check cancellation before first real stage ───────────────────────

        if self._is_cancelled(cancellation_token):
            return self._cancel_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                cancellation_token,
            )

        # ── Stage 2: Planning ────────────────────────────────────────────────

        session.state_machine.transition(OrchestratorLifecycleState.PLANNING)
        context.orchestrator_stage = _STAGE_PLANNING

        record = session.audit.start_stage(_STAGE_PLANNING, {
            "topic":        context.topic,
            "has_sop":      str(getattr(context, "sop_match_found", False)),
            "has_playbook": str(getattr(context, "workflow_playbook", None) is not None),
        })
        t0 = time.monotonic()
        try:
            plan = self._planner.plan(context)
            context.investigation_plan = plan
            dur = _ms(t0)
            record.mark_complete(_now_iso(), dur, {
                "plan_id":    str(getattr(plan, "plan_id", "?")),
                "step_count": str(len(getattr(plan, "steps", []))),
                "topic":      str(getattr(plan, "topic", context.topic)),
            })
            session.metrics.record_stage(_STAGE_PLANNING, dur, success=True)
            LOGGER.debug(
                "pipeline.planning ok case_id=%s plan_id=%s steps=%d",
                context.case_id, getattr(plan, "plan_id", "?"),
                len(getattr(plan, "steps", [])),
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            record.mark_failed(_now_iso(), dur, "PLANNING_ERROR", str(exc))
            session.metrics.record_stage(_STAGE_PLANNING, dur, success=False)
            session.add_error(_STAGE_PLANNING, "PLANNING_ERROR", str(exc))
            LOGGER.exception("pipeline.planning failed case_id=%s", context.case_id)
            session.state_machine.force_terminal(OrchestratorLifecycleState.FAILED)
            return self._build_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                status="FAILED",
                recovery_hint=(
                    "Investigation planning failed. Verify that a playbook is registered "
                    "for this topic and that the InvestigationContext has a valid topic."
                ),
            )

        # ── Check cancellation / timeout ─────────────────────────────────────

        if self._is_cancelled(cancellation_token):
            return self._cancel_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                cancellation_token,
            )
        if session.is_timed_out():
            return self._timeout_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
            )

        # ── Stage 3: Collection ──────────────────────────────────────────────

        session.state_machine.transition(OrchestratorLifecycleState.COLLECTING)
        context.orchestrator_stage = _STAGE_COLLECTION

        record = session.audit.start_stage(_STAGE_COLLECTION, {
            "plan_id":    str(getattr(plan, "plan_id", "?")),
            "step_count": str(len(getattr(plan, "steps", []))),
        })
        t0 = time.monotonic()
        try:
            bundle = self._collector.collect(plan, context)
            context.evidence_bundle = bundle
            dur = _ms(t0)
            record.mark_complete(_now_iso(), dur, {
                "bundle_id":  str(getattr(bundle, "bundle_id", "?")),
                "item_count": str(len(getattr(bundle, "items", []))),
            })
            session.metrics.record_stage(_STAGE_COLLECTION, dur, success=True)
            LOGGER.debug(
                "pipeline.collection ok case_id=%s bundle_id=%s items=%d",
                context.case_id, getattr(bundle, "bundle_id", "?"),
                len(getattr(bundle, "items", [])),
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            record.mark_failed(_now_iso(), dur, "COLLECTION_ERROR", str(exc))
            session.metrics.record_stage(_STAGE_COLLECTION, dur, success=False)
            session.add_error(_STAGE_COLLECTION, "COLLECTION_ERROR", str(exc))
            LOGGER.exception("pipeline.collection failed case_id=%s", context.case_id)
            session.state_machine.force_terminal(OrchestratorLifecycleState.FAILED)
            return self._build_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                status="PARTIAL",
                recovery_hint=(
                    "Evidence collection failed. Retry with complete slot values "
                    "or check tool executor health."
                ),
            )

        # ── Check cancellation / timeout ─────────────────────────────────────

        if self._is_cancelled(cancellation_token):
            return self._cancel_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                cancellation_token,
            )
        if session.is_timed_out():
            return self._timeout_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
            )

        # ── Stage 4: Knowledge ───────────────────────────────────────────────

        session.state_machine.transition(OrchestratorLifecycleState.KNOWLEDGE)
        context.orchestrator_stage = _STAGE_KNOWLEDGE

        existing_count = len(getattr(context, "knowledge_entries", []))
        record = session.audit.start_stage(_STAGE_KNOWLEDGE, {
            "has_provider":      str(self._knowledge is not None),
            "existing_entries":  str(existing_count),
        })
        t0 = time.monotonic()
        try:
            if self._knowledge is not None:
                enriched = self._knowledge.enrich(context)
                if enriched:
                    context.knowledge_entries = list(enriched)
                knowledge = list(context.knowledge_entries)
            else:
                knowledge = list(getattr(context, "knowledge_entries", []))
            dur = _ms(t0)
            record.mark_complete(_now_iso(), dur, {
                "knowledge_count": str(len(knowledge)),
            })
            session.metrics.record_stage(_STAGE_KNOWLEDGE, dur, success=True)
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            knowledge = list(getattr(context, "knowledge_entries", []))
            record.mark_failed(_now_iso(), dur, "KNOWLEDGE_ERROR", str(exc))
            session.metrics.record_stage(_STAGE_KNOWLEDGE, dur, success=False)
            session.add_error(_STAGE_KNOWLEDGE, "KNOWLEDGE_WARNING", str(exc))
            LOGGER.warning(
                "pipeline.knowledge failed (non-fatal), continuing: %s", exc
            )

        # ── Check cancellation / timeout ─────────────────────────────────────

        if self._is_cancelled(cancellation_token):
            return self._cancel_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                cancellation_token,
            )
        if session.is_timed_out():
            return self._timeout_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
            )

        # ── Stage 5: Root Cause ──────────────────────────────────────────────

        session.state_machine.transition(OrchestratorLifecycleState.ROOT_CAUSE)
        context.orchestrator_stage = _STAGE_ROOT_CAUSE

        record = session.audit.start_stage(_STAGE_ROOT_CAUSE, {
            "bundle_id":  str(getattr(bundle, "bundle_id", "?")),
            "item_count": str(len(getattr(bundle, "items", []))),
        })
        t0 = time.monotonic()
        try:
            root_cause = self._rca.analyze(bundle, context)
            context.root_cause_analysis = root_cause
            dur = _ms(t0)
            category = getattr(root_cause, "category", "?")
            record.mark_complete(_now_iso(), dur, {
                "analysis_id": str(getattr(root_cause, "analysis_id", "?")),
                "category":    str(category),
                "confidence":  str(getattr(root_cause, "confidence", "?")),
            })
            session.metrics.record_stage(_STAGE_ROOT_CAUSE, dur, success=True)
            LOGGER.debug(
                "pipeline.root_cause ok case_id=%s analysis_id=%s category=%s",
                context.case_id, getattr(root_cause, "analysis_id", "?"), category,
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            record.mark_failed(_now_iso(), dur, "ROOT_CAUSE_ERROR", str(exc))
            session.metrics.record_stage(_STAGE_ROOT_CAUSE, dur, success=False)
            session.add_error(_STAGE_ROOT_CAUSE, "ROOT_CAUSE_ERROR", str(exc))
            LOGGER.exception("pipeline.root_cause failed case_id=%s", context.case_id)
            session.state_machine.force_terminal(OrchestratorLifecycleState.FAILED)
            return self._build_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                status="PARTIAL",
                recovery_hint=(
                    "Root cause analysis failed. Verify the EvidenceBundle is valid "
                    "and non-empty, then retry."
                ),
            )

        # ── Check cancellation / timeout ─────────────────────────────────────

        if self._is_cancelled(cancellation_token):
            return self._cancel_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
                cancellation_token,
            )
        if session.is_timed_out():
            return self._timeout_result(
                session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
            )

        # ── Stage 6: Observation ─────────────────────────────────────────────

        session.state_machine.transition(OrchestratorLifecycleState.OBSERVATION)
        context.orchestrator_stage = _STAGE_OBSERVATION

        record = session.audit.start_stage(_STAGE_OBSERVATION, {
            "analysis_id": str(getattr(root_cause, "analysis_id", "?")),
            "category":    str(getattr(root_cause, "category", "?")),
        })
        t0 = time.monotonic()
        try:
            observation = self._obs.generate(root_cause, bundle, context)
            context.observation = observation
            obs_status = getattr(observation, "status", None)
            context.observation_status = (
                getattr(obs_status, "value", str(obs_status)) if obs_status else None
            )
            context.observation_version  = getattr(observation, "generator_version", None)
            context.observation_timestamp = getattr(observation, "generated_at", None)
            dur = _ms(t0)
            record.mark_complete(_now_iso(), dur, {
                "observation_id": str(getattr(observation, "observation_id", "?")),
                "status":         str(obs_status),
            })
            session.metrics.record_stage(_STAGE_OBSERVATION, dur, success=True)
            LOGGER.debug(
                "pipeline.observation ok case_id=%s obs_id=%s",
                context.case_id, getattr(observation, "observation_id", "?"),
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            record.mark_failed(_now_iso(), dur, "OBSERVATION_ERROR", str(exc))
            session.metrics.record_stage(_STAGE_OBSERVATION, dur, success=False)
            session.add_error(_STAGE_OBSERVATION, "OBSERVATION_WARNING", str(exc))
            LOGGER.warning(
                "pipeline.observation failed (non-fatal), returning partial result: %s", exc
            )
            # Observation failure is non-fatal: we have plan, evidence, and root_cause.

        # ── Finalize ─────────────────────────────────────────────────────────

        session.state_machine.transition(OrchestratorLifecycleState.COMPLETED)
        context.orchestrator_stage = "completed"

        pipeline_dur          = _ms(pipeline_start)
        session.completed_at  = _now_iso()
        session.metrics.set_pipeline_duration(pipeline_dur)
        context.orchestrator_completed_at = session.completed_at
        context.orchestrator_status       = "COMPLETED"

        status = "COMPLETED" if observation is not None else "PARTIAL"

        LOGGER.info(
            "pipeline.complete case_id=%s topic=%s status=%s duration_ms=%d",
            context.case_id, context.topic, status, pipeline_dur,
        )
        return self._build_result(
            session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
            status=status,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _validate_context(self, context: Any) -> None:
        """Validate required InvestigationContext fields. Raises InvalidContextError."""
        if not getattr(context, "topic", ""):
            raise InvalidContextError(
                "InvestigationContext.topic is required and must be non-empty"
            )
        if getattr(context, "tenant_context", None) is None:
            raise InvalidContextError(
                "InvestigationContext.tenant_context is required"
            )
        if not getattr(context, "case_id", ""):
            raise InvalidContextError(
                "InvestigationContext.case_id is required and must be non-empty"
            )

    def _is_cancelled(self, token: CancellationToken | None) -> bool:
        return token is not None and token.is_cancelled

    def _build_result(
        self,
        session:        InvestigationSession,
        pipeline_start: float,
        plan:           Any,
        bundle:         Any,
        knowledge:      list,
        root_cause:     Any,
        observation:    Any,
        *,
        status:         str,
        recovery_hint:  str | None = None,
    ) -> OrchestratorInvestigationResult:
        """Build the canonical result from the session and stage outputs."""
        dur = _ms(pipeline_start)
        session.metrics.set_pipeline_duration(dur)
        now = _now_iso()
        if session.completed_at is None:
            session.completed_at = now

        context = session.context
        result_id = _new_id()
        context.orchestrator_result_id = result_id

        return OrchestratorInvestigationResult(
            result_id=result_id,
            session_id=session.session_id,
            case_id=session.case_id,
            topic=context.topic,
            status=status,
            plan=plan,
            evidence=bundle,
            knowledge_entries=list(knowledge),
            root_cause=root_cause,
            observation=observation,
            metrics=session.metrics,
            audit_timeline=session.audit,
            errors=list(session.errors),
            stage_timings=dict(session.metrics.stage_durations_ms),
            pipeline_duration_ms=dur,
            started_at=session.started_at,
            completed_at=session.completed_at,
            cancellation_reason=session.cancellation_reason,
            recovery_hint=recovery_hint,
        )

    def _cancel_result(
        self,
        session:            InvestigationSession,
        pipeline_start:     float,
        plan:               Any,
        bundle:             Any,
        knowledge:          list,
        root_cause:         Any,
        observation:        Any,
        token:              CancellationToken | None,
    ) -> OrchestratorInvestigationResult:
        reason = (token.reason if token is not None else "") or "caller requested cancellation"
        session.cancellation_reason = reason
        session.cancelled_at = _now_iso()
        session.state_machine.force_terminal(OrchestratorLifecycleState.CANCELLED)
        LOGGER.info(
            "pipeline.cancelled case_id=%s reason=%s", session.case_id, reason
        )
        return self._build_result(
            session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
            status="CANCELLED",
            recovery_hint="Investigation was cancelled. Retry when resources are available.",
        )

    def _timeout_result(
        self,
        session:        InvestigationSession,
        pipeline_start: float,
        plan:           Any,
        bundle:         Any,
        knowledge:      list,
        root_cause:     Any,
        observation:    Any,
    ) -> OrchestratorInvestigationResult:
        session.cancellation_reason = "timeout"
        session.cancelled_at = _now_iso()
        session.state_machine.force_terminal(OrchestratorLifecycleState.CANCELLED)
        LOGGER.warning("pipeline.timeout case_id=%s", session.case_id)
        return self._build_result(
            session, pipeline_start, plan, bundle, knowledge, root_cause, observation,
            status="CANCELLED",
            recovery_hint=(
                "Investigation timed out. Retry with a longer timeout_seconds "
                "or a simpler investigation plan."
            ),
        )
