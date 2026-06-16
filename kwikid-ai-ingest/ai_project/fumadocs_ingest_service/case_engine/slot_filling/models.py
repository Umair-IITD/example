"""
case_engine/slot_filling/models.py

Core slot-filling data types.

SlotDefinition — static descriptor (what the slot is, how to validate it).
SlotValue      — runtime state (current value, status, attempt count).
ClarificationQuestion — the structured question returned to the caller.

No LLM. No async. No side effects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SlotStatus(str, Enum):
    EMPTY   = "EMPTY"    # never answered
    PENDING = "PENDING"  # question asked; waiting for user response
    FILLED  = "FILLED"   # validated value accepted
    INVALID = "INVALID"  # answer provided but failed validation


@dataclass(frozen=True)
class SlotDefinition:
    """
    Static descriptor for a single slot.

    Instances are defined once in topic_registry.py and never mutated.
    """
    name:                 str
    description:          str
    clarification_prompt: str
    required:             bool = True
    valid_values:         frozenset[str] | None = None    # enum-type: any of these (case-insensitive)
    validation_pattern:   re.Pattern | None = None        # regex fullmatch on stripped value
    max_attempts:         int = 2                         # after N INVALID responses → escalate

    def is_valid_value(self, value: str) -> bool:
        """Return True if value passes this slot's validation rules."""
        stripped = value.strip() if value else ""
        if not stripped:
            return False
        if self.valid_values is not None:
            return stripped.upper() in {v.upper() for v in self.valid_values}
        if self.validation_pattern is not None:
            return bool(self.validation_pattern.fullmatch(stripped))
        return True  # free-form: any non-empty string is valid


@dataclass
class SlotValue:
    """Runtime state of a single slot for one case."""
    slot_name:     str
    status:        SlotStatus = SlotStatus.EMPTY
    value:         str | None = None
    attempt_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status":        self.status.value,
            "value":         self.value,
            "attempt_count": self.attempt_count,
        }

    @classmethod
    def from_dict(cls, slot_name: str, d: dict[str, Any]) -> "SlotValue":
        return cls(
            slot_name=slot_name,
            status=SlotStatus(d.get("status", SlotStatus.EMPTY.value)),
            value=d.get("value"),
            attempt_count=int(d.get("attempt_count", 0)),
        )


@dataclass(frozen=True)
class ClarificationQuestion:
    """Structured question to present to the caller for the next required slot."""
    slot_name:    str
    prompt_text:  str
    is_required:  bool
    valid_values: tuple[str, ...] | None = None  # shown as options for enum slots

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "slot_name":   self.slot_name,
            "prompt_text": self.prompt_text,
            "is_required": self.is_required,
        }
        if self.valid_values is not None:
            d["valid_values"] = list(self.valid_values)
        return d
