"""
case_engine/adapters/__init__.py

Sprint 2.27: Adapter Framework public API.

The Production Adapter Framework provides a unified routing layer between
the workflow execution pipeline and external vendor systems.
"""
from case_engine.adapters.models import (
    AdapterType,
    AdapterOperation,
    AdapterStatus,
    AdapterRequest,
    AdapterResponse,
    AdapterExecutionResult,
)
from case_engine.adapters.base import Adapter
from case_engine.adapters.freshdesk_adapter import FreshdeskAdapter
from case_engine.adapters.portal_adapter import AdminPortalAdapter
from case_engine.adapters.asana_adapter import AsanaAdapter
from case_engine.adapters.monitoring_adapter import MonitoringAdapter
from case_engine.adapters.adapter_registry import AdapterRegistry, DuplicateAdapterError
from case_engine.adapters.adapter_router import AdapterRouter
from case_engine.adapters.execution_adapter import (
    AdapterBackedExecutionAdapter,
    get_adapter_for_action,
    list_routable_action_types,
)

__all__ = [
    # models
    "AdapterType",
    "AdapterOperation",
    "AdapterStatus",
    "AdapterRequest",
    "AdapterResponse",
    "AdapterExecutionResult",
    # base
    "Adapter",
    # concrete adapters
    "FreshdeskAdapter",
    "AdminPortalAdapter",
    "AsanaAdapter",
    "MonitoringAdapter",
    # registry
    "AdapterRegistry",
    "DuplicateAdapterError",
    # router
    "AdapterRouter",
    # execution bridge
    "AdapterBackedExecutionAdapter",
    "get_adapter_for_action",
    "list_routable_action_types",
]
