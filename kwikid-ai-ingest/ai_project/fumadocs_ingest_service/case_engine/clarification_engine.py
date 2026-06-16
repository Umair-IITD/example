"""
case_engine/clarification_engine.py

ClarificationEngine — deterministic rule-based slot filling.

Given a topic and the current slot state (dict of SlotValues), the engine:
  1. Returns the next clarification question for the first unfilled required slot.
  2. Accepts and validates a slot value (explicit or regex-extracted).
  3. Checks whether all required slots are filled.
  4. Checks whether any slot has exceeded max_attempts (escalation signal).
  5. Attempts deterministic extraction of enum slots from free-form text.

No LLM. No async. No side effects. All methods are pure functions of their inputs.
"""
from __future__ import annotations

import re

from case_engine.models import TopicKey
from case_engine.slot_filling.models import (
    ClarificationQuestion,
    SlotDefinition,
    SlotStatus,
    SlotValue,
)
from case_engine.topic_registry import get_registry


class ClarificationEngine:
    """
    Deterministic clarification engine for slot filling.

    Stateless — all state lives in the slot_values dict passed by the caller.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def next_question(
        self,
        topic: TopicKey,
        slot_values: dict[str, SlotValue],
    ) -> ClarificationQuestion | None:
        """
        Return the clarification question for the first unfilled required slot.

        Returns None if:
        - Topic has no registry (UNKNOWN).
        - All required slots are FILLED.
        """
        registry = get_registry(topic)
        if registry is None:
            return None

        for slot_def in registry.required:
            sv = slot_values.get(slot_def.name)
            if sv is None or sv.status != SlotStatus.FILLED:
                valid = sorted(slot_def.valid_values) if slot_def.valid_values else None
                return ClarificationQuestion(
                    slot_name=slot_def.name,
                    prompt_text=slot_def.clarification_prompt,
                    is_required=True,
                    valid_values=tuple(valid) if valid else None,
                )
        return None

    def accept_slot_value(
        self,
        topic: TopicKey,
        slot_name: str,
        raw_value: str,
        slot_values: dict[str, SlotValue],
    ) -> SlotValue:
        """
        Validate raw_value for slot_name.

        Returns a new SlotValue:
        - FILLED if validation passes.
        - INVALID if validation fails (attempt_count incremented).

        Does not mutate slot_values — the caller must update the dict with the result.
        """
        registry = get_registry(topic)
        slot_def: SlotDefinition | None = registry.definition_for(slot_name) if registry else None

        existing = slot_values.get(slot_name)
        current_attempts = existing.attempt_count if existing else 0

        if slot_def is None or slot_def.is_valid_value(raw_value):
            return SlotValue(
                slot_name=slot_name,
                status=SlotStatus.FILLED,
                value=raw_value.strip() if raw_value else raw_value,
                attempt_count=current_attempts,
            )
        return SlotValue(
            slot_name=slot_name,
            status=SlotStatus.INVALID,
            value=None,
            attempt_count=current_attempts + 1,
        )

    def all_required_filled(
        self,
        topic: TopicKey,
        slot_values: dict[str, SlotValue],
    ) -> bool:
        """Return True when every required slot for this topic is FILLED."""
        registry = get_registry(topic)
        if registry is None:
            return False
        return all(
            slot_values.get(d.name, SlotValue(slot_name=d.name)).status == SlotStatus.FILLED
            for d in registry.required
        )

    def any_max_attempts_exceeded(
        self,
        topic: TopicKey,
        slot_values: dict[str, SlotValue],
    ) -> bool:
        """
        Return True if any required slot has reached or exceeded its max_attempts.

        When True, the case should transition to ESCALATED (caller's responsibility).
        """
        registry = get_registry(topic)
        if registry is None:
            return False
        for slot_def in registry.required:
            sv = slot_values.get(slot_def.name)
            if sv is not None and sv.attempt_count >= slot_def.max_attempts:
                return True
        return False

    def extract_from_text(
        self,
        topic: TopicKey,
        text: str,
        slot_values: dict[str, SlotValue],
    ) -> dict[str, SlotValue]:
        """
        Attempt deterministic extraction from free-form text.

        Only extracts enum-type slots (valid_values is set) using keyword matching.
        Free-form slots (validation_pattern only) are not extracted from text —
        they require explicit user input.

        Returns a new dict with any newly-filled slots added. Does not overwrite
        already-FILLED slots.
        """
        registry = get_registry(topic)
        if registry is None:
            return dict(slot_values)

        updated = dict(slot_values)
        for slot_def in registry.all_definitions():
            if slot_def.name in updated and updated[slot_def.name].status == SlotStatus.FILLED:
                continue
            if slot_def.valid_values:
                for valid in slot_def.valid_values:
                    if re.search(rf"\b{re.escape(valid)}\b", text, re.IGNORECASE):
                        updated[slot_def.name] = self.accept_slot_value(
                            topic, slot_def.name, valid, updated
                        )
                        break
        return updated

    # ── Serialization helpers ─────────────────────────────────────────────────

    @staticmethod
    def slot_values_to_dict(slot_values: dict[str, SlotValue]) -> dict[str, dict]:
        """Serialize slot_values for JSON response or DB storage."""
        return {name: sv.to_dict() for name, sv in slot_values.items()}

    @staticmethod
    def slot_values_from_dict(raw: dict[str, dict]) -> dict[str, SlotValue]:
        """Deserialize slot_values from JSON or DB storage."""
        return {name: SlotValue.from_dict(name, d) for name, d in raw.items()}
