"""
case_engine/vision/provider.py

Sprint 2.38: VisionProvider abstract interface and NullVisionProvider.

Per flow_diagram.mermaid: VISION → HYBRIDRAG (evidence feed).
Per blueprint Section 31 Knowledge Retrieval Flow: visual analysis feeds
into the hybrid RAG layer as a structured evidence provider.

VisionProvider is the abstract interface for all vision analysis backends:
  - Claude Vision (Anthropic) — future sprint
  - AWS Rekognition — future sprint
  - Azure Computer Vision — future sprint

NullVisionProvider is the default (no-op) implementation that returns
SKIPPED for every request. Used when no vision service is configured.

Dependency direction:
  provider.py → vision/models.py (VisionReference, VisionEvidence, VisionAnalysis, etc.)
  Does NOT import from case_engine/investigation, case_engine/knowledge,
  case_engine/workflows, or case_engine/tools.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum

from case_engine.vision.models import (
    VisionAnalysis,
    VisionAnalysisStatus,
    VisionEvidence,
    VisionReference,
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())

LOGGER = logging.getLogger(__name__)


# ── Capability declaration ─────────────────────────────────────────────────────

class VisionCapability(str, Enum):
    """
    Capability flags that a VisionProvider may support.

    Used to select the appropriate provider for a given VisionReference.
    """
    IMAGE_OCR          = "IMAGE_OCR"
    IMAGE_ANALYSIS     = "IMAGE_ANALYSIS"
    VIDEO_ANALYSIS     = "VIDEO_ANALYSIS"
    DOCUMENT_EXTRACTION = "DOCUMENT_EXTRACTION"
    LIVENESS_CHECK     = "LIVENESS_CHECK"
    FACE_MATCH         = "FACE_MATCH"


# ── Abstract provider ──────────────────────────────────────────────────────────

class VisionProvider(ABC):
    """
    Abstract interface for vision analysis backends.

    Contract:
    - analyse() MUST never raise — return VisionEvidence with status=FAILED
      if the backend errors.
    - is_available() returns False if the provider is not configured.
    - capabilities returns the set of VisionCapability this provider supports.

    Implementors:
    - NullVisionProvider (this module) — no-op, always returns SKIPPED
    - ClaudeVisionProvider (future sprint) — Anthropic Claude vision
    - RekognitionProvider (future sprint) — AWS Rekognition
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier for this provider (e.g. "claude_vision")."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> set[VisionCapability]:
        """The set of VisionCapability this provider supports."""
        ...

    @abstractmethod
    def analyse(self, reference: VisionReference) -> VisionEvidence:
        """
        Analyse a visual input and return structured evidence.

        MUST never raise. On error: return VisionEvidence with
        analysis.status = FAILED and error_code / error_message populated.

        Args:
            reference: Pointer to the visual input artefact.

        Returns:
            VisionEvidence — always. Never None. Never raises.
        """
        ...

    def is_available(self) -> bool:
        """Return True if this provider is configured and reachable."""
        return True

    def supports(self, capability: VisionCapability) -> bool:
        """Return True if this provider supports the given capability."""
        return capability in self.capabilities


# ── Null (no-op) provider ──────────────────────────────────────────────────────

class NullVisionProvider(VisionProvider):
    """
    No-op vision provider. Returns SKIPPED for every analysis request.

    Used as the default implementation when no vision service is configured.
    All calls are safe — never raises, never calls external services.

    is_available() returns False to signal to callers that vision analysis
    is not operational in this deployment.
    """

    @property
    def provider_name(self) -> str:
        return "null_vision"

    @property
    def capabilities(self) -> set[VisionCapability]:
        return set()

    def is_available(self) -> bool:
        return False

    def analyse(self, reference: VisionReference) -> VisionEvidence:
        """Always returns a VisionEvidence with status=SKIPPED. Never raises."""
        analysis = VisionAnalysis(
            analysis_id=_new_id(),
            reference_id=reference.reference_id,
            modality=reference.modality,
            status=VisionAnalysisStatus.SKIPPED,
            findings=[],
            confidence=0.0,
            raw_output={},
            analysed_at=_now_iso(),
            provider_name=self.provider_name,
            error_code=None,
            error_message=None,
            duration_ms=0,
        )
        LOGGER.debug(
            "null_vision_provider.skipped reference_id=%s modality=%s",
            reference.reference_id, reference.modality.value,
        )
        return VisionEvidence(
            evidence_id=_new_id(),
            reference=reference,
            analysis=analysis,
            collected_at=_now_iso(),
            case_id="",
        )
