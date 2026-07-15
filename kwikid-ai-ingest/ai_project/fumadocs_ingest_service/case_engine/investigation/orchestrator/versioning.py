"""
case_engine/investigation/orchestrator/versioning.py

Sprint 2.46: Semantic versioning for the Investigation Orchestrator.

Independent versioning — does not inherit from any other sprint version.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ORCHESTRATOR_VERSION = "1.0.0"
ORCHESTRATOR_SPRINT  = "2.46"


@dataclass(frozen=True)
class OrchestratorVersion:
    major:  int
    minor:  int
    patch:  int
    sprint: str = ORCHESTRATOR_SPRINT

    @classmethod
    def current(cls) -> "OrchestratorVersion":
        return cls(major=1, minor=0, patch=0)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": str(self),
            "sprint":  self.sprint,
            "major":   self.major,
            "minor":   self.minor,
            "patch":   self.patch,
        }
