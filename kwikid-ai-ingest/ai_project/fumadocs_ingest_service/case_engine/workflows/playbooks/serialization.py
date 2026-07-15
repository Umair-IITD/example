"""
case_engine/workflows/playbooks/serialization.py

Sprint 2.40: Workflow Playbook System — lossless serialization.

PlaybookSerializer converts WorkflowPlaybook ↔ dict (JSON-compatible).

Design:
  - to_dict() is provided directly on each model via to_dict() methods.
  - PlaybookSerializer handles the top-level round-trip and summary views.
  - No information loss in to_dict() — from_dict() can reconstruct the object.
  - from_dict() is provided for future database/YAML persistence.

Note: Full from_dict() round-trip requires all enum values and nested types to
be present in the dict. Partial dicts are not supported — use to_dict() output.
"""
from __future__ import annotations

from typing import Any

from case_engine.investigation.evidence.models import EvidencePriority
from case_engine.investigation.planner.models import (
    EvidenceKind,
    EvidenceRequirement,
    RetryPolicy,
)
from case_engine.tools.tool_models import ToolCapability
from case_engine.workflows.playbooks.models import (
    PlaybookEntryCondition,
    PlaybookExitCondition,
    PlaybookGraph,
    PlaybookStatus,
    PlaybookStep,
    PlaybookStepKind,
    RiskLevel,
    ValidationRule,
    WorkflowPlaybook,
)
from case_engine.workflows.playbooks.versioning import WorkflowVersion


class PlaybookSerializer:
    """
    Lossless serialization for WorkflowPlaybook.

    Usage:
        serializer = PlaybookSerializer()
        d = serializer.to_dict(playbook)
        playbook2 = serializer.from_dict(d)
        assert playbook == playbook2  # True for all fields
    """

    def to_dict(self, playbook: WorkflowPlaybook) -> dict[str, Any]:
        """Convert WorkflowPlaybook to a JSON-compatible dict. Lossless."""
        return playbook.to_dict()

    def to_summary(self, playbook: WorkflowPlaybook) -> dict[str, Any]:
        """Return a compact summary dict for listings and admin views."""
        return {
            "playbook_id":  playbook.playbook_id,
            "name":         playbook.name,
            "version":      str(playbook.version),
            "topic":        playbook.topic,
            "status":       playbook.status.value,
            "enabled":      playbook.enabled,
            "step_count":   playbook.step_count(),
            "is_global":    playbook.is_global,
            "risk_level":   playbook.risk_level.value,
            "tags":         list(playbook.tags),
            "created_at":   playbook.created_at,
            "updated_at":   playbook.updated_at,
        }

    def from_dict(self, d: dict[str, Any]) -> WorkflowPlaybook:
        """Reconstruct a WorkflowPlaybook from a dict produced by to_dict()."""
        version_d = d["version"]
        version = WorkflowVersion(
            major=version_d["major"],
            minor=version_d["minor"],
            patch=version_d["patch"],
            deprecated=version_d.get("deprecated", False),
            replacement=version_d.get("replacement"),
        )

        steps = tuple(
            self._step_from_dict(sd) for sd in d["investigation_graph"]["steps"]
        )
        parallel_groups = tuple(
            frozenset(g) for g in d["investigation_graph"].get("parallel_groups", [])
        )
        graph = PlaybookGraph(steps=steps, parallel_groups=parallel_groups)

        entry_conditions = tuple(
            PlaybookEntryCondition(
                condition_id=c["condition_id"],
                description=c["description"],
                slot_key=c["slot_key"],
                operator=c["operator"],
                value=c.get("value"),
                required=c.get("required", True),
            )
            for c in d.get("entry_conditions", [])
        )

        exit_conditions = tuple(
            PlaybookExitCondition(
                condition_id=c["condition_id"],
                description=c["description"],
                evidence_key=c["evidence_key"],
                required=c.get("required", True),
            )
            for c in d.get("exit_conditions", [])
        )

        required_evidence = tuple(
            self._evidence_req_from_dict(r) for r in d.get("required_evidence", [])
        )

        return WorkflowPlaybook(
            playbook_id=d["playbook_id"],
            name=d["name"],
            version=version,
            topic=d["topic"],
            description=d["description"],
            required_slots=tuple(d.get("required_slots", [])),
            optional_slots=tuple(d.get("optional_slots", [])),
            investigation_graph=graph,
            entry_conditions=entry_conditions,
            exit_conditions=exit_conditions,
            required_evidence=required_evidence,
            priority=EvidencePriority(d["priority"]),
            risk_level=RiskLevel(d["risk_level"]),
            estimated_duration_seconds=float(d.get("estimated_duration_seconds", 0.0)),
            requires_approval=bool(d.get("requires_approval", False)),
            client_scope=tuple(d.get("client_scope", [])),
            enabled=bool(d.get("enabled", True)),
            metadata=dict(d.get("metadata", {})),
            tags=tuple(d.get("tags", [])),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            status=PlaybookStatus(d["status"]),
        )

    def _step_from_dict(self, d: dict[str, Any]) -> PlaybookStep:
        evidence_required = tuple(
            self._evidence_req_from_dict(r) for r in d.get("evidence_required", [])
        )
        validation_rules = tuple(
            ValidationRule(
                rule_id=v["rule_id"],
                description=v["description"],
                field=v["field"],
                operator=v["operator"],
                value=v.get("value"),
            )
            for v in d.get("validation_rules", [])
        )
        retry_d = d.get("retry_policy", {})
        retry_policy = RetryPolicy(
            max_attempts=retry_d.get("max_attempts", 2),
            backoff_seconds=retry_d.get("backoff_seconds", 0.0),
            retry_on_timeout=retry_d.get("retry_on_timeout", True),
            retry_on_partial=retry_d.get("retry_on_partial", False),
        )
        cap_raw = d.get("required_capability")
        capability = ToolCapability(cap_raw) if cap_raw else None

        return PlaybookStep(
            step_id=d["step_id"],
            name=d["name"],
            description=d["description"],
            kind=PlaybookStepKind(d["kind"]),
            evidence_required=evidence_required,
            depends_on=tuple(d.get("depends_on", [])),
            parallel_group=d.get("parallel_group"),
            fallback=d.get("fallback"),
            retry_policy=retry_policy,
            required_capability=capability,
            timeout_seconds=float(d.get("timeout_seconds", 30.0)),
            optional=bool(d.get("optional", False)),
            critical=bool(d.get("critical", False)),
            estimated_duration_seconds=float(d.get("estimated_duration_seconds", 5.0)),
            validation_rules=validation_rules,
        )

    def _evidence_req_from_dict(self, d: dict[str, Any]) -> EvidenceRequirement:
        cap_raw = d.get("capability_hint")
        return EvidenceRequirement(
            requirement_id=d["requirement_id"],
            kind=EvidenceKind(d["kind"]),
            title=d["title"],
            description=d["description"],
            priority=EvidencePriority(d["priority"]),
            required=bool(d.get("required", True)),
            expected_fields=tuple(d.get("expected_fields", [])),
            validation_hints=tuple(d.get("validation_hints", [])),
            capability_hint=ToolCapability(cap_raw) if cap_raw else None,
        )
