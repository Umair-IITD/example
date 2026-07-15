"""
case_engine/investigation/root_cause/versioning.py

Sprint 2.43: Semantic versioning for the Root Cause Engine.

EngineVersion supports comparison, compatibility checks, and serialization.

Dependency direction:
  versioning.py → stdlib only
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ENGINE_MAJOR = 2
_ENGINE_MINOR = 43
_ENGINE_PATCH = 0

CURRENT_ENGINE_VERSION = f"{_ENGINE_MAJOR}.{_ENGINE_MINOR}.{_ENGINE_PATCH}"
CURRENT_REGISTRY_VERSION = "1.0.0"


@dataclass(frozen=True)
class EngineVersion:
    """
    Semantic version for the Root Cause Engine.

    Comparison semantics:
      major: breaking change (incompatible rule interface change)
      minor: backward-compatible addition (new rules, new fields)
      patch: backward-compatible fix (rule tuning, confidence tweaks)

    Two versions are compatible if they share the same major version.
    """
    major: int
    minor: int
    patch: int

    @classmethod
    def from_string(cls, version_str: str) -> "EngineVersion":
        """
        Parse a semantic version string.

        Raises ValueError if the string is not in 'major.minor.patch' format.
        """
        parts = version_str.split(".")
        if len(parts) != 3:
            raise ValueError(
                f"Invalid version string {version_str!r}: "
                "expected 'major.minor.patch'"
            )
        try:
            return cls(
                major=int(parts[0]),
                minor=int(parts[1]),
                patch=int(parts[2]),
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid version string {version_str!r}: {exc}"
            ) from exc

    @classmethod
    def current(cls) -> "EngineVersion":
        """Return the current engine version."""
        return cls(major=_ENGINE_MAJOR, minor=_ENGINE_MINOR, patch=_ENGINE_PATCH)

    @property
    def as_string(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def is_compatible_with(self, other: "EngineVersion") -> bool:
        """Two versions are compatible if they share the same major version."""
        return self.major == other.major

    def is_newer_than(self, other: "EngineVersion") -> bool:
        return (self.major, self.minor, self.patch) > (other.major, other.minor, other.patch)

    def is_older_than(self, other: "EngineVersion") -> bool:
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def to_dict(self) -> dict[str, Any]:
        return {
            "major":     self.major,
            "minor":     self.minor,
            "patch":     self.patch,
            "as_string": self.as_string,
        }

    def __str__(self) -> str:
        return self.as_string

    def __repr__(self) -> str:
        return f"EngineVersion({self.as_string!r})"
