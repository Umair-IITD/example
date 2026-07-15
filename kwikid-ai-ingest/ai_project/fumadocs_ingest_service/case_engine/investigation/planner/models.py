"""
case_engine/investigation/planner/models.py

Sprint 2.39: Investigation Planning domain models.

Evidence-centric planning types produced by the InvestigationPlanner
(EvidencePlanningEngine). These replace the tool-centric Sprint 2.18
InvestigationPlan/InvestigationStep for all Sprint 2.39+ planning.

Type hierarchy:
  EvidenceKind              — category of evidence to collect
  EvidencePriority          — (imported from investigation.evidence.models)
  ToolCapability            — (imported from tools.tool_models)
  EvidenceRequirement       — what evidence a step needs (never which tool)
  PreconditionKind          — category of a step precondition
  StepPrecondition          — guard that must be satisfied before a step executes
  StepOutput                — what a step produces on success
  RetryPolicy               — how to retry a failed step
  FailureStrategy           — what to do when a step fails
  DependencyType            — relationship between two steps in the graph
  StepDependency            — typed edge between two PlanningSteps
  PlanningStep              — one evidence-collection unit in the plan
  InvestigationPriority     — overall urgency of the investigation
  EstimatedComplexity       — estimated difficulty of the investigation
  FallbackStrategy          — what to do when the plan cannot complete normally
  CompletionCondition       — condition that must be true for the plan to complete
  InvestigationGraph        — DAG of PlanningSteps with dependency edges
  InvestigationPlan         — rich, frozen, fully-typed planning output

Dependency direction (leaf-near node):
  models.py → case_engine.investigation.evidence.models (EvidencePriority)
  models.py → case_engine.tools.tool_models (ToolCapability)
  models.py → stdlib only
  models.py does NOT import from investigation.context, investigation.models,
  workflows, knowledge, or any other case_engine sub-package.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from case_engine.investigation.evidence.models import EvidencePriority
from case_engine.tools.tool_models import ToolCapability


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Enumerations ───────────────────────────────────────────────────────────────

class EvidenceKind(str, Enum):
    """
    Category of evidence that a planning step requires.

    Describes WHAT kind of evidence is needed — not which tool fetches it.
    The Evidence Collector decides which tool satisfies each EvidenceKind.
    """
    SESSION       = "SESSION"        # Session lifecycle state
    LOG           = "LOG"            # Log/failure analysis
    SUMMARY       = "SUMMARY"        # Aggregated / historical summary
    VISION        = "VISION"         # Computer vision analysis
    DATABASE      = "DATABASE"       # Direct DB query result
    API           = "API"            # External API response (user, onboarding)
    KNOWLEDGE     = "KNOWLEDGE"      # SOP or KB article
    WORKFLOW      = "WORKFLOW"       # Workflow execution state
    CONFIGURATION = "CONFIGURATION"  # Feature flag or config value
    HUMAN         = "HUMAN"          # Human confirmation or input


class InvestigationPriority(str, Enum):
    """Overall urgency of an investigation."""
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    NORMAL   = "NORMAL"
    LOW      = "LOW"


class EstimatedComplexity(str, Enum):
    """Estimated difficulty of an investigation based on its step count and evidence requirements."""
    TRIVIAL   = "TRIVIAL"    # 1 step, 1 evidence item
    LOW       = "LOW"        # 2 steps
    MEDIUM    = "MEDIUM"     # 3–4 steps
    HIGH      = "HIGH"       # 5–6 steps or vision required
    VERY_HIGH = "VERY_HIGH"  # 7+ steps or multi-system


class FallbackStrategy(str, Enum):
    """What to do when the investigation plan cannot complete normally."""
    ESCALATE_IMMEDIATELY  = "ESCALATE_IMMEDIATELY"   # Escalate at first failure
    PARTIAL_INVESTIGATION = "PARTIAL_INVESTIGATION"  # Proceed with available evidence
    SKIP_OPTIONAL_STEPS   = "SKIP_OPTIONAL_STEPS"    # Skip optional steps on failure
    RETRY_FAILED_STEPS    = "RETRY_FAILED_STEPS"     # Retry before escalating


class FailureStrategy(str, Enum):
    """How the planner handles a failed step when building the plan."""
    CONTINUE = "CONTINUE"  # Continue with remaining steps despite this failure
    RETRY    = "RETRY"     # Retry this step (up to retry_policy.max_attempts)
    SKIP     = "SKIP"      # Skip this step and continue
    ABORT    = "ABORT"     # Abort the plan immediately
    ESCALATE = "ESCALATE"  # Escalate case without further investigation


class DependencyType(str, Enum):
    """Relationship type between two steps in the investigation graph."""
    SEQUENTIAL  = "SEQUENTIAL"   # B must start after A completes successfully
    CONDITIONAL = "CONDITIONAL"  # B runs after A if a condition is true
    FALLBACK    = "FALLBACK"     # B runs after A fails (B is A's fallback)
    PARALLEL    = "PARALLEL"     # B can run concurrently with A (no dependency)


class PreconditionKind(str, Enum):
    """Category of a step precondition."""
    SLOT_PRESENT     = "SLOT_PRESENT"      # A named slot must be filled
    EVIDENCE_PRESENT = "EVIDENCE_PRESENT"  # A prior evidence item must exist
    ALWAYS           = "ALWAYS"            # Unconditional (no precondition)
    NEVER            = "NEVER"             # Never satisfied (used to disable a step)


# ── Leaf domain types ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceRequirement:
    """
    Specifies WHAT evidence a planning step needs — never WHICH tool fetches it.

    The Evidence Collector selects an appropriate tool based on EvidenceKind
    and the capability_hint. The planner does not name tools.

    Fields:
        requirement_id:   Stable identifier (deterministic, not UUID)
        kind:             Category of evidence required
        title:            Short human-readable name
        description:      Detailed explanation for audit and logging
        priority:         How critical this evidence is to the investigation
        required:         True = the plan cannot complete without this evidence
        expected_fields:  Field names expected in the collected evidence payload
        validation_hints: Hints for the Evidence Collector about how to validate
        capability_hint:  ToolCapability that could satisfy this requirement
    """
    requirement_id:   str
    kind:             EvidenceKind
    title:            str
    description:      str
    priority:         EvidencePriority
    required:         bool
    expected_fields:  tuple[str, ...]
    validation_hints: tuple[str, ...]
    capability_hint:  ToolCapability | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id":   self.requirement_id,
            "kind":             self.kind.value,
            "title":            self.title,
            "description":      self.description,
            "priority":         self.priority.value,
            "required":         self.required,
            "expected_fields":  list(self.expected_fields),
            "validation_hints": list(self.validation_hints),
            "capability_hint":  self.capability_hint.value if self.capability_hint else None,
        }


@dataclass(frozen=True)
class RetryPolicy:
    """
    Retry configuration for a PlanningStep.

    Consumed by the Evidence Collector when executing steps.
    The planner attaches this to each step; the collector enforces it.
    """
    max_attempts:     int   = 2
    backoff_seconds:  float = 0.0
    retry_on_timeout: bool  = True
    retry_on_partial: bool  = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts":     self.max_attempts,
            "backoff_seconds":  self.backoff_seconds,
            "retry_on_timeout": self.retry_on_timeout,
            "retry_on_partial": self.retry_on_partial,
        }


@dataclass(frozen=True)
class CompletionCondition:
    """
    A condition that must be satisfied for the InvestigationPlan to be complete.

    The Evidence Collector checks all required CompletionConditions after
    running the plan to determine whether the investigation succeeded.

    Fields:
        condition_id: Stable identifier
        description:  Human-readable completion criterion
        required:     True = plan fails if this condition is not met
    """
    condition_id: str
    description:  str
    required:     bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "description":  self.description,
            "required":     self.required,
        }


@dataclass(frozen=True)
class StepPrecondition:
    """
    A guard that must be satisfied before a PlanningStep can execute.

    Checked by the Evidence Collector before running a step.
    If not satisfied, the step is skipped or the plan is aborted
    depending on the step's failure_strategy.

    Fields:
        precondition_id: Stable identifier
        description:     Human-readable guard explanation
        kind:            Category of precondition
        key:             Slot name, evidence key, or other identifier
    """
    precondition_id: str
    description:     str
    kind:            PreconditionKind
    key:             str

    def to_dict(self) -> dict[str, Any]:
        return {
            "precondition_id": self.precondition_id,
            "description":     self.description,
            "kind":            self.kind.value,
            "key":             self.key,
        }


@dataclass(frozen=True)
class StepOutput:
    """
    Describes what a PlanningStep produces on success.

    Consumed by downstream steps to declare that a dependency is satisfied,
    and by the Evidence Collector to know where to store the result.

    Fields:
        output_id:   Stable identifier
        description: Human-readable output description
        kind:        Category of evidence this output produces
        key:         Context key where this output is stored in InvestigationContext
    """
    output_id:   str
    description: str
    kind:        EvidenceKind
    key:         str

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_id":   self.output_id,
            "description": self.description,
            "kind":        self.kind.value,
            "key":         self.key,
        }


@dataclass(frozen=True)
class StepDependency:
    """
    A typed dependency edge between two PlanningSteps in the investigation graph.

    Convention: from_step_id ENABLES to_step_id (from must complete first).

    Fields:
        from_step_id:    The prerequisite step (must complete/fail first)
        to_step_id:      The dependent step (runs after from completes)
        dependency_type: Relationship type (SEQUENTIAL, CONDITIONAL, FALLBACK, PARALLEL)
        condition:       Guard expression for CONDITIONAL dependencies
    """
    from_step_id:    str
    to_step_id:      str
    dependency_type: DependencyType
    condition:       str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_step_id":    self.from_step_id,
            "to_step_id":      self.to_step_id,
            "dependency_type": self.dependency_type.value,
            "condition":       self.condition,
        }


# ── PlanningStep ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlanningStep:
    """
    One evidence-collection unit in an InvestigationPlan.

    A PlanningStep declares WHAT evidence to collect and HOW the collection
    should behave. It never names a specific tool — only the EvidenceKind
    and ToolCapability hints required.

    The Evidence Collector receives these steps and selects appropriate tools.

    Fields:
        step_id:                Stable identifier (e.g., "step_01_session_evidence")
        order:                  0-based execution order for linear traversal
        title:                  Short human-readable name
        description:            What this step investigates and why
        evidence_required:      Evidence this step must collect
        evidence_priority:      Overall priority for this step's evidence
        candidate_capabilities: ToolCapability values that could satisfy this step
        preconditions:          Guards that must be satisfied before executing
        outputs:                What this step produces on success
        retry_policy:           How to handle transient failures
        failure_strategy:       What to do if this step fails after retries
        parallelizable:         True = this step can run concurrently with others
        optional:               True = plan can complete even if this step is skipped
        depends_on:             step_ids this step depends on (mirrors graph edges)
    """
    step_id:                str
    order:                  int
    title:                  str
    description:            str
    evidence_required:      tuple[EvidenceRequirement, ...]
    evidence_priority:      EvidencePriority
    candidate_capabilities: tuple[ToolCapability, ...]
    preconditions:          tuple[StepPrecondition, ...]
    outputs:                tuple[StepOutput, ...]
    retry_policy:           RetryPolicy
    failure_strategy:       FailureStrategy
    parallelizable:         bool
    optional:               bool
    depends_on:             tuple[str, ...]

    @property
    def is_root(self) -> bool:
        """True if this step has no dependencies."""
        return len(self.depends_on) == 0

    def required_evidence_count(self) -> int:
        """Count of required (non-optional) evidence requirements."""
        return sum(1 for r in self.evidence_required if r.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":                self.step_id,
            "order":                  self.order,
            "title":                  self.title,
            "description":            self.description,
            "evidence_required":      [r.to_dict() for r in self.evidence_required],
            "evidence_priority":      self.evidence_priority.value,
            "candidate_capabilities": [c.value for c in self.candidate_capabilities],
            "preconditions":          [p.to_dict() for p in self.preconditions],
            "outputs":                [o.to_dict() for o in self.outputs],
            "retry_policy":           self.retry_policy.to_dict(),
            "failure_strategy":       self.failure_strategy.value,
            "parallelizable":         self.parallelizable,
            "optional":               self.optional,
            "depends_on":             list(self.depends_on),
        }


# ── InvestigationGraph ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InvestigationGraph:
    """
    Directed Acyclic Graph (DAG) of PlanningSteps.

    Represents the full dependency structure of an InvestigationPlan.
    Supports linear chains, parallel branches, conditional branches,
    and fallback branches.

    Fields:
        nodes:           All steps in the plan (ordered by step.order)
        edges:           All dependency edges between steps
        parallel_groups: Sets of step_ids that can execute concurrently
    """
    nodes:           tuple[PlanningStep, ...]
    edges:           tuple[StepDependency, ...]
    parallel_groups: tuple[frozenset[str], ...]

    # ── Graph queries ──────────────────────────────────────────────────────────

    def _step_by_id(self, step_id: str) -> PlanningStep | None:
        for n in self.nodes:
            if n.step_id == step_id:
                return n
        return None

    def get_root_steps(self) -> list[PlanningStep]:
        """Steps with no incoming SEQUENTIAL edges — can execute immediately."""
        steps_with_prereqs = {
            e.to_step_id for e in self.edges
            if e.dependency_type == DependencyType.SEQUENTIAL
        }
        return [n for n in self.nodes if n.step_id not in steps_with_prereqs]

    def get_next_steps(
        self,
        completed: set[str],
        failed: set[str],
    ) -> list[PlanningStep]:
        """
        Return steps whose prerequisites are all satisfied.

        A step is ready when:
        - It has not already run (not in completed or failed)
        - All SEQUENTIAL prerequisites are in completed
        - All FALLBACK prerequisites are in failed (fallback activates on failure)
        """
        already_run = completed | failed
        result = []
        for node in self.nodes:
            if node.step_id in already_run:
                continue
            prereqs = [e for e in self.edges if e.to_step_id == node.step_id]
            if not prereqs:
                continue  # root step already returned by get_root_steps
            all_satisfied = True
            for prereq in prereqs:
                if prereq.dependency_type == DependencyType.SEQUENTIAL:
                    if prereq.from_step_id not in completed:
                        all_satisfied = False
                        break
                elif prereq.dependency_type == DependencyType.FALLBACK:
                    if prereq.from_step_id not in failed:
                        all_satisfied = False
                        break
            if all_satisfied:
                result.append(node)
        return result

    def is_linear(self) -> bool:
        """True if the graph is a simple chain — each step has at most one successor/predecessor."""
        for node in self.nodes:
            outgoing = [e for e in self.edges if e.from_step_id == node.step_id]
            incoming = [e for e in self.edges if e.to_step_id == node.step_id]
            if len(outgoing) > 1 or len(incoming) > 1:
                return False
        return True

    def topological_order(self) -> list[PlanningStep]:
        """
        Return steps in topological (dependency-first) order.

        Steps with no dependencies come first. If multiple steps have the same
        prerequisites (parallel), they appear together in insertion order.
        """
        visited: set[str] = set()
        result: list[PlanningStep] = []

        def visit(step: PlanningStep) -> None:
            if step.step_id in visited:
                return
            visited.add(step.step_id)
            prereqs = [
                self._step_by_id(e.from_step_id)
                for e in self.edges
                if e.to_step_id == step.step_id
                and e.dependency_type == DependencyType.SEQUENTIAL
            ]
            for p in prereqs:
                if p:
                    visit(p)
            result.append(step)

        for node in self.nodes:
            visit(node)
        return result

    def step_count(self) -> int:
        return len(self.nodes)

    def required_step_count(self) -> int:
        return sum(1 for n in self.nodes if not n.optional)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_count":      len(self.nodes),
            "edge_count":      len(self.edges),
            "parallel_groups": [sorted(g) for g in self.parallel_groups],
            "is_linear":       self.is_linear(),
            "nodes":           [n.to_dict() for n in self.nodes],
            "edges":           [e.to_dict() for e in self.edges],
        }


# ── InvestigationPlan ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InvestigationPlan:
    """
    Sprint 2.39: Rich, evidence-centric investigation plan.

    Produced by InvestigationPlanner (engine.py) and stored in
    InvestigationContext.investigation_plan.

    This replaces the Sprint 2.18 tool-based InvestigationPlan as the primary
    planning artifact. The planner NEVER executes tools — it only produces this plan.
    The Evidence Collector reads the plan and decides which tools to invoke.

    Fields:
        plan_id:                Stable UUID for this plan
        workflow_id:            WorkflowDefinition.workflow_id that drove the plan (may be None)
        topic:                  Investigation topic (e.g., "VKYC_SESSION_FAILURE")
        client_id:              Tenant client_id for this investigation
        case_id:                Case identifier (for tracing and backward compat)
        objective:              Human-readable investigation objective
        investigation_priority: Urgency of this investigation
        estimated_complexity:   Difficulty estimate based on step count and evidence
        expected_evidence:      All EvidenceRequirements across all steps (flattened)
        confidence_threshold:   Minimum confidence needed to resolve without escalation
        completion_conditions:  Conditions that must be true for the plan to complete
        fallback_strategy:      What to do if normal completion fails
        created_at:             ISO timestamp when this plan was created
        steps:                  Ordered PlanningSteps (the linear execution view)
        graph:                  Full DAG with dependencies and parallel groups
    """
    plan_id:                str
    workflow_id:            str | None
    topic:                  str
    client_id:              str
    case_id:                str
    objective:              str
    investigation_priority: InvestigationPriority
    estimated_complexity:   EstimatedComplexity
    expected_evidence:      tuple[EvidenceRequirement, ...]
    confidence_threshold:   float
    completion_conditions:  tuple[CompletionCondition, ...]
    fallback_strategy:      FallbackStrategy
    created_at:             str
    steps:                  tuple[PlanningStep, ...]
    graph:                  InvestigationGraph

    # ── Convenience queries ────────────────────────────────────────────────────

    @property
    def required_evidence(self) -> tuple[EvidenceRequirement, ...]:
        """All EvidenceRequirements where required=True."""
        return tuple(r for r in self.expected_evidence if r.required)

    @property
    def optional_evidence(self) -> tuple[EvidenceRequirement, ...]:
        """All EvidenceRequirements where required=False."""
        return tuple(r for r in self.expected_evidence if not r.required)

    @property
    def required_steps(self) -> tuple[PlanningStep, ...]:
        """Steps that are not optional."""
        return tuple(s for s in self.steps if not s.optional)

    def step_by_id(self, step_id: str) -> PlanningStep | None:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def has_vision_evidence(self) -> bool:
        """True if any evidence requirement needs VISION kind."""
        return any(r.kind == EvidenceKind.VISION for r in self.expected_evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id":                self.plan_id,
            "workflow_id":            self.workflow_id,
            "topic":                  self.topic,
            "client_id":              self.client_id,
            "case_id":                self.case_id,
            "objective":              self.objective,
            "investigation_priority": self.investigation_priority.value,
            "estimated_complexity":   self.estimated_complexity.value,
            "confidence_threshold":   self.confidence_threshold,
            "fallback_strategy":      self.fallback_strategy.value,
            "created_at":             self.created_at,
            "step_count":             len(self.steps),
            "required_step_count":    len(self.required_steps),
            "evidence_count":         len(self.expected_evidence),
            "required_evidence_count": len(self.required_evidence),
            "completion_conditions":  [c.to_dict() for c in self.completion_conditions],
            "steps":                  [s.to_dict() for s in self.steps],
            "graph":                  self.graph.to_dict(),
        }
