"""
case_engine/actions/__init__.py

Sprint 2.21: Action Proposal domain public API.

Factory:
    build_action_proposal_service() — wires engine + risk engine + optional audit

Public re-exports for consumers:
    ActionProposalService, ActionProposalEngine, RiskAssessmentEngine
    ProposedActionType, ProposalRiskLevel
    ActionProposalBundle, ActionProposalItem, ActionReasoning, ActionRiskAssessment
"""
from __future__ import annotations

from typing import Any

from case_engine.actions.models import (
    ActionProposalBundle,
    ActionProposalItem,
    ActionReasoning,
    ActionRiskAssessment,
    ProposedActionType,
    ProposalRiskLevel,
)
from case_engine.actions.proposal import ActionProposalEngine
from case_engine.actions.risk import RiskAssessmentEngine
from case_engine.actions.service import ActionProposalService


def build_action_proposal_service(
    audit_logger: Any = None,
) -> ActionProposalService:
    """
    Factory for ActionProposalService.

    Wires:
      RiskAssessmentEngine → ActionProposalEngine → ActionProposalService

    audit_logger: optional AuditLogger instance. None = no-op audit.
    """
    risk_engine   = RiskAssessmentEngine()
    proposal_engine = ActionProposalEngine(risk_engine=risk_engine)
    return ActionProposalService(
        proposal_engine=proposal_engine,
        audit_logger=audit_logger,
    )


__all__ = [
    "build_action_proposal_service",
    "ActionProposalService",
    "ActionProposalEngine",
    "RiskAssessmentEngine",
    "ActionProposalBundle",
    "ActionProposalItem",
    "ActionReasoning",
    "ActionRiskAssessment",
    "ProposedActionType",
    "ProposalRiskLevel",
]
