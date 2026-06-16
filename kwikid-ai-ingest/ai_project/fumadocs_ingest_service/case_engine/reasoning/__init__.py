"""
case_engine/reasoning/

Sprint 2.17: Reasoning Framework Foundation (meta-workflow reasoning).
Sprint 2.24: Investigation Reasoning Engine (root-cause → action recommendation).

Public API (Sprint 2.17):
  from case_engine.reasoning import ReasoningEngine, ReasoningContext, ReasoningDecision

Public API (Sprint 2.24):
  from case_engine.reasoning import (
      InvestigationReasoningEngine, ReasoningService,
      ReasoningResult, ReasoningBundle, ReasoningOutcome,
  )
"""
# Sprint 2.17 — meta-workflow reasoning
from case_engine.reasoning.reasoning_models import ReasoningContext, ReasoningDecision, ReasoningStep
from case_engine.reasoning.reasoning_engine import ReasoningEngine

# Sprint 2.24 — investigation reasoning
from case_engine.reasoning.models import (
    ReasoningBundle,
    ReasoningOutcome,
    ReasoningRecommendation,
    ReasoningResult,
    ReasoningTrace,
)
from case_engine.reasoning.engine import InvestigationReasoningEngine, build_reasoning_engine
from case_engine.reasoning.service import ReasoningService, build_reasoning_service

__all__ = [
    # Sprint 2.17
    "ReasoningContext",
    "ReasoningDecision",
    "ReasoningEngine",
    "ReasoningStep",
    # Sprint 2.24
    "InvestigationReasoningEngine",
    "ReasoningBundle",
    "ReasoningOutcome",
    "ReasoningRecommendation",
    "ReasoningResult",
    "ReasoningService",
    "ReasoningTrace",
    "build_reasoning_engine",
    "build_reasoning_service",
]
