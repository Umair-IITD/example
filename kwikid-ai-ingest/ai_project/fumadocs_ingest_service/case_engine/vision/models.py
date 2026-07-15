"""
case_engine/vision/models.py

Sprint 2.38: Vision analysis domain models.

Per flow_diagram.mermaid: VISION feeds into HYBRIDRAG as an Evidence Provider.

Defines the structured types for computer vision analysis results:
  VisionModality      — IMAGE / VIDEO / DOCUMENT
  VisionFinding       — what was detected in the visual input
  VisionAnalysisStatus — pipeline status of the analysis
  VisionReference     — pointer to a visual input artefact (S3 key, URL, file path)
  VisionAnalysis      — structured output of a vision provider run
  VisionEvidence      — base class for visual evidence; polymorphic root
  ImageEvidence       — image-specific analysis result
  VideoEvidence       — video-specific analysis result
  DocumentEvidence    — document/PDF analysis result

Note: This module's VideoEvidence is a VISION ANALYSIS result, distinct from
investigation.models.VideoEvidence (an INVESTIGATION TOOL evidence item).
They live in separate namespaces — no naming conflict at runtime.

Dependency direction:
  This module imports ONLY from the standard library.
  It is a leaf dependency node — no case_engine imports allowed.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Enumerations ───────────────────────────────────────────────────────────────

class VisionModality(str, Enum):
    """Category of visual input being analysed."""
    IMAGE    = "IMAGE"
    VIDEO    = "VIDEO"
    DOCUMENT = "DOCUMENT"


class VisionFinding(str, Enum):
    """What was detected in the visual input by the vision provider."""
    CAMERA_ISSUE     = "CAMERA_ISSUE"
    AUDIO_ISSUE      = "AUDIO_ISSUE"
    BLANK_SCREEN     = "BLANK_SCREEN"
    BLURRY_IMAGE     = "BLURRY_IMAGE"
    LIVENESS_FAILURE = "LIVENESS_FAILURE"
    DOCUMENT_ISSUE   = "DOCUMENT_ISSUE"
    NETWORK_ARTIFACT = "NETWORK_ARTIFACT"
    ERROR_MESSAGE    = "ERROR_MESSAGE"
    NORMAL           = "NORMAL"
    UNKNOWN          = "UNKNOWN"


class VisionAnalysisStatus(str, Enum):
    """Pipeline status of a vision analysis run."""
    PENDING     = "PENDING"
    COMPLETED   = "COMPLETED"
    FAILED      = "FAILED"
    SKIPPED     = "SKIPPED"
    UNSUPPORTED = "UNSUPPORTED"


# ── VisionReference ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VisionReference:
    """
    A pointer to a visual input artefact.

    Passed to VisionProvider.analyse(). Never embeds the raw bytes —
    the provider resolves the URI to fetch the content.

    Fields:
        reference_id:  Stable identifier for this visual input
        modality:      IMAGE / VIDEO / DOCUMENT
        uri:           S3 key, file path, or URL (provider-specific)
        content_type:  MIME type (e.g. "image/jpeg", "video/mp4")
        captured_at:   ISO timestamp when the artefact was captured
        description:   Optional human-readable label (e.g. "VKYC session recording")
        metadata:      Provider-specific extra fields
    """
    reference_id:  str
    modality:      VisionModality
    uri:           str
    content_type:  str
    captured_at:   str = field(default_factory=_now_iso)
    description:   str = ""
    metadata:      dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "modality":     self.modality.value,
            "uri":          self.uri,
            "content_type": self.content_type,
            "captured_at":  self.captured_at,
            "description":  self.description,
        }


# ── VisionAnalysis ─────────────────────────────────────────────────────────────

@dataclass
class VisionAnalysis:
    """
    Structured output of a vision provider run.

    One VisionAnalysis corresponds to one VisionReference processed by one
    VisionProvider. Immutable after the provider returns it.

    Fields:
        analysis_id:   UUID for this analysis run
        reference_id:  VisionReference.reference_id this analysis covers
        modality:      Matches VisionReference.modality
        status:        COMPLETED / FAILED / SKIPPED / UNSUPPORTED
        findings:      List of VisionFinding values detected
        confidence:    0.0–1.0 overall confidence in the findings
        raw_output:    Provider-specific raw result dict (for debugging/audit)
        analysed_at:   ISO timestamp when analysis completed
        error_code:    Short error tag if status != COMPLETED
        error_message: Human-readable error if status != COMPLETED
        provider_name: Identifier of the vision provider that ran the analysis
        duration_ms:   Wall-clock time of the analysis
    """
    analysis_id:   str
    reference_id:  str
    modality:      VisionModality
    status:        VisionAnalysisStatus
    findings:      list[VisionFinding]
    confidence:    float
    raw_output:    dict[str, Any]
    analysed_at:   str
    provider_name: str
    error_code:    str | None = None
    error_message: str | None = None
    duration_ms:   int = 0

    def has_finding(self, finding: VisionFinding) -> bool:
        return finding in self.findings

    def is_normal(self) -> bool:
        return self.findings == [VisionFinding.NORMAL] or (
            self.status == VisionAnalysisStatus.COMPLETED and not self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id":   self.analysis_id,
            "reference_id":  self.reference_id,
            "modality":      self.modality.value,
            "status":        self.status.value,
            "findings":      [f.value for f in self.findings],
            "confidence":    self.confidence,
            "analysed_at":   self.analysed_at,
            "provider_name": self.provider_name,
            "error_code":    self.error_code,
            "error_message": self.error_message,
            "duration_ms":   self.duration_ms,
        }


# ── VisionEvidence hierarchy ───────────────────────────────────────────────────

@dataclass
class VisionEvidence:
    """
    Base class for visual analysis evidence.

    Each VisionEvidence pairs a VisionReference (the input pointer) with a
    VisionAnalysis (the structured result). Subtypes carry modality-specific
    fields (OCR text, duration, page count, etc.).

    VisionProvider.analyse() returns this type or one of its subclasses.

    Fields:
        evidence_id:  UUID for this evidence record
        reference:    The visual input that was analysed
        analysis:     The analysis result from the vision provider
        collected_at: ISO timestamp when this evidence was stored
        case_id:      Case this evidence belongs to (empty string in tests)
    """
    evidence_id:  str
    reference:    VisionReference
    analysis:     VisionAnalysis
    collected_at: str
    case_id:      str

    @property
    def modality(self) -> VisionModality:
        return self.reference.modality

    @property
    def status(self) -> VisionAnalysisStatus:
        return self.analysis.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id":  self.evidence_id,
            "case_id":      self.case_id,
            "modality":     self.modality.value,
            "status":       self.status.value,
            "reference":    self.reference.to_dict(),
            "analysis":     self.analysis.to_dict(),
            "collected_at": self.collected_at,
        }


@dataclass
class ImageEvidence(VisionEvidence):
    """
    Vision evidence from a static image.

    Extends VisionEvidence with image-specific extraction results.

    Fields:
        ocr_text:             Text extracted from the image via OCR
        contains_error_dialog: True if the image shows a software error dialog
    """
    ocr_text:              str  = ""
    contains_error_dialog: bool = False

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["ocr_text"] = self.ocr_text
        base["contains_error_dialog"] = self.contains_error_dialog
        return base


@dataclass
class VideoEvidence(VisionEvidence):
    """
    Vision evidence from a video recording (e.g. VKYC session).

    Extends VisionEvidence with video-specific analysis results.

    Fields:
        duration_seconds: Total duration of the video in seconds
        key_timestamps:   ISO timestamps of key events detected in the video
        audio_present:    True if audio track was detected
    """
    duration_seconds: float       = 0.0
    key_timestamps:   list[str]   = field(default_factory=list)
    audio_present:    bool        = False

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["duration_seconds"] = self.duration_seconds
        base["key_timestamps"]   = self.key_timestamps
        base["audio_present"]    = self.audio_present
        return base


@dataclass
class DocumentEvidence(VisionEvidence):
    """
    Vision evidence from a document image or PDF.

    Extends VisionEvidence with document-specific extraction results.

    Fields:
        page_count:     Number of pages in the document
        ocr_text:       Full text extracted from the document via OCR
        document_type:  Classification of the document (e.g. "ID_CARD", "PASSPORT", "BANK_STATEMENT")
        is_legible:     True if the document is readable (not blurry or obscured)
    """
    page_count:    int  = 1
    ocr_text:      str  = ""
    document_type: str  = ""
    is_legible:    bool = True

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["page_count"]    = self.page_count
        base["ocr_text"]      = self.ocr_text
        base["document_type"] = self.document_type
        base["is_legible"]    = self.is_legible
        return base
