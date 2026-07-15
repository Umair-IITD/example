"""
case_engine/investigation/pipeline/stages.py

Sprint 2.47: Concrete investigation pipeline stage adapters.

Each class wraps an existing Sprint 2.39–2.44 component and implements
the InvestigationStage protocol from pipeline/contract.py.

Stage adapters are independently testable and composable — they do NOT
depend on PipelineOrchestrator (Sprint 2.46) or any orchestration layer.
The production pipeline (InvestigationOrchestrator) remains the single
entry point; these adapters provide independently testable stage units.

Stages:
  ValidateStage     — validates InvestigationContext has required fields
  PlanningStage     — wraps InvestigationPlanner (Sprint 2.39)
  CollectionStage   — wraps EvidenceCollector (Sprint 2.42)
  KnowledgeStage    — wraps KnowledgeEnrichmentProvider (Sprint 2.46)
  RootCauseStage    — wraps RootCauseEngine (Sprint 2.43)
  ObservationStage  — wraps ObservationGenerator (Sprint 2.44)

Context field ownership (write-once per stage):
  planning    → context.investigation_plan
  collection  → context.evidence_bundle
  knowledge   → context.knowledge_entries (merges)
  root_cause  → context.root_cause_analysis
  observation → context.observation, context.observation_status,
                context.observation_version, context.observation_timestamp

Dependency direction:
  stages.py → pipeline/contract.py (StageResult, StageStatus, StageSeverity)
  stages.py → investigation/context.py via TYPE_CHECKING
  stages.py → stdlib (time, logging)
  stages.py does NOT import from: orchestrator/, workflows/, api/, tenant/
"""
from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

from case_engine.investigation.pipeline.contract import (
    InvestigationStage,  # noqa: F401 — re-exported via __init__
    StageSeverity,
    StageResult,
    StageStatus,
)

if TYPE_CHECKING:
    from case_engine.investigation.context import InvestigationContext

LOGGER = logging.getLogger(__name__)


def _ms(start: float) -> int:
    return max(0, int((time.monotonic() - start) * 1000))


# ── Stage name constants (match pipeline.py Sprint 2.46) ──────────────────────

STAGE_VALIDATE    = "validate"
STAGE_PLANNING    = "planning"
STAGE_COLLECTION  = "collection"
STAGE_KNOWLEDGE   = "knowledge"
STAGE_ROOT_CAUSE  = "root_cause"
STAGE_OBSERVATION = "observation"


# ── ValidateStage ──────────────────────────────────────────────────────────────

