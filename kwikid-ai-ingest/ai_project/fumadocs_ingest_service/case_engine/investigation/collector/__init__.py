"""
case_engine/investigation/collector/__init__.py

Sprint 2.42: Evidence Collector Engine — public API.

The Evidence Collector Engine executes an InvestigationPlan produced by
InvestigationPlanner and collects evidence from registered EvidenceProviders.

Primary entry point:
  EvidenceCollector — instantiate with a ProviderRegistry, call collect()

Provider registration:
  ProviderRegistry      — register EvidenceProviders by EvidenceKind
  build_default_registry() — factory with NullProvider fallback

Data contracts:
  CollectionContext  — lightweight context passed to every provider
  CollectionResult   — what a provider returns
  EvidenceProvider   — base Protocol all providers must satisfy
  ToolProvider       — protocol for tool-based evidence
  KnowledgeProvider  — protocol for knowledge evidence
  SOPProvider        — protocol for SOP evidence
  VisionProvider     — protocol for vision evidence
  WorkflowProvider   — protocol for workflow evidence

Execution types:
  StepStatus           — lifecycle status of a step execution
  StepExecutionRecord  — complete record of one step execution
  PlanExecutionSummary — aggregate of all step records

Metrics:
  StepMetric        — per-step metric snapshot
  CollectionMetrics — aggregate metrics for one plan run

Validation:
  CollectorValidator — validates results, preconditions, and plans

Exceptions:
  CollectorError              — base exception
  CollectorConfigurationError — bad registry setup
  ProviderNotFoundError       — no provider for EvidenceKind
  ProviderExecutionError      — provider raised unexpectedly
  StepPreconditionError       — precondition not met
  PlanExecutionError          — plan cannot proceed
  CollectorTimeoutError       — step exceeded time budget
"""
from __future__ import annotations

from case_engine.investigation.collector.collector import EvidenceCollector
from case_engine.investigation.collector.contracts import (
    CollectionContext,
    CollectionResult,
    EvidenceProvider,
    KnowledgeProvider,
    SOPProvider,
    ToolProvider,
    VisionProvider,
    WorkflowProvider,
)
from case_engine.investigation.collector.exceptions import (
    CollectorConfigurationError,
    CollectorError,
    CollectorTimeoutError,
    PlanExecutionError,
    ProviderExecutionError,
    ProviderNotFoundError,
    StepPreconditionError,
)
from case_engine.investigation.collector.execution import (
    PlanExecutionSummary,
    StepExecutionRecord,
    StepStatus,
)
from case_engine.investigation.collector.metrics import CollectionMetrics, StepMetric
from case_engine.investigation.collector.registry import ProviderRegistry, build_default_registry
from case_engine.investigation.collector.validators import CollectorValidator

__all__ = [
    # Main engine
    "EvidenceCollector",
    # Registry
    "ProviderRegistry",
    "build_default_registry",
    # Data contracts
    "CollectionContext",
    "CollectionResult",
    "EvidenceProvider",
    "ToolProvider",
    "KnowledgeProvider",
    "SOPProvider",
    "VisionProvider",
    "WorkflowProvider",
    # Execution types
    "StepStatus",
    "StepExecutionRecord",
    "PlanExecutionSummary",
    # Metrics
    "StepMetric",
    "CollectionMetrics",
    # Validation
    "CollectorValidator",
    # Exceptions
    "CollectorError",
    "CollectorConfigurationError",
    "ProviderNotFoundError",
    "ProviderExecutionError",
    "StepPreconditionError",
    "PlanExecutionError",
    "CollectorTimeoutError",
]
