"""
case_engine/knowledge/sop/versioning.py

Sprint 2.41: SOPSemanticVersion — semantic versioning for SOP documents.

Mirrors the WorkflowVersion design from Sprint 2.40 but for the SOP domain.
Supports full comparison ordering, deprecation, compatibility checks, and
parse from string.

Note: The existing SOPVersion in models.py is a changelog record (who changed
what and when). SOPSemanticVersion is the numeric version for ordering and
compatibility logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, order=False)
class SOPSemanticVersion:
    """
    Semantic version for a SOP document: major.minor.patch.

    - Two SOPs are compatible if they share the same major version.
    - Comparison operators produce a total ordering (major, minor, patch).
    - Deprecation returns a new instance with deprecated=True.
    """

    major: int
    minor: int
    patch: int
    deprecated: bool = False
    replacement: str | None = None

    # ── String representation ──────────────────────────────────────────────────

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __repr__(self) -> str:
        return f"SOPSemanticVersion({self!s})"

    # ── Comparison operators ───────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SOPSemanticVersion):
            return NotImplemented
        return (self.major, self.minor, self.patch) == (other.major, other.minor, other.patch)

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SOPSemanticVersion):
            return NotImplemented
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SOPSemanticVersion):
            return NotImplemented
        return (self.major, self.minor, self.patch) <= (other.major, other.minor, other.patch)

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, SOPSemanticVersion):
            return NotImplemented
        return (self.major, self.minor, self.patch) > (other.major, other.minor, other.patch)

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, SOPSemanticVersion):
            return NotImplemented
        return (self.major, self.minor, self.patch) >= (other.major, other.minor, other.patch)

    # ── Compatibility ──────────────────────────────────────────────────────────

    def is_compatible(self, other: SOPSemanticVersion) -> bool:
        """Return True if other is compatible with this version (same major)."""
        return self.major == other.major

    # ── Mutation (returns new instance — frozen dataclass) ─────────────────────

    def deprecate(self, replacement: str | None = None) -> SOPSemanticVersion:
        """Return a new SOPSemanticVersion marked as deprecated."""
        return SOPSemanticVersion(
            major=self.major,
            minor=self.minor,
            patch=self.patch,
            deprecated=True,
            replacement=replacement,
        )

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "major":       self.major,
            "minor":       self.minor,
            "patch":       self.patch,
            "version_str": str(self),
            "deprecated":  self.deprecated,
            "replacement": self.replacement,
        }

    # ── Factories ──────────────────────────────────────────────────────────────

    @classmethod
    def parse(cls, version_str: str) -> SOPSemanticVersion:
        """
        Parse a version string into a SOPSemanticVersion.

        Accepts:
          "1.0"     → SOPSemanticVersion(1, 0, 0)
          "1.0.0"   → SOPSemanticVersion(1, 0, 0)
          "2.3.1"   → SOPSemanticVersion(2, 3, 1)

        Raises SOPVersionConflictError on invalid format.
        """
        from case_engine.knowledge.sop.exceptions import SOPVersionConflictError

        raw = version_str.strip()
        parts = raw.split(".")
        if len(parts) not in (2, 3):
            raise SOPVersionConflictError(
                f"Invalid SOP version format: {version_str!r}. "
                f"Expected 'major.minor' or 'major.minor.patch'."
            )
        try:
            major = int(parts[0])
            minor = int(parts[1])
            patch = int(parts[2]) if len(parts) == 3 else 0
        except ValueError as exc:
            raise SOPVersionConflictError(
                f"Non-numeric component in SOP version: {version_str!r}"
            ) from exc
        return cls(major=major, minor=minor, patch=patch)

    @classmethod
    def v1(cls) -> SOPSemanticVersion:
        """Return the canonical 1.0.0 version."""
        return cls(major=1, minor=0, patch=0)

    @classmethod
    def v2(cls) -> SOPSemanticVersion:
        """Return the canonical 2.0.0 version."""
        return cls(major=2, minor=0, patch=0)
