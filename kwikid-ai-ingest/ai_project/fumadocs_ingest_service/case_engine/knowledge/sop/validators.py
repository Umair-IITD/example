"""
case_engine/knowledge/sop/validators.py

Sprint 2.41: SOPValidator — validates SOPDocument objects before registration.

Validation rules:
  V01: SOPDocument must have at least one step.
  V02: All step_ids within a SOP must be unique.
  V03: No step may declare itself as a dependency (self-reference).
  V04: All step_ids referenced in dependencies must exist in the SOP.
  V05: Step dependencies must not form a directed cycle (Kahn's algorithm).
  V06: sop_id must be non-empty.
  V07: topic must be non-empty.
  V08: version must be non-empty.
  V09: Critical steps must have at least one expected_output or required_input.
  V10: ACTIVE + enabled=False conflict is invalid.
  V11: Empty-string entries in applicable_to or client_scope are invalid.
  V12: escalation_threshold must be between 0.0 and 1.0 (inclusive).

SOPValidator.validate() returns a list of error strings (empty = valid).
SOPValidator.validate_or_raise() raises SOPValidationError on first failure.
SOPValidator.is_valid() returns a bool.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any

from case_engine.knowledge.sop.exceptions import SOPGraphCycleError, SOPValidationError
from case_engine.knowledge.sop.models import SOPDocument, SOPStatus

LOGGER = logging.getLogger(__name__)


class SOPValidator:
    """
    Validates SOPDocument objects against a fixed set of rules.

    All methods are stateless and safe to call concurrently.
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    def validate(self, sop: SOPDocument) -> list[str]:
        """
        Run all validation rules against sop.

        Returns:
            List of error strings. Empty list means the SOP is valid.
        """
        errors: list[str] = []
        errors.extend(self._v01_non_empty_steps(sop))
        errors.extend(self._v02_unique_step_ids(sop))
        errors.extend(self._v03_no_self_deps(sop))
        errors.extend(self._v04_no_missing_deps(sop))
        errors.extend(self._v05_no_cycle(sop))
        errors.extend(self._v06_non_empty_sop_id(sop))
        errors.extend(self._v07_non_empty_topic(sop))
        errors.extend(self._v08_non_empty_version(sop))
        errors.extend(self._v09_critical_step_has_io(sop))
        errors.extend(self._v10_active_enabled_conflict(sop))
        errors.extend(self._v11_no_empty_scope_strings(sop))
        errors.extend(self._v12_escalation_threshold_range(sop))
        return errors

    def validate_or_raise(self, sop: SOPDocument) -> None:
        """
        Run all validation rules and raise SOPValidationError if any fail.

        Raises:
            SOPValidationError with .errors populated.
        """
        errors = self.validate(sop)
        if errors:
            raise SOPValidationError(
                f"SOPDocument {sop.sop_id!r} failed validation "
                f"({len(errors)} error(s)): {errors[0]}",
                errors=errors,
            )

    def is_valid(self, sop: SOPDocument) -> bool:
        """Return True if the SOP passes all validation rules."""
        return len(self.validate(sop)) == 0

    def validate_all(self, sops: list[SOPDocument]) -> dict[str, list[str]]:
        """
        Validate multiple SOPs.

        Returns:
            Dict mapping sop_id → list[str] for every SOP that has errors.
            SOPs that pass validation are NOT included in the result.
        """
        result: dict[str, list[str]] = {}
        for sop in sops:
            errors = self.validate(sop)
            if errors:
                result[sop.sop_id] = errors
        return result

    # ── Validation Rules ───────────────────────────────────────────────────────

    @staticmethod
    def _v01_non_empty_steps(sop: SOPDocument) -> list[str]:
        if len(sop.steps) == 0:
            return [f"V01: SOPDocument {sop.sop_id!r} has no steps."]
        return []

    @staticmethod
    def _v02_unique_step_ids(sop: SOPDocument) -> list[str]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for step in sop.steps:
            if step.step_id in seen:
                duplicates.append(step.step_id)
            seen.add(step.step_id)
        if duplicates:
            return [f"V02: Duplicate step IDs in SOP {sop.sop_id!r}: {duplicates}"]
        return []

    @staticmethod
    def _v03_no_self_deps(sop: SOPDocument) -> list[str]:
        errors: list[str] = []
        for step in sop.steps:
            if step.step_id in step.dependencies:
                errors.append(
                    f"V03: Step {step.step_id!r} in SOP {sop.sop_id!r} lists itself as a dependency."
                )
        return errors

    @staticmethod
    def _v04_no_missing_deps(sop: SOPDocument) -> list[str]:
        valid_ids = {s.step_id for s in sop.steps}
        errors: list[str] = []
        for step in sop.steps:
            for dep in step.dependencies:
                if dep not in valid_ids:
                    errors.append(
                        f"V04: Step {step.step_id!r} in SOP {sop.sop_id!r} depends on "
                        f"non-existent step {dep!r}."
                    )
        return errors

    @staticmethod
    def _v05_no_cycle(sop: SOPDocument) -> list[str]:
        """
        Kahn's algorithm: detect directed cycle in step dependencies.

        Returns an error string if a cycle is detected.
        """
        if not sop.steps:
            return []

        adjacency: dict[str, list[str]] = {s.step_id: [] for s in sop.steps}
        in_degree: dict[str, int] = {s.step_id: 0 for s in sop.steps}

        for step in sop.steps:
            for dep in step.dependencies:
                if dep in adjacency:
                    adjacency[dep].append(step.step_id)
                    in_degree[step.step_id] += 1

        queue: deque[str] = deque(sid for sid, deg in in_degree.items() if deg == 0)
        processed = 0
        while queue:
            node = queue.popleft()
            processed += 1
            for neighbour in adjacency[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if processed < len(sop.steps):
            return [f"V05: Step dependencies in SOP {sop.sop_id!r} form a cycle."]
        return []

    @staticmethod
    def _v06_non_empty_sop_id(sop: SOPDocument) -> list[str]:
        if not sop.sop_id or not sop.sop_id.strip():
            return ["V06: sop_id must be non-empty."]
        return []

    @staticmethod
    def _v07_non_empty_topic(sop: SOPDocument) -> list[str]:
        if not sop.topic or not sop.topic.strip():
            return [f"V07: topic must be non-empty in SOP {sop.sop_id!r}."]
        return []

    @staticmethod
    def _v08_non_empty_version(sop: SOPDocument) -> list[str]:
        if not sop.version or not sop.version.strip():
            return [f"V08: version must be non-empty in SOP {sop.sop_id!r}."]
        return []

    @staticmethod
    def _v09_critical_step_has_io(sop: SOPDocument) -> list[str]:
        """Critical steps must declare at least one required_input or expected_output."""
        errors: list[str] = []
        for step in sop.steps:
            if step.critical and not step.required_inputs and not step.expected_outputs:
                errors.append(
                    f"V09: Critical step {step.step_id!r} in SOP {sop.sop_id!r} "
                    f"must declare at least one required_input or expected_output."
                )
        return errors

    @staticmethod
    def _v10_active_enabled_conflict(sop: SOPDocument) -> list[str]:
        """ACTIVE status with enabled=False is a contradictory configuration."""
        if sop.status == SOPStatus.ACTIVE and not sop.enabled:
            return [
                f"V10: SOP {sop.sop_id!r} has status=ACTIVE but enabled=False. "
                f"ACTIVE SOPs must have enabled=True."
            ]
        return []

    @staticmethod
    def _v11_no_empty_scope_strings(sop: SOPDocument) -> list[str]:
        errors: list[str] = []
        for scope_list, field_name in [
            (sop.applicable_to, "applicable_to"),
            (sop.client_scope, "client_scope"),
        ]:
            for entry in scope_list:
                if not entry or not entry.strip():
                    errors.append(
                        f"V11: SOP {sop.sop_id!r} has empty string in {field_name}."
                    )
        return errors

    @staticmethod
    def _v12_escalation_threshold_range(sop: SOPDocument) -> list[str]:
        if not (0.0 <= sop.escalation_threshold <= 1.0):
            return [
                f"V12: SOP {sop.sop_id!r} escalation_threshold={sop.escalation_threshold} "
                f"is outside valid range [0.0, 1.0]."
            ]
        return []
