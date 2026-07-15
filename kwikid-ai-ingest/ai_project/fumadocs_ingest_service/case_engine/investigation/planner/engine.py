"""
case_engine/investigation/planner/engine.py

Sprint 2.39: InvestigationPlanner — Evidence Planning Engine.

Reads InvestigationContext and produces an InvestigationPlan.
NEVER executes tools. NEVER calls APIs. NEVER modifies external state.
NEVER generates customer replies. Plans only.

Responsibilities:
  - Select the PlanningRule that matches the context topic.
  - Resolve the WorkflowDefinition (via WorkflowRepository ABC).
  - Resolve the SOPDocument (via SOPResolver duck-type).
  - Delegate plan construction to the selected rule.
  - Return MinimalFallbackRule plan on any error — never raises.

Pipeline position: Stage 5 (after SlotFillingEngine, SOPResolver, KnowledgeOrchestrator).

Dependency direction:
  engine.py → planner/rules.py (PlanningRule, get_default_rules, MinimalFallbackRule)
  engine.py → planner/models.py (InvestigationPlan)
  engine.py → investigation/context.py (InvestigationContext) via TYPE_CHECKING
  engine.py → workflows/models.py (WorkflowDefinition) via TYPE_CHECKING
  engine.py → knowledge/sop/models.py (SOPDocument) via TYPE_CHECKING
  engine.py does NOT import from evidence/, tools/, vision/, tenant/ directly
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

from case_engine.investigation.evidence.models import EvidencePriority
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
    StepDependency,
    StepOutput,
    StepPrecondition,
)
from case_engine.investigation.planner.rules import (
    DefaultWorkflowPlanningRule,
    MinimalFallbackRule,
    PlanningRule,
    get_default_rules,
)
from case_engine.workflows.playbooks.models import WorkflowPlaybook

if TYPE_CHECKING:
    from case_engine.investigation.context import InvestigationContext
    from case_engine.knowledge.sop.models import SOPDocument
    from case_engine.workflows.models import WorkflowDefinition

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_plan_id() -> str:
    return str(uuid.uuid4())


# ── Workflow resolver protocol ─────────────────────────────────────────────────

@runtime_checkable
class WorkflowRepository(Protocol):
    """
    Structural protocol for any object that can resolve a WorkflowDefinition.

    PlaybookRegistry already satisfies this interface — no adapter needed.
    """
    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        ...


# ── SOP resolver protocol ──────────────────────────────────────────────────────

@runtime_checkable
class SOPResolverProtocol(Protocol):
    """
    Structural protocol for any object that can resolve a SOPDocument.

    Used by the engine to retrieve the matching SOP for a topic.
    The engine only reads from the resolved SOP — never modifies it.
    """
    def resolve(self, topic: str, context: dict[str, Any]) -> SOPDocument | None:
        ...


# ── Knowledge provider protocol ────────────────────────────────────────────────

@runtime_checkable
class KnowledgeProvider(Protocol):
    """
    Structural protocol for any object that can supply knowledge metadata.

    Used by the engine for evidence hints only — not for procedural instructions.
    The engine never executes knowledge queries; it only reads metadata that
    was already collected by the KnowledgeOrchestrator.
    """
    def get_evidence_hints(self, topic: str) -> dict[str, Any]:
        ...


# ── Playbook resolver protocol ─────────────────────────────────────────────────

@runtime_checkable
class PlaybookResolverProtocol(Protocol):
    """
    Structural protocol for WorkflowPlaybookResolver (Sprint 2.40).

    WorkflowPlaybookResolver already satisfies this interface — no adapter needed.
    """
    def resolve(
        self,
        topic: str,
        client_id: str | None = None,
        slots: dict[str, Any] | None = None,
    ) -> WorkflowPlaybook:
        ...

    def can_resolve(self, topic: str, client_id: str | None = None) -> bool:
        ...


# ── PlaybookPlanningRule ───────────────────────────────────────────────────────

_PRIORITY_RANK: dict[EvidencePriority, int] = {
    EvidencePriority.CRITICAL: 0,
    EvidencePriority.HIGH:     1,
    EvidencePriority.NORMAL:   2,
    EvidencePriority.LOW:      3,
}

_PRIORITY_TO_INVESTIGATION: dict[EvidencePriority, InvestigationPriority] = {
    EvidencePriority.CRITICAL: InvestigationPriority.CRITICAL,
    EvidencePriority.HIGH:     InvestigationPriority.HIGH,
    EvidencePriority.NORMAL:   InvestigationPriority.NORMAL,
    EvidencePriority.LOW:      InvestigationPriority.LOW,
}


class PlaybookPlanningRule(PlanningRule):
    """
    Sprint 2.40: Converts a WorkflowPlaybook into an InvestigationPlan.

    Bridges the data-driven WorkflowPlaybook (template) to the typed
    InvestigationPlan (case instance) consumed by the Evidence Collector.

    Conversion:
      PlaybookStep → PlanningStep
      PlaybookGraph (topological order) → InvestigationGraph
      PlaybookStep.evidence_required → PlanningStep.evidence_required (same type)
      PlaybookStep.retry_policy → PlanningStep.retry_policy (same type)
      PlaybookStep.critical → FailureStrategy.ESCALATE | CONTINUE
      PlaybookStep.parallel_group → PlanningStep.parallelizable
      PlaybookStep.depends_on → StepPrecondition + StepDependency edges
    """

    def __init__(self, playbook: WorkflowPlaybook) -> None:
        self._playbook = playbook

    def topic_key(self) -> str:
        return self._playbook.topic.upper().replace("-", "_")

    def generate_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
        workflow_id: str | None,
        slots: dict[str, Any],
        workflow: Any,
        sop: Any,
    ) -> InvestigationPlan:
        """
        Derive an InvestigationPlan from this rule's WorkflowPlaybook.
        Never raises. The workflow and sop arguments are accepted but not used
        — the playbook is the authoritative evidence specification.
        """
        try:
            ordered_steps = self._playbook.investigation_graph.topological_order()
        except ValueError:
            ordered_steps = list(self._playbook.investigation_graph.steps)

        step_ids = {s.step_id for s in ordered_steps}

        planning_steps: list[PlanningStep] = []
        for order, pb_step in enumerate(ordered_steps):
            preconditions = tuple(
                StepPrecondition(
                    precondition_id=f"pre_{pb_step.step_id}_{dep_id}",
                    description=f"Step {dep_id!r} must complete",
                    kind=PreconditionKind.EVIDENCE_PRESENT,
                    key=dep_id,
                )
                for dep_id in pb_step.depends_on
                if dep_id in step_ids
            )

            outputs = tuple(
                StepOutput(
                    output_id=f"out_{pb_step.step_id}_{req.requirement_id}",
                    description=req.description,
                    kind=req.kind,
                    key=req.requirement_id,
                )
                for req in pb_step.evidence_required
            )

            failure_strategy = (
                FailureStrategy.ESCALATE if pb_step.critical else FailureStrategy.CONTINUE
            )

            best_priority: EvidencePriority = EvidencePriority.NORMAL
            if pb_step.evidence_required:
                best_priority = min(
                    pb_step.evidence_required,
                    key=lambda r: _PRIORITY_RANK.get(r.priority, 2),
                ).priority

            candidate_capabilities = (
                (pb_step.required_capability,) if pb_step.required_capability else ()
            )

            planning_steps.append(PlanningStep(
                step_id=pb_step.step_id,
                order=order,
                title=pb_step.name,
                description=pb_step.description,
                evidence_required=pb_step.evidence_required,
                evidence_priority=best_priority,
                candidate_capabilities=candidate_capabilities,
                preconditions=preconditions,
                outputs=outputs,
                retry_policy=pb_step.retry_policy,
                failure_strategy=failure_strategy,
                parallelizable=pb_step.is_parallelizable,
                optional=pb_step.optional,
                depends_on=pb_step.depends_on,
            ))

        steps_tuple = tuple(planning_steps)

        edges: list[StepDependency] = [
            StepDependency(
                from_step_id=dep_id,
                to_step_id=ps.step_id,
                dependency_type=DependencyType.SEQUENTIAL,
            )
            for ps in planning_steps
            for dep_id in ps.depends_on
            if dep_id in step_ids
        ]

        parallel_group_names = {
            s.parallel_group
            for s in self._playbook.investigation_graph.steps
            if s.parallel_group is not None
        }
        parallel_groups = tuple(
            frozenset(
                s.step_id
                for s in self._playbook.investigation_graph.steps
                if s.parallel_group == name
            )
            for name in parallel_group_names
        )
        parallel_groups = tuple(g for g in parallel_groups if len(g) > 1)

        graph = InvestigationGraph(
            nodes=steps_tuple,
            edges=tuple(edges),
            parallel_groups=parallel_groups,
        )

        seen_req_ids: set[str] = set()
        expected_evidence: list[EvidenceRequirement] = []
        for step in planning_steps:
            for req in step.evidence_required:
                if req.requirement_id not in seen_req_ids:
                    seen_req_ids.add(req.requirement_id)
                    expected_evidence.append(req)

        completion_conditions: list[CompletionCondition] = [
            CompletionCondition(
                condition_id=f"cond_{step.step_id}_{req.requirement_id}",
                description=f"{req.title} collected in {step.title}",
                required=True,
            )
            for step in planning_steps
            if not step.optional
            for req in step.evidence_required
            if req.required
        ]

        n = len(steps_tuple)
        has_vision = any(
            req.kind == EvidenceKind.VISION
            for s in steps_tuple
            for req in s.evidence_required
        )
        if n <= 1:
            complexity = EstimatedComplexity.TRIVIAL
        elif n == 2:
            complexity = EstimatedComplexity.LOW
        elif n <= 4:
            complexity = EstimatedComplexity.HIGH if has_vision else EstimatedComplexity.MEDIUM
        elif n <= 6:
            complexity = EstimatedComplexity.HIGH
        else:
            complexity = EstimatedComplexity.VERY_HIGH

        investigation_priority = _PRIORITY_TO_INVESTIGATION.get(
            self._playbook.priority, InvestigationPriority.NORMAL
        )

        return InvestigationPlan(
            plan_id=plan_id,
            workflow_id=workflow_id,
            topic=topic,
            client_id=client_id,
            case_id=case_id,
            objective=self._playbook.description,
            investigation_priority=investigation_priority,
            estimated_complexity=complexity,
            expected_evidence=tuple(expected_evidence),
            confidence_threshold=0.75,
            completion_conditions=tuple(completion_conditions),
            fallback_strategy=FallbackStrategy.PARTIAL_INVESTIGATION,
            created_at=_now_iso(),
            steps=steps_tuple,
            graph=graph,
        )


# ── InvestigationPlanner ───────────────────────────────────────────────────────

class InvestigationPlanner:
    """
    Sprint 2.39 Evidence Planning Engine.

    Produces an InvestigationPlan from an InvestigationContext.

    This class is the Sprint 2.39 evidence-based planner. The Sprint 2.18
    tool-based planner is preserved in _legacy.py and re-exported from __init__.py
    for backward compatibility.

    Safety contract:
      - plan() NEVER raises — returns MinimalFallbackRule plan on any error.
      - plan() NEVER executes tools, calls APIs, modifies state, or generates replies.
      - plan() is idempotent — calling it twice on the same context is safe.
      - All returned InvestigationPlan objects are frozen (immutable).

    Constructor:
      workflow_repository:  Object satisfying WorkflowRepository protocol (optional).
                            If provided, used to resolve WorkflowDefinition by ID.
                            If None, engine uses context.workflow_definition directly.
      sop_resolver:         Object satisfying SOPResolverProtocol (optional).
                            If provided, used to resolve SOP for the topic.
                            If None, engine uses context.sop_document directly.
      rules:                Planning rules to consult (in order). If None, uses
                            get_default_rules() — the 5 topic-specific rules.
      knowledge_provider:   Optional knowledge provider for evidence hints.
                            Never used for procedural instructions.
    """

    def __init__(
        self,
        workflow_repository: WorkflowRepository | None = None,
        sop_resolver: SOPResolverProtocol | None = None,
        rules: tuple[PlanningRule, ...] | None = None,
        knowledge_provider: KnowledgeProvider | None = None,
        playbook_resolver: PlaybookResolverProtocol | None = None,
    ) -> None:
        self._workflow_repository = workflow_repository
        self._sop_resolver        = sop_resolver
        self._rules               = rules if rules is not None else get_default_rules()
        self._knowledge_provider  = knowledge_provider
        self._playbook_resolver   = playbook_resolver
        self._default_workflow_rule = DefaultWorkflowPlanningRule()
        self._fallback_rule         = MinimalFallbackRule()

    # ── Public API ─────────────────────────────────────────────────────────────

    def plan(self, context: InvestigationContext) -> InvestigationPlan:
        """
        Produce an InvestigationPlan from the given context.

        Selection order:
          1. Topic-specific rule (from self._rules, first match wins).
          2. DefaultWorkflowPlanningRule (if a workflow is resolvable).
          3. MinimalFallbackRule (absolute last resort).

        Never raises. Returns MinimalFallbackRule plan on any exception.
        """
        plan_id   = _new_plan_id()
        case_id   = context.case_id
        client_id = context.tenant_context.client_id if context.tenant_context else "unknown"
        topic     = (context.topic or "UNKNOWN").upper().replace("-", "_")

        try:
            workflow = self._resolve_workflow(context)
            sop      = self._resolve_sop(context, topic)
            rule     = self._select_rule(topic, workflow, client_id)
            workflow_id = workflow.workflow_id if workflow else None

            LOGGER.info(
                "investigation_planner.plan case_id=%s topic=%s rule=%s workflow_id=%s",
                case_id, topic, type(rule).__name__, workflow_id,
            )

            investigation_plan = rule.generate_plan(
                plan_id=plan_id,
                case_id=case_id,
                client_id=client_id,
                topic=topic,
                workflow_id=workflow_id,
                slots=dict(context.slots),
                workflow=workflow,
                sop=sop,
            )

            LOGGER.info(
                "investigation_planner.plan_complete case_id=%s steps=%d evidence=%d",
                case_id, len(investigation_plan.steps), len(investigation_plan.expected_evidence),
            )
            return investigation_plan

        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "investigation_planner.plan_error case_id=%s topic=%s error=%s — using fallback",
                case_id, topic, exc, exc_info=True,
            )
            return self._fallback_plan(plan_id, case_id, client_id, topic)

    # ── Rule selection ─────────────────────────────────────────────────────────

    def _select_rule(
        self,
        topic: str,
        workflow: WorkflowDefinition | None,
        client_id: str | None = None,
    ) -> PlanningRule:
        """
        Select the best PlanningRule for the given topic and workflow.

        Priority:
          1. PlaybookPlanningRule (Sprint 2.40) — if playbook_resolver is set and resolves topic.
          2. First topic-specific rule that applies_to(topic).
          3. DefaultWorkflowPlanningRule if a workflow is present.
          4. MinimalFallbackRule.
        """
        if self._playbook_resolver is not None:
            try:
                if self._playbook_resolver.can_resolve(topic, client_id):
                    playbook = self._playbook_resolver.resolve(topic, client_id)
                    LOGGER.info(
                        "investigation_planner.playbook_rule topic=%s client=%s playbook_id=%s",
                        topic, client_id, playbook.playbook_id,
                    )
                    return PlaybookPlanningRule(playbook)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "investigation_planner.playbook_resolve_failed topic=%s error=%s — falling back",
                    topic, exc,
                )

        for rule in self._rules:
            if rule.applies_to(topic):
                return rule

        if workflow is not None:
            LOGGER.info(
                "investigation_planner.using_default_workflow_rule topic=%s workflow_id=%s",
                topic, workflow.workflow_id,
            )
            return self._default_workflow_rule

        LOGGER.warning(
            "investigation_planner.no_rule_found topic=%s — using minimal fallback",
            topic,
        )
        return self._fallback_rule

    # ── Workflow resolution ────────────────────────────────────────────────────

    def _resolve_workflow(
        self,
        context: InvestigationContext,
    ) -> WorkflowDefinition | None:
        """
        Resolve a WorkflowDefinition for the context.

        Resolution order:
          1. If workflow_repository is available and context has a workflow_definition
             with a workflow_id, look it up for a fresh copy.
          2. Use context.workflow_definition directly.
          3. Return None if neither is available.
        """
        if context.workflow_definition is not None:
            if self._workflow_repository is not None:
                workflow_id = getattr(context.workflow_definition, "workflow_id", None)
                if workflow_id:
                    try:
                        resolved = self._workflow_repository.get_workflow(workflow_id)
                        if resolved is not None:
                            return resolved
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning(
                            "investigation_planner.workflow_lookup_failed workflow_id=%s error=%s",
                            workflow_id, exc,
                        )
            return context.workflow_definition
        return None

    # ── SOP resolution ─────────────────────────────────────────────────────────

    def _resolve_sop(
        self,
        context: InvestigationContext,
        topic: str,
    ) -> SOPDocument | None:
        """
        Resolve a SOPDocument for the topic.

        Resolution order:
          1. If sop_resolver is available, call resolve(topic, slots).
          2. Use context.sop_document directly.
          3. Return None.

        Sprint 2.41: when resolved, writes resolved_sop, sop_version, sop_source,
        sop_client_scope, sop_status to context as a side effect.

        The SOP is used as evidence hints only — the planner never executes
        SOP steps or procedures.
        """
        if self._sop_resolver is not None:
            try:
                resolved = self._sop_resolver.resolve(topic, dict(context.slots))
                if resolved is not None:
                    self._write_sop_to_context(context, resolved, topic)
                    return resolved
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "investigation_planner.sop_resolve_failed topic=%s error=%s",
                    topic, exc,
                )
        return context.sop_document if context.sop_match_found else None

    def _write_sop_to_context(
        self,
        context: InvestigationContext,
        sop: SOPDocument,
        topic: str,
    ) -> None:
        """Write Sprint 2.41 SOP resolution metadata to context (side-effect only)."""
        context.resolved_sop = sop
        context.sop_version  = sop.version
        context.sop_status   = sop.status.value
        context.sop_client_scope = sop.client_scope

        can_resolve_fn = getattr(self._sop_resolver, "can_resolve", None)
        if callable(can_resolve_fn):
            client_id = (
                context.tenant_context.client_id if context.tenant_context else None
            )
            try:
                if can_resolve_fn(topic, client_id):
                    is_global = getattr(sop, "is_global", lambda: True)()
                    context.sop_source = "global" if is_global else "client_specific"
                else:
                    context.sop_source = "fallback"
            except Exception:  # noqa: BLE001
                context.sop_source = "global"
        else:
            is_global = getattr(sop, "is_global", lambda: True)()
            context.sop_source = "global" if is_global else "client_specific"

    # ── Fallback ───────────────────────────────────────────────────────────────

    def _fallback_plan(
        self,
        plan_id: str,
        case_id: str,
        client_id: str,
        topic: str,
    ) -> InvestigationPlan:
        """Return a minimal valid plan without raising."""
        try:
            return self._fallback_rule.generate_plan(
                plan_id=plan_id,
                case_id=case_id,
                client_id=client_id,
                topic=topic,
                workflow_id=None,
                slots={},
                workflow=None,
                sop=None,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.critical(
                "investigation_planner.fallback_failed — this should never happen: %s",
                exc, exc_info=True,
            )
            raise RuntimeError(
                f"InvestigationPlanner.plan() encountered an unrecoverable error: {exc}"
            ) from exc
