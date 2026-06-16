"""
case_engine/workflows/playbook_registry.py

Sprint 2.16: PlaybookRegistry — loads, validates, and indexes YAML playbooks.

Design:
- Playbooks are loaded once at process startup (or on first access) from the
  case_engine/workflows/playbooks/ directory.
- Each YAML file describes one WorkflowDefinition.
- The registry indexes definitions by topic and by workflow_id.
- Only one active version per topic is supported at this level; future versioning
  support is reserved via the `version` field and `get_all_for_topic()` method.
- Validation happens at load time, not at execution time. Invalid playbooks
  raise PlaybookValidationError and prevent startup.
- Thread-safe: registry is immutable after build().

Public API:
  registry = PlaybookRegistry.build()           # load from default directory
  registry = PlaybookRegistry.build(path)       # load from custom directory
  defn = registry.get(topic)                    # → WorkflowDefinition | None
  defn = registry.get_by_id(workflow_id)        # → WorkflowDefinition | None
  topics = registry.list_topics()               # → list[str]
  all   = registry.list_all()                   # → list[WorkflowDefinition]
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from case_engine.workflows.models import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowStepType,
)

LOGGER = logging.getLogger(__name__)

_DEFAULT_PLAYBOOK_DIR = Path(__file__).parent / "playbooks"

_REQUIRED_STEP_FIELDS: frozenset[str] = frozenset({"step_id", "type", "name"})
_VALID_STEP_TYPES: frozenset[str] = frozenset(t.value for t in WorkflowStepType)
_TERMINAL_TARGETS: frozenset[str] = frozenset({"RESOLVE", "ESCALATE"})
_VALID_OPERATORS: frozenset[str] = frozenset({"eq", "neq", "exists", "not_exists", "in", "not_in"})


class PlaybookValidationError(ValueError):
    """Raised when a YAML playbook file fails schema validation."""


class PlaybookRegistry:
    """
    Immutable registry of WorkflowDefinitions indexed by topic and workflow_id.

    Build once with PlaybookRegistry.build(); treat as read-only thereafter.
    """

    def __init__(self, definitions: list[WorkflowDefinition]) -> None:
        self._by_topic:  dict[str, WorkflowDefinition] = {}
        self._by_id:     dict[str, WorkflowDefinition] = {}
        for defn in definitions:
            self._by_topic[defn.topic] = defn
            self._by_id[defn.workflow_id] = defn

    # ── Lookup ─────────────────────────────────────────────────────────────────

    def get(self, topic: str) -> WorkflowDefinition | None:
        """Return the active playbook for a topic, or None."""
        return self._by_topic.get(topic)

    def get_by_id(self, workflow_id: str) -> WorkflowDefinition | None:
        """Return a playbook by its workflow_id, or None."""
        return self._by_id.get(workflow_id)

    def list_topics(self) -> list[str]:
        """Return all registered topic strings."""
        return sorted(self._by_topic.keys())

    def list_all(self) -> list[WorkflowDefinition]:
        """Return all registered WorkflowDefinitions."""
        return sorted(self._by_id.values(), key=lambda d: (d.topic, d.version))

    def __len__(self) -> int:
        return len(self._by_id)

    # ── Factory ────────────────────────────────────────────────────────────────

    @classmethod
    def build(cls, playbook_dir: Path | str | None = None) -> "PlaybookRegistry":
        """
        Load all *.yml / *.yaml files from playbook_dir and build the registry.

        Raises PlaybookValidationError if any file fails validation.
        Raises FileNotFoundError if playbook_dir does not exist.
        """
        directory = Path(playbook_dir) if playbook_dir else _DEFAULT_PLAYBOOK_DIR
        if not directory.exists():
            raise FileNotFoundError(f"Playbook directory not found: {directory}")

        definitions: list[WorkflowDefinition] = []
        yaml_files = sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml"))

        if not yaml_files:
            LOGGER.warning("playbook_registry: no YAML files found in %s", directory)

        for path in yaml_files:
            LOGGER.debug("playbook_registry: loading %s", path.name)
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                defn = cls._parse(raw, source=path.name)
                definitions.append(defn)
                LOGGER.info(
                    "playbook_registry: loaded workflow_id=%s topic=%s version=%s steps=%d",
                    defn.workflow_id, defn.topic, defn.version, len(defn.steps),
                )
            except PlaybookValidationError:
                raise
            except Exception as exc:
                raise PlaybookValidationError(
                    f"Failed to parse playbook {path.name}: {exc}"
                ) from exc

        return cls(definitions)

    # ── Parsing ────────────────────────────────────────────────────────────────

    @classmethod
    def _parse(cls, raw: Any, source: str = "<unknown>") -> WorkflowDefinition:
        """Parse a raw YAML dict into a validated WorkflowDefinition."""
        if not isinstance(raw, dict):
            raise PlaybookValidationError(f"{source}: top-level must be a mapping")

        for required in ("workflow_id", "topic", "version", "name", "steps"):
            if not raw.get(required):
                raise PlaybookValidationError(f"{source}: missing required field '{required}'")

        raw_steps: list[Any] = raw.get("steps", [])
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PlaybookValidationError(f"{source}: 'steps' must be a non-empty list")

        # Validate and build step index for navigation pointer resolution
        step_ids: set[str] = {s.get("step_id", "") for s in raw_steps if isinstance(s, dict)}
        steps = tuple(
            cls._parse_step(i, s, step_ids, source)
            for i, s in enumerate(raw_steps)
        )

        # Parse entry_conditions
        raw_entry = raw.get("entry_conditions", [])
        entry_conditions = tuple(
            cls._parse_condition(c, source) for c in (raw_entry or [])
        )

        required_slots = tuple(raw.get("required_slots") or [])

        # Sprint 2.17: Optional investigation metadata (not validated — advisory only)
        investigation_steps = tuple(
            dict(s) for s in (raw.get("investigation_steps") or [])
            if isinstance(s, dict)
        )
        tool_candidates = tuple(
            str(t) for t in (raw.get("tool_candidates") or [])
        )
        resolution_paths = dict(raw.get("resolution_paths") or {})

        return WorkflowDefinition(
            workflow_id=str(raw["workflow_id"]),
            topic=str(raw["topic"]),
            version=str(raw["version"]),
            name=str(raw["name"]),
            description=str(raw.get("description") or ""),
            required_slots=required_slots,
            entry_conditions=entry_conditions,
            steps=steps,
            investigation_steps=investigation_steps,
            tool_candidates=tool_candidates,
            resolution_paths=resolution_paths,
        )

    @classmethod
    def _parse_step(
        cls,
        index: int,
        raw: Any,
        all_step_ids: set[str],
        source: str,
    ) -> WorkflowStep:
        if not isinstance(raw, dict):
            raise PlaybookValidationError(f"{source} step[{index}]: must be a mapping")

        for req in _REQUIRED_STEP_FIELDS:
            if not raw.get(req):
                raise PlaybookValidationError(
                    f"{source} step[{index}]: missing required field '{req}'"
                )

        step_type_str = str(raw["type"]).upper()
        if step_type_str not in _VALID_STEP_TYPES:
            raise PlaybookValidationError(
                f"{source} step[{index}]: unknown type '{raw['type']}'. "
                f"Valid: {sorted(_VALID_STEP_TYPES)}"
            )
        step_type = WorkflowStepType(step_type_str)

        # Validate navigation pointers
        on_success = str(raw.get("on_success") or "RESOLVE")
        on_failure = str(raw.get("on_failure") or "ESCALATE")
        on_approval = raw.get("on_approval")
        on_rejection = raw.get("on_rejection")

        for label, pointer in [("on_success", on_success), ("on_failure", on_failure)]:
            if pointer not in _TERMINAL_TARGETS and pointer not in all_step_ids:
                raise PlaybookValidationError(
                    f"{source} step[{index}] '{raw['step_id']}': "
                    f"'{label}' references unknown step_id '{pointer}'"
                )

        # Validate PROPOSE_ACTION fields
        if step_type == WorkflowStepType.PROPOSE_ACTION:
            if not raw.get("action_type"):
                raise PlaybookValidationError(
                    f"{source} step[{index}]: PROPOSE_ACTION requires 'action_type'"
                )
            if not raw.get("action_namespace"):
                raise PlaybookValidationError(
                    f"{source} step[{index}]: PROPOSE_ACTION requires 'action_namespace'"
                )
            risk = str(raw.get("risk_level") or "SAFE").upper()
            if risk not in ("SAFE", "REVERSIBLE", "IRREVERSIBLE"):
                raise PlaybookValidationError(
                    f"{source} step[{index}]: invalid risk_level '{risk}'"
                )
            if risk == "REVERSIBLE" and not raw.get("rollback_action_type"):
                raise PlaybookValidationError(
                    f"{source} step[{index}]: REVERSIBLE action requires 'rollback_action_type'"
                )

        # Parse conditions for CHECK_CONDITION steps
        raw_conditions = raw.get("conditions") or []
        conditions = tuple(
            cls._parse_condition(c, source, step_index=index)
            for c in raw_conditions
        )

        return WorkflowStep(
            step_index=index,
            step_id=str(raw["step_id"]),
            step_type=step_type,
            name=str(raw["name"]),
            description=str(raw.get("description") or ""),
            action_type=raw.get("action_type"),
            action_namespace=raw.get("action_namespace"),
            action_params_template=dict(raw.get("action_params") or {}),
            risk_level=str(raw.get("risk_level") or "SAFE").upper(),
            rollback_action_type=raw.get("rollback_action_type"),
            conditions=conditions,
            on_success=on_success,
            on_failure=on_failure,
            on_approval=str(on_approval) if on_approval else None,
            on_rejection=str(on_rejection) if on_rejection else None,
        )

    @classmethod
    def _parse_condition(
        cls,
        raw: Any,
        source: str,
        step_index: int | None = None,
    ) -> WorkflowCondition:
        ctx = f"{source}" + (f" step[{step_index}]" if step_index is not None else "")
        if not isinstance(raw, dict):
            raise PlaybookValidationError(f"{ctx}: condition must be a mapping")
        if not raw.get("field"):
            raise PlaybookValidationError(f"{ctx}: condition missing 'field'")
        if not raw.get("operator"):
            raise PlaybookValidationError(f"{ctx}: condition missing 'operator'")
        op = str(raw["operator"]).lower()
        if op not in _VALID_OPERATORS:
            raise PlaybookValidationError(
                f"{ctx}: unknown operator '{op}'. Valid: {sorted(_VALID_OPERATORS)}"
            )
        return WorkflowCondition(
            field=str(raw["field"]),
            operator=op,
            value=raw.get("value"),
            description=str(raw.get("description") or ""),
        )
