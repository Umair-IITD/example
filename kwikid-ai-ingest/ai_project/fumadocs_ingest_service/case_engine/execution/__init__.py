"""
case_engine/execution/__init__.py

Sprint 2.23: Execution Layer package.

Public API re-exports.
"""
from case_engine.execution.models import (
    ExecutionStatus,
    VerificationStatus,
    RecoveryStrategy,
    RecoveryStatus,
    ResolutionStatus,
    ExecutionResult,
    ExecutionAttempt,
    ExecutionBundle,
    VerificationResult,
    RecoveryResult,
    ResolutionResult,
)
from case_engine.execution.executor import (
    ExecutionAdapter,
    MockExecutionAdapter,
    ActionExecutor,
    build_action_executor,
)
from case_engine.execution.verification import VerificationEngine
from case_engine.execution.recovery import RecoveryEngine, MAX_RETRIES
from case_engine.execution.resolution import ResolutionEngine
from case_engine.execution.service import ExecutionService, build_execution_service

__all__ = [
    # models
    "ExecutionStatus",
    "VerificationStatus",
    "RecoveryStrategy",
    "RecoveryStatus",
    "ResolutionStatus",
    "ExecutionResult",
    "ExecutionAttempt",
    "ExecutionBundle",
    "VerificationResult",
    "RecoveryResult",
    "ResolutionResult",
    # executor
    "ExecutionAdapter",
    "MockExecutionAdapter",
    "ActionExecutor",
    "build_action_executor",
    # engines
    "VerificationEngine",
    "RecoveryEngine",
    "MAX_RETRIES",
    "ResolutionEngine",
    # service
    "ExecutionService",
    "build_execution_service",
]
