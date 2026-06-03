"""
runtime — Sprint 2.6 production runtime assembly and health layer.

Public API:
    build_production_runtime  — compose the full execution stack in one call
    ProductionRuntime         — typed container for all wired components
    HealthService             — service-layer health aggregation
    ServiceHealth             — health snapshot returned by HealthService.check()
"""
from runtime.assembly import ProductionRuntime, build_production_runtime
from runtime.health import HealthService, ServiceHealth

__all__ = [
    "build_production_runtime",
    "ProductionRuntime",
    "HealthService",
    "ServiceHealth",
]
