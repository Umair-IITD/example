"""
case_engine/knowledge/sop

Sprint 2.38 (original) + Sprint 2.41 (expanded): SOP Repository package.

Blueprint Section 6: SOP Repository contains support SOPs, resolution
procedures, and escalation rules.

Public API — models:
  SOPDocument, SOPStep, SOPVersion, SOPStatus, SOPActionType
  SOPStepKind, SOPParameter, SOPExecutionHints, SOPOutcome, SOPReference
  SOPCondition, SOPDecision, SOPInstruction, SOPProcedure
  SOPTriggerCondition, SOPTriggerOperator

Public API — repository:
  SOPRepository (ABC), SOPRegistry (Sprint 2.38 simple)
  SOPDocumentRepository, RepositorySOPStats

Public API — resolver:
  SOPDocumentResolver

Public API — loader / registry:
  SOPLoader
  get_default_sop_registry, get_default_sop_resolver, reset_sop_registry

Public API — validators / exceptions / versioning / serialization:
  SOPValidator, SOPSerializer, SOPSemanticVersion
  SOPError, SOPNotFoundError, SOPValidationError, DuplicateSOPError
  SOPVersionConflictError, SOPRegistryError, SOPGraphCycleError

Public API — defaults:
  build_defaults, get_minimal_sop

Legacy Sprint 2.38:
  SOPProvider, SOPResolver (KnowledgeProvider adapter — distinct from SOPDocumentResolver)
"""

# ── Models ──────────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.models import (
    SOPActionType,
    SOPCondition,
    SOPDecision,
    SOPDocument,
    SOPExecutionHints,
    SOPInstruction,
    SOPOutcome,
    SOPParameter,
    SOPProcedure,
    SOPReference,
    SOPStatus,
    SOPStep,
    SOPStepKind,
    SOPTriggerCondition,
    SOPTriggerOperator,
    SOPVersion,
)

# ── Exceptions ───────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.exceptions import (
    DuplicateSOPError,
    SOPError,
    SOPGraphCycleError,
    SOPNotFoundError,
    SOPRegistryError,
    SOPValidationError,
    SOPVersionConflictError,
)

# ── Versioning ───────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.versioning import SOPSemanticVersion

# ── Validators ───────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.validators import SOPValidator

# ── Serialization ────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.serialization import SOPSerializer

# ── Repository ───────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.repository import (
    RepositorySOPStats,
    SOPDocumentRepository,
    SOPRegistry,
    SOPRepository,
)

# ── Resolver ─────────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.resolver import SOPDocumentResolver

# ── Loader ───────────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.loader import SOPLoader

# ── Registry (singleton) ─────────────────────────────────────────────────────
from case_engine.knowledge.sop.registry import (
    get_default_sop_registry,
    get_default_sop_resolver,
    reset_sop_registry,
)

# ── Defaults ─────────────────────────────────────────────────────────────────
from case_engine.knowledge.sop.defaults import build_defaults, get_minimal_sop

# ── Legacy Sprint 2.38 provider adapter ──────────────────────────────────────
from case_engine.knowledge.sop.provider import SOPProvider, SOPResolver

__all__ = [
    # models
    "SOPDocument", "SOPStep", "SOPVersion", "SOPStatus", "SOPActionType",
    "SOPStepKind", "SOPParameter", "SOPExecutionHints", "SOPOutcome",
    "SOPReference", "SOPCondition", "SOPDecision", "SOPInstruction",
    "SOPProcedure", "SOPTriggerCondition", "SOPTriggerOperator",
    # exceptions
    "SOPError", "SOPNotFoundError", "SOPValidationError", "DuplicateSOPError",
    "SOPVersionConflictError", "SOPRegistryError", "SOPGraphCycleError",
    # versioning
    "SOPSemanticVersion",
    # validators
    "SOPValidator",
    # serialization
    "SOPSerializer",
    # repository
    "SOPRepository", "SOPRegistry", "SOPDocumentRepository", "RepositorySOPStats",
    # resolver
    "SOPDocumentResolver",
    # loader
    "SOPLoader",
    # registry
    "get_default_sop_registry", "get_default_sop_resolver", "reset_sop_registry",
    # defaults
    "build_defaults", "get_minimal_sop",
    # legacy provider
    "SOPProvider", "SOPResolver",
]
