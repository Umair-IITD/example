"""
case_engine/investigation/orchestrator/orchestrator.py

Sprint 2.46: InvestigationOrchestrator — production entry point for investigation.

This is the ONLY production entry point for investigation. All callers (Workflow
Engine, CaseService, admin routes) must go through this class. Direct use of
InvestigationService, InvestigationPlanner, EvidenceCollector, RootCauseEngine,
or ObservationGenerator for investigation purposes is NOT permitted in new code.

Architecture:
  InvestigationOrchestrator
    - owns one PipelineOrchestrator (injected on construction)
    - creates one InvestigationSession per investigate() call
    - never raises — all exceptions produce a FAILED result via _emergency_result()
    - updates OrchestratorGlobalMetrics after every call (class-level singleton)

Dependency direction:
  orchestrator.py → orchestrator/pipeline.py (PipelineOrchestrator, protocols)
  orchestrator.py → orchestrator/models.py (InvestigationSession, OrchestratorInvestigationResult)
  orchestrator.py → orchestrator/metrics.py (OrchestratorGlobalMetrics, OrchestratorMetrics)
  orchestrator.py → orchestrator/audit.py (OrchestratorAudit)
  orchestrator.py → orchestrator/versioning.py (ORCHESTRATOR_VERSION, ORCHESTRATOR_SPRINT)
  orchestrator.py → investigation/context.py (InvestigationContext) via TYPE_CHECKING
  orchestrator.py does NOT import from: workflows/, tenant/, action_gateway/, api/
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from case_engine.investigation.orchestrator.audit import OrchestratorAudit
from case_engine.investigation.orchestrator.metrics import (
    OrchestratorGlobalMetrics,
    OrchestratorMetrics,
)
from case_engine.investigation.orchestrator.models import (
    CancellationToken,
    InvestigationSession,
    OrchestratorInvestigationResult,
)
from case_engine.investigation.orchestrator.pipeline import (
    CollectorProtocol,
    KnowledgeEnrichmentProvider,
    ObservationGeneratorProtocol,
    PipelineOrchestrator,
    PlannerProtocol,
    RootCauseEngineProtocol,
)
from case_engine.investigation.orchestrator.versioning import (
    ORCHESTRATOR_SPRINT,
    ORCHESTRATOR_VERSION,
)

if TYPE_CHECKING:
    from case_engine.investigation.context import InvestigationContext

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _ms(start: float) -> int:
    return max(0, int((time.monotonic() - start) * 1000))


class InvestigationOrchestrator:
    """
    Production entry point for the investigation pipeline.

    Creates one InvestigationSession per call to investigate(), runs the
    full 6-stage pipeline via PipelineOrchestrator, updates global metrics,
    and returns OrchestratorInvestigationResult.

    Never raises — all exceptions produce a FAILED result via _emergency_result().

    Class-level _global_metrics is shared across all instances so that
    monitoring has a process-lifetime view of investigation outcomes.
    """

    _global_metrics: OrchestratorGlobalMetrics = OrchestratorGlobalMetrics()

    def __init__(
        self,
        planner:            PlannerProtocol,
        collector:          CollectorProtocol,
        rca_engine:         RootCauseEngineProtocol,
        obs_gen:            ObservationGeneratorProtocol,
        knowledge_provider: KnowledgeEnrichmentProvider | None = None,
    ) -> None:
        self._pipeline = PipelineOrchestrator(
            planner=planner,
            collector=collector,
            rca_engine=rca_engine,
            obs_gen=obs_gen,
            knowledge_provider=knowledge_provider,
        )
        LOGGER.debug(
            "orchestrator.init version=%s sprint=%s has_knowledge=%s",
            ORCHESTRATOR_VERSION, ORCHESTRATOR_SPRINT, knowledge_provider is not None,
        )

    def investigate(
        self,
        context:            "InvestigationContext",
        *,
        timeout_seconds:    float | None             = None,
        cancellation_token: CancellationToken | None = None,
    ) -> OrchestratorInvestigationResult:
        """
        Run the full investigation pipeline for the given context. Never raises.

        Args:
            context:            Fully populated InvestigationContext.
            timeout_seconds:    Optional hard deadline. Checked between stages.
            cancellation_token: Optional token for caller-initiated cancellation.

        Returns:
            OrchestratorInvestigationResult with status COMPLETED, PARTIAL, FAILED,
            or CANCELLED. Never None. Never raises.
        """
        start = time.monotonic()
        try:
            session = InvestigationSession.create(
                context=context,
                timeout_seconds=timeout_seconds,
            )

            # ── Write Sprint 2.46 context ownership fields ────────────────────
            context.orchestrator_session_id = session.session_id
            context.orchestrator_started_at = session.started_at
            context.orchestrator_stage      = "created"
            context.orchestrator_status     = "RUNNING"

            LOGGER.info(
                "orchestrator.investigate.start session_id=%s case_id=%s topic=%s",
                session.session_id, context.case_id, context.topic,
            )

            result = self._pipeline.run(session, cancellation_token)

            # ── Finalize context result fields ────────────────────────────────
            context.orchestrator_result_id = result.result_id
            context.orchestrator_status    = result.status

            # ── Update global metrics ─────────────────────────────────────────
            if result.status == "COMPLETED":
                self._global_metrics.record_completed()
            elif result.status == "CANCELLED":
                self._global_metrics.record_cancelled()
            else:
                # FAILED or PARTIAL both increment the failed counter
                self._global_metrics.record_failed()
                for stage_name in session.metrics.stage_failures:
                    self._global_metrics.record_stage_failure(stage_name)

            LOGGER.info(
                "orchestrator.investigate.done session_id=%s status=%s duration_ms=%d",
                session.session_id, result.status, _ms(start),
            )
            return result

        except Exception as exc:  # noqa: BLE001
            duration_ms = _ms(start)
            LOGGER.exception(
                "orchestrator.investigate.emergency case_id=%s duration_ms=%d",
                getattr(context, "case_id", "?"), duration_ms,
            )
            self._global_metrics.record_failed()
            return self._emergency_result(context, exc, duration_ms)

    @classmethod
    def global_metrics(cls) -> OrchestratorGlobalMetrics:
        """Return the shared process-lifetime metrics for all orchestrator instances."""
        return cls._global_metrics

    @classmethod
    def reset_global_metrics(cls) -> None:
        """Reset global metrics to zero. For test isolation only."""
        cls._global_metrics.reset()

    # ── Private helpers ────────────────────────────────────────────────────────

    def _emergency_result(
        self,
        context:     "InvestigationContext",
        exc:         Exception,
        duration_ms: int,
    ) -> OrchestratorInvestigationResult:
        """
        Minimal FAILED result for catastrophic failures that escape the pipeline.

        This path is unreachable in normal operation — the pipeline wraps all
        exceptions. It exists as a last-resort safety net.
        """
        now     = _now_iso()
        sid     = _new_id()
        rid     = _new_id()
        case_id = getattr(context, "case_id", "unknown")
        topic   = getattr(context, "topic",   "unknown")

        metrics = OrchestratorMetrics()
        metrics.set_pipeline_duration(duration_ms)

        audit = OrchestratorAudit(
            session_id=sid,
            case_id=case_id,
            started_at=now,
        )

        return OrchestratorInvestigationResult(
            result_id=rid,
            session_id=sid,
            case_id=case_id,
            topic=topic,
            status="FAILED",
            plan=None,
            evidence=None,
            knowledge_entries=[],
            root_cause=None,
            observation=None,
            metrics=metrics,
            audit_timeline=audit,
            errors=[{
                "stage":      "orchestrator",
                "error_code": "EMERGENCY_FAILURE",
                "message":    str(exc)[:500],
                "timestamp":  now,
            }],
            stage_timings={},
            pipeline_duration_ms=duration_ms,
            started_at=now,
            completed_at=now,
            cancellation_reason=None,
            recovery_hint=(
                "An unexpected error occurred in the orchestrator. "
                "This is a system-level issue. Check logs and retry."
            ),
        )


def build_investigation_orchestrator(
    knowledge_provider: KnowledgeEnrichmentProvider | None = None,
) -> InvestigationOrchestrator:
    """
    Factory: build a production-ready InvestigationOrchestrator with default components.

    Instantiates Sprint 2.39 InvestigationPlanner, Sprint 2.42 EvidenceCollector,
    Sprint 2.43 RootCauseEngine, Sprint 2.44 ObservationGenerator with
    default registries and no external dependencies.

    For production use with tool execution, construct components manually and
    pass them to InvestigationOrchestrator directly.

    Args:
        knowledge_provider: Optional KnowledgeEnrichmentProvider. If None,
                            context.knowledge_entries are used as-is.

    Returns:
        Ready-to-use InvestigationOrchestrator.
    """
    from case_engine.investigation.collector.collector import EvidenceCollector
    from case_engine.investigation.observation.generator import ObservationGenerator
    from case_engine.investigation.planner.engine import InvestigationPlanner
    from case_engine.investigation.root_cause.engine import RootCauseEngine

    return InvestigationOrchestrator(
        planner=InvestigationPlanner(),
        collector=EvidenceCollector(),
        rca_engine=RootCauseEngine(),
        obs_gen=ObservationGenerator(),
        knowledge_provider=knowledge_provider,
    )