class ValidateStage:
    """
    Stage 1: Validate InvestigationContext has all required pipeline fields.

    Required fields: case_id (non-empty), topic (non-empty), tenant_context.
    Does not call any external component.
    Severity: FATAL — an invalid context cannot be processed.
    """

    @property
    def stage_name(self) -> str:
        return STAGE_VALIDATE

    @property
    def severity(self) -> StageSeverity:
        return StageSeverity.FATAL

    @property
    def expected_outputs(self) -> frozenset[str]:
        return frozenset()

    def execute(self, context: "InvestigationContext") -> StageResult:
        t0 = time.monotonic()
        try:
            errors = self._validate(context)
            if errors:
                return StageResult(
                    stage_name=STAGE_VALIDATE,
                    status=StageStatus.FAILURE,
                    duration_ms=_ms(t0),
                    error_message="; ".join(errors),
                    error_code="CONTEXT_INVALID",
                    context_keys_written=frozenset(),
                )
            return StageResult(
                stage_name=STAGE_VALIDATE,
                status=StageStatus.SUCCESS,
                duration_ms=_ms(t0),
                output={"validated": True},
                context_keys_written=frozenset(),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("validate_stage.execute error")
            return StageResult(
                stage_name=STAGE_VALIDATE,
                status=StageStatus.FAILURE,
                duration_ms=_ms(t0),
                error_message=str(exc),
                error_code="VALIDATE_EXCEPTION",
                context_keys_written=frozenset(),
            )

    def _validate(self, context: Any) -> list[str]:
        errors: list[str] = []
        if not getattr(context, "case_id", ""):
            errors.append("case_id is required and must be non-empty")
        if not getattr(context, "topic", ""):
            errors.append("topic is required and must be non-empty")
        if getattr(context, "tenant_context", None) is None:
            errors.append("tenant_context is required")
        return errors


# ── PlanningStage ──────────────────────────────────────────────────────────────

class PlanningStage:
    """
    Stage 2: Produce an InvestigationPlan from the context.

    Wraps InvestigationPlanner (Sprint 2.39).
    The planner never raises — it returns a MinimalFallbackRule plan on error.
    Sets context.investigation_plan on success.
    Severity: FATAL — no plan means no evidence collection.
    """

    def __init__(self, planner: Any) -> None:
        self._planner = planner

    @property
    def stage_name(self) -> str:
        return STAGE_PLANNING

    @property
    def severity(self) -> StageSeverity:
        return StageSeverity.FATAL

    @property
    def expected_outputs(self) -> frozenset[str]:
        return frozenset({"investigation_plan"})

    def execute(self, context: "InvestigationContext") -> StageResult:
        t0 = time.monotonic()
        try:
            plan = self._planner.plan(context)
            context.investigation_plan = plan
            dur = _ms(t0)
            LOGGER.debug(
                "planning_stage.ok case_id=%s plan_id=%s steps=%d",
                getattr(context, "case_id", "?"),
                getattr(plan, "plan_id", "?"),
                len(getattr(plan, "steps", [])),
            )
            return StageResult(
                stage_name=STAGE_PLANNING,
                status=StageStatus.SUCCESS,
                duration_ms=dur,
                output=plan,
                context_keys_written=frozenset({"investigation_plan"}),
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            LOGGER.exception("planning_stage.error case_id=%s", getattr(context, "case_id", "?"))
            return StageResult(
                stage_name=STAGE_PLANNING,
                status=StageStatus.FAILURE,
                duration_ms=dur,
                error_message=str(exc),
                error_code="PLANNING_ERROR",
                context_keys_written=frozenset(),
            )


# ── CollectionStage ────────────────────────────────────────────────────────────

class CollectionStage:
    """
    Stage 3: Collect evidence per the InvestigationPlan.

    Wraps EvidenceCollector (Sprint 2.42).
    Reads context.investigation_plan; returns FAILURE if plan is absent.
    Sets context.evidence_bundle on success.
    Severity: FATAL — no evidence means root cause cannot proceed.
    """

    def __init__(self, collector: Any) -> None:
        self._collector = collector

    @property
    def stage_name(self) -> str:
        return STAGE_COLLECTION

    @property
    def severity(self) -> StageSeverity:
        return StageSeverity.FATAL

    @property
    def expected_outputs(self) -> frozenset[str]:
        return frozenset({"evidence_bundle"})

    def execute(self, context: "InvestigationContext") -> StageResult:
        t0 = time.monotonic()
        plan = getattr(context, "investigation_plan", None)
        if plan is None:
            return StageResult(
                stage_name=STAGE_COLLECTION,
                status=StageStatus.FAILURE,
                duration_ms=_ms(t0),
                error_message="investigation_plan not in context — run PlanningStage first",
                error_code="MISSING_DEPENDENCY",
                context_keys_written=frozenset(),
            )
        try:
            bundle = self._collector.collect(plan, context)
            context.evidence_bundle = bundle
            dur = _ms(t0)
            LOGGER.debug(
                "collection_stage.ok case_id=%s bundle_id=%s items=%d",
                getattr(context, "case_id", "?"),
                getattr(bundle, "bundle_id", "?"),
                len(getattr(bundle, "items", [])),
            )
            return StageResult(
                stage_name=STAGE_COLLECTION,
                status=StageStatus.SUCCESS,
                duration_ms=dur,
                output=bundle,
                context_keys_written=frozenset({"evidence_bundle"}),
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            LOGGER.exception("collection_stage.error case_id=%s", getattr(context, "case_id", "?"))
            return StageResult(
                stage_name=STAGE_COLLECTION,
                status=StageStatus.FAILURE,
                duration_ms=dur,
                error_message=str(exc),
                error_code="COLLECTION_ERROR",
                context_keys_written=frozenset(),
            )


# ── KnowledgeStage ─────────────────────────────────────────────────────────────

class KnowledgeStage:
    """
    Stage 4: Enrich context with knowledge entries.

    Wraps an optional KnowledgeEnrichmentProvider (Sprint 2.46).
    If no provider is given, the stage is a no-op and returns SUCCESS with
    the existing context.knowledge_entries as output.
    Sets context.knowledge_entries when a provider is present.
    Severity: NON_FATAL — knowledge enrichment failure is recoverable.
    """

    def __init__(self, knowledge_provider: Any | None = None) -> None:
        self._provider = knowledge_provider

    @property
    def stage_name(self) -> str:
        return STAGE_KNOWLEDGE

    @property
    def severity(self) -> StageSeverity:
        return StageSeverity.NON_FATAL

    @property
    def expected_outputs(self) -> frozenset[str]:
        return frozenset({"knowledge_entries"}) if self._provider is not None else frozenset()

    def execute(self, context: "InvestigationContext") -> StageResult:
        t0 = time.monotonic()
        existing = list(getattr(context, "knowledge_entries", []))

        if self._provider is None:
            return StageResult(
                stage_name=STAGE_KNOWLEDGE,
                status=StageStatus.SKIPPED,
                duration_ms=_ms(t0),
                output=existing,
                context_keys_written=frozenset(),
            )

        try:
            enriched = self._provider.enrich(context)
            if enriched:
                context.knowledge_entries = list(enriched)
            entries = list(getattr(context, "knowledge_entries", []))
            dur = _ms(t0)
            LOGGER.debug(
                "knowledge_stage.ok case_id=%s entries=%d",
                getattr(context, "case_id", "?"), len(entries),
            )
            return StageResult(
                stage_name=STAGE_KNOWLEDGE,
                status=StageStatus.SUCCESS,
                duration_ms=dur,
                output=entries,
                context_keys_written=frozenset({"knowledge_entries"}),
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            LOGGER.warning(
                "knowledge_stage.error (non-fatal) case_id=%s: %s",
                getattr(context, "case_id", "?"), exc,
            )
            return StageResult(
                stage_name=STAGE_KNOWLEDGE,
                status=StageStatus.FAILURE,
                duration_ms=dur,
                output=existing,
                error_message=str(exc),
                error_code="KNOWLEDGE_ERROR",
                context_keys_written=frozenset(),
            )


# ── RootCauseStage ─────────────────────────────────────────────────────────────

class RootCauseStage:
    """
    Stage 5: Analyze evidence to determine the root cause.

    Wraps RootCauseEngine (Sprint 2.43).
    Reads context.evidence_bundle; returns FAILURE if bundle is absent.
    Sets context.root_cause_analysis on success.
    Severity: FATAL — no root cause means no observation.
    """

    def __init__(self, rca_engine: Any) -> None:
        self._engine = rca_engine

    @property
    def stage_name(self) -> str:
        return STAGE_ROOT_CAUSE

    @property
    def severity(self) -> StageSeverity:
        return StageSeverity.FATAL

    @property
    def expected_outputs(self) -> frozenset[str]:
        return frozenset({"root_cause_analysis"})

    def execute(self, context: "InvestigationContext") -> StageResult:
        t0 = time.monotonic()
        bundle = getattr(context, "evidence_bundle", None)
        if bundle is None:
            return StageResult(
                stage_name=STAGE_ROOT_CAUSE,
                status=StageStatus.FAILURE,
                duration_ms=_ms(t0),
                error_message="evidence_bundle not in context — run CollectionStage first",
                error_code="MISSING_DEPENDENCY",
                context_keys_written=frozenset(),
            )
        try:
            analysis = self._engine.analyze(bundle, context)
            context.root_cause_analysis = analysis
            dur = _ms(t0)
            LOGGER.debug(
                "root_cause_stage.ok case_id=%s analysis_id=%s category=%s",
                getattr(context, "case_id", "?"),
                getattr(analysis, "analysis_id", "?"),
                getattr(analysis, "category", "?"),
            )
            return StageResult(
                stage_name=STAGE_ROOT_CAUSE,
                status=StageStatus.SUCCESS,
                duration_ms=dur,
                output=analysis,
                context_keys_written=frozenset({"root_cause_analysis"}),
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            LOGGER.exception("root_cause_stage.error case_id=%s", getattr(context, "case_id", "?"))
            return StageResult(
                stage_name=STAGE_ROOT_CAUSE,
                status=StageStatus.FAILURE,
                duration_ms=dur,
                error_message=str(exc),
                error_code="ROOT_CAUSE_ERROR",
                context_keys_written=frozenset(),
            )


# ── ObservationStage ───────────────────────────────────────────────────────────

class ObservationStage:
    """
    Stage 6: Format root cause analysis into an Observation.

    Wraps ObservationGenerator (Sprint 2.44).
    Reads context.root_cause_analysis and context.evidence_bundle.
    Sets context.observation and related metadata fields on success.
    Severity: NON_FATAL — an observation failure leaves a PARTIAL result.
    """

    def __init__(self, obs_gen: Any) -> None:
        self._gen = obs_gen

    @property
    def stage_name(self) -> str:
        return STAGE_OBSERVATION

    @property
    def severity(self) -> StageSeverity:
        return StageSeverity.NON_FATAL

    @property
    def expected_outputs(self) -> frozenset[str]:
        return frozenset({
            "observation",
            "observation_status",
            "observation_version",
            "observation_timestamp",
        })

    def execute(self, context: "InvestigationContext") -> StageResult:
        t0 = time.monotonic()
        analysis = getattr(context, "root_cause_analysis", None)
        bundle   = getattr(context, "evidence_bundle", None)
        if analysis is None:
            return StageResult(
                stage_name=STAGE_OBSERVATION,
                status=StageStatus.FAILURE,
                duration_ms=_ms(t0),
                error_message="root_cause_analysis not in context — run RootCauseStage first",
                error_code="MISSING_DEPENDENCY",
                context_keys_written=frozenset(),
            )
        try:
            observation = self._gen.generate(analysis, bundle, context)
            context.observation = observation
            obs_status = getattr(observation, "status", None)
            context.observation_status = (
                getattr(obs_status, "value", str(obs_status)) if obs_status else None
            )
            context.observation_version   = getattr(observation, "generator_version", None)
            context.observation_timestamp = getattr(observation, "generated_at", None)
            dur = _ms(t0)
            LOGGER.debug(
                "observation_stage.ok case_id=%s obs_id=%s status=%s",
                getattr(context, "case_id", "?"),
                getattr(observation, "observation_id", "?"),
                obs_status,
            )
            return StageResult(
                stage_name=STAGE_OBSERVATION,
                status=StageStatus.SUCCESS,
                duration_ms=dur,
                output=observation,
                context_keys_written=frozenset({
                    "observation",
                    "observation_status",
                    "observation_version",
                    "observation_timestamp",
                }),
            )
        except Exception as exc:  # noqa: BLE001
            dur = _ms(t0)
            LOGGER.warning(
                "observation_stage.error (non-fatal) case_id=%s: %s",
                getattr(context, "case_id", "?"), exc,
            )
            return StageResult(
                stage_name=STAGE_OBSERVATION,
                status=StageStatus.FAILURE,
                duration_ms=dur,
                error_message=str(exc),
                error_code="OBSERVATION_ERROR",
                context_keys_written=frozenset(),
            )
