"""
case_engine/knowledge/sop/serialization.py

Sprint 2.41: SOPSerializer — JSON-compatible roundtrip for SOPDocument.

Converts a SOPDocument to a plain dict (JSON-compatible) and back.
All fields are preserved including the new Sprint 2.41 extensions.

to_dict() output is guaranteed to be json.dumps()-compatible:
  - No custom types, no datetime objects, no Enum objects
  - All values are str, int, float, bool, list, dict, or None

from_dict() re-constructs the full SOPDocument from to_dict() output.
"""
from __future__ import annotations

import json
from typing import Any

from case_engine.knowledge.sop.models import (
    SOPActionType,
    SOPCondition,
    SOPDecision,
    SOPDocument,
    SOPExecutionHints,
    SOPInstruction,
    SOPOutcome,
    SOPParameter,
    SOPProcedure,
    SOPReference,
    SOPStatus,
    SOPStep,
    SOPStepKind,
    SOPTriggerCondition,
    SOPTriggerOperator,
    SOPVersion,
)


class SOPSerializer:
    """
    Converts SOPDocument ↔ dict.

    All conversion methods are static — no state is held.
    """

    # ── SOPDocument ────────────────────────────────────────────────────────────

    @staticmethod
    def to_dict(sop: SOPDocument) -> dict[str, Any]:
        """Return a JSON-compatible dict representation of sop."""
        return sop.to_dict()

    @staticmethod
    def to_json(sop: SOPDocument) -> str:
        """Return a JSON string representation of sop."""
        return json.dumps(SOPSerializer.to_dict(sop), default=str)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> SOPDocument:
        """
        Reconstruct a SOPDocument from a dict produced by to_dict().

        Raises KeyError or ValueError if required fields are missing or malformed.
        """
        trigger_conditions = tuple(
            SOPSerializer._tc_from_dict(tc) for tc in data.get("trigger_conditions", [])
        )
        steps = tuple(
            SOPSerializer._step_from_dict(s) for s in data.get("steps", [])
        )
        current_version = (
            SOPSerializer._ver_from_dict(data["current_version"])
            if data.get("current_version")
            else None
        )
        procedures = tuple(
            SOPSerializer._proc_from_dict(p) for p in data.get("procedures", [])
        )
        references = tuple(
            SOPSerializer._ref_from_dict(r) for r in data.get("references", [])
        )
        parameters = tuple(
            SOPSerializer._param_from_dict(p) for p in data.get("parameters", [])
        )

        return SOPDocument(
            sop_id=data["sop_id"],
            title=data["title"],
            topic=data["topic"],
            status=SOPStatus(data["status"]),
            version=data["version"],
            trigger_conditions=trigger_conditions,
            steps=steps,
            escalation_threshold=float(data.get("escalation_threshold", 0.4)),
            applicable_to=tuple(data.get("applicable_to", [])),
            tags=tuple(data.get("tags", [])),
            current_version=current_version,
            description=data.get("description", ""),
            procedures=procedures,
            references=references,
            parameters=parameters,
            estimated_resolution_minutes=int(data.get("estimated_resolution_minutes", 30)),
            requires_approval=bool(data.get("requires_approval", False)),
            enabled=bool(data.get("enabled", True)),
            client_scope=tuple(data.get("client_scope", [])),
        )

    @staticmethod
    def to_summary(sop: SOPDocument) -> dict[str, Any]:
        """Return a lightweight summary dict (no steps/procedures detail)."""
        return {
            "sop_id":          sop.sop_id,
            "title":           sop.title,
            "topic":           sop.topic,
            "status":          sop.status.value,
            "version":         sop.version,
            "step_count":      len(sop.steps),
            "is_global":       sop.is_global(),
            "requires_approval": sop.requires_approval,
            "enabled":         sop.enabled,
            "tags":            list(sop.tags),
        }

    # ── SOPStep ────────────────────────────────────────────────────────────────

    @staticmethod
    def _step_from_dict(data: dict[str, Any]) -> SOPStep:
        hints_raw = data.get("execution_hints")
        hints = SOPSerializer._hints_from_dict(hints_raw) if hints_raw else None

        return SOPStep(
            step_number=int(data["step_number"]),
            step_id=data["step_id"],
            action_type=SOPActionType(data["action_type"]),
            title=data["title"],
            instruction=data["instruction"],
            expected_outcome=data.get("expected_outcome", ""),
            on_failure=data.get("on_failure"),
            kind=SOPStepKind(data.get("kind", SOPStepKind.EXECUTE.value)),
            required_inputs=tuple(data.get("required_inputs", [])),
            expected_outputs=tuple(data.get("expected_outputs", [])),
            dependencies=tuple(data.get("dependencies", [])),
            execution_hints=hints,
            parameters=tuple(
                SOPSerializer._param_from_dict(p) for p in data.get("parameters", [])
            ),
            outcomes=tuple(
                SOPSerializer._outcome_from_dict(o) for o in data.get("outcomes", [])
            ),
            references=tuple(
                SOPSerializer._ref_from_dict(r) for r in data.get("references", [])
            ),
            critical=bool(data.get("critical", False)),
            optional=bool(data.get("optional", False)),
            timeout_seconds=int(data.get("timeout_seconds", 300)),
            parallel_group=data.get("parallel_group"),
        )

    # ── SOPTriggerCondition ────────────────────────────────────────────────────

    @staticmethod
    def _tc_from_dict(data: dict[str, Any]) -> SOPTriggerCondition:
        return SOPTriggerCondition(
            field=data["field"],
            operator=SOPTriggerOperator(data["operator"]),
            value=data.get("value"),
            description=data.get("description", ""),
        )

    # ── SOPVersion ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ver_from_dict(data: dict[str, Any]) -> SOPVersion:
        return SOPVersion(
            version=data["version"],
            created_at=data.get("created_at", ""),
            created_by=data.get("created_by", "system"),
            change_note=data.get("change_note", ""),
        )

    # ── SOPParameter ──────────────────────────────────────────────────────────

    @staticmethod
    def _param_from_dict(data: dict[str, Any]) -> SOPParameter:
        return SOPParameter(
            name=data["name"],
            param_type=data.get("param_type", "string"),
            required=bool(data.get("required", True)),
            description=data.get("description", ""),
            default_value=data.get("default_value"),
        )

    # ── SOPExecutionHints ─────────────────────────────────────────────────────

    @staticmethod
    def _hints_from_dict(data: dict[str, Any]) -> SOPExecutionHints:
        return SOPExecutionHints(
            estimated_seconds=int(data.get("estimated_seconds", 60)),
            can_be_automated=bool(data.get("can_be_automated", False)),
            requires_system_access=bool(data.get("requires_system_access", True)),
            requires_approval=bool(data.get("requires_approval", False)),
            tool_hint=data.get("tool_hint", ""),
            system_reference=data.get("system_reference", ""),
        )

    # ── SOPOutcome ────────────────────────────────────────────────────────────

    @staticmethod
    def _outcome_from_dict(data: dict[str, Any]) -> SOPOutcome:
        return SOPOutcome(
            outcome_id=data["outcome_id"],
            description=data["description"],
            next_step_id=data.get("next_step_id"),
            is_success=bool(data.get("is_success", True)),
            is_terminal=bool(data.get("is_terminal", False)),
        )

    # ── SOPReference ──────────────────────────────────────────────────────────

    @staticmethod
    def _ref_from_dict(data: dict[str, Any]) -> SOPReference:
        return SOPReference(
            ref_id=data["ref_id"],
            title=data["title"],
            ref_type=data.get("ref_type", "doc"),
            url=data.get("url", ""),
        )

    # ── SOPProcedure ──────────────────────────────────────────────────────────

    @staticmethod
    def _proc_from_dict(data: dict[str, Any]) -> SOPProcedure:
        steps = tuple(
            SOPSerializer._step_from_dict(s) for s in data.get("steps", [])
        )
        entry_condition = None
        if data.get("entry_condition"):
            ec = data["entry_condition"]
            from case_engine.knowledge.sop.models import SOPCondition
            entry_condition = SOPCondition(
                field=ec["field"],
                operator=SOPTriggerOperator(ec["operator"]),
                value=ec.get("value"),
                description=ec.get("description", ""),
            )
        return SOPProcedure(
            procedure_id=data["procedure_id"],
            name=data["name"],
            steps=steps,
            entry_condition=entry_condition,
            description=data.get("description", ""),
        )
