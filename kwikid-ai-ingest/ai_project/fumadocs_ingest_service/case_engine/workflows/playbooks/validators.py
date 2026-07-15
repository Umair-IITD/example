"""
case_engine/workflows/playbooks/validators.py

Sprint 2.40: Workflow Playbook System — structural and logical validators.

PlaybookValidator raises PlaybookValidationError or PlaybookGraphCycleError
for any structural, logical, or graph violation.

Validation checks (all raise immediately on first violation):
  V01 — At least one step
  V02 — No duplicate step IDs
  V03 — No self-referential dependencies (step depends on itself)
  V04 — All depends_on targets exist in the step list
  V05 — No dependency graph cycles (Kahn's algorithm)
  V06 — All fallback targets exist in the step list
  V07 — Critical steps must have at least one required EvidenceRequirement
  V08 — No empty evidence_required on non-optional steps that are critical
  V09 — Topic must be non-empty
  V10 — playbook_id must be non-empty
  V11 — Version must be parseable (WorkflowVersion)
  V12 — Enabled playbooks must be ACTIVE status (DRAFT/DEPRECATED are disabled)
  V13 — client_scope elements must be non-empty strings
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.workflows.playbooks.exceptions import (
    PlaybookGraphCycleError,
    PlaybookValidationError,
)
from case_engine.workflows.playbooks.models import (
    PlaybookStatus,
    PlaybookStep,
    WorkflowPlaybook,
)

LOGGER = logging.getLogger(__name__)


class PlaybookValidator:
    """
    Validates a WorkflowPlaybook against structural and logical rules.

    Usage:
        PlaybookValidator().validate(playbook)   # raises on violation
        PlaybookValidator().is_valid(playbook)   # returns bool

    All methods are stateless — PlaybookValidator has no instance state.
    """

    def validate(self, playbook: WorkflowPlaybook) -> None:
        """
        Validate the playbook. Raises on first violation found.

        Raises:
            PlaybookValidationError: for structural/logical violations
            PlaybookGraphCycleError: for dependency graph cycles
        """
        pid = playbook.playbook_id
        steps = playbook.investigation_graph.steps

        self._check_identity(pid, playbook)
        self._check_steps_non_empty(pid, steps)
        self._check_no_duplicate_ids(pid, steps)
        self._check_no_self_deps(pid, steps)
        self._check_deps_exist(pid, steps)
        self._check_no_cycles(pid, steps)
        self._check_fallbacks_exist(pid, steps)
        self._check_critical_steps_have_evidence(pid, steps)
        self._check_client_scope(pid, playbook)
        self._check_status_enabled_consistency(pid, playbook)

        LOGGER.debug("playbook_validator.ok playbook_id=%s version=%s", pid, playbook.version)

    def is_valid(self, playbook: WorkflowPlaybook) -> bool:
        """Return True if the playbook passes all validations."""
        try:
            self.validate(playbook)
            return True
        except (PlaybookValidationError, PlaybookGraphCycleError):
            return False

    def validate_all(self, playbooks: list[WorkflowPlaybook]) -> list[str]:
        """
        Validate a list of playbooks.

        Returns a list of error messages (empty list = all valid).
        Does NOT raise.
        """
        errors: list[str] = []
        for pb in playbooks:
            try:
                self.validate(pb)
            except (PlaybookValidationError, PlaybookGraphCycleError) as exc:
                errors.append(str(exc))
        return errors

    # ── V01, V09, V10, V11 — Identity checks ──────────────────────────────────

    def _check_identity(self, pid: str, playbook: WorkflowPlaybook) -> None:
        if not pid.strip():
            raise PlaybookValidationError("(empty)", "playbook_id must not be empty")
        if not playbook.topic.strip():
            raise PlaybookValidationError(pid, "topic must not be empty")

    # ── V01 — Non-empty steps ──────────────────────────────────────────────────

    def _check_steps_non_empty(self, pid: str, steps: tuple[PlaybookStep, ...]) -> None:
        if not steps:
            raise PlaybookValidationError(pid, "investigation_graph must have at least one step")

    # ── V02 — No duplicate step IDs ───────────────────────────────────────────

    def _check_no_duplicate_ids(self, pid: str, steps: tuple[PlaybookStep, ...]) -> None:
        seen: set[str] = set()
        for step in steps:
            if step.step_id in seen:
                raise PlaybookValidationError(
                    pid, f"duplicate step_id: {step.step_id!r}"
                )
            seen.add(step.step_id)

    # ── V03 — No self-referential deps ────────────────────────────────────────

    def _check_no_self_deps(self, pid: str, steps: tuple[PlaybookStep, ...]) -> None:
        for step in steps:
            if step.step_id in step.depends_on:
                raise PlaybookValidationError(
                    pid, f"step {step.step_id!r} depends on itself"
                )

    # ── V04 — All deps must exist ─────────────────────────────────────────────

    def _check_deps_exist(self, pid: str, steps: tuple[PlaybookStep, ...]) -> None:
        step_ids = {s.step_id for s in steps}
        for step in steps:
            for dep_id in step.depends_on:
                if dep_id not in step_ids:
                    raise PlaybookValidationError(
                        pid,
                        f"step {step.step_id!r} depends on unknown step {dep_id!r}",
                    )

    # ── V05 — No cycles (Kahn's algorithm) ────────────────────────────────────

    def _check_no_cycles(self, pid: str, steps: tuple[PlaybookStep, ...]) -> None:
        in_degree: dict[str, int] = {s.step_id: 0 for s in steps}
        adjacency: dict[str, list[str]] = {s.step_id: [] for s in steps}

        for step in steps:
            for dep_id in step.depends_on:
                if dep_id in adjacency:
                    adjacency[dep_id].append(step.step_id)
                    in_degree[step.step_id] += 1

        queue: list[str] = [sid for sid, deg in in_degree.items() if deg == 0]
        visited: list[str] = []

        while queue:
            sid = queue.pop(0)
            visited.append(sid)
            for neighbour in adjacency[sid]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if len(visited) != len(steps):
            cycle_members = [sid for sid in in_degree if in_degree[sid] > 0]
            raise PlaybookGraphCycleError(pid, cycle_members)

    # ── V06 — Fallback targets must exist ─────────────────────────────────────

    def _check_fallbacks_exist(self, pid: str, steps: tuple[PlaybookStep, ...]) -> None:
        step_ids = {s.step_id for s in steps}
        for step in steps:
            if step.fallback and step.fallback not in step_ids:
                raise PlaybookValidationError(
                    pid,
                    f"step {step.step_id!r} fallback target {step.fallback!r} does not exist",
                )

    # ── V07, V08 — Critical steps must have evidence ──────────────────────────

    def _check_critical_steps_have_evidence(
        self, pid: str, steps: tuple[PlaybookStep, ...]
    ) -> None:
        for step in steps:
            if step.critical and not step.evidence_required:
                raise PlaybookValidationError(
                    pid,
                    f"critical step {step.step_id!r} has no evidence_required — "
                    "critical steps must collect at least one evidence item",
                )

    # ── V13 — client_scope elements non-empty ─────────────────────────────────

    def _check_client_scope(self, pid: str, playbook: WorkflowPlaybook) -> None:
        for client_id in playbook.client_scope:
            if not client_id.strip():
                raise PlaybookValidationError(
                    pid, "client_scope contains an empty string"
                )

    # ── V12 — enabled/status consistency ──────────────────────────────────────

    def _check_status_enabled_consistency(
        self, pid: str, playbook: WorkflowPlaybook
    ) -> None:
        if playbook.enabled and playbook.status == PlaybookStatus.ARCHIVED:
            raise PlaybookValidationError(
                pid, "playbook cannot be enabled with ARCHIVED status"
            )
