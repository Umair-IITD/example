"""
case_engine/investigation/observation

Sprint 2.44: Observation Generator package.

Public surface (callers import from here):

  from case_engine.investigation.observation import ObservationGenerator
  from case_engine.investigation.observation import Observation
  from case_engine.investigation.observation import ObservationStatus
  from case_engine.investigation.observation import build_default_registry
  from case_engine.investigation.observation import observation_to_dict
  from case_engine.investigation.observation import observation_from_dict

Architectural invariant:
  This package ONLY formats. The ObservationGenerator converts a
  RootCauseAnalysis into an Observation — it never reasons, retrieves,
  calls tools/providers/APIs, invokes LLMs, or modifies analysis data.
"""
from case_engine.investigation.observation.contracts import (
    ObservationGeneratorProtocol,
    ObservationTemplate,
)
from case_engine.investigation.observation.exceptions import (
    InvalidAnalysisError,
    InvalidInputError,
    ObservationConfigurationError,
    ObservationDeserializationError,
    ObservationError,
    ObservationSerializationError,
    ObservationValidationError,
    TemplateNotFoundError,
    TemplateRenderError,
)
from case_engine.investigation.observation.generator import ObservationGenerator
from case_engine.investigation.observation.metrics import (
    GeneratorMetrics,
    ObservationMetric,
)
from case_engine.investigation.observation.models import (
    DecisionTraceSummary,
    InvestigationStepRecord,
    Observation,
    ObservationAuditMetadata,
    ObservationDraft,
    ObservationStatus,
    REQUIRED_SECTION_HEADERS,
    TimelineEntry,
)
from case_engine.investigation.observation.registry import (
    TemplateRegistry,
    build_default_registry,
)
from case_engine.investigation.observation.serialization import (
    observation_from_dict,
    observation_from_json,
    observation_to_dict,
    observation_to_json,
)
from case_engine.investigation.observation.versioning import (
    CURRENT_GENERATOR_VERSION,
    CURRENT_TEMPLATE_REGISTRY_VERSION,
    GeneratorVersion,
)

__all__ = [
    # Generator
    "ObservationGenerator",
    # Models
    "Observation",
    "ObservationStatus",
    "ObservationDraft",
    "ObservationAuditMetadata",
    "TimelineEntry",
    "InvestigationStepRecord",
    "DecisionTraceSummary",
    "REQUIRED_SECTION_HEADERS",
    # Contracts
    "ObservationTemplate",
    "ObservationGeneratorProtocol",
    # Registry
    "TemplateRegistry",
    "build_default_registry",
    # Serialization
    "observation_to_dict",
    "observation_to_json",
    "observation_from_dict",
    "observation_from_json",
    # Versioning
    "GeneratorVersion",
    "CURRENT_GENERATOR_VERSION",
    "CURRENT_TEMPLATE_REGISTRY_VERSION",
    # Metrics
    "GeneratorMetrics",
    "ObservationMetric",
    # Exceptions
    "ObservationError",
    "ObservationConfigurationError",
    "TemplateNotFoundError",
    "TemplateRenderError",
    "InvalidAnalysisError",
    "InvalidInputError",
    "ObservationValidationError",
    "ObservationSerializationError",
    "ObservationDeserializationError",
]
