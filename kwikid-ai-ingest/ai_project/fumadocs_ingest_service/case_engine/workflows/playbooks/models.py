"""
case_engine/workflows/playbooks/models.py

Sprint 2.40: Workflow Playbook System — domain models.

WorkflowPlaybook is an investigation specification consumed by the
InvestigationPlanner (Sprint 2.39). It defines WHAT evidence to collect,
in what order, and under what conditions — never HOW tools execute.

Key distinction from existing models:
  WorkflowDefinition (Sprint 2.16) → drives WorkflowEngine (orchestration)
  WorkflowPlaybook   (Sprint 2.40) → drives InvestigationPlanner (evidence collection)

The two models are complementary and at different abstraction levels.

Dependency direction:
  models.py → investigation/evidence/models.py (EvidencePriority)
  models.py → investigation/planner/models.py (EvidenceRequirement, RetryPolicy)
  models.py → tools/tool_models.py (ToolCapability)
  models.py → workflows/playbooks/versioning.py (WorkflowVersion)
  models.py → stdlib only
  models.py does NOT import from investigation/context.py, workflows/models.py,
  or knowledge/ — it is a near-leaf node.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from case_engine.investigation.evidence.models import EvidencePriority
from case_engine.investigation.planner.models import EvidenceRequirement, RetryPolicy
from case_engine.tools.tool_models import ToolCapability
from case_engine.workflows.playbooks.versioning import WorkflowVersion


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Enumerations ───────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    """Risk level of the investigation actions defined in a playbook."""
    SAFE       = "SAFE"        # No state changes; read-only evidence collection
    REVERSIBLE = "REVERSIBLE"  # State changes but can be undone (e.g., session reset)
    HIGH       = "HIGH"        # Significant impact; requires approval
    CRITICAL   = "CRITICAL"    # Financial or irreversible impact; requires senior approval


class PlaybookStatus(str, Enum):
    """Lifecycle status of a WorkflowPlaybook."""
    DRAFT      = "DRAFT"       # Not yet activated; not returned by resolver
    ACTIVE     = "ACTIVE"      # Production-ready; returned by resolver
    DEPRECATED = "DEPRECATED"  # Replaced; can be retrieved by ID but not resolved
    ARCHIVED   = "ARCHIVED"    # Removed from active registry; read-only


class PlaybookStepKind(str, Enum):
    """Category of investigation activity a PlaybookStep performs."""
    COLLECT_EVIDENCE = "COLLECT_EVIDENCE"  # Gather raw evidence (session, log, API)
    ANALYZE          = "ANALYZE"           # Analyse existing evidence (vision, correlation)
    VALIDATE         = "VALIDATE"          # Validate that collected evidence meets criteria
    CORRELATE        = "CORRELATE"         # Cross-reference two or more evidence items
    ASSESS           = "ASSESS"            # Assess risk or eligibility for action


# ── Condition types ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlaybookEntryCondition:
    """
    Condition checked BEFORE a WorkflowPlaybook can start.

    If any required entry condition fails, the playbook is skipped or the
    investigation escalates immediately.

    Fields:
        condition_id: Stable identifier
        description:  Human-readable explanation
        slot_key:     Name of the slot or context key to check
        operator:     Check type (exists, not_exists, eq, neq)
        value:        Expected value (for eq/neq checks)
        required:     True = playbook cannot run if condition fails
    """
    condition_id: str
    description:  str
    slot_key:     str
    operator:     str          # "exists" | "not_exists" | "eq" | "neq"
    value:        str | None = None
    required:     bool = True

    def evaluate(self, slots: dict[str, Any]) -> bool:
        raw = slots.get(self.slot_key)
        if self.operator == "exists":
            return raw is not None
        if self.operator == "not_exists":
            return raw is None
        if raw is None:
            return False
        if self.operator == "eq":
            return str(raw) == str(self.value)
        if self.operator == "neq":
            return str(raw) != str(self.value)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "description":  self.description,
            "slot_key":     self.slot_key,
            "operator":     self.operator,
            "value":        self.value,
            "required":     self.required,
        }


@dataclass(frozen=True)
class PlaybookExitCondition:
    """
    Condition checked AFTER a WorkflowPlaybook completes.

    The Evidence Collector checks these to determine whether the investigation
    succeeded. If a required exit condition fails, the plan is marked incomplete
    and the fallback strategy is activated.

    Fields:
        condition_id:     Stable identifier
        description:      Human-readable completion criterion
        evidence_key:     Key in the evidence bundle to check
        required:         True = plan fails if this evidence is absent
    """
    condition_id:  str
    description:   str
    evidence_key:  str
    required:      bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "description":  self.description,
            "evidence_key": self.evidence_key,
            "required":     self.required,
        }


@dataclass(frozen=True)
class ValidationRule:
    """
    A rule that validates collected evidence from a PlaybookStep.

    Consumed by the Evidence Collector after each step to confirm
    the collected evidence meets quality criteria.

    Fields:
        rule_id:     Stable identifier
        description: What this rule checks
        field:       Field name in the collected evidence payload
        operator:    Validation operator (exists, not_exists, not_empty, min_length)
        value:       Expected value or threshold (operator-dependent)
    """
    rule_id:     str
    description: str
    field:       str
    operator:    str        # "exists" | "not_empty" | "min_length" | "not_null"
    value:       Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id":     self.rule_id,
            "description": self.description,
            "field":       self.field,
            "operator":    self.operator,
            "value":       self.value,
        }


# ── PlaybookStep ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlaybookStep:
    """
    One investigation evidence-collection unit in a WorkflowPlaybook.

    A PlaybookStep is a TEMPLATE (immutable spec). The InvestigationPlanner
    converts it into a PlanningStep (case-specific instance) when building
    the InvestigationPlan.

    PlaybookStep never names specific tools. Only EvidenceKind + ToolCapability hints.

    Fields:
        step_id:              Stable identifier (unique within playbook)
        name:                 Short human-readable name
        description:          What this step investigates and why
        kind:                 Category of investigation activity
        evidence_required:    Evidence this step must collect (from Sprint 2.39)
        depends_on:           step_ids that must complete before this step
        parallel_group:       Non-None = this step can run concurrently with others in same group
        fallback:             step_id to run if this step fails (override for FailureStrategy)
        retry_policy:         How to handle transient failures (from Sprint 2.39)
        required_capability:  ToolCapability that can satisfy this step
        timeout_seconds:      Maximum execution time allowed
        optional:             True = plan can complete without this step
        critical:             True = failure causes immediate escalation
        estimated_duration_seconds: Advisory duration for scheduling
        validation_rules:     Rules to validate collected evidence
    """
    step_id:                    str
    name:                       str
    description:                str
    kind:                       PlaybookStepKind
    evidence_required:          tuple[EvidenceRequirement, ...]
    depends_on:                 tuple[str, ...]
    parallel_group:             str | None
    fallback:                   str | None
    retry_policy:               RetryPolicy
    required_capability:        ToolCapability | None
    timeout_seconds:            float
    optional:                   bool
    critical:                   bool
    estimated_duration_seconds: float
    validation_rules:           tuple[ValidationRule, ...]

    @property
    def is_root(self) -> bool:
        return len(self.depends_on) == 0

    @property
    def is_parallelizable(self) -> bool:
        return self.parallel_group is not None

    def required_evidence_count(self) -> int:
        return sum(1 for r in self.evidence_required if r.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":                    self.step_id,
            "name":                       self.name,
            "description":                self.description,
            "kind":                       self.kind.value,
            "evidence_required":          [r.to_dict() for r in self.evidence_required],
            "depends_on":                 list(self.depends_on),
            "parallel_group":             self.parallel_group,
            "fallback":                   self.fallback,
            "retry_policy":               self.retry_policy.to_dict(),
            "required_capability":        self.required_capability.value if self.required_capability else None,
            "timeout_seconds":            self.timeout_seconds,
            "optional":                   self.optional,
            "critical":                   self.critical,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "validation_rules":           [v.to_dict() for v in self.validation_rules],
        }


# ── PlaybookGraph ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlaybookGraph:
    """
    Directed Acyclic Graph (DAG) of PlaybookSteps.

    Represents the full dependency structure of a WorkflowPlaybook.
    Validated at construction time: no cycles, no missing deps, no duplicate IDs.

    Unlike InvestigationGraph (Sprint 2.39, which is case-specific output),
    PlaybookGraph is a TEMPLATE — immutable, shared across all case instances.

    Fields:
        steps:           All PlaybookSteps in definition order
        parallel_groups: Groups of step IDs that can execute concurrently
    """
    steps:           tuple[PlaybookStep, ...]
    parallel_groups: tuple[frozenset[str], ...]

    def step_by_id(self, step_id: str) -> PlaybookStep | None:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def get_root_steps(self) -> list[PlaybookStep]:
        """Steps with no depends_on — can execute immediately."""
        return [s for s in self.steps if s.is_root]

    def topological_order(self) -> list[PlaybookStep]:
        """
        Return steps in dependency-first order using Kahn's algorithm.

        Raises ValueError if a cycle is detected (should not happen after
        validation, but defensive).
        """
        step_map = {s.step_id: s for s in self.steps}
        in_degree: dict[str, int] = {s.step_id: 0 for s in self.steps}
        adjacency: dict[str, list[str]] = {s.step_id: [] for s in self.steps}

        for step in self.steps:
            for dep_id in step.depends_on:
                if dep_id in adjacency:
                    adjacency[dep_id].append(step.step_id)
                    in_degree[step.step_id] += 1

        queue: list[str] = [sid for sid, deg in in_degree.items() if deg == 0]
        result: list[PlaybookStep] = []

        while queue:
            sid = queue.pop(0)
            result.append(step_map[sid])
            for neighbour in adjacency[sid]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if len(result) != len(self.steps):
            raise ValueError("PlaybookGraph cycle detected during topological sort")
        return result

    def has_cycle(self) -> bool:
        """Return True if the graph contains a dependency cycle."""
        try:
            self.topological_order()
            return False
        except ValueError:
            return True

    def is_linear(self) -> bool:
        """True if every step has at most one predecessor and one successor."""
        for step in self.steps:
            outgoing = [s for s in self.steps if step.step_id in s.depends_on]
            if len(outgoing) > 1 or len(step.depends_on) > 1:
                return False
        return True

    def step_count(self) -> int:
        return len(self.steps)

    def required_step_count(self) -> int:
        return sum(1 for s in self.steps if not s.optional)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_count":      len(self.steps),
            "parallel_groups": [sorted(g) for g in self.parallel_groups],
            "is_linear":       self.is_linear(),
            "steps":           [s.to_dict() for s in self.steps],
        }


# ── Repository statistics ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class RepositoryStats:
    """Summary statistics for a WorkflowPlaybookRepository."""
    total_playbooks:      int
    active_playbooks:     int
    deprecated_playbooks: int
    draft_playbooks:      int
    archived_playbooks:   int
    topics_covered:       tuple[str, ...]
    client_scoped:        int  # playbooks with non-empty client_scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_playbooks":      self.total_playbooks,
            "active_playbooks":     self.active_playbooks,
            "deprecated_playbooks": self.deprecated_playbooks,
            "draft_playbooks":      self.draft_playbooks,
            "archived_playbooks":   self.archived_playbooks,
            "topics_covered":       list(self.topics_covered),
            "client_scoped":        self.client_scoped,
        }


# ── WorkflowPlaybook ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkflowPlaybook:
    """
    Sprint 2.40: Investigation specification consumed by the InvestigationPlanner.

    A WorkflowPlaybook defines WHAT evidence to collect for a given investigation
    topic. It is data-driven (immutable template) and replaces the hardcoded
    PlanningRule implementations from Sprint 2.39 as the primary input to the planner.

    The planner reads WorkflowPlaybook and converts it into an InvestigationPlan
    (Sprint 2.39) for one specific case. The PlaybookGraph is the template;
    the InvestigationGraph is the case instance.

    Relationship to WorkflowDefinition (Sprint 2.16):
      WorkflowDefinition → drives WorkflowEngine (CLARIFY → INVESTIGATE → REASON → EXECUTE)
      WorkflowPlaybook   → drives InvestigationPlanner (WHAT evidence to collect)
    Both are read by the pipeline; they are complementary, not redundant.

    Fields:
        playbook_id:           Unique identifier (e.g., "vkyc_investigation_v1")
        name:                  Human-readable name
        version:               Semantic version
        topic:                 Uppercase topic key (e.g., "VKYC_SESSION_FAILURE")
        description:           Full description of playbook purpose
        required_slots:        Slot names that must be filled before investigation
        optional_slots:        Slot names that enhance but don't block investigation
        investigation_graph:   DAG of PlaybookSteps (the evidence collection plan)
        entry_conditions:      Conditions checked before investigation starts
        exit_conditions:       Conditions checked after investigation completes
        required_evidence:     All EvidenceRequirements across all steps (flattened)
        priority:              Urgency of this investigation
        risk_level:            Risk level of actions this investigation supports
        estimated_duration_seconds: Advisory total duration
        requires_approval:     True if actions from this investigation need human approval
        client_scope:          Empty = all clients; non-empty = only named clients
        enabled:               False = never returned by resolver
        metadata:              Arbitrary key-value pairs for extension
        tags:                  Searchable labels
        created_at:            ISO timestamp
        updated_at:            ISO timestamp of last update
        status:                Lifecycle status
    """
    playbook_id:                str
    name:                       str
    version:                    WorkflowVersion
    topic:                      str
    description:                str
    required_slots:             tuple[str, ...]
    optional_slots:             tuple[str, ...]
    investigation_graph:        PlaybookGraph
    entry_conditions:           tuple[PlaybookEntryCondition, ...]
    exit_conditions:            tuple[PlaybookExitCondition, ...]
    required_evidence:          tuple[EvidenceRequirement, ...]
    priority:                   EvidencePriority
    risk_level:                 RiskLevel
    estimated_duration_seconds: float
    requires_approval:          bool
    client_scope:               tuple[str, ...]
    enabled:                    bool
    metadata:                   dict[str, Any]
    tags:                       tuple[str, ...]
    created_at:                 str
    updated_at:                 str
    status:                     PlaybookStatus

    # ── Convenience queries ────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.status == PlaybookStatus.ACTIVE and self.enabled

    @property
    def is_global(self) -> bool:
        """True if this playbook applies to all clients (no client restriction)."""
        return len(self.client_scope) == 0

    def applies_to_client(self, client_id: str) -> bool:
        """True if this playbook can be used for the given client."""
        return self.is_global or client_id in self.client_scope

    def version_str(self) -> str:
        return str(self.version)

    def step_count(self) -> int:
        return self.investigation_graph.step_count()

    def has_vision_evidence(self) -> bool:
        from case_engine.investigation.planner.models import EvidenceKind
        return any(r.kind == EvidenceKind.VISION for r in self.required_evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "playbook_id":                self.playbook_id,
            "name":                       self.name,
            "version":                    self.version.to_dict(),
            "topic":                      self.topic,
            "description":                self.description,
            "required_slots":             list(self.required_slots),
            "optional_slots":             list(self.optional_slots),
            "investigation_graph":        self.investigation_graph.to_dict(),
            "entry_conditions":           [c.to_dict() for c in self.entry_conditions],
            "exit_conditions":            [c.to_dict() for c in self.exit_conditions],
            "required_evidence":          [r.to_dict() for r in self.required_evidence],
            "priority":                   self.priority.value,
            "risk_level":                 self.risk_level.value,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "requires_approval":          self.requires_approval,
            "client_scope":               list(self.client_scope),
            "enabled":                    self.enabled,
            "metadata":                   dict(self.metadata),
            "tags":                       list(self.tags),
            "created_at":                 self.created_at,
            "updated_at":                 self.updated_at,
            "status":                     self.status.value,
            "step_count":                 self.step_count(),
            "is_active":                  self.is_active,
            "is_global":                  self.is_global,
        }
