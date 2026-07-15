"""
case_engine/workflows/playbooks/versioning.py

Sprint 2.40: WorkflowVersion — semantic version for investigation playbooks.

Supports major.minor.patch comparisons, deprecation tracking,
and string serialization. Immutable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True, order=True)
class WorkflowVersion:
    """
    Semantic version for a WorkflowPlaybook.

    Supports rich comparison (< > == != <= >=) so repositories can order
    playbooks and pick the latest version automatically.

    String format: "major.minor.patch"  (e.g., "1.0.0", "2.3.1")

    Deprecation:
        deprecated=True marks a version as no longer recommended.
        The repository will prefer non-deprecated versions when resolving.
        A deprecated version can still be retrieved by explicit lookup.

    Replacement:
        replacement is the version string of the recommended replacement
        (populated when deprecating a version).
    """
    major:       int
    minor:       int
    patch:       int
    deprecated:  bool = False
    replacement: str | None = None

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __repr__(self) -> str:
        dep = " [DEPRECATED]" if self.deprecated else ""
        return f"WorkflowVersion({self!s}{dep})"

    def is_compatible_with(self, other: WorkflowVersion) -> bool:
        """True if this version has the same major version as other (semver compat)."""
        return self.major == other.major

    def deprecate(self, replacement: str | None = None) -> WorkflowVersion:
        """Return a new WorkflowVersion marked as deprecated."""
        return WorkflowVersion(
            major=self.major,
            minor=self.minor,
            patch=self.patch,
            deprecated=True,
            replacement=replacement,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "major":       self.major,
            "minor":       self.minor,
            "patch":       self.patch,
            "version_str": str(self),
            "deprecated":  self.deprecated,
            "replacement": self.replacement,
        }

    @classmethod
    def parse(cls, version_str: str) -> WorkflowVersion:
        """
        Parse a version string "major.minor.patch".

        Raises ValueError for invalid format.
        """
        m = _VERSION_PATTERN.match(version_str.strip())
        if not m:
            raise ValueError(
                f"Invalid version string {version_str!r}. "
                f"Expected format: major.minor.patch (e.g., '1.0.0')"
            )
        return cls(major=int(m.group(1)), minor=int(m.group(2)), patch=int(m.group(3)))

    @classmethod
    def v1(cls) -> WorkflowVersion:
        return cls(major=1, minor=0, patch=0)

    @classmethod
    def v2(cls) -> WorkflowVersion:
        return cls(major=2, minor=0, patch=0)
