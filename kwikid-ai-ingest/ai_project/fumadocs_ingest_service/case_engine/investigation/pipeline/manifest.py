"""
case_engine/investigation/pipeline/manifest.py

Sprint 2.47: Investigation Pipeline Manifest.

The manifest is the authoritative description of the canonical investigation
pipeline: which stages exist, their order, severity, dependencies, and
expected context outputs.

Types:
  StageDescriptor  — immutable description of one pipeline stage
  PipelineManifest — ordered collection of stage descriptors + validation

get_default_manifest() returns the 6-stage canonical pipeline:
  validate → planning → collection → knowledge → root_cause → observation

Dependency direction:
  manifest.py → pipeline/contract.py (StageSeverity)
  manifest.py → stdlib only
  manifest.py does NOT import from case_engine sub-packages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from case_engine.investigation.pipeline.contract import StageSeverity
from case_engine.investigation.pipeline.stages import (
    STAGE_COLLECTION,
    STAGE_KNOWLEDGE,
    STAGE_OBSERVATION,
    STAGE_PLANNING,
    STAGE_ROOT_CAUSE,
    STAGE_VALIDATE,
)


# ── StageDescriptor ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StageDescriptor:
    """
    Immutable description of one pipeline stage.

    Fields:
      name             — matches the stage's stage_name property
      severity         — FATAL or NON_FATAL (effect of failure on pipeline)
      depends_on       — names of stages that must complete before this one
      expected_outputs — InvestigationContext field names this stage should write
      description      — human-readable stage description
    """
    name:             str
    severity:         StageSeverity
    depends_on:       frozenset[str]
    expected_outputs: frozenset[str]
    description:      str = ""

    def is_fatal(self) -> bool:
        return self.severity == StageSeverity.FATAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":             self.name,
            "severity":         self.severity.value,
            "depends_on":       sorted(self.depends_on),
            "expected_outputs": sorted(self.expected_outputs),
            "description":      self.description,
        }


# ── PipelineManifest ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PipelineManifest:
    """
    Ordered, validated description of the investigation pipeline.

    Stages are listed in execution order. The manifest is the authoritative
    record of which stages compose the pipeline and their relationships.

    Fields:
      stages  — tuple of StageDescriptors in execution order
      version — manifest schema version (default "1.0.0")
      sprint  — sprint that introduced this manifest (default "2.47")
    """
    stages:  tuple[StageDescriptor, ...]
    version: str = "1.0.0"
    sprint:  str = "2.47"

    # ── Queries ────────────────────────────────────────────────────────────────

    def stage_names(self) -> tuple[str, ...]:
        """Return stage names in execution order."""
        return tuple(s.name for s in self.stages)

    def get_stage(self, name: str) -> StageDescriptor | None:
        """Return the descriptor for a stage by name, or None if not found."""
        for stage in self.stages:
            if stage.name == name:
                return stage
        return None

    def fatal_stages(self) -> tuple[StageDescriptor, ...]:
        """Return all FATAL stages in execution order."""
        return tuple(s for s in self.stages if s.severity == StageSeverity.FATAL)

    def non_fatal_stages(self) -> tuple[StageDescriptor, ...]:
        """Return all NON_FATAL stages in execution order."""
        return tuple(s for s in self.stages if s.severity == StageSeverity.NON_FATAL)

    # ── Validation ─────────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """
        Validate manifest structural integrity.
        Returns list of issue descriptions — empty means valid.
        """
        issues: list[str] = []
        names = [s.name for s in self.stages]

        # Duplicate names
        seen: set[str] = set()
        for name in names:
            if name in seen:
                issues.append(f"duplicate stage name: {name!r}")
            seen.add(name)

        # Dependency references
        for stage in self.stages:
            for dep in stage.depends_on:
                if dep not in seen:
                    issues.append(
                        f"stage {stage.name!r} depends_on unknown stage {dep!r}"
                    )

        # Dependency ordering: dep must appear before stage
        name_idx = {name: i for i, name in enumerate(names)}
        for stage in self.stages:
            idx = name_idx[stage.name]
            for dep in stage.depends_on:
                dep_idx = name_idx.get(dep)
                if dep_idx is not None and dep_idx >= idx:
                    issues.append(
                        f"stage {stage.name!r} at position {idx} depends on "
                        f"{dep!r} at position {dep_idx} (dependency must come first)"
                    )

        return issues

    def is_valid(self) -> bool:
        return not self.validate()

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "version":     self.version,
            "sprint":      self.sprint,
            "stage_count": len(self.stages),
            "stages":      [s.to_dict() for s in self.stages],
        }


# ── Default manifest factory ───────────────────────────────────────────────────

def get_default_manifest() -> PipelineManifest:
    """
    Return the canonical 6-stage investigation pipeline manifest.

    Stage order:
      1. validate    — FATAL    — no outputs (checks only)
      2. planning    — FATAL    — writes investigation_plan
      3. collection  — FATAL    — writes evidence_bundle (depends: planning)
      4. knowledge   — NON_FATAL— writes knowledge_entries (depends: collection)
      5. root_cause  — FATAL    — writes root_cause_analysis (depends: collection)
      6. observation — NON_FATAL— writes observation fields (depends: root_cause)
    """
    return PipelineManifest(
        stages=(
            StageDescriptor(
                name=STAGE_VALIDATE,
                severity=StageSeverity.FATAL,
                depends_on=frozenset(),
                expected_outputs=frozenset(),
                description=(
                    "Validate InvestigationContext has required fields: "
                    "case_id, topic, tenant_context."
                ),
            ),
            StageDescriptor(
                name=STAGE_PLANNING,
                severity=StageSeverity.FATAL,
                depends_on=frozenset(),
                expected_outputs=frozenset({"investigation_plan"}),
                description=(
                    "Produce InvestigationPlan from context topic and playbook. "
                    "Wraps InvestigationPlanner (Sprint 2.39)."
                ),
            ),
            StageDescriptor(
                name=STAGE_COLLECTION,
                severity=StageSeverity.FATAL,
                depends_on=frozenset({STAGE_PLANNING}),
                expected_outputs=frozenset({"evidence_bundle"}),
                description=(
                    "Collect evidence items per InvestigationPlan. "
                    "Wraps EvidenceCollector (Sprint 2.42)."
                ),
            ),
            StageDescriptor(
                name=STAGE_KNOWLEDGE,
                severity=StageSeverity.NON_FATAL,
                depends_on=frozenset({STAGE_COLLECTION}),
                expected_outputs=frozenset({"knowledge_entries"}),
                description=(
                    "Enrich context with knowledge entries from knowledge provider. "
                    "No-op if no provider is configured."
                ),
            ),
            StageDescriptor(
                name=STAGE_ROOT_CAUSE,
                severity=StageSeverity.FATAL,
                depends_on=frozenset({STAGE_COLLECTION}),
                expected_outputs=frozenset({"root_cause_analysis"}),
                description=(
                    "Analyze EvidenceBundle to determine root cause. "
                    "Wraps RootCauseEngine (Sprint 2.43)."
                ),
            ),
            StageDescriptor(
                name=STAGE_OBSERVATION,
                severity=StageSeverity.NON_FATAL,
                depends_on=frozenset({STAGE_ROOT_CAUSE}),
                expected_outputs=frozenset({
                    "observation",
                    "observation_status",
                    "observation_version",
                    "observation_timestamp",
                }),
                description=(
                    "Format RootCauseAnalysis into a structured Observation. "
                    "Wraps ObservationGenerator (Sprint 2.44)."
                ),
            ),
        ),
    )
