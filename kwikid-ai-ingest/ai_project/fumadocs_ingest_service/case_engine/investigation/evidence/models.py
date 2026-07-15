"""
case_engine/investigation/evidence/models.py

Sprint 2.38: Extended evidence domain.

Defines EvidencePriority, EvidenceStatus, EvidenceConfidence, EvidenceMetadata,
and EvidenceReference — typed enrichment that complements the base Evidence /
EvidenceBundle types in case_engine/investigation/models.py.

These types are consumed by:
  - InvestigationContext (investigation/context.py): stores EvidenceReference pointers
  - VisionEvidence (vision/models.py): cross-links to collected evidence items
  - RootCauseAnalysis: references supporting evidence by ID + priority

Dependency direction:
  This module imports ONLY from the standard library.
  It is a leaf dependency node — no case_engine imports allowed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# Priority ordering: CRITICAL (0) < HIGH (1) < NORMAL (2) < LOW (3)
_PRIORITY_RANK: tuple[str, ...] = ("CRITICAL", "HIGH", "NORMAL", "LOW")


class EvidencePriority(str, Enum):
    """
    Triage priority of a piece of evidence.

    Ordered from most urgent (CRITICAL) to least urgent (LOW).
    Supports rich comparison so evidence lists can be sorted by priority.
    """
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    NORMAL   = "NORMAL"
    LOW      = "LOW"

    def _rank(self) -> int:
        return _PRIORITY_RANK.index(self.value)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, EvidencePriority):
            return NotImplemented
        return self._rank() < other._rank()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, EvidencePriority):
            return NotImplemented
        return self._rank() > other._rank()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, EvidencePriority):
            return NotImplemented
        return self._rank() <= other._rank()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, EvidencePriority):
            return NotImplemented
        return self._rank() >= other._rank()


class EvidenceStatus(str, Enum):
    """Lifecycle status of an evidence item during collection."""
    PENDING    = "PENDING"
    COLLECTED  = "COLLECTED"
    FAILED     = "FAILED"
    INVALID    = "INVALID"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class EvidenceConfidence:
    """
    Confidence assessment for a single evidence item.

    score is clamped to [0.0, 1.0] in __post_init__.

    Fields:
        score:     0.0 (no confidence) to 1.0 (fully confident)
        rationale: Human-readable reason for the score
        basis:     Machine-readable tag (e.g. "tool_success", "partial", "heuristic")
    """
    score:     float
    rationale: str = ""
    basis:     str = "tool_success"

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", max(0.0, min(1.0, self.score)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "score":     self.score,
            "rationale": self.rationale,
            "basis":     self.basis,
        }


@dataclass(frozen=True)
class EvidenceMetadata:
    """
    Provenance metadata for an evidence item.

    Captures where the evidence came from, how it was collected, and how
    reliable it is considered to be for root cause analysis.

    Fields:
        source_system:     System that produced the raw data
                           (e.g. "freshdesk", "vkyc_platform", "unity_id")
        collection_method: How the evidence was collected
                           (e.g. "api_call", "log_parse", "webhook")
        reliability_tier:  Subjective quality tier:
                           "primary" (direct tool call), "derived" (computed),
                           "inferred" (heuristic)
        tags:              Arbitrary searchable labels
        provenance_url:    Optional URL to the source record for audit trail
    """
    source_system:     str
    collection_method: str
    reliability_tier:  str                = "primary"
    tags:              tuple[str, ...]    = field(default_factory=tuple)
    provenance_url:    str                = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_system":     self.source_system,
            "collection_method": self.collection_method,
            "reliability_tier":  self.reliability_tier,
            "tags":              list(self.tags),
            "provenance_url":    self.provenance_url,
        }


@dataclass(frozen=True)
class EvidenceReference:
    """
    A lightweight pointer to a collected evidence item.

    Does not embed the payload — used by RootCauseAnalysis to reference
    supporting evidence, and by InvestigationContext to cross-link
    vision analyses to their source evidence items.

    Fields:
        evidence_id:   Matches Evidence.evidence_id or VisionAnalysis.analysis_id
        evidence_type: String category (EvidenceType.value or VisionModality.value)
        source_system: Origin system that produced the evidence
        collected_at:  ISO 8601 timestamp of collection
        priority:      Triage priority for ranking this evidence item
    """
    evidence_id:   str
    evidence_type: str
    source_system: str
    collected_at:  str
    priority:      EvidencePriority = EvidencePriority.NORMAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id":   self.evidence_id,
            "evidence_type": self.evidence_type,
            "source_system": self.source_system,
            "collected_at":  self.collected_at,
            "priority":      self.priority.value,
        }
