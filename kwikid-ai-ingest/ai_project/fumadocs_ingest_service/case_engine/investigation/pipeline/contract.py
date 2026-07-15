"""
case_engine/investigation/pipeline/contract.py

Sprint 2.47: Canonical Investigation Pipeline Stage Contract.

Defines the formal interface every pipeline stage must implement:
  - InvestigationStage  — structural protocol (runtime-checkable)
  - StageResult         — typed output from any stage execution
  - StageStatus         — SUCCESS | FAILURE | SKIPPED | PARTIAL
  - StageSeverity       — FATAL | NON_FATAL

Every stage in the investigation pipeline (validate, planning, collection,
knowledge, root_cause, observation) must satisfy InvestigationStage.

Stage severity semantics:
  FATAL     — a failure should stop the pipeline; result is FAILED or PARTIAL
  NON_FATAL — a failure is logged and the pipeline continues

Dependency direction:
  contract.py → stdlib only
  contract.py does NOT import from any case_engine sub-package.
  All case_engine modules may import from here safely.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from case_engine.investigation.context import InvestigationContext


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Status ─────────────────────────────────────────────────────────────────────

class StageStatus(str, Enum):
    """Execution outcome of a single pipeline stage."""
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    SKIPPED = "SKIPPED"
    PARTIAL = "PARTIAL"


# ── Severity ───────────────────────────────────────────────────────────────────

class StageSeverity(str, Enum):
    """
    Whether a stage failure terminates the pipeline.

    FATAL:     failure stops the pipeline; overall result is FAILED or PARTIAL.
    NON_FATAL: failure is recorded and the pipeline continues; result may be PARTIAL.
    """
    FATAL     = "FATAL"
    NON_FATAL = "NON_FATAL"


# ── StageResult ────────────────────────────────────────────────────────────────

@dataclass
class StageResult:
    """
    Canonical typed result from a single investigation pipeline stage.

    Every InvestigationStage.execute() call returns exactly one StageResult.
    The result is always returned — stages never raise.

    Fields:
      stage_name            — matches pipeline stage name constant
      status                — SUCCESS, FAILURE, SKIPPED, or PARTIAL
      duration_ms           — wall-clock execution time in milliseconds (≥ 0)
      output                — stage-specific output object; None on failure
      error_message         — human-readable description; None on success
      error_code            — machine-readable code; None on success
      result_id             — unique UUID for this stage result
      executed_at           — ISO 8601 UTC timestamp when stage ran
      context_keys_written  — frozenset of InvestigationContext fields mutated
    """
    stage_name:           str
    status:               StageStatus
    duration_ms:          int
    output:               Any | None     = None
    error_message:        str | None     = None
    error_code:           str | None     = None
    result_id:            str            = field(default_factory=_new_id)
    executed_at:          str            = field(default_factory=_now_iso)
    context_keys_written: frozenset[str] = field(default_factory=frozenset)

    # ── Convenience predicates ─────────────────────────────────────────────────

    @property
    def succeeded(self) -> bool:
        """True if the stage produced usable output (SUCCESS or PARTIAL)."""
        return self.status in {StageStatus.SUCCESS, StageStatus.PARTIAL}

    @property
    def failed(self) -> bool:
        """True if the stage failed and produced no output."""
        return self.status == StageStatus.FAILURE

    @property
    def was_skipped(self) -> bool:
        """True if the stage was intentionally bypassed."""
        return self.status == StageStatus.SKIPPED

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_name":           self.stage_name,
            "status":               self.status.value,
            "duration_ms":          self.duration_ms,
            "result_id":            self.result_id,
            "executed_at":          self.executed_at,
            "has_output":           self.output is not None,
            "error_code":           self.error_code,
            "error_message":        (
                self.error_message[:200] if self.error_message else None
            ),
            "context_keys_written": sorted(self.context_keys_written),
        }


# ── InvestigationStage Protocol ────────────────────────────────────────────────

@runtime_checkable
class InvestigationStage(Protocol):
    """
    Structural protocol for every investigation pipeline stage.

    Every concrete stage must:
      1. Expose a unique stage_name (matches a pipeline stage constant).
      2. Expose severity — FATAL or NON_FATAL.
      3. Expose expected_outputs — set of InvestigationContext field names written.
      4. Implement execute(context) → StageResult. Must never raise.

    Design rules for implementors:
      - execute() catches ALL exceptions internally and returns FAILURE status.
      - execute() sets fields in context before returning StageResult.
      - execute() records context_keys_written accurately.
      - execute() always returns within a finite time (no blocking I/O).
    """

    @property
    def stage_name(self) -> str:
        """Unique name for this stage (e.g. "planning", "root_cause")."""
        ...

    @property
    def severity(self) -> StageSeverity:
        """FATAL if failure should stop the pipeline; NON_FATAL to continue."""
        ...

    @property
    def expected_outputs(self) -> frozenset[str]:
        """
        Set of InvestigationContext field names this stage is expected to write.
        Used by ContextIntegrityGuard for post-stage validation.
        """
        ...

    def execute(self, context: "InvestigationContext") -> StageResult:
        """
        Execute this stage on the given context. Must never raise.

        Reads required inputs from context, runs stage logic,
        writes outputs back to context, returns StageResult.
        """
        ...
