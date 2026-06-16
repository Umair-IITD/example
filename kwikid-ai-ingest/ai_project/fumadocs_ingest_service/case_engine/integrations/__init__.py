"""
case_engine/integrations

Sprint 2.27.5: Universal integration routing layer.

Exposes RouterService as the single dispatch point for all action routing.
Every action execution passes through this layer before reaching adapters.

Blueprint principle: "No external action may bypass Action Gateway" (Section 16).
This layer enforces that principle at the integration level.
"""
from case_engine.integrations.router_service import (
    RouterResult,
    RouterService,
    build_router_service,
)

__all__ = [
    "RouterResult",
    "RouterService",
    "build_router_service",
]
