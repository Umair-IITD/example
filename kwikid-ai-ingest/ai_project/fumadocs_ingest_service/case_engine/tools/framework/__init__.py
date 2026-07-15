"""
case_engine/tools/framework/__init__.py

Sprint 2.45: Production Tool Framework — public surface.

This package provides the complete production tool execution layer:
  models       → all domain model classes and enums
  versioning   → framework version constants and FrameworkVersion
  lifecycle    → ToolLifecycleManager, ToolLifecycleStatus
  metrics      → ToolFrameworkMetrics
  registry     → ProductionToolRegistry
  executor     → ProductionToolExecutor
  adapters     → ToolAdapter ABC and Protocol stubs
  pipeline     → ExecutionPipeline
  bridge       → ToolExecutorProvider (Evidence Collector bridge)
  validators   → ToolFrameworkValidators
  serialization → JSON roundtrip helpers
"""
from case_engine.tools.framework.adapters import ToolAdapter
from case_engine.tools.framework.executor import ProductionToolExecutor
from case_engine.tools.framework.lifecycle import ToolLifecycleManager, ToolLifecycleStatus
from case_engine.tools.framework.metrics import ToolFrameworkMetrics
from case_engine.tools.framework.models import (
    ToolAuthenticationRequirement,
    ToolAvailability,
    ToolContext,
    ToolDependency,
    ToolExecutionAudit,
    ToolExecutionError,
    ToolExecutionMetrics,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionTrace,
    ToolHealth,
    ToolMetadata,
    ToolPermissionRequirement,
    ToolRetryPolicy,
    ToolScope,
    ToolStatus,
    ToolTimeout,
)
from case_engine.tools.framework.pipeline import ExecutionPipeline
from case_engine.tools.framework.registry import ProductionToolRegistry
from case_engine.tools.framework.validators import ToolFrameworkValidators
from case_engine.tools.framework.versioning import (
    CURRENT_EXECUTOR_VERSION,
    CURRENT_FRAMEWORK_VERSION,
    CURRENT_REGISTRY_VERSION,
    FrameworkVersion,
)

__all__ = [
    # Version
    "CURRENT_FRAMEWORK_VERSION",
    "CURRENT_REGISTRY_VERSION",
    "CURRENT_EXECUTOR_VERSION",
    "FrameworkVersion",
    # Models
    "ToolStatus",
    "ToolTimeout",
    "ToolRetryPolicy",
    "ToolScope",
    "ToolDependency",
    "ToolAuthenticationRequirement",
    "ToolPermissionRequirement",
    "ToolMetadata",
    "ToolContext",
    "ToolExecutionRequest",
    "ToolExecutionMetrics",
    "ToolExecutionAudit",
    "ToolExecutionTrace",
    "ToolHealth",
    "ToolAvailability",
    "ToolExecutionError",
    "ToolExecutionResult",
    # Lifecycle
    "ToolLifecycleStatus",
    "ToolLifecycleManager",
    # Metrics
    "ToolFrameworkMetrics",
    # Registry
    "ProductionToolRegistry",
    # Executor
    "ProductionToolExecutor",
    # Adapters
    "ToolAdapter",
    # Pipeline
    "ExecutionPipeline",
    # Validators
    "ToolFrameworkValidators",
]
