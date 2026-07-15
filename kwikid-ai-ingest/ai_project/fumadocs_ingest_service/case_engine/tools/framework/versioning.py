"""
case_engine/tools/framework/versioning.py

Sprint 2.45: Tool Framework semantic versioning.

Independent version line from observation (2.44.x) and root-cause (2.43.x).
"""
from __future__ import annotations

from dataclasses import dataclass


CURRENT_FRAMEWORK_VERSION: str = "2.45.0"
CURRENT_REGISTRY_VERSION: str  = "2.45.0"
CURRENT_EXECUTOR_VERSION: str  = "2.45.0"


@dataclass(frozen=True)
class FrameworkVersion:
    """Parsed semantic version for the Tool Framework."""
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse(cls, version_str: str) -> "FrameworkVersion":
        """Parse a semver string. Raises ValueError on bad input."""
        parts = version_str.strip().split(".")
        if len(parts) != 3:
            raise ValueError(f"Invalid version string: {version_str!r}")
        return cls(int(parts[0]), int(parts[1]), int(parts[2]))

    def is_compatible_with(self, other: "FrameworkVersion") -> bool:
        """Return True if this version is backward-compatible with other (same major)."""
        return self.major == other.major
