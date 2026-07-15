"""
case_engine/investigation/root_cause/__init__.py

Sprint 2.43: Public API for the Root Cause Engine package.

Primary entry points:
  RootCauseEngine          — analyze(bundle, context?) → RootCauseAnalysis
  RootCauseAnalysis        — rich, fully auditable analysis result
  RuleEvaluationContext    — context passed to every rule
  build_default_registry() — construct a registry with all 11 standard rules

All public types are importable directly from this package:
  from case_engine.investigation.root_cause import RootCauseEngine, RootCauseAnalysis
"""
from __future__ import annotations

from case_engine.investigation.root_cause.contracts import (
    RuleEvaluationContext,
    RuleEvaluator,
    RuleResult,
)
from case_engine.investigation.root_cause.engine import RootCauseEngine
from case_engine.investigation.root_cause.exceptions import (
    EngineTimeoutError,
    InvalidEvidenceError,
    RootCauseConfigurationError,
    RootCauseError,
    RuleExecutionError,
    RuleNotFoundError,
)
from case_engine.investigation.root_cause.metrics import EngineMetrics, RuleExecutionMetric
from case_engine.investigation.root_cause.models import (
    AuditMetadata,
    ConfidenceAdjustment,
    ConfidenceBreakdown,
    ContradictionRecord,
    ContradictionSeverity,
    DecisionTrace,
    EscalationLevel,
    EscalationRecommendation,
    EvidenceReference,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
    RuleMatch,
    RuleMatchStatus,
)
from case_engine.investigation.root_cause.registry import (
    RegistryStatistics,
    RuleRegistry,
    build_default_registry,
)
from case_engine.investigation.root_cause.serialization import RootCauseSerializer
from case_engine.investigation.root_cause.validators import RootCauseValidator
from case_engine.investigation.root_cause.versioning import (
    CURRENT_ENGINE_VERSION,
    CURRENT_REGISTRY_VERSION,
    EngineVersion,
)

__all__ = [
    # Engine
    "RootCauseEngine",
    # Analysis output
    "RootCauseAnalysis",
    # Contracts
    "RuleEvaluationContext",
    "RuleEvaluator",
    "RuleResult",
    # Registry
    "RuleRegistry",
    "RegistryStatistics",
    "build_default_registry",
    # Models
    "RootCauseCategory",
    "RecommendedAction",
    "RuleMatchStatus",
    "EscalationLevel",
    "ContradictionSeverity",
    "EvidenceReference",
    "ConfidenceAdjustment",
    "ConfidenceBreakdown",
    "RuleMatch",
    "ContradictionRecord",
    "DecisionTrace",
    "EscalationRecommendation",
    "AuditMetadata",
    # Exceptions
    "RootCauseError",
    "RootCauseConfigurationError",
    "RuleNotFoundError",
    "RuleExecutionError",
    "InvalidEvidenceError",
    "EngineTimeoutError",
    # Utilities
    "RootCauseSerializer",
    "RootCauseValidator",
    "EngineMetrics",
    "RuleExecutionMetric",
    "EngineVersion",
    "CURRENT_ENGINE_VERSION",
    "CURRENT_REGISTRY_VERSION",
]
