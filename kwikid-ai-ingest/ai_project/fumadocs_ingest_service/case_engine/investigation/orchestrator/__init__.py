"""
case_engine/investigation/orchestrator/__init__.py

Sprint 2.46: Investigation Orchestrator package public surface.

The InvestigationOrchestrator is the ONLY production entry point for investigation.
All other exports are supporting types and protocols.
"""
from case_engine.investigation.orchestrator.audit import AuditStageRecord, OrchestratorAudit
from case_engine.investigation.orchestrator.exceptions import (
    CancellationError,
    InvalidContextError,
    OrchestratorError,
    SessionAlreadyStartedError,
    StageError,
)
from case_engine.investigation.orchestrator.metrics import (
    OrchestratorGlobalMetrics,
    OrchestratorMetrics,
)
from case_engine.investigation.orchestrator.models import (
    CancellationToken,
    InvestigationSession,
    OrchestratorInvestigationResult,
)
from case_engine.investigation.orchestrator.orchestrator import (
    InvestigationOrchestrator,
    build_investigation_orchestrator,
)
from case_engine.investigation.orchestrator.pipeline import (
    CollectorProtocol,
    KnowledgeEnrichmentProvider,
    ObservationGeneratorProtocol,
    PipelineOrchestrator,
    PlannerProtocol,
    RootCauseEngineProtocol,
)
from case_engine.investigation.orchestrator.serialization import result_to_dict, result_to_json
from case_engine.investigation.orchestrator.state_machine import (
    OrchestratorLifecycleState,
    OrchestratorStateMachine,
)
from case_engine.investigation.orchestrator.versioning import (
    ORCHESTRATOR_SPRINT,
    ORCHESTRATOR_VERSION,
    OrchestratorVersion,
)

__all__ = [
    # Core
    "InvestigationOrchestrator",
    "build_investigation_orchestrator",
    # Session & Result
    "CancellationToken",
    "InvestigationSession",
    "OrchestratorInvestigationResult",
    # State machine
    "OrchestratorLifecycleState",
    "OrchestratorStateMachine",
    # Metrics
    "OrchestratorMetrics",
    "OrchestratorGlobalMetrics",
    # Audit
    "OrchestratorAudit",
    "AuditStageRecord",
    # Pipeline & Protocols
    "PipelineOrchestrator",
    "PlannerProtocol",
    "CollectorProtocol",
    "RootCauseEngineProtocol",
    "ObservationGeneratorProtocol",
    "KnowledgeEnrichmentProvider",
    # Serialization
    "result_to_dict",
    "result_to_json",
    # Exceptions
    "OrchestratorError",
    "StageError",
    "CancellationError",
    "InvalidContextError",
    "SessionAlreadyStartedError",
    # Versioning
    "ORCHESTRATOR_VERSION",
    "ORCHESTRATOR_SPRINT",
    "OrchestratorVersion",
]
