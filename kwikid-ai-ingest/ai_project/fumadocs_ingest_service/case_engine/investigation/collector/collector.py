"""
case_engine/investigation/collector/collector.py

Sprint 2.42: EvidenceCollector — main public class for the Evidence Collector Engine.
Sprint 2.45: Integrated with ProductionToolExecutor via ToolExecutorProvider bridge.

Responsibilities:
  - Accept an InvestigationPlan (Sprint 2.39) and InvestigationContext
  - Delegate plan execution to StepOrchestrator
  - Convert StepExecutionRecords into typed Evidence objects
  - Build and return an EvidenceBundle
  - Write CollectionMetrics to InvestigationContext
  - NEVER raise — all errors are captured in the EvidenceBundle

Pipeline position: Stage 5b (after InvestigationPlanner, before RootCauseEngine).

This class produces an EvidenceBundle compatible with the existing
RootCauseEngine and ObservationGenerator (Sprint 2.18 consumers).

Dependency direction:
  collector.py → collector/orchestrator.py (StepOrchestrator)
  collector.py → collector/metrics.py (CollectionMetrics)
  collector.py → collector/execution.py (StepExecutionRecord, StepStatus)
  collector.py → collector/registry.py (ProviderRegistry, build_default_registry)
  collector.py → collector/validators.py (CollectorValidator)
  collector.py → collector/contracts.py (CollectionContext)
  collector.py → collector/dispatcher.py (ProviderDispatcher)
  collector.py → investigation/models.py (Evidence subtypes, EvidenceBundle, ...)
  collector.py → investigation/planner/models.py (InvestigationPlan, EvidenceKind)
  collector.py → investigation/context.py (InvestigationContext) via TYPE_CHECKING
  collector.py → tools/framework/bridge.py (Sprint 2.45 ToolExecutorProvider wiring)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from case_engine.investigation.collector.contracts import CollectionContext
from case_engine.investigation.collector.dispatcher import ProviderDispatcher
from case_engine.investigation.collector.execution import StepExecutionRecord, StepStatus
from case_engine.investigation.collector.metrics import CollectionMetrics
from case_engine.investigation.collector.orchestrator import StepOrchestrator
from case_engine.investigation.collector.registry import ProviderRegistry, build_default_registry
from case_engine.investigation.collector.validators import CollectorValidator
from case_engine.investigation.models import (
    Evidence,
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    LogEvidence,
    MetricEvidence,
    SessionEvidence,
    SummaryEvidence,
    UserEvidence,
    VideoEvidence,
)
from case_engine.investigation.planner.models import EvidenceKind, InvestigationPlan

if TYPE_CHECKING:
    from case_engine.investigation.context import InvestigationContext

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# Maps EvidenceKind.value → (EvidenceType, EvidenceSource, Evidence subclass)
_KIND_TO_EVIDENCE: dict[str, tuple[EvidenceType, EvidenceSource, type[Evidence]]] = {
    EvidenceKind.SESSION.value:       (EvidenceType.SESSION, EvidenceSource.GET_SESSION_DETAILS,   SessionEvidence),
    EvidenceKind.LOG.value:           (EvidenceType.LOG,     EvidenceSource.GET_FAILURE_REASON,    LogEvidence),
    EvidenceKind.SUMMARY.value:       (EvidenceType.SUMMARY, EvidenceSource.GET_CASE_HISTORY,      SummaryEvidence),
    EvidenceKind.API.value:           (EvidenceType.USER,    EvidenceSource.GET_USER_DETAILS,      UserEvidence),
    EvidenceKind.DATABASE.value:      (EvidenceType.USER,    EvidenceSource.GET_USER_DETAILS,      UserEvidence),
    EvidenceKind.KNOWLEDGE.value:     (EvidenceType.LOG,     EvidenceSource.GET_FAILURE_REASON,    LogEvidence),
    EvidenceKind.WORKFLOW.value:      (EvidenceType.SUMMARY, EvidenceSource.GET_ONBOARDING_STATUS, SummaryEvidence),
    EvidenceKind.VISION.value:        (EvidenceType.VIDEO,   EvidenceSource.GET_SESSION_DETAILS,   VideoEvidence),
    EvidenceKind.CONFIGURATION.value: (EvidenceType.LOG,     EvidenceSource.GET_FAILURE_REASON,    LogEvidence),
    EvidenceKind.HUMAN.value:         (EvidenceType.SUMMARY, EvidenceSource.GET_CASE_HISTORY,      SummaryEvidence),
}

_DEFAULT_EVIDENCE_MAPPING: tuple[EvidenceType, EvidenceSource, type[Evidence]] = (
    EvidenceType.LOG, EvidenceSource.GET_FAILURE_REASON, LogEvidence
)


class EvidenceCollector:
    """
    Sprint 2.42 Evidence Collector Engine.
    Sprint 2.45: Optional tool_executor integration via ToolExecutorProvider bridge.

    Executes an InvestigationPlan using registered EvidenceProviders and
    produces an EvidenceBundle for downstream consumption by RootCauseEngine.

    Safety contract:
      - collect() NEVER raises — returns EvidenceBundle with failed items on error.
      - collect() NEVER calls LLMs, makes decisions, or modifies external state.
      - collect() is idempotent — calling it twice on the same plan/context is safe.

    Constructor:
      registry:      ProviderRegistry mapping EvidenceKind → EvidenceProvider.
                     Defaults to build_default_registry() (NullProvider fallback).
      validator:     Optional CollectorValidator for pre-execution plan validation.
      max_workers:   Maximum concurrent provider workers for parallel steps.
      tool_executor: Optional ProductionToolExecutor (Sprint 2.45). When provided,
                     a ToolExecutorProvider bridge is wired into the registry so all
                     tool-backed evidence kinds route through the production executor.
                     When absent, the collector behaves exactly as in Sprint 2.42.
    """

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        validator: CollectorValidator | None = None,
        max_workers: int = 8,
        tool_executor: Any | None = None,
    ) -> None:
        self._registry   = registry or build_default_registry()
        self._validator  = validator or CollectorValidator()

        # Sprint 2.45: wire ToolExecutorProvider into registry when executor is provided
        # Lazy import breaks the collector ↔ bridge circular import chain.
        if tool_executor is not None:
            from case_engine.tools.framework.bridge import _wire_tool_executor_into_registry
            _wire_tool_executor_into_registry(self._registry, tool_executor)
            LOGGER.debug("evidence_collector.tool_executor_wired executor=%r", tool_executor)

        dispatcher       = ProviderDispatcher(self._registry, self._validator)
        self._orchestrator = StepOrchestrator(dispatcher, self._validator, max_workers)

    def collect(
        self,
        plan: InvestigationPlan,
        context: InvestigationContext,
    ) -> EvidenceBundle:
        """
        Execute the plan and produce an EvidenceBundle.

        Steps:
          1. Validate plan structure (warn if invalid, continue anyway).
          2. Build CollectionContext from InvestigationContext.
          3. Execute all steps via StepOrchestrator.
          4. Convert StepExecutionRecords to Evidence objects.
          5. Build EvidenceBundle.
          6. Write CollectionMetrics to context.
          7. Return bundle.

        Never raises.
        """
        bundle_id    = str(uuid.uuid4())
        collected_at = _now_iso()

        # 1. Validate plan
        ok, reasons = self._validator.validate_plan(plan)
        if not ok:
            LOGGER.warning(
                "evidence_collector.plan_invalid plan_id=%s reasons=%s",
                plan.plan_id, reasons,
            )

        # 2. Build CollectionContext
        coll_ctx = _build_collection_context(context)

        # 3. Execute
        try:
            summary = self._orchestrator.execute(plan, coll_ctx)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "evidence_collector.orchestrator_failed plan_id=%s error=%s",
                plan.plan_id, exc, exc_info=True,
            )
            empty_bundle = EvidenceBundle(
                bundle_id=bundle_id,
                case_id=plan.case_id,
                topic=plan.topic,
                plan_id=plan.plan_id,
                items=[],
                collected_at=collected_at,
            )
            return empty_bundle

        # 4. Convert records to Evidence
        items: list[Evidence] = []
        for record in summary.records:
            ev = self._record_to_evidence(record)
            items.append(ev)

        # 5. Build bundle
        bundle = EvidenceBundle(
            bundle_id=bundle_id,
            case_id=plan.case_id,
            topic=plan.topic,
            plan_id=plan.plan_id,
            items=items,
            collected_at=collected_at,
        )

        # 6. Write metrics to context
        try:
            metrics = CollectionMetrics.from_summary(summary)
            _write_metrics_to_context(context, metrics, bundle)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "evidence_collector.metrics_write_failed error=%s", exc
            )

        LOGGER.info(
            "evidence_collector.collect_complete plan_id=%s bundle_id=%s "
            "total=%d success=%d failed=%d skipped=%d",
            plan.plan_id, bundle_id, len(items),
            summary.successful_steps, summary.failed_steps, summary.skipped_steps,
        )

        return bundle

    # ── Private ────────────────────────────────────────────────────────────────

    def _record_to_evidence(self, record: StepExecutionRecord) -> Evidence:
        """Convert a StepExecutionRecord into a typed Evidence object."""
        evidence_id  = str(uuid.uuid4())
        collected_at = record.completed_at

        kind_str = ""
        if record.final_result is not None:
            kind_str = record.final_result.evidence_kind

        ev_type, ev_source, ev_class = _KIND_TO_EVIDENCE.get(kind_str, _DEFAULT_EVIDENCE_MAPPING)

        if record.succeeded and record.final_result is not None:
            payload       = record.final_result.payload
            invocation_id = record.final_result.provider_name
            error_code    = None
            error_message = None
            success       = True
        elif record.skipped:
            payload       = {}
            invocation_id = ""
            error_code    = "STEP_SKIPPED"
            error_message = record.skipped_reason or "step was skipped"
            success       = False
        else:
            res           = record.final_result
            payload       = {}
            invocation_id = res.provider_name if res else ""
            error_code    = res.error_code if res else "STEP_FAILED"
            error_message = res.error_message if res else "step execution failed"
            success       = False

        return ev_class(
            evidence_id=evidence_id,
            evidence_type=ev_type,
            source=ev_source,
            tool_name=record.step_id,
            payload=payload,
            collected_at=collected_at,
            invocation_id=invocation_id,
            success=success,
            error_code=error_code,
            error_message=error_message,
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_collection_context(context: InvestigationContext) -> CollectionContext:
    """Extract CollectionContext from InvestigationContext."""
    tenant_id = ""
    if context.tenant_context is not None:
        tenant_id = getattr(context.tenant_context, "client_id", "") or ""
    return CollectionContext(
        case_id=context.case_id,
        topic=context.topic or "",
        slots=dict(context.slots),
        tenant_id=tenant_id,
        metadata={
            "channel":   context.channel,
            "ticket_id": context.ticket_id,
        },
    )


def _write_metrics_to_context(
    context: InvestigationContext,
    metrics: CollectionMetrics,
    bundle: EvidenceBundle,
) -> None:
    """Write collection metrics and stats to InvestigationContext."""
    if hasattr(context, "collection_metrics"):
        context.collection_metrics = metrics
    if hasattr(context, "collector_stats"):
        context.collector_stats = {
            "total_steps":       metrics.total_steps,
            "successful_steps":  metrics.successful_steps,
            "failed_steps":      metrics.failed_steps,
            "skipped_steps":     metrics.skipped_steps,
            "total_duration_ms": metrics.total_duration_ms,
            "bundle_id":         bundle.bundle_id,
        }
