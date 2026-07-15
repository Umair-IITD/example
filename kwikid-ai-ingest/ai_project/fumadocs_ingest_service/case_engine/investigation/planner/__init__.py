"""
case_engine/investigation/planner/__init__.py

Sprint 2.39: Investigation Planner package.

Backward-compatible re-export of Sprint 2.18 legacy planner so all existing
imports continue to work unchanged:
  from case_engine.investigation.planner import InvestigationPlanner
  from case_engine.investigation.planner import _extract_case_id

Sprint 2.39 evidence-based planner:
  from case_engine.investigation.planner.engine import InvestigationPlanner as EvidencePlanningEngine

Sprint 2.39 domain models:
  from case_engine.investigation.planner.models import InvestigationPlan, PlanningStep, ...
"""
from case_engine.investigation.planner._legacy import InvestigationPlanner, _extract_case_id

__all__ = ["InvestigationPlanner", "_extract_case_id"]
