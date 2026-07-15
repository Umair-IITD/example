"""
case_engine/vision — Vision analysis provider interface and models.

Public API:
  VisionProvider      — abstract provider (analyse reference → evidence)
  NullVisionProvider  — no-op implementation (returns SKIPPED)
  VisionCapability    — capability flags for providers
  VisionReference     — pointer to a visual input artefact
  VisionAnalysis      — structured analysis result
  VisionEvidence      — base evidence class for visual analysis outputs
  ImageEvidence       — image-specific evidence
  VideoEvidence       — video-specific evidence
  DocumentEvidence    — document-specific evidence
  VisionModality      — IMAGE / VIDEO / DOCUMENT
  VisionFinding       — what was detected in the visual input
  VisionAnalysisStatus — PENDING / COMPLETED / FAILED / SKIPPED / UNSUPPORTED
"""
from case_engine.vision.models import (
    DocumentEvidence,
    ImageEvidence,
    VideoEvidence,
    VisionAnalysis,
    VisionAnalysisStatus,
    VisionEvidence,
    VisionFinding,
    VisionModality,
    VisionReference,
)
from case_engine.vision.provider import (
    NullVisionProvider,
    VisionCapability,
    VisionProvider,
)

__all__ = [
    # Provider
    "VisionProvider",
    "NullVisionProvider",
    "VisionCapability",
    # Models
    "VisionModality",
    "VisionFinding",
    "VisionAnalysisStatus",
    "VisionReference",
    "VisionAnalysis",
    "VisionEvidence",
    "ImageEvidence",
    "VideoEvidence",
    "DocumentEvidence",
]
