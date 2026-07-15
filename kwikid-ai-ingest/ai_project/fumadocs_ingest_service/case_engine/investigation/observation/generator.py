"""
case_engine/investigation/observation/generator.py

Sprint 2.44: ObservationGenerator — RootCauseAnalysis → Observation.

Per blueprint Section 14 and flow_diagram.mermaid (ROOTCAUSE → OBSGEN →
FDNOTE): this component represents the internal investigation note written
by an experienced L1 support engineer.

CRITICAL ARCHITECTURAL RULE (blueprint + flow diagram):
  The Observation Generator MUST NEVER:
    perform reasoning, call tools, call providers, perform retrieval,
    perform RAG, call APIs, execute workflows, invoke LLMs, execute
    actions, modify evidence, modify confidence, modify recommendations,
    or change the root cause.
  It ONLY formats. Everything analytical comes from RootCauseAnalysis.

Inputs (only): RootCauseAnalysis, EvidenceBundle, InvestigationContext,
SOPDocument, WorkflowPlaybook. SOP and playbook are traceability-only —
their ids are recorded in audit metadata and never used for decisions.

Dependency direction:
  generator.py → observation/{models,validators,registry,metrics,versioning,exceptions}
  generator.py → case_engine.investigation.models / root_cause.models (types)
  generator.py → stdlib (time, uuid, datetime, logging)
  NO imports of tools, providers, knowledge retrieval, or LLM layers.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from case_engine.investigation.observation.exceptions import (
    ObservationValidationError,
)
from case_engine.investigation.observation.metrics import (
    GeneratorMetrics,
    ObservationMetric,
)
from case_engine.investigation.observation.models import (
    DecisionTraceSummary,
    InvestigationStepRecord,
    Observation,
    ObservationAuditMetadata,
    ObservationDraft,
    ObservationStatus,
    TimelineEntry,
)
from case_engine.investigation.observation.registry import (
    TemplateRegistry,
    build_default_registry,
)
from case_engine.investigation.observation.validators import (
    validate_analysis,
    validate_inputs,
    validate_observation,
    verify_evidence_preservation,
)
from case_engine.investigation.observation.versioning import (
    CURRENT_GENERATOR_VERSION,
    CURRENT_TEMPLATE_REGISTRY_VERSION,
)

if TYPE_CHECKING:
    from case_engine.investigation.context import InvestigationContext
    from case_engine.investigation.models import EvidenceBundle
    from case_engine.investigation.root_cause.models import RootCauseAnalysis
    from case_engine.knowledge.sop.models import SOPDocument
    from case_engine.workflows.playbooks.models import WorkflowPlaybook

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ObservationGenerator:
    """
    Sprint 2.44 Observation Generator.

    Stateless per call (metrics aside) — one instance is safe to share
    across requests. Deterministic: same inputs → same observation text.
    """

    def __init__(
        self,
        registry: TemplateRegistry | None = None,
        metrics: GeneratorMetrics | None = None,
    ) -> None:
        self._registry = registry if registry is not None else build_default_registry()
        self._metrics = metrics if metrics is not None else GeneratorMetrics()

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate(
        self,
        analysis: "RootCauseAnalysis",
        bundle: "EvidenceBundle | None" = None,
        context: "InvestigationContext | None" = None,
        sop: "SOPDocument | None" = None,
        playbook: "WorkflowPlaybook | None" = None,
    ) -> Observation:
        """
        Convert a RootCauseAnalysis into a fully typed Observation.

        Raises:
            InvalidAnalysisError:        analysis is structurally invalid.
            InvalidInputError:           bundle/context disagree with analysis.
            ObservationValidationError:  generated note failed the output gate
                                         or the evidence-preservation guardrail.
        """
        started = time.perf_counter()

        validate_analysis(analysis)
        validate_inputs(analysis, bundle, context)

        effective_bundle = bundle if bundle is not None else (
            context.evidence_bundle if context is not None else None
        )
        effective_sop = sop if sop is not None else (
            (context.resolved_sop or context.sop_document)
            if context is not None else None
        )
        effective_playbook = playbook if playbook is not None else (
            context.workflow_playbook if context is not None else None
        )

        topic = ""
        if effective_bundle is not None and effective_bundle.topic:
            topic = effective_bundle.topic
        elif context is not None and context.topic:
            topic = context.topic

        generated_at = _now_iso()

        draft = ObservationDraft(
            analysis=analysis,
            case_id=analysis.case_id,
            topic=topic,
            timeline=self._build_timeline(effective_bundle, analysis, generated_at),
            investigation_steps=self._build_step_records(context, effective_bundle),
            decision_trace_summary=DecisionTraceSummary.from_trace(
                analysis.decision_trace
            ),
            evidence_items=(
                tuple(effective_bundle.items) if effective_bundle is not None else ()
            ),
            generated_at=generated_at,
            generator_version=CURRENT_GENERATOR_VERSION,
        )

        template = self._registry.resolve(analysis.category.value, topic)
        text, used_template, render_failed = self._render(template, draft)

        if render_failed:
            status = ObservationStatus.ERROR
        elif used_template.is_fallback:
            status = ObservationStatus.FALLBACK
        elif effective_bundle is None:
            status = ObservationStatus.PARTIAL
        else:
            status = ObservationStatus.COMPLETE

        duration_ms = int((time.perf_counter() - started) * 1000)
        observation_id = str(uuid.uuid4())

        audit = ObservationAuditMetadata(
            observation_id=observation_id,
            generator_version=CURRENT_GENERATOR_VERSION,
            template_registry_version=CURRENT_TEMPLATE_REGISTRY_VERSION,
            template_id=used_template.template_id,
            analysis_id=analysis.analysis_id,
            evidence_bundle_id=(
                effective_bundle.bundle_id if effective_bundle is not None else None
            ),
            case_id=analysis.case_id,
            context_id=context.context_id if context is not None else None,
            sop_id=effective_sop.sop_id if effective_sop is not None else None,
            playbook_id=(
                effective_playbook.playbook_id
                if effective_playbook is not None else None
            ),
            generated_at=generated_at,
            generation_duration_ms=duration_ms,
        )

        observation = Observation(
            observation_id=observation_id,
            case_id=analysis.case_id,
            topic=topic,
            status=status,
            issue_summary=self._first_section(text),
            evidence_summary=self._section(text, "=== EVIDENCE SUMMARY ==="),
            supporting_evidence=tuple(analysis.supporting_evidence),
            contradicting_evidence=tuple(analysis.contradicting_evidence),
            missing_evidence=tuple(analysis.missing_evidence),
            contradictions=tuple(analysis.decision_trace.contradictions_detected),
            root_cause_category=analysis.category,
            root_cause_explanation=analysis.explanation,
            confidence=analysis.confidence,
            confidence_breakdown=analysis.confidence_breakdown,
            recommended_action=analysis.recommended_action,
            escalation=analysis.escalation_recommendation,
            timeline=draft.timeline,
            investigation_steps=draft.investigation_steps,
            decision_trace_summary=draft.decision_trace_summary,
            evidence_references=tuple(analysis.evidence_references),
            observation_text=text,
            template_id=used_template.template_id,
            analysis_id=analysis.analysis_id,
            audit_metadata=audit,
            generated_at=generated_at,
            version=CURRENT_GENERATOR_VERSION,
        )

        validate_observation(observation)
        violations = verify_evidence_preservation(observation, analysis)
        if violations:
            raise ObservationValidationError(
                observation_id,
                f"evidence preservation guardrail violated: {violations}",
            )

        self._metrics.record(ObservationMetric(
            observation_id=observation_id,
            template_id=used_template.template_id,
            duration_ms=duration_ms,
            contradiction_count=len(observation.contradictions),
            escalated=observation.escalation.should_escalate,
            confidence=observation.confidence,
            status=status.value,
            recorded_at=generated_at,
        ))

        LOGGER.info(
            "observation_generator.generated observation_id=%s case_id=%s "
            "template=%s status=%s chars=%d",
            observation_id, analysis.case_id, used_template.template_id,
            status.value, len(text),
        )
        return observation

    def apply_to_context(
        self,
        observation: Observation,
        context: "InvestigationContext",
    ) -> None:
        """
        Write the observation into the InvestigationContext (Sprint 2.44
        fields) and keep the legacy observation_text field in sync.
        """
        context.observation = observation
        context.observation_version = observation.version
        context.observation_timestamp = observation.generated_at
        context.observation_status = observation.status.value
        context.observation_metadata = observation.audit_metadata.to_dict()
        context.observation_text = observation.observation_text

    def get_metrics(self) -> dict[str, Any]:
        """Return a snapshot of aggregate generation metrics."""
        return self._metrics.snapshot()

    # ── Private ────────────────────────────────────────────────────────────────

    def _render(self, template: Any, draft: ObservationDraft) -> tuple[str, Any, bool]:
        """
        Render the draft with the resolved template; on failure try the
        fallback; on total failure produce the deterministic error note.

        Returns (text, template_used, render_failed).
        """
        try:
            return template.render(draft), template, False
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "observation_generator.render_failed template=%s case_id=%s",
                template.template_id, draft.case_id,
            )

        fallback = self._registry.fallback
        if fallback is not None and fallback.template_id != template.template_id:
            try:
                return fallback.render(draft), fallback, False
            except Exception:  # noqa: BLE001
                LOGGER.exception(
                    "observation_generator.fallback_render_failed case_id=%s",
                    draft.case_id,
                )
                template = fallback

        return self._error_text(draft), template, True

    @staticmethod
    def _error_text(draft: ObservationDraft) -> str:
        return (
            "[OBSERVATION ERROR — MANUAL REVIEW REQUIRED]\n\n"
            f"Case ID:     {draft.case_id}\n"
            f"Topic:       {draft.topic or 'UNKNOWN'}\n"
            f"Analysis ID: {draft.analysis.analysis_id}\n\n"
            "Automated observation could not be rendered. "
            "The root cause analysis is available under the analysis ID above. "
            "Please investigate manually."
        )

    @staticmethod
    def _build_timeline(
        bundle: "EvidenceBundle | None",
        analysis: "RootCauseAnalysis",
        generated_at: str,
    ) -> tuple[TimelineEntry, ...]:
        entries: list[TimelineEntry] = []
        if bundle is not None:
            for item in bundle.items:
                outcome = "collected" if item.success else "collection failed"
                entries.append(TimelineEntry(
                    timestamp=item.collected_at,
                    event=f"Evidence {outcome}: {item.evidence_type.value}",
                    source=item.tool_name,
                    evidence_id=item.evidence_id,
                ))
            entries.append(TimelineEntry(
                timestamp=bundle.collected_at,
                event="Evidence bundle finalized",
                source="evidence_collector",
            ))
        entries.append(TimelineEntry(
            timestamp=analysis.timestamp,
            event="Root cause analysis completed",
            source="root_cause_engine",
        ))
        entries.append(TimelineEntry(
            timestamp=generated_at,
            event="Observation generated",
            source="observation_generator",
        ))
        return tuple(sorted(entries, key=lambda e: e.timestamp))

    @staticmethod
    def _build_step_records(
        context: "InvestigationContext | None",
        bundle: "EvidenceBundle | None",
    ) -> tuple[InvestigationStepRecord, ...]:
        succeeded_tools: set[str] = set()
        if bundle is not None:
            succeeded_tools = {item.tool_name for item in bundle.successful_items}

        plan = context.investigation_plan if context is not None else None
        if plan is not None and plan.steps:
            return tuple(
                InvestigationStepRecord(
                    step_id=step.step_id,
                    sequence=step.sequence,
                    tool_name=step.tool_name,
                    purpose=step.purpose,
                    evidence_collected=step.tool_name in succeeded_tools,
                )
                for step in plan.steps
            )

        if bundle is not None and bundle.items:
            return tuple(
                InvestigationStepRecord(
                    step_id=f"step-{index + 1}",
                    sequence=index,
                    tool_name=item.tool_name,
                    purpose=f"Collect {item.evidence_type.value} evidence",
                    evidence_collected=item.success,
                )
                for index, item in enumerate(bundle.items)
            )

        return ()

    @staticmethod
    def _first_section(text: str) -> str:
        return text.split("\n\n", 1)[0]

    @staticmethod
    def _section(text: str, header: str) -> str:
        if header not in text:
            return ""
        start = text.index(header)
        end = text.find("\n\n===", start)
        return text[start:end] if end != -1 else text[start:]
