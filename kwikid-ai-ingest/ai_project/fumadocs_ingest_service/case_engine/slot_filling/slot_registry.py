"""
case_engine/slot_filling/slot_registry.py

SlotRegistry — maps a topic's required and optional slot definitions.

Instances are created in topic_registry.py and shared read-only across all requests.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from case_engine.slot_filling.models import SlotDefinition


@dataclass(frozen=True)
class SlotRegistry:
    """
    All slot definitions for a single topic.

    required: slots that must be FILLED before case can progress.
    optional: slots collected if available but not blocking.
    """
    required: tuple[SlotDefinition, ...]
    optional: tuple[SlotDefinition, ...] = field(default_factory=tuple)

    def all_definitions(self) -> list[SlotDefinition]:
        return list(self.required) + list(self.optional)

    def definition_for(self, slot_name: str) -> SlotDefinition | None:
        for d in self.all_definitions():
            if d.name == slot_name:
                return d
        return None

    def required_names(self) -> tuple[str, ...]:
        return tuple(d.name for d in self.required)

    def optional_names(self) -> tuple[str, ...]:
        return tuple(d.name for d in self.optional)
