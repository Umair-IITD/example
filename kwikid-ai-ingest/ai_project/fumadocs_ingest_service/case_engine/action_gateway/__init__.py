"""
case_engine/action_gateway/__init__.py

Sprint 2.22: Action Gateway package.

This package shadows the old case_engine/action_gateway.py module.
Python prefers packages over modules at the same path, so all existing
imports of `case_engine.action_gateway` now resolve here.

Backwards compatibility (Sprint 2.1 public API — unchanged):
    from case_engine.action_gateway import ActionGateway
    from case_engine.action_gateway import ActionGatewayError
    from case_engine.action_gateway import DuplicateActionError
    from case_engine.action_gateway import build_action_gateway

Sprint 2.22 additions (new API):
    from case_engine.action_gateway import ActionGatewayService
    from case_engine.action_gateway import build_action_gateway_service
    from case_engine.action_gateway import ProposalGateway
    from case_engine.action_gateway import GatewayRiskEngine
    from case_engine.action_gateway import ApprovalEngine
    from case_engine.action_gateway import (
        ActionGatewayDecision,
        ActionGatewayResult,
        GatewayApprovalDecision,
        GatewayRiskLevel,
        GatewayApprovalStatus,
        GatewayValidationStatus,
    )
"""

# ── Sprint 2.1 backwards-compatible re-exports ────────────────────────────────
from case_engine.action_gateway.execution_gateway import (
    ActionGateway,
    ActionGatewayError,
    DuplicateActionError,
    build_action_gateway,
)

# ── Sprint 2.22 new exports ───────────────────────────────────────────────────
from case_engine.action_gateway.models import (
    ActionGatewayDecision,
    ActionGatewayResult,
    GatewayApprovalDecision,
    GatewayApprovalStatus,
    GatewayRiskLevel,
    GatewayValidationStatus,
)
from case_engine.action_gateway.gateway import ProposalGateway
from case_engine.action_gateway.risk_engine import GatewayRiskEngine
from case_engine.action_gateway.approval_engine import ApprovalEngine
from case_engine.action_gateway.service import (
    ActionGatewayService,
    build_action_gateway_service,
)

__all__ = [
    # Sprint 2.1 — backwards compat
    "ActionGateway",
    "ActionGatewayError",
    "DuplicateActionError",
    "build_action_gateway",
    # Sprint 2.22 — gateway pipeline
    "ActionGatewayService",
    "build_action_gateway_service",
    "ProposalGateway",
    "GatewayRiskEngine",
    "ApprovalEngine",
    # Sprint 2.22 — models
    "ActionGatewayDecision",
    "ActionGatewayResult",
    "GatewayApprovalDecision",
    "GatewayApprovalStatus",
    "GatewayRiskLevel",
    "GatewayValidationStatus",
]
