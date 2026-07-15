"""
case_engine/investigation

Sprint 2.18: Investigation Layer public API.

Canonical import path for callers:
  from case_engine.investigation import InvestigationService, build_investigation_service
"""
from case_engine.investigation._collector_sprint218 import EvidenceCollector
from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    InvestigationPlan,
    InvestigationResult,
    InvestigationStep,
    LogEvidence,
    MetricEvidence,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
    SessionEvidence,
    SummaryEvidence,
    UserEvidence,
    VideoEvidence,
)
from case_engine.investigation._observation_sprint218 import ObservationGenerator
from case_engine.investigation.planner import InvestigationPlanner
from case_engine.investigation._root_cause_sprint218 import RootCauseEngine
from case_engine.investigation.service import InvestigationService

__all__ = [
    # Service
    "InvestigationService",
    "build_investigation_service",
    # Components
    "InvestigationPlanner",
    "EvidenceCollector",
    "RootCauseEngine",
    "ObservationGenerator",
    # Models
    "InvestigationPlan",
    "InvestigationStep",
    "EvidenceBundle",
    "EvidenceType",
    "EvidenceSource",
    "UserEvidence",
    "SessionEvidence",
    "LogEvidence",
    "SummaryEvidence",
    "VideoEvidence",
    "MetricEvidence",
    "RootCauseAnalysis",
    "RootCauseCategory",
    "RecommendedAction",
    "InvestigationResult",
]


def build_investigation_service(
    tool_executor: object,
    audit_logger: object | None = None,
) -> InvestigationService:
    """
    Factory: build a fully wired InvestigationService.

    Args:
        tool_executor: ToolExecutor instance from the tool layer.
        audit_logger:  AuditLogger instance (optional; pass None in tests).

    Returns:
        Ready-to-use InvestigationService.
    """
    planner   = InvestigationPlanner()
    collector = EvidenceCollector(tool_executor)  # type: ignore[arg-type]
    rca       = RootCauseEngine()
    obs       = ObservationGenerator()
    return InvestigationService(
        planner=planner,
        collector=collector,
        rca_engine=rca,
        obs_gen=obs,
        audit_logger=audit_logger,  # type: ignore[arg-type]
    )
