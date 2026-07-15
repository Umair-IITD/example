"""
case_engine/investigation/pipeline/guard.py

Sprint 2.47: Context Integrity Guard.

ContextIntegrityGuard verifies that InvestigationContext contains the expected
fields after each pipeline stage completes. This is the runtime enforcement
of the Pipeline Contract's expected_outputs specifications.

Responsibilities:
  - check_after_stage(stage_name, context) → list of missing field names
  - assert_after_stage(stage_name, context) → raises ContextIntegrityError
  - check_full_pipeline(context) → dict of {stage_name: [missing_fields]}
  - is_complete(context) → bool (True if all expected outputs are present)

Design rules:
  - Never modifies context — read-only queries only.
  - Uses getattr with None sentinel — no AttributeError on unknown fields.
  - Field presence test: field value is not None.
  - PII-free: never reads or logs field values, only names.

Dependency direction:
  guard.py → pipeline/stages.py (STAGE_* constants)
  guard.py → stdlib only
  guard.py does NOT import from orchestrator/, workflows/, api/
"""
from __future__ import annotations

from typing import Any

from case_engine.investigation.pipeline.stages import (
    STAGE_COLLECTION,
    STAGE_KNOWLEDGE,
    STAGE_OBSERVATION,
    STAGE_PLANNING,
    STAGE_ROOT_CAUSE,
    STAGE_VALIDATE,
)


# ── Exception ──────────────────────────────────────────────────────────────────

class ContextIntegrityError(Exception):
    """
    Raised by ContextIntegrityGuard.assert_after_stage() when a required
    InvestigationContext field is missing after a pipeline stage completes.
    """


# ── Guard ──────────────────────────────────────────────────────────────────────

class ContextIntegrityGuard:
    """
    Verifies InvestigationContext field integrity at each pipeline stage boundary.

    Usage:
        guard = ContextIntegrityGuard()
        missing = guard.check_after_stage("planning", context)
        # missing == [] → context is valid after planning
        # missing == ["investigation_plan"] → planning forgot to set plan

    The guard checks ONLY the outputs each stage is expected to have written.
    It does not check pre-conditions (inputs) — that is the stage's own
    responsibility (stages return FAILURE status if their dependency is missing).
    """

    # Required fields per stage (after that stage has run successfully)
    _REQUIRED_AFTER: dict[str, frozenset[str]] = {
        STAGE_VALIDATE:    frozenset(),
        STAGE_PLANNING:    frozenset({"investigation_plan"}),
        STAGE_COLLECTION:  frozenset({"evidence_bundle"}),
        STAGE_KNOWLEDGE:   frozenset(),           # knowledge_entries may remain empty []
        STAGE_ROOT_CAUSE:  frozenset({"root_cause_analysis"}),
        STAGE_OBSERVATION: frozenset({"observation"}),
    }

    # Fields that must be present for the full pipeline to be considered complete
    _FULL_PIPELINE_REQUIRED: frozenset[str] = frozenset({
        "investigation_plan",
        "evidence_bundle",
        "root_cause_analysis",
    })

    def check_after_stage(self, stage_name: str, context: Any) -> list[str]:
        """
        Return list of InvestigationContext field names that are missing
        after the given stage. An empty list means the stage completed correctly.

        Returns [] for unknown stage names — guard is lenient about unknown stages.
        """
        required = self._REQUIRED_AFTER.get(stage_name, frozenset())
        return [
            field_name
            for field_name in sorted(required)
            if getattr(context, field_name, None) is None
        ]

    def assert_after_stage(self, stage_name: str, context: Any) -> None:
        """
        Raise ContextIntegrityError if any required field is missing.
        Silent (no exception) if the stage output is complete.
        """
        missing = self.check_after_stage(stage_name, context)
        if missing:
            raise ContextIntegrityError(
                f"After stage {stage_name!r}: missing required context fields: "
                f"{missing!r}"
            )

    def check_full_pipeline(self, context: Any) -> dict[str, list[str]]:
        """
        Check all stage boundaries.
        Returns {stage_name: [missing_fields]} for every stage with missing fields.
        Returns {} if the context is fully populated.
        """
        issues: dict[str, list[str]] = {}
        for stage_name in (
            STAGE_VALIDATE,
            STAGE_PLANNING,
            STAGE_COLLECTION,
            STAGE_KNOWLEDGE,
            STAGE_ROOT_CAUSE,
            STAGE_OBSERVATION,
        ):
            missing = self.check_after_stage(stage_name, context)
            if missing:
                issues[stage_name] = missing
        return issues

    def is_complete(self, context: Any) -> bool:
        """
        Return True if the context contains all fields that a fully successful
        pipeline is expected to have written.

        This is a minimum-bar check: it only tests the non-optional outputs
        (plan, bundle, root_cause_analysis). Observation is optional (non-fatal stage).
        """
        return all(
            getattr(context, field_name, None) is not None
            for field_name in self._FULL_PIPELINE_REQUIRED
        )

    def missing_core_fields(self, context: Any) -> list[str]:
        """Return list of missing core pipeline output fields."""
        return [
            f for f in sorted(self._FULL_PIPELINE_REQUIRED)
            if getattr(context, f, None) is None
        ]
