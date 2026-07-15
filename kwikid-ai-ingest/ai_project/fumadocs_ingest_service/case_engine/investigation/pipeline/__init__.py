"""
case_engine/investigation/pipeline/__init__.py

Sprint 2.47: Business Pipeline Integration — public surface.

This package defines the canonical Investigation Pipeline Contract and
supporting utilities for stage-level testing, validation, and documentation.

The production entry point for investigation remains InvestigationOrchestrator
(Sprint 2.46). This package adds the formal stage contract so that every
pipeline component can be tested in isolation and the pipeline structure
is machine-readable.
"""
from case_engine.investigation.pipeline.contract import (
    InvestigationStage,
    StageSeverity,
    StageResult,
    StageStatus,
)
from case_engine.investigation.pipeline.guard import (
    ContextIntegrityError,
    ContextIntegrityGuard,
)
from case_engine.investigation.pipeline.manifest import (
    PipelineManifest,
    StageDescriptor,
    get_default_manifest,
)
from case_engine.investigation.pipeline.stages import (
    STAGE_COLLECTION,
    STAGE_KNOWLEDGE,
    STAGE_OBSERVATION,
    STAGE_PLANNING,
    STAGE_ROOT_CAUSE,
    STAGE_VALIDATE,
    CollectionStage,
    KnowledgeStage,
    ObservationStage,
    PlanningStage,
    RootCauseStage,
    ValidateStage,
)

__all__ = [
    # Contract
    "InvestigationStage",
    "StageSeverity",
    "StageResult",
    "StageStatus",
    # Stages
    "ValidateStage",
    "PlanningStage",
    "CollectionStage",
    "KnowledgeStage",
    "RootCauseStage",
    "ObservationStage",
    # Stage name constants
    "STAGE_VALIDATE",
    "STAGE_PLANNING",
    "STAGE_COLLECTION",
    "STAGE_KNOWLEDGE",
    "STAGE_ROOT_CAUSE",
    "STAGE_OBSERVATION",
    # Manifest
    "PipelineManifest",
    "StageDescriptor",
    "get_default_manifest",
    # Guard
    "ContextIntegrityError",
    "ContextIntegrityGuard",
]
