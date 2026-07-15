"""
case_engine/investigation/context.py

Sprint 2.38: InvestigationContext — shared mutable accumulator for the pipeline.

Per blueprint Section 31 (Knowledge Retrieval Flow) and the architecture
reconciliation audit: every stage in the investigation pipeline reads from and
writes to ONE shared context object. This eliminates parameter-passing chains
and makes the pipeline state fully observable at any point.

Pipeline stages write to InvestigationContext in this order:
  1. CaseService/WorkflowEngine: ticket + tenant + topic + workflow_definition
  2. SlotFillingEngine: slots, missing_slots, clarification_rounds
  3. SOPResolver: sop_document, sop_steps, sop_match_found
  4. KnowledgeOrchestrator: knowledge_entries
  5. InvestigationPlanner/EvidenceCollector: investigation_plan, evidence_bundle
  6. ToolExecutor: tool_results
  7. VisionProvider: vision_analyses
  8. RootCauseEngine: root_cause
  9. ReasoningEngine: reasoning_result, recommendations
  10. ProposalEngine: action_proposal
  11. ProposalGateway: gateway_decision
  12. ActionExecutor: execution_result, verification_result
  13. ObservationGenerator: observation_text

InvestigationContext does NOT implement any of those stages.
It is a data holder, not a service.

Dependency direction:
  context.py → workflows/models.py (WorkflowDefinition, WorkflowExecutionResult)
  context.py → knowledge/sop/models.py (SOPDocument, SOPStep)
  context.py → knowledge/base.py (KnowledgeEntry)
  context.py → investigation/models.py (InvestigationPlan, EvidenceBundle, RootCauseAnalysis)
  context.py → tools/tool_models.py (ToolResult)
  context.py → tenant/models.py (TenantContext)
  context.py → vision/models.py (VisionEvidence)
  Nothing imports from context.py in this direction — callers pass context in.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from case_engine.investigation.collector.metrics import CollectionMetrics
    from case_engine.investigation.observation.models import Observation
    from case_engine.investigation.root_cause.models import (
        RootCauseAnalysis as RichRootCauseAnalysis,
    )
    from case_engine.workflows.playbooks.models import WorkflowPlaybook

from case_engine.investigation.models import (
    EvidenceBundle,
    InvestigationPlan,
    RootCauseAnalysis,
)
from case_engine.knowledge.base import KnowledgeEntry
from case_engine.knowledge.sop.models import SOPDocument, SOPStep
from case_engine.tenant.models import TenantContext
from case_engine.tools.tool_models import ToolResult
from case_engine.vision.models import VisionEvidence
from case_engine.workflows.models import WorkflowDefinition, WorkflowExecutionResult

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Investigation State machine ────────────────────────────────────────────────

class InvestigationState(str, Enum):
    """
    Lifecycle state of an investigation pipeline run.

    Terminal states (RESOLVED, ESCALATED, FAILED) cannot be transitioned away
    from. All other states are transient — the pipeline advances through them
    in order.
    """
    CREATED          = "CREATED"           # Context initialized, not yet running
    CLASSIFYING      = "CLASSIFYING"       # Topic classification in progress
    COLLECTING_SLOTS = "COLLECTING_SLOTS"  # Gathering missing slot values
    INVESTIGATING    = "INVESTIGATING"     # Evidence collection running
    REASONING        = "REASONING"         # Root cause + knowledge analysis
    PROPOSING        = "PROPOSING"         # Action proposal generation
    VALIDATING       = "VALIDATING"        # Action gateway validation
    EXECUTING        = "EXECUTING"         # Action execution
    VERIFYING        = "VERIFYING"         # Post-execution verification
    RESOLVED         = "RESOLVED"          # Terminal: case resolved successfully
    ESCALATED        = "ESCALATED"         # Terminal: escalated to human agent
    FAILED           = "FAILED"            # Terminal: pipeline error


_TERMINAL_STATES: frozenset[InvestigationState] = frozenset({
    InvestigationState.RESOLVED,
    InvestigationState.ESCALATED,
    InvestigationState.FAILED,
})


# ── Timing record ──────────────────────────────────────────────────────────────

@dataclass
class InvestigationTiming:
    """
    Timestamps for key pipeline milestones.

    All fields are ISO 8601 strings or None (not yet reached).
    Populated by pipeline stages as they complete.
    """
    started_at:               str
    classified_at:            str | None = None
    slots_collected_at:       str | None = None
    investigation_started_at: str | None = None
    reasoning_started_at:     str | None = None
    proposal_generated_at:    str | None = None
    gateway_validated_at:     str | None = None
    execution_started_at:     str | None = None
    resolved_at:              str | None = None
    escalated_at:             str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at":               self.started_at,
            "classified_at":            self.classified_at,
            "slots_collected_at":       self.slots_collected_at,
            "investigation_started_at": self.investigation_started_at,
            "reasoning_started_at":     self.reasoning_started_at,
            "proposal_generated_at":    self.proposal_generated_at,
            "gateway_validated_at":     self.gateway_validated_at,
            "execution_started_at":     self.execution_started_at,
            "resolved_at":              self.resolved_at,
            "escalated_at":             self.escalated_at,
        }


# ── InvestigationContext ───────────────────────────────────────────────────────

@dataclass
class InvestigationContext:
    """
    Shared mutable accumulator for one investigation pipeline run.

    Every pipeline stage reads from and writes to this object.
    The context is created at the start of a case investigation and
    discarded after the case is resolved or escalated.

    It is NOT persisted directly — pipeline stages persist their own
    outputs (e.g. WorkflowExecutionResult to Supabase) and can
    reconstruct the context from those if needed.

    No business logic. No LLM calls. Data holder only.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    context_id: str
    case_id:    str

    # ── Ticket ────────────────────────────────────────────────────────────────
    ticket_id:          str
    ticket_subject:     str
    ticket_description: str
    customer_id:        str
    customer_email:     str
    channel:            str  # "email" | "portal" | "whatsapp" | "api"

    # ── Tenant ────────────────────────────────────────────────────────────────
    tenant_context: TenantContext

    # ── Classification ────────────────────────────────────────────────────────
    topic:                    str
    classification_confidence: float

    # ── Slot management ───────────────────────────────────────────────────────
    slots:               dict[str, Any] = field(default_factory=dict)
    missing_slots:       list[str]      = field(default_factory=list)
    clarification_rounds: int           = 0

    # ── Workflow ──────────────────────────────────────────────────────────────
    workflow_definition: WorkflowDefinition | None      = None
    workflow_result:     WorkflowExecutionResult | None = None

    # ── Playbook (Sprint 2.40) ────────────────────────────────────────────────
    workflow_playbook: WorkflowPlaybook | None = None

    # ── SOP ───────────────────────────────────────────────────────────────────
    sop_document:    SOPDocument | None     = None
    sop_match_found: bool                   = False
    sop_steps:       tuple[SOPStep, ...]   = field(default_factory=tuple)

    # ── SOP resolution detail (Sprint 2.41) ───────────────────────────────────
    resolved_sop:     SOPDocument | None = None
    sop_version:      str | None         = None
    sop_source:       str | None         = None   # "global" | "client_specific" | "fallback"
    sop_client_scope: tuple[str, ...]    = field(default_factory=tuple)
    sop_status:       str | None         = None

    # ── Knowledge ─────────────────────────────────────────────────────────────
    knowledge_entries: list[KnowledgeEntry] = field(default_factory=list)

    # ── Investigation ─────────────────────────────────────────────────────────
    investigation_plan: InvestigationPlan | None = None
    evidence_bundle:    EvidenceBundle | None    = None

    # ── Collector stats (Sprint 2.42) ─────────────────────────────────────────
    collection_metrics: CollectionMetrics | None = None
    collector_stats:    dict[str, Any] | None    = None

    # ── Tools ─────────────────────────────────────────────────────────────────
    tool_results: list[ToolResult] = field(default_factory=list)

    # ── Vision ────────────────────────────────────────────────────────────────
    vision_analyses: list[VisionEvidence] = field(default_factory=list)

    # ── Root cause & reasoning ────────────────────────────────────────────────
    root_cause:      RootCauseAnalysis | None = None
    reasoning_result: dict[str, Any] | None   = None
    recommendations: list[str]                = field(default_factory=list)

    # ── Sprint 2.43: Rich root cause analysis ─────────────────────────────────
    root_cause_analysis:      RichRootCauseAnalysis | None = None   # type: ignore[type-arg]
    root_cause_confidence:    float                         = 0.0
    root_cause_version:       str | None                   = None
    root_cause_recommendation: str | None                  = None
    decision_trace_summary:   dict[str, Any] | None        = None

    # ── Action proposal & gateway ─────────────────────────────────────────────
    action_proposal:  dict[str, Any] | None = None
    gateway_decision: dict[str, Any] | None = None

    # ── Execution & verification ──────────────────────────────────────────────
    execution_result:    dict[str, Any] | None = None
    verification_result: dict[str, Any] | None = None

    # ── Observation (legacy Sprint 2.18 plain-text field) ────────────────────
    observation_text: str | None = None

    # ── Observation (Sprint 2.44 rich typed fields) ───────────────────────────
    observation:           Observation | None    = None  # type: ignore[type-arg]
    observation_version:   str | None            = None
    observation_timestamp: str | None            = None
    observation_status:    str | None            = None
    observation_metadata:  dict[str, Any] | None = None

    # ── Tool Framework (Sprint 2.45) ──────────────────────────────────────────
    tool_execution_summary:   dict[str, Any] | None      = None
    tool_framework_metrics:   dict[str, Any] | None      = None
    tool_audit_trail:         list[dict[str, Any]]       = field(default_factory=list)
    tool_failures:            list[dict[str, Any]]       = field(default_factory=list)
    tool_execution_timestamp: str | None                 = None
    tool_framework_version:   str | None                 = None

    # ── Orchestrator (Sprint 2.46) ────────────────────────────────────────────
    orchestrator_session_id:   str | None = None
    orchestrator_stage:        str | None = None
    orchestrator_started_at:   str | None = None
    orchestrator_completed_at: str | None = None
    orchestrator_result_id:    str | None = None
    orchestrator_status:       str | None = None

    # ── Audit trail ───────────────────────────────────────────────────────────
    audit_events: list[dict[str, Any]] = field(default_factory=list)

    # ── Scoring ───────────────────────────────────────────────────────────────
    overall_confidence: float = 0.0

    # ── State machine ─────────────────────────────────────────────────────────
    state: InvestigationState = InvestigationState.CREATED

    # ── Timing ────────────────────────────────────────────────────────────────
    timing: InvestigationTiming = field(
        default_factory=lambda: InvestigationTiming(started_at=_now_iso())
    )

    # ── Metadata ──────────────────────────────────────────────────────────────
    context_metadata:   dict[str, Any] = field(default_factory=dict)
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    memory:             dict[str, Any] = field(default_factory=dict)

    # ── Resolution ────────────────────────────────────────────────────────────
    resolution_status: str | None = None
    escalation_reason: str | None = None
    completed_at:      str | None = None

    # ─────────────────────────────────────────────────────────────────────────
    # State machine
    # ─────────────────────────────────────────────────────────────────────────

    def is_terminal(self) -> bool:
        """Return True if this context is in a terminal state."""
        return self.state in _TERMINAL_STATES

    def transition_to(self, new_state: InvestigationState) -> None:
        """
        Advance the state machine to new_state.

        Raises ValueError if the context is already in a terminal state.
        Idempotent for the same state (no-op if new_state == self.state).
        """
        if self.is_terminal():
            raise ValueError(
                f"Cannot transition from terminal state {self.state.value!r} "
                f"to {new_state.value!r}"
            )
        if new_state != self.state:
            LOGGER.debug(
                "investigation_context.transition case_id=%s %s → %s",
                self.case_id, self.state.value, new_state.value,
            )
            self.state = new_state

    # ─────────────────────────────────────────────────────────────────────────
    # Mutation helpers
    # ─────────────────────────────────────────────────────────────────────────

    def add_audit_event(
        self,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append a timestamped audit event to the audit trail."""
        self.audit_events.append({
            "event_type": event_type,
            "timestamp":  _now_iso(),
            "state":      self.state.value,
            "details":    details or {},
        })

    def add_tool_result(self, result: ToolResult) -> None:
        """Append a ToolResult to the tool_results list."""
        self.tool_results.append(result)

    def add_vision_analysis(self, analysis: VisionEvidence) -> None:
        """Append a VisionEvidence to the vision_analyses list."""
        self.vision_analyses.append(analysis)

    # ─────────────────────────────────────────────────────────────────────────
    # Slot management
    # ─────────────────────────────────────────────────────────────────────────

    def get_slot(self, name: str) -> Any:
        """Return the value of a named slot, or None if not present."""
        return self.slots.get(name)

    def set_slot(self, name: str, value: Any) -> None:
        """Set a slot value and remove it from missing_slots if present."""
        self.slots[name] = value
        if name in self.missing_slots:
            self.missing_slots.remove(name)

    # ─────────────────────────────────────────────────────────────────────────
    # Presence checks
    # ─────────────────────────────────────────────────────────────────────────

    def has_tenant(self) -> bool:
        """Return True if tenant_context is set."""
        return self.tenant_context is not None

    def has_topic(self) -> bool:
        """Return True if a topic has been classified."""
        return bool(self.topic)

    def has_evidence(self) -> bool:
        """Return True if an evidence bundle has been collected."""
        return self.evidence_bundle is not None

    def has_root_cause(self) -> bool:
        """Return True if root cause analysis has completed."""
        return self.root_cause is not None

    def has_rich_root_cause(self) -> bool:
        """Return True if Sprint 2.43 RootCauseAnalysis has been written."""
        return self.root_cause_analysis is not None

    def has_rich_observation(self) -> bool:
        """Return True if Sprint 2.44 Observation has been written."""
        return self.observation is not None

    def has_tool_execution_summary(self) -> bool:
        """Return True if Sprint 2.45 tool execution summary has been written."""
        return self.tool_execution_summary is not None

    def has_orchestrator_session(self) -> bool:
        """Return True if Sprint 2.46 orchestrator has set a session_id."""
        return self.orchestrator_session_id is not None

    def has_orchestrator_result(self) -> bool:
        """Return True if Sprint 2.46 orchestrator has produced a result."""
        return self.orchestrator_result_id is not None

    def has_sop(self) -> bool:
        """Return True if a matching SOP was found."""
        return self.sop_match_found and self.sop_document is not None

    def has_resolved_sop(self) -> bool:
        """Return True if Sprint 2.41 SOPDocumentResolver has written a resolved SOP."""
        return self.resolved_sop is not None

    def has_workflow(self) -> bool:
        """Return True if a workflow definition is loaded."""
        return self.workflow_definition is not None

    def has_playbook_spec(self) -> bool:
        """Return True if a WorkflowPlaybook has been resolved for this investigation."""
        return self.workflow_playbook is not None

    def slots_complete(self) -> bool:
        """Return True if there are no remaining missing slots."""
        return len(self.missing_slots) == 0

    # ─────────────────────────────────────────────────────────────────────────
    # Serialization
    # ─────────────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """
        Return a serializable representation of this context.

        Heavy payloads (evidence_bundle items, vision raw_output) are
        summarised by count rather than fully expanded, to keep the
        dict suitable for logging and API responses.
        """
        return {
            "context_id":               self.context_id,
            "case_id":                  self.case_id,
            "ticket_id":                self.ticket_id,
            "ticket_subject":           self.ticket_subject,
            "customer_id":              self.customer_id,
            "customer_email":           self.customer_email,
            "channel":                  self.channel,
            "topic":                    self.topic,
            "classification_confidence": self.classification_confidence,
            "state":                    self.state.value,
            "overall_confidence":       self.overall_confidence,
            "slots":                    self.slots,
            "missing_slots":            self.missing_slots,
            "clarification_rounds":     self.clarification_rounds,
            "sop_match_found":          self.sop_match_found,
            "sop_id":                   self.sop_document.sop_id if self.sop_document else None,
            "resolved_sop_id":          self.resolved_sop.sop_id if self.resolved_sop else None,
            "sop_version":              self.sop_version,
            "sop_source":               self.sop_source,
            "sop_client_scope":         list(self.sop_client_scope),
            "sop_status":               self.sop_status,
            "workflow_id":              (
                self.workflow_definition.workflow_id if self.workflow_definition else None
            ),
            "playbook_id":              (
                self.workflow_playbook.playbook_id if self.workflow_playbook else None
            ),
            "knowledge_entry_count":    len(self.knowledge_entries),
            "tool_result_count":        len(self.tool_results),
            "vision_analysis_count":    len(self.vision_analyses),
            "audit_event_count":        len(self.audit_events),
            "root_cause_category":      (
                self.root_cause.category.value if self.root_cause else None
            ),
            "root_cause_confidence":    (
                self.root_cause.confidence if self.root_cause else None
            ),
            "root_cause_analysis_id":   (
                self.root_cause_analysis.analysis_id if self.root_cause_analysis else None
            ),
            "root_cause_rich_category": (
                self.root_cause_analysis.category.value if self.root_cause_analysis else None
            ),
            "root_cause_version":       self.root_cause_version,
            "root_cause_recommendation": self.root_cause_recommendation,
            "has_action_proposal":      self.action_proposal is not None,
            "has_gateway_decision":     self.gateway_decision is not None,
            "has_observation":          self.observation_text is not None,
            "observation_id":           (
                self.observation.observation_id if self.observation is not None else None
            ),
            "observation_version":      self.observation_version,
            "observation_timestamp":    self.observation_timestamp,
            "observation_status":       self.observation_status,
            "tool_execution_summary":   self.tool_execution_summary,
            "tool_framework_metrics":   self.tool_framework_metrics,
            "tool_audit_trail_count":   len(self.tool_audit_trail),
            "tool_failures_count":      len(self.tool_failures),
            "tool_execution_timestamp": self.tool_execution_timestamp,
            "tool_framework_version":   self.tool_framework_version,
            "resolution_status":        self.resolution_status,
            "escalation_reason":        self.escalation_reason,
            "completed_at":             self.completed_at,
            "timing":                   self.timing.to_dict(),
            "orchestrator_session_id":   self.orchestrator_session_id,
            "orchestrator_stage":        self.orchestrator_stage,
            "orchestrator_started_at":   self.orchestrator_started_at,
            "orchestrator_completed_at": self.orchestrator_completed_at,
            "orchestrator_result_id":    self.orchestrator_result_id,
            "orchestrator_status":       self.orchestrator_status,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Factory
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        case_id: str,
        ticket_id: str,
        ticket_subject: str,
        ticket_description: str,
        customer_id: str,
        customer_email: str,
        channel: str,
        tenant_context: TenantContext,
        topic: str,
        classification_confidence: float = 0.0,
    ) -> "InvestigationContext":
        """
        Factory method: create a fresh InvestigationContext for a new investigation.

        Generates a new context_id. All optional pipeline fields default to
        None / empty. The state starts at CREATED.
        """
        return cls(
            context_id=_new_id(),
            case_id=case_id,
            ticket_id=ticket_id,
            ticket_subject=ticket_subject,
            ticket_description=ticket_description,
            customer_id=customer_id,
            customer_email=customer_email,
            channel=channel,
            tenant_context=tenant_context,
            topic=topic,
            classification_confidence=classification_confidence,
        )
